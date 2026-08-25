"""Qwen-Agent orchestration for chat turns (MASTER_SPEC §4.3).

`run_turn` drives `qwen_agent.agents.Assistant` — the Qwen-Agent FnCall
loop plus the app's `BaseTool` set (see `app/agents/tools.py`) — on a
worker thread, translating each generator yield (an accumulated list of
`Message`s from the streaming Ollama call) into the SSE event contract
consumed by `app/api/chat.py` and the frontend:

    thinking  {"text": <delta>}   (reasoning_content, reasoning models)
    token     {"text": <delta>}
    tool_*    (emitted by the tools; monotonic per-turn index)
    done | cancelled | error      (exactly one terminal event)

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
    every chunk as it arrives (raw streaming path)."""

    def __init__(self, inner):
        self._inner = inner

    def __iter__(self):
        return self

    def __next__(self):
        chunk = next(self._inner)
        _copy_reasoning(chunk)
        return chunk


class OllamaTextChat(TextChatAtOAI):
    """`TextChatAtOAI` with Ollama's `reasoning` field surfaced as
    `reasoning_content`, the key Qwen-Agent's adapter reads.

    The wrapper wraps the `openai.OpenAI(...).chat.completions.create`
    closure assigned in `TextChatAtOAI.__init__`, so both the streaming
    iterator and the non-streaming response are shimmed in one place."""

    def __init__(self, cfg: dict | None = None):
        super().__init__(cfg)
        inner = self._chat_complete_create
        self._chat_complete_create = self._with_reasoning_shim(inner)

    @staticmethod
    def _with_reasoning_shim(inner):
        def wrapped(*args, **kwargs):
            response = inner(*args, **kwargs)
            if kwargs.get("stream"):
                return _ReasoningStream(response)
            _copy_reasoning(response)
            return response

        return wrapped


def build_llm(model: str) -> OllamaTextChat:
    """Qwen-Agent LLM instance pointing the OpenAI-compatible client at Ollama.

    `use_raw_api=True` is REQUIRED here: Qwen-Agent's default (non-raw)
    tool-calling mode injects a Nous-style tool prompt into the system
    message, which Ollama + Qwen3.x refuse with `HTTP 500 EOF`; the native
    OpenAI `tools` API (sent by `raw_chat`) works and returns correct
    `tool_calls` (streaming: `delta.reasoning` + `delta.tool_calls`).
    All generate parameters must live under the `generate_cfg` sub-dict —
    `BaseChatModel` reads that dict; top-level keys are silently ignored.
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
            },
        }
    )


def build_assistant(model: str, tools: list) -> Assistant:
    return Assistant(function_list=tools, llm=build_llm(model), name="qwen-assist", files=None)


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


async def run_turn(
    *,
    model: str,
    system: str,
    history: list[dict],
    repo: object | None = None,
    emit,
    cancel: asyncio.Event,
    assistant: Assistant | None = None,
) -> str:
    """Run one user turn via Qwen-Agent; emits the SSE events live.

    Returns "done" | "cancelled" | "error". `assistant` is a test seam
    (defaults to a real Assistant built for `model`).
    """
    loop = asyncio.get_running_loop()
    rt = TurnRuntime(emit=emit, cancel=cancel, loop=loop)
    if assistant is None:
        assistant = build_assistant(model, build_tools(rt, repo))

    messages: list[dict] = [{"role": "system", "content": system}, *history]
    q: asyncio.Queue = asyncio.Queue()

    def drive() -> None:
        try:
            for item in assistant.run(messages, lang="en"):
                if rt.cancel.is_set() or rt.aborted:
                    raise asyncio.CancelledError()
                rt.put(q, ("yield", item))
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
        await emit("done", {"text": full_text})
    elif status == "cancelled":
        await emit("cancelled", {"text": full_text})
    else:
        await emit("error", {"message": _short_error(error)})
    return status
