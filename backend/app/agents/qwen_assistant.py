"""Qwen-Agent orchestration for chat turns (MASTER_SPEC §4.3).

`run_turn` drives `qwen_agent.agents.Assistant` — the Qwen-Agent FnCall
loop plus the app's `BaseTool` set (see `app/agents/tools.py`) — on a
worker thread, translating each generator yield (an accumulated list of
`Message`s from the streaming Ollama call) into the SSE event contract
consumed by `app/api/chat.py` and the frontend:

    thinking  {"text": <delta>}   (reasoning_content, reasoning models)
    token     {"text": <delta>}
    tool_*    (emitted by the tools; monotonic per-turn index)
    done | cancelled | error      (exactly one terminal event; `done` also
                                   carries `usage` — the last LLM call's
                                   prompt/completion/total tokens)

Cancellation: the user-Stop `asyncio.Event` is observed (a) by the driver
between yields and (b) inside every awaited tool call, which cancels the
scheduled future — the sandbox kills its container on the same event. An
in-flight *LLM* stream cannot be interrupted (Qwen-Agent's blocking
OpenAI-compatible client keeps no abortable handle); the abort is
therefore bounded by at most one further LLM call before the worker
thread notices the flag. The in-progress HTTP stream is left to finish
and its response is discarded.
"""

import asyncio
import logging
from collections.abc import Iterator

from qwen_agent.agents.assistant import Assistant
from qwen_agent.llm.oai import TextChatAtOAI
from qwen_agent.llm.schema import FUNCTION, SYSTEM

from app.core.settings import get_settings

from .runtime import TurnRuntime
from .tools import build_tools

logger = logging.getLogger(__name__)

#: Generate settings for the shared Ollama models. `max_input_tokens` keeps
#: the (RAG-injected) prompt within the 32k-class Ollama context windows;
#: `request_timeout` is the per-chunk read timeout of the OpenAI client.
DEFAULT_TEMPERATURE = 0.3
DEFAULT_MAX_TOKENS = 8192
DEFAULT_MAX_INPUT_TOKENS = 24000
DEFAULT_REQUEST_TIMEOUT = 300

#: Reasoning-effort levels (UI selector) → per-request parameters sent at the
#: TOP LEVEL of the OpenAI-compatible chat-completions body. `reasoning_effort`
#: is the effective knob on these Qwen3.x builds (verified against Ollama);
#: `think` / `max_thinking_tokens` are accepted by the endpoint and kept for
#: portability to other deployments. They must be delivered via the openai
#: SDK's `extra_body` channel — `chat.completions.create()` rejects unknown
#: bare kwargs — and since Qwen-Agent forwards generate_cfg keys as kwargs,
#: `extra_body` inside generate_cfg is the one path that reaches the body.
EFFORT_LEVELS: dict[str, dict] = {
    "low": {"think": False, "reasoning_effort": "low", "max_thinking_tokens": 0},
    "medium": {"think": True, "reasoning_effort": "low", "max_thinking_tokens": 2048},
    "high": {"think": True, "reasoning_effort": "medium", "max_thinking_tokens": 8192},
    "xhigh": {"think": True, "reasoning_effort": "high", "max_thinking_tokens": 16384},
}
DEFAULT_EFFORT = "medium"


def effort_body(effort: str | None) -> dict:
    """The request-body parameters for a (possibly unknown) effort label."""
    return EFFORT_LEVELS.get(effort or DEFAULT_EFFORT, EFFORT_LEVELS[DEFAULT_EFFORT])


def _copy_reasoning(obj: object) -> None:
    """Ollama emits thinking in the extra `reasoning` field of a chunk's
    delta (or a message); Qwen-Agent's OAI adapter only checks
    `reasoning_content`, so without this copy the model's thinking is
    silently dropped. openai SDK v1 models are `extra='allow'`, so the
    copied attribute is readable via `hasattr`/`getattr`."""
    for choice in getattr(obj, "choices", None) or []:
        carrier = getattr(choice, "delta", None)
        if carrier is None:
            carrier = getattr(choice, "message", None)
        if carrier is None:
            continue
        thinking = getattr(carrier, "reasoning", None)
        if thinking and not getattr(carrier, "reasoning_content", None):
            try:
                carrier.reasoning_content = thinking
            except Exception:
                pass


class _ReasoningStream:
    """Iterator wrapper that copies `reasoning` → `reasoning_content` on
    every chunk as it arrives (raw streaming path) and harvests the usage
    block Ollama sends in the stream's final chunk (`usage.prompt_tokens`
    drives the UI's context-used display)."""

    def __init__(self, inner, llm: "OllamaTextChat"):
        self._inner = inner
        self._llm = llm

    def __iter__(self):
        return self

    def __next__(self):
        chunk = next(self._inner)
        _copy_reasoning(chunk)
        self._llm._capture_usage(chunk)
        return chunk


class OllamaTextChat(TextChatAtOAI):
    """`TextChatAtOAI` with Ollama's `reasoning` field surfaced as
    `reasoning_content`, the key Qwen-Agent's adapter reads, and the stream's
    usage block captured into `last_usage`.

    The wrapper wraps the `openai.OpenAI(...).chat.completions.create`
    closure assigned in `TextChatAtOAI.__init__`, so both the streaming
    iterator and the non-streaming response are shimmed in one place."""

    def __init__(self, cfg: dict | None = None):
        super().__init__(cfg)
        self.last_usage: dict | None = None
        inner = self._chat_complete_create
        self._chat_complete_create = self._with_reasoning_shim(inner)

    def _with_reasoning_shim(self, inner):
        def wrapped(*args, **kwargs):
            response = inner(*args, **kwargs)
            if kwargs.get("stream"):
                return _ReasoningStream(response, self)
            _copy_reasoning(response)
            self._capture_usage(response)
            return response

        return wrapped

    def _capture_usage(self, response: object) -> None:
        """Keep the last usage block with usable `prompt_tokens` (the final
        chunk of a `stream_options.include_usage` stream, or a full
        non-stream response)."""
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        entry: dict = {}
        for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
            try:
                value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
            except Exception:
                value = None
            if isinstance(value, int):
                entry[name] = value
        if "prompt_tokens" in entry:
            self.last_usage = entry


def build_llm(model: str, effort: str | None = DEFAULT_EFFORT) -> OllamaTextChat:
    """Qwen-Agent LLM instance pointing the OpenAI-compatible client at Ollama.

    `use_raw_api=True` is REQUIRED here: Qwen-Agent's default (non-raw)
    tool-calling mode injects a Nous-style tool prompt into the system
    message, which Ollama + Qwen3.x refuse with `HTTP 500 EOF`; the native
    OpenAI `tools` API (sent by `raw_chat`) works and returns correct
    `tool_calls` (streaming: `delta.reasoning` + `delta.tool_calls`).
    All generate parameters must live under the `generate_cfg` sub-dict —
    `BaseChatModel` reads that dict; top-level keys are silently ignored.

    `effort` (a UI label from `EFFORT_LEVELS`) selects the per-request
    thinking parameters, sent under `extra_body` so they reach the top level
    of the JSON body (unknown bare kwargs would be rejected by the
    openai SDK). Every call made by this instance — the FnCall loop and the
    final-answer fallback — carries it.

    `stream_options.include_usage` asks Ollama for the usage block in the
    stream's final chunk; `OllamaTextChat` captures it into `last_usage`
    (the `prompt_tokens` figure behind the UI's "% of context used").
    """
    s = get_settings()
    host = s.effective_ollama_host.rstrip("/")
    return OllamaTextChat(
        {
            "model": model,
            "model_server": f"{host}/v1",
            "api_key": "ollama",  # placeholder; Ollama ignores it
            "generate_cfg": {
                "temperature": DEFAULT_TEMPERATURE,
                "max_tokens": DEFAULT_MAX_TOKENS,
                "max_input_tokens": DEFAULT_MAX_INPUT_TOKENS,
                "request_timeout": DEFAULT_REQUEST_TIMEOUT,
                "max_retries": 2,
                "use_raw_api": True,
                "extra_body": {
                    "stream_options": {"include_usage": True},
                    **effort_body(effort),
                },
            },
        }
    )


def build_assistant(model: str, tools: list, effort: str | None = DEFAULT_EFFORT) -> Assistant:
    return Assistant(function_list=tools, llm=build_llm(model, effort), name="qwen-assist", files=None)


def _attr(msg: object, key: str) -> object:
    value = getattr(msg, key, None)
    if value is None and isinstance(msg, dict):
        value = msg.get(key)
    return value


def _state_at(lst: list[str], i: int) -> str:
    """Slot i of a per-position prev-state list, padding past any holes
    (skipped FUNCTION/SYSTEM entries) with empty strings."""
    if i >= len(lst):
        lst.extend([""] * (i - len(lst) + 1))
    return lst[i]


async def _translate(
    items: list,
    prev_content: list[str],
    prev_reasoning: list[str],
    emit,
) -> str:
    """Map one accumulated `Message` list to SSE deltas (suffix tracking)."""
    added = ""
    for i, msg in enumerate(items):
        role = _attr(msg, "role")
        if role in (FUNCTION, SYSTEM):
            continue
        reasoning = _attr(msg, "reasoning_content")
        if isinstance(reasoning, str) and reasoning:
            prev = _state_at(prev_reasoning, i)
            suffix = reasoning[len(prev):] if reasoning.startswith(prev) else reasoning
            if suffix:
                await emit("thinking", {"text": suffix})
            prev_reasoning[i] = reasoning
        content = _attr(msg, "content")
        if isinstance(content, str) and content:
            prev = _state_at(prev_content, i)
            # A non-suffix full replacement would mean a stream reset;
            # re-emit the whole thing rather than dropping it.
            suffix = content[len(prev):] if content.startswith(prev) else content
            if suffix:
                added += suffix
                await emit("token", {"text": suffix})
            prev_content[i] = content
    return added


async def _get_or_cancel(q: asyncio.Queue, cancel: asyncio.Event):
    """`q.get()` that also resolves if the Stop event fires first (None)."""
    get_task = asyncio.ensure_future(q.get())
    cancel_task = asyncio.ensure_future(cancel.wait())
    try:
        done, _ = await asyncio.wait({get_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED)
    except asyncio.CancelledError:
        return None
    if get_task in done and not get_task.cancelled():
        return get_task.result()
    return None


def _short_error(exc: Exception | None) -> str:
    base = exc if exc is not None else Exception("qwen-agent turn failed")
    text = str(getattr(base, "message", None) or base or base.__class__.__name__)
    text = " ".join(text.split())
    return text[:300] or "qwen-agent turn failed"


def _needs_final_answer(last: list) -> bool:
    """True when the turn ends with NO assistant answer: the final message
    is either a tool result (the per-run LLM-call budget ran out right
    after a tool call) or an assistant message with empty content (the
    model's last call was thinking-only, which ends the loop with no text)."""
    tail = last[-1]
    if _attr(tail, "role") in (FUNCTION, SYSTEM):
        return True
    content = _attr(tail, "content")
    return not (isinstance(content, str) and content.strip())


def final_answer_stream(assistant, conversation: list) -> Iterator[list]:
    """One tool-free LLM call that forces a plain-text answer when the FnCall
    loop ended without one (see `_needs_final_answer`). Re-asking the same
    conversation with `functions=None` guarantees a plain-text completion.

    `conversation` must be the FULL message list (system + user + the
    accumulated assistant/tool messages): Qwen-Agent's own input validator
    (``_truncate_input_messages_roughly``) rejects a request whose first
    non-system message is not a user message, while `FnCallAgent.run`
    yields only the accumulated suffix — never the input messages.
    """
    msgs = list(conversation)
    # Drop the dangling empty assistant message the loop leaves behind for a
    # thinking-only response — it carries no content and would become the
    # last request message.
    while msgs:
        tail = msgs[-1]
        if _attr(tail, "role") in (FUNCTION, SYSTEM):
            break
        content = _attr(tail, "content")
        if isinstance(content, str) and content.strip():
            break
        msgs.pop()
    return assistant._call_llm(
        messages=msgs,
        functions=None,
        stream=True,
        extra_generate_cfg={"lang": "en"},
    )


def _assistant_usage(assistant) -> dict | None:
    """The turn's captured LLM usage — the LAST call's prompt tokens are the
    truest "context used" figure (system + history + RAG + tool results).
    None when the endpoint sent no usage block."""
    llm = getattr(assistant, "llm", None)
    if llm is None:
        return None
    usage = getattr(llm, "last_usage", None)
    return usage if isinstance(usage, dict) else None


async def run_turn(
    *,
    model: str,
    system: str,
    history: list[dict],
    repo: object | None = None,
    emit,
    cancel: asyncio.Event,
    assistant: Assistant | None = None,
    effort: str | None = DEFAULT_EFFORT,
) -> str:
    """Run one user turn via Qwen-Agent; emits the SSE events live.

    Returns "done" | "cancelled" | "error". `assistant` is a test seam
    (defaults to a real Assistant built for `model`). `effort` selects the
    per-request thinking parameters (see `EFFORT_LEVELS`) for the built
    assistant; a caller-supplied `assistant` owns its own LLM and ignores it.
    """
    loop = asyncio.get_running_loop()
    rt = TurnRuntime(emit=emit, cancel=cancel, loop=loop)
    if assistant is None:
        assistant = build_assistant(model, build_tools(rt, repo), effort)

    messages: list[dict] = [{"role": "system", "content": system}, *history]
    q: asyncio.Queue = asyncio.Queue()

    def drive() -> None:
        try:
            last: list | None = None
            for item in assistant.run(messages, lang="en"):
                if rt.cancel.is_set() or rt.aborted:
                    raise asyncio.CancelledError()
                last = item
                rt.put(q, ("yield", item))
            if last is not None and not (rt.cancel.is_set() or rt.aborted) and _needs_final_answer(last):
                # The turn ended with NO assistant answer (tool-call budget
                # exhausted right after a tool result, or a thinking-only
                # final call). Force one final tool-less answer from the
                # same history. `last` is only the accumulated suffix
                # (FnCallAgent never re-yields the input messages), so the
                # fallback call must be re-anchored onto them.
                #
                # Retry once if the first attempt produces no visible text
                # (e.g. a thinking-only response). If both attempts fail,
                # emit a `warning` SSE event so the UI can inform the user.
                got_text = False
                for _attempt in range(2):
                    if rt.cancel.is_set() or rt.aborted:
                        raise asyncio.CancelledError()
                    for batch in final_answer_stream(assistant, messages + last):
                        if rt.cancel.is_set() or rt.aborted:
                            raise asyncio.CancelledError()
                        if not batch:
                            continue
                        if any(
                            isinstance(_attr(m, "content"), str) and _attr(m, "content").strip()
                            for m in batch
                        ):
                            got_text = True
                        rt.put(q, ("yield", list(last) + list(batch)))
                    if got_text:
                        break
                if not got_text:
                    rt.emit_event(
                        "warning",
                        {"message": "Turn ended without a final answer (LLM call budget exhausted)."},
                    )
            rt.put(q, ("done", None))
        except asyncio.CancelledError:
            rt.aborted = True
            rt.put(q, ("aborted", None))
        except Exception as exc:  # LLM/tool failure: surface as a turn error
            logger.exception("Qwen-Agent turn failed")
            rt.put(q, ("error", exc))

    # NOTE: `loop.run_in_executor(None, …)` rather than `asyncio.to_thread` —
    # 3.13 changed to_thread to return a *coroutine* (not scheduled until
    # awaited); run_in_executor schedules a real Future on every version,
    # which is what the cancel/teardown path below needs.
    task = loop.run_in_executor(None, drive)
    full_text = ""
    prev_content: list[str] = []
    prev_reasoning: list[str] = []
    status = "done"
    error: Exception | None = None
    try:
        while True:
            item = await _get_or_cancel(q, cancel)
            if item is None:
                status = "cancelled"
                break
            kind, payload = item
            if kind == "yield":
                full_text += await _translate(payload, prev_content, prev_reasoning, emit)
            elif kind == "done":
                status = "done"
                break
            elif kind == "aborted":
                status = "cancelled"
                break
            else:  # "error"
                status = "error"
                error = payload  # type: ignore[assignment]
                break
    finally:
        if not task.done():
            task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    if status == "done":
        payload: dict = {"text": full_text}
        usage = _assistant_usage(assistant)
        if usage:
            payload["usage"] = usage
        await emit("done", payload)
    elif status == "cancelled":
        await emit("cancelled", {"text": full_text})
    else:
        await emit("error", {"message": _short_error(error)})
    return status
