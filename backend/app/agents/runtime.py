"""Sync ↔ async bridge for the Qwen-Agent adapter.

Qwen-Agent's agent loop is *synchronous* (a single worker thread, a
blocking OpenAI-compatible client), while the app is asyncio end-to-end
(SSE emit, git subprocesses, sandbox, the user-Stop event). This module
is the glue:

- ``wait_async`` — run a coroutine on the app's event loop and block the
  worker thread until it completes. When the user-Stop event fires, the
  scheduled future is cancelled (in-flight tools observe the very same
  event, so the sandbox is killed mid-run) and
  ``asyncio.CancelledError`` is raised: a ``BaseException``, so it escapes
  Qwen-Agent's ``except Exception`` tool wrapper and unwinds the whole
  agent loop immediately — no further LLM call is started.
- ``emit_event`` / ``put`` — thread-safe SSE emission and hand-off of
  generator items to the driver's asyncio queue.
- ``tool_index`` — a monotonic per-turn tool index. The frontend maps
  ``tool_output``/``tool_end`` onto a CUMULATIVE tool-calls list by index;
  a per-round index would overwrite earlier entries.
"""

import asyncio
import concurrent.futures
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

Emit = Callable[[str, dict], Awaitable[None]]


@dataclass
class TurnRuntime:
    """Per-turn state shared between the agent worker thread and the event loop."""

    emit: Emit
    cancel: asyncio.Event
    loop: asyncio.AbstractEventLoop
    next_tool_index: int = 0
    #: Set once Stop is observed (drives post-abort cleanup decisions).
    aborted: bool = False

    def tool_index(self) -> int:
        i = self.next_tool_index
        self.next_tool_index += 1
        return i

    def _submit(self, coro: Awaitable) -> concurrent.futures.Future:
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def wait_async(self, coro: Awaitable) -> object:
        """Block this (worker) thread until `coro` completes on the loop."""
        fut = self._submit(coro)
        try:
            while True:
                if self.cancel.is_set():
                    fut.cancel()
                    self.aborted = True
                    raise asyncio.CancelledError()
                try:
                    return fut.result(timeout=0.25)
                except concurrent.futures.TimeoutError:
                    continue
        finally:
            if not fut.done():
                fut.cancel()

    def emit_event(self, name: str, data: dict) -> None:
        """Emit one SSE event from the worker thread (bounded, non-fatal)."""
        try:
            self._submit(self.emit(name, data)).result(timeout=10)
        except Exception:
            # Losing UI events while the event loop is shutting down is
            # acceptable; the turn itself has already finished.
            pass

    def put(self, q: asyncio.Queue, item: object) -> None:
        """Thread-safe hand-off of a generator item to the driver queue."""
        try:
            self._submit(q.put(item)).result(timeout=15)
        except Exception:
            self.aborted = True  # driver is gone; treat as abort
