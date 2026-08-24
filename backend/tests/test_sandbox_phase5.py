"""Phase 5: sandbox manager lifecycle + code_interpreter tool + agent streaming.

The manager's docker-CLI seams (`_exec`, `_start`) are replaced with fakes, so
no real daemon is needed. The agent-loop tests use a canned-delta FakeClient.
"""

from __future__ import annotations

import asyncio

import pytest

import app.sandbox.manager as sandbox_manager
from app.llm import tools
from app.llm.agent import run_turn
from app.llm.client import DoneDelta, TextDelta, ToolCallDelta
from app.llm.tools import Tool, ToolContext
from app.sandbox import RunResult, SandboxError, SandboxManager

MODEL = "test-model"


class FakeStream:
    """readline(): queued lines first, then block until the 'dies' event."""

    def __init__(self, lines: list[bytes], dies: asyncio.Event):
        self._lines = list(lines)
        self._dies = dies

    async def readline(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        await self._dies.wait()  # container still "running"
        return b""


class FakeProc:
    def __init__(self, fd: FakeDocker):
        self.stdout = FakeStream(fd.stdout_lines, fd.dies)
        self.stderr = FakeStream(fd.stderr_lines, fd.dies)
        self.returncode = fd.exit_code

    async def wait(self) -> int:
        return self.returncode

    def kill(self) -> None:
        pass


class FakeDocker:
    """Fake docker CLI backend for the manager's _exec / _start seams."""

    def __init__(
        self,
        *,
        image_exists_rc: int = 0,
        build_rc: int = 0,
        exit_code: int = 0,
        unblock_on_kill: bool = False,
    ):
        self.image_exists_rc = image_exists_rc
        self.build_rc = build_rc
        self.exit_code = exit_code
        self.unblock_on_kill = unblock_on_kill
        self.dies = asyncio.Event()
        self.stdout_lines: list[bytes] = []
        self.stderr_lines: list[bytes] = []
        self.exec_calls: list[list[str]] = []
        self.start_calls: list[tuple[str, str]] = []
        self.kills: list[tuple[str, str]] = []

    async def fake_exec(
        self, args: list[str], *, input_bytes: bytes | None = None, timeout: float = 60.0
    ) -> tuple[int, str, str]:
        args = list(args)
        self.exec_calls.append(args)
        if args[0] == "image":  # `image inspect <name>`
            return self.image_exists_rc, "", ""
        if args[0] == "build":
            if self.build_rc == 0:
                return 0, "built ok\n", ""
            return 1, "", "fake build failure line\n"
        if args[0] == "kill":
            self.kills.append((args[-1], args[1].split("=", 1)[1]))
            if self.unblock_on_kill:
                self.dies.set()
            return 0, "", ""
        if args[0] == "rm":
            return 0, "", ""
        raise AssertionError(f"unexpected docker args: {args}")

    async def fake_start(self, name: str, code: str) -> FakeProc:
        self.start_calls.append((name, code))
        return FakeProc(self)


def make_manager(fd: FakeDocker, *, timeout_seconds: float | None = None) -> SandboxManager:
    mgr = SandboxManager(
        image_name="qwen-code-sandbox:latest",
        timeout_seconds=timeout_seconds if timeout_seconds is not None else 120,
        memory="1g",
        cpus="2",
        network="none",
        user="65534:65534",
        tmpfs="/tmp:rw,size=64m",
        pids_limit=256,
    )
    mgr._exec = fd.fake_exec  # type: ignore[method-assign]
    mgr._start = fd.fake_start  # type: ignore[method-assign]
    return mgr


def _sync(coro) -> object:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# --------------------------------------------------------------------------- #
# run lifecycle (normal / exit code / timeout / cancel / cleanup)
# --------------------------------------------------------------------------- #
def test_run_args_carry_full_hardening_set():
    mgr = make_manager(FakeDocker())
    assert mgr._run_args("qcsbx-abc") == [
        "run",
        "-i",
        "--rm",
        "--name",
        "qcsbx-abc",
        "--read-only",
        "--tmpfs=/tmp:rw,size=64m",
        "--memory=1g",
        "--memory-swap=1g",
        "--cpus=2",
        "--pids-limit=256",
        "--network=none",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--user",
        "65534:65534",
        "qwen-code-sandbox:latest",
        "python",
        "-",
    ]


def test_normal_run_streams_both_channels_and_cleans_up():
    fd = FakeDocker()
    fd.stdout_lines = [b"line1\n", b"line2\n"]
    fd.stderr_lines = [b"warn:1\n"]
    fd.dies.set()  # container exits on its own
    mgr = make_manager(fd)

    seen: list[str] = []

    async def on_output(text: str) -> None:
        seen.append(text)

    res = _sync(mgr.run("print(1)", on_output=on_output))

    assert isinstance(res, RunResult) and res.ok
    assert res.exit_code == 0 and not res.timed_out and not res.cancelled
    assert res.stdout == "line1\nline2\n"
    assert res.stderr == "warn:1\n"
    # incremental streaming: every line lands, in stream order
    assert "".join(seen) == "line1\nline2\nwarn:1\n"
    # script was fed on stdin (no host paths involved); container removed
    name, code = fd.start_calls[0]
    assert name.startswith("qcsbx-") and code == "print(1)"
    assert ["rm", "-f", name] in fd.exec_calls
    assert fd.kills == []


def test_nonzero_exit_is_not_ok():
    fd = FakeDocker(exit_code=3)
    fd.dies.set()
    fd.stdout_lines = [b"boom input\n"]
    mgr = make_manager(fd)
    res = _sync(mgr.run("x", on_output=None))
    assert res.exit_code == 3
    assert res.ok is False


def test_empty_code_rejected_before_any_docker_call():
    fd = FakeDocker()
    mgr = make_manager(fd)
    with pytest.raises(SandboxError):
        _sync(mgr.run("   \n  "))
    assert fd.exec_calls == [] and fd.start_calls == []


def test_timeout_kills_container_and_notes_it(monkeypatch):
    fd = FakeDocker(unblock_on_kill=True)
    fd.stdout_lines = [b"started\n"]  # then hangs until killed
    mgr = make_manager(fd, timeout_seconds=0.05)
    res = _sync(mgr.run("import time; time.sleep(999)"))

    assert res.timed_out and not res.cancelled and res.ok is False
    assert res.stdout.endswith("[timed out after 0.05s — killed]")
    name = fd.start_calls[0][0]
    assert (name, "KILL") in fd.kills
    assert ["rm", "-f", name] in fd.exec_calls


def test_cancel_is_sigterm_then_kill(monkeypatch):
    monkeypatch.setattr(sandbox_manager, "TERM_GRACE_SECONDS", 0.01)
    fd = FakeDocker(unblock_on_kill=True)
    fd.stderr_lines = [b"working\n"]
    mgr = make_manager(fd)
    cancel = asyncio.Event()
    cancel.set()  # the user already hit Stop
    res = _sync(mgr.run("x", cancel=cancel))

    assert res.cancelled and not res.timed_out and res.ok is False
    assert res.stdout.endswith("[stopped by user]")
    name = fd.start_calls[0][0]
    assert fd.kills == [(name, "TERM"), (name, "KILL")]
    assert ["rm", "-f", name] in fd.exec_calls


def test_remove_is_best_effort_even_when_failed():
    fd = FakeDocker()
    fd.dies.set()
    mgr = make_manager(fd)
    real_exec = fd.fake_exec

    async def exec_rm_fails(args, *, input_bytes=None, timeout=60.0):
        if list(args)[:1] == ["rm"]:
            raise SandboxError("daemon gone")
        return await real_exec(args, input_bytes=input_bytes, timeout=timeout)

    mgr._exec = exec_rm_fails  # type: ignore[method-assign]
    res = _sync(mgr.run("x"))
    assert res.ok  # failure to clean up must not sink the run result


# --------------------------------------------------------------------------- #
# image management
# --------------------------------------------------------------------------- #
def test_ensure_image_skips_build_when_present():
    fd = FakeDocker(image_exists_rc=0)
    mgr = make_manager(fd)
    _sync(mgr.ensure_image("sandbox"))
    assert ["image", "inspect", "qwen-code-sandbox:latest"] in fd.exec_calls
    assert not any(a[0] == "build" for a in fd.exec_calls)


def test_ensure_image_builds_when_missing():
    fd = FakeDocker(image_exists_rc=1, build_rc=0)
    mgr = make_manager(fd)
    _sync(mgr.ensure_image("sandbox"))
    assert ["build", "-t", "qwen-code-sandbox:latest", "sandbox"] in fd.exec_calls


def test_ensure_image_build_failure_raises():
    fd = FakeDocker(image_exists_rc=1, build_rc=1)
    mgr = make_manager(fd)
    with pytest.raises(SandboxError, match="build failed"):
        _sync(mgr.ensure_image("sandbox"))


# --------------------------------------------------------------------------- #
# code_interpreter handler
# --------------------------------------------------------------------------- #
class FakeSandboxManager:
    timeout_seconds = 5

    def __init__(self) -> None:
        self.ensured_with: str | None = None
        self.code: str | None = None
        self.cancel_seen: asyncio.Event | None = None

    async def ensure_image(self, build_dir: str) -> None:
        self.ensured_with = build_dir

    async def run(self, code, on_output=None, cancel=None):
        self.code = code
        self.cancel_seen = cancel
        if on_output is not None:
            await on_output("a\n")
            await on_output("b\n")
        return RunResult(stdout="a\nb\n", stderr="e\n", exit_code=0, duration_ms=7)


def test_code_interpreter_streams_tool_output_events(monkeypatch):
    fake = FakeSandboxManager()
    monkeypatch.setattr(tools, "get_sandbox_manager", lambda: fake)
    events: list[tuple[str, dict]] = []

    async def emit(name: str, data: dict) -> None:
        events.append((name, data))

    ctx = ToolContext(emit=emit, index=1, cancel=asyncio.Event())
    out = _sync(tools.code_interpreter({"code": "print(1)"}, ctx))

    assert fake.ensured_with == "sandbox"
    assert fake.code == "print(1)"
    assert out.splitlines()[0] == "exit code: 0"
    assert "STDOUT:\na\nb\n" in out
    assert "STDERR:\ne\n" in out
    assert events == [
        ("tool_output", {"index": 1, "text": "a\n"}),
        ("tool_output", {"index": 1, "text": "b\n"}),
    ]


def test_code_interpreter_requires_nonempty_code():
    async def emit(_name: str, _data: dict) -> None:
        pass

    ctx = ToolContext(emit=emit, index=0, cancel=asyncio.Event())
    with pytest.raises(ValueError, match="non-empty"):
        _sync(tools.code_interpreter({}, ctx))
    with pytest.raises(ValueError, match="non-empty"):
        _sync(tools.code_interpreter({"code": "  "}, ctx))


def test_sandbox_manager_singleton_and_reset(monkeypatch):
    tools.reset_sandbox_manager()
    m1 = tools.get_sandbox_manager()
    m2 = tools.get_sandbox_manager()
    assert m1 is m2
    tools.reset_sandbox_manager()
    # fresh process-wide default values from settings
    m3 = tools.get_sandbox_manager()
    assert m3.image_name == "qwen-code-sandbox:latest"
    tools.reset_sandbox_manager()


# --------------------------------------------------------------------------- #
# agent loop with streaming tools
# --------------------------------------------------------------------------- #
class FakeClient:
    def __init__(self, rounds: list):
        self.rounds = list(rounds)

    async def stream_chat(self, **_kw):
        for delta in self.rounds.pop(0):
            yield delta


async def _stream(client, handler) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []

    async def emit(name: str, data: dict) -> None:
        events.append((name, data))

    tool = Tool(
        name="code_interpreter",
        description="d",
        parameters={"type": "object", "properties": {"code": {"type": "string"}}},
        handler=handler,
        streams=True,
    )
    await run_turn(
        client=client,
        model=MODEL,
        system="sys",
        history=[{"role": "user", "content": "go"}],
        tools=[tool],
        emit=emit,
        cancel=asyncio.Event(),
    )
    return events


def test_streaming_tool_emits_no_duplicate_final_output():
    async def handler(_args: dict, ctx: ToolContext) -> str:
        await ctx.emit("tool_output", {"index": ctx.index, "text": "chunk1\n"})
        await ctx.emit("tool_output", {"index": ctx.index, "text": "chunk2\n"})
        return "exit code: 0"

    client = FakeClient(
        [
            [
                ToolCallDelta(0, id="c1", name="code_interpreter", arguments_chunk='{"code": "print(1)"}'),
                DoneDelta("tool_calls"),
            ],
            [TextDelta("done!"), DoneDelta("stop")],
        ]
    )
    events = _sync(_stream(client, handler))
    names = [n for n, _ in events]
    assert names == ["tool_start", "tool_output", "tool_output", "tool_end", "token", "done"]
    texts = [d["text"] for n, d in events if n == "tool_output"]
    assert texts == ["chunk1\n", "chunk2\n"]  # final "exit code: 0" NOT re-emitted
    tool_end = events[3][1]
    assert tool_end["ok"] is True and tool_end["index"] == 0


def test_failing_streaming_tool_still_emits_error_text():
    async def handler(_args: dict, ctx: ToolContext) -> str:
        await ctx.emit("tool_output", {"index": ctx.index, "text": "partial\n"})
        raise RuntimeError("boom")

    client = FakeClient(
        [
            [
                ToolCallDelta(0, id="c1", name="code_interpreter", arguments_chunk='{"code": "x"}'),
                DoneDelta("tool_calls"),
            ],
            [TextDelta("ok"), DoneDelta("stop")],
        ]
    )
    events = _sync(_stream(client, handler))
    names = [n for n, _ in events]
    assert names == ["tool_start", "tool_output", "tool_output", "tool_end", "token", "done"]
    texts = [d["text"] for n, d in events if n == "tool_output"]
    assert texts[0] == "partial\n"
    assert "tool failed" in texts[1] and "boom" in texts[1]
    assert events[3][1]["ok"] is False
