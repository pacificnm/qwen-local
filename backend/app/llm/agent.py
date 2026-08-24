"""The agent loop: LLM turns + tool rounds, emitting SSE-shaped events.

Event contract (consumed by app/api/chat.py → SSE, spec docs/API.md):
    ("thinking",    {"text": delta})   # reasoning models only; not persisted
    ("token",       {"text": delta})
    ("tool_start",  {"tool": name, "index": i, "arguments": {…}})
    ("tool_output", {"index": i, "text": ≤64 KB})
    ("tool_end",    {"index": i, "ok": bool, "duration_ms": n})
    ("done",        {"text": full answer})
    ("cancelled",   {"text": partial answer})
    ("error",       {"message": short})

Streaming tools (`Tool.streams=True`, e.g. code_interpreter) push MULTIPLE
`tool_output` events with the same `index` while running (their live stdout/
stderr); the loop then emits `tool_end` and appends the capped final result
to the conversation context. Non-streaming tools get exactly one final
`tool_output`. Cancellation: `cancel` (asyncio.Event) is checked before every
round, around every tool call, per chunk inside the LLM stream
(LLMClient.should_cancel), and passed to handlers via ToolContext — the
sandbox kills its container when it fires. A hard task cancellation
(client disconnect) propagates out as asyncio.CancelledError; the endpoint
persists whatever text arrived.
"""

import asyncio
import json
import time
from dataclasses import dataclass, field

from .client import DoneDelta, LLMClient, LLMError, TextDelta, ThinkingDelta, ToolCallDelta
from .tools import Emit, Tool, ToolContext, parse_tool_arguments

MAX_TOOL_ROUNDS = 3
TOOL_OUTPUT_CAP = 64 * 1024  # per docs/API.md


@dataclass
class ToolLogEntry:
    name: str
    arguments: dict
    ok: bool
    error: str | None = None
    duration_ms: int = 0


@dataclass
class TurnResult:
    text: str = ""
    tool_log: list[ToolLogEntry] = field(default_factory=list)
    rounds: int = 0
    status: str = "done"  # done | cancelled | error


@dataclass
class _AccumulatedCall:
    id: str = ""
    name: str = ""
    arguments: str = ""


async def run_turn(
    *,
    client: LLMClient,
    model: str,
    system: str,
    history: list[dict],
    tools: list[Tool],
    emit: Emit,
    cancel: asyncio.Event,
    max_rounds: int = MAX_TOOL_ROUNDS,
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> TurnResult:
    """Run one user turn end-to-end; returns the outcome, emits events live."""
    tool_by_name = {t.name: t for t in tools}
    result = TurnResult()
    convo: list[dict] = [{"role": "system", "content": system}, *history]

    forced = False
    while True:
        if cancel.is_set():
            return await _finish(result, "cancelled", emit)

        acc: dict[int, _AccumulatedCall] = {}
        round_text = ""

        try:
            async for delta in client.stream_chat(
                model=model,
                messages=convo,
                tools=None if forced else ([t.to_openai() for t in tools] or None),
                temperature=temperature,
                max_tokens=max_tokens,
                should_cancel=lambda: cancel.is_set(),
            ):
                if isinstance(delta, ThinkingDelta):
                    # Chain-of-thought: surfaced to the UI, never part of the
                    # persisted answer.
                    await emit("thinking", {"text": delta.text})
                elif isinstance(delta, TextDelta):
                    result.text += delta.text
                    round_text += delta.text
                    await emit("token", {"text": delta.text})
                elif isinstance(delta, ToolCallDelta):
                    call = acc.setdefault(delta.index, _AccumulatedCall())
                    if delta.id:
                        call.id = delta.id
                    if delta.name:
                        call.name = delta.name
                    call.arguments += delta.arguments_chunk
                elif isinstance(delta, DoneDelta):
                    pass
        except LLMError as exc:
            result.status = "error"
            await emit("error", {"message": exc.detail})
            return result
        # asyncio.CancelledError is intentionally NOT caught: a hard stop
        # (client disconnect / task.cancel) unwinds here and the endpoint
        # persists the partial text in the shared state object.

        # A soft stop (the endpoint's asyncio.Event) makes stream_chat return
        # mid-round; honour it as a cancel rather than falling through to done.
        if cancel.is_set():
            return await _finish(result, "cancelled", emit)

        result.rounds += 1
        calls = [acc[i] for i in sorted(acc)]

        if not calls:
            return await _finish(result, "done", emit)

        if forced:
            # tools=None was sent but the model still emitted a call (quirk);
            # satisfy nothing, close the turn with the text gathered.
            return await _finish(result, "done", emit)

        convo.append(
            {
                "role": "assistant",
                "content": round_text or None,
                "tool_calls": [
                    {
                        "id": c.id or f"call_{result.rounds}_{i}",
                        "type": "function",
                        "function": {"name": c.name, "arguments": c.arguments or "{}"},
                    }
                    for i, c in enumerate(calls)
                ],
            }
        )

        for i, call in enumerate(calls):
            if cancel.is_set():
                return await _finish(result, "cancelled", emit)
            args = parse_tool_arguments(call.arguments)
            await emit("tool_start", {"tool": call.name, "index": i, "arguments": args})

            tool = tool_by_name.get(call.name)
            t0 = time.perf_counter()
            ok = True
            error: str | None = None
            output = ""
            if tool is None:
                ok = False
                error = f"unknown tool: {call.name}"
                output = error
            else:
                ctx = ToolContext(emit=emit, index=i, cancel=cancel)
                try:
                    output = await tool.handler(args, ctx)
                except Exception as exc:  # tool bugs must not kill the turn
                    ok = False
                    error = f"{exc.__class__.__name__}: {exc}"
                    output = f"tool failed: {error}"
            duration_ms = int((time.perf_counter() - t0) * 1000)

            capped = _cap(output)
            # Streaming tools already pushed their live output while running;
            # emit the final one only for non-streaming tools and failures
            # (so error text is still visible for streaming tools too).
            if tool is None or not ok or not tool.streams:
                await emit("tool_output", {"index": i, "text": capped})
            await emit("tool_end", {"index": i, "ok": ok, "duration_ms": duration_ms})
            result.tool_log.append(
                ToolLogEntry(
                    name=call.name, arguments=args, ok=ok, error=error, duration_ms=duration_ms
                )
            )
            convo.append(
                {
                    "role": "tool",
                    "tool_call_id": f"call_{result.rounds}_{i}",
                    "content": capped,
                }
            )

        if result.rounds >= max_rounds:
            forced = True
            convo.append(
                {
                    "role": "user",
                    "content": "Tool budget is used up. Answer now from the information "
                    "already gathered.",
                }
            )


async def _finish(result: TurnResult, status: str, emit: Emit) -> TurnResult:
    result.status = status
    event = {
        "done": ("done", {"text": result.text}),
        "cancelled": ("cancelled", {"text": result.text}),
    }[status]
    await emit(event[0], event[1])
    return result


def _cap(text: str) -> str:
    if len(text) <= TOOL_OUTPUT_CAP:
        return text
    note = f"\n… [truncated to {TOOL_OUTPUT_CAP // 1024} KB]"
    return text[: TOOL_OUTPUT_CAP - len(note)] + note  # total never exceeds the cap


def tool_log_json(result: TurnResult) -> str:
    """Compact tool log for the assistant Message.tool_calls column."""
    return json.dumps(
        [
            {
                "name": e.name,
                "ok": e.ok,
                "error": e.error,
                "duration_ms": e.duration_ms,
                "arguments": e.arguments,
            }
            for e in result.tool_log
        ]
    )
