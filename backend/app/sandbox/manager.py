"""Ephemeral Docker sandbox manager (spec §4.5).

Every code interpreter run gets a FRESH, hardened container:

    docker run -i --rm
        --name qcsbx-<uuid>
        --read-only --tmpfs /tmp:rw,size=64m
        --memory 1g --memory-swap 1g --cpus 2 --pids-limit 256
        --network none
        --cap-drop ALL --security-opt no-new-privileges
        --user 65534:65534
        <image> python -

The script is injected on stdin (`python -`) instead of a bind mount, so no
host↔container file path is ever involved — the backend can drive the host
Docker daemon through the socket whether it runs in a container or on the
host itself (dev runs).

Stop semantics (spec §4.5 "120 s SIGKILL hard timeout" + §4.3 "SIGTERM→SIGKILL
the sandbox on Stop"):
- hard timeout        -> `docker kill -s KILL <name>`
- user stop (event)   -> `docker kill -s TERM <name>`, brief grace, `KILL`
The container is always removed best-effort in `finally` (`docker rm -f`),
covering crashes, cancellations, and exceptions.

`_exec` (one-shot CLI ops) and `_start` (the streaming run) are the
monkey-patch seams used by the test suite — tests never touch a real daemon.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ..core.settings import get_settings

#: Upper bound on accumulated output per stream (backend memory guard against
#: run-away `print` loops). Streaming to the UI stops beyond this too.
MAX_CAPTURE_BYTES = 1 * 1024 * 1024

#: Grace window after a SIGTERM (user stop) before escalating to SIGKILL.
TERM_GRACE_SECONDS = 2.0

OnOutput = Callable[[str], Awaitable[None]]


class SandboxError(RuntimeError):
    """Raised when the sandbox image is missing/unbuildable or a run cannot start."""


@dataclass
class RunResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False
    cancelled: bool = False
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    duration_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.cancelled


class _Capture:
    """Per-stream output accumulator with a hard byte cap."""

    __slots__ = ("_parts", "_bytes", "truncated")

    def __init__(self) -> None:
        self._parts: list[str] = []
        self._bytes = 0
        self.truncated = False

    def take(self, text: str) -> bool:
        """Record `text` (and return True if callers should propagate it)."""
        if self.truncated:
            return False
        self._bytes += len(text.encode("utf-8"))
        if self._bytes > MAX_CAPTURE_BYTES:
            self.truncated = True
            return False
        self._parts.append(text)
        return True

    def text(self) -> str:
        return "".join(self._parts)


class SandboxManager:
    """Drives the Docker CLI (via the host socket) for ephemeral Python runs."""

    def __init__(
        self,
        image_name: str | None = None,
        timeout_seconds: int | None = None,
        memory: str | None = None,
        cpus: str | None = None,
        network: str | None = None,
        user: str | None = None,
        tmpfs: str | None = None,
        pids_limit: int | None = None,
        docker_cli: str = "docker",
    ) -> None:
        s = get_settings()
        self.image_name = image_name or s.sandbox_image_name
        self.timeout_seconds = timeout_seconds or s.sandbox_timeout_seconds
        self.memory = memory or s.sandbox_memory
        self.cpus = cpus or s.sandbox_cpus
        self.network = network or s.sandbox_network
        self.user = user or s.sandbox_user
        self.tmpfs = tmpfs or s.sandbox_tmpfs
        self.pids_limit = pids_limit or s.sandbox_pids_limit
        self.docker_cli = docker_cli

    # ------------------------------------------------------------------ #
    # docker CLI seams
    # ------------------------------------------------------------------ #
    async def _exec(
        self, args: list[str], *, input_bytes: bytes | None = None, timeout: float = 60.0
    ) -> tuple[int, str, str]:
        """One-shot CLI op. Returns (returncode, stdout, stderr)."""
        proc = await asyncio.create_subprocess_exec(
            self.docker_cli,
            *args,
            stdin=asyncio.subprocess.PIPE if input_bytes is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(input=input_bytes), timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise SandboxError(f"`docker {' '.join(args[:2])}` timed out after {timeout:.0f}s") from None
        return proc.returncode or 0, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")

    async def _start(
        self, name: str, code: str, command: list[str] | None = None
    ) -> asyncio.subprocess.Process:
        """Spawn the streaming `docker run` and feed the script on stdin."""
        proc = await asyncio.create_subprocess_exec(
            self.docker_cli,
            *self._run_args(name, command),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert proc.stdin is not None
        proc.stdin.write(code.encode("utf-8"))
        proc.stdin.close()
        return proc

    # ------------------------------------------------------------------ #
    # image management
    # ------------------------------------------------------------------ #
    async def image_exists(self) -> bool:
        rc, _, _ = await self._exec(["image", "inspect", self.image_name], timeout=30)
        return rc == 0

    async def ensure_image(self, build_dir: str) -> None:
        """Build the runtime image from `build_dir` unless it already exists."""
        if await self.image_exists():
            return
        rc, out, err = await self._exec(
            ["build", "-t", self.image_name, build_dir], timeout=1800
        )
        if rc != 0:
            tail = (err or out).strip().splitlines()[-5:]
            raise SandboxError(f"sandbox image build failed (exit {rc}): {' | '.join(tail)}")

    # ------------------------------------------------------------------ #
    # run lifecycle
    # ------------------------------------------------------------------ #
    def _run_args(self, name: str, command: list[str] | None = None) -> list[str]:
        cmd = command if command is not None else ["python", "-"]
        return [
            "run",
            "-i",
            "--rm",
            "--name",
            name,
            "--read-only",
            f"--tmpfs={self.tmpfs}",
            f"--memory={self.memory}",
            f"--memory-swap={self.memory}",
            f"--cpus={self.cpus}",
            f"--pids-limit={self.pids_limit}",
            f"--network={self.network}",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--user",
            self.user,
            self.image_name,
            *cmd,
        ]

    async def kill(self, name: str, *, signal: str = "KILL") -> None:
        """Best-effort signal delivery; tolerated if the container is already gone."""
        try:
            await self._exec(["kill", f"-s={signal}", name], timeout=30)
        except (SandboxError, FileNotFoundError):
            pass

    async def remove(self, name: str) -> None:
        try:
            await self._exec(["rm", "-f", name], timeout=30)
        except (SandboxError, FileNotFoundError):
            pass

    async def run(
        self,
        code: str,
        on_output: OnOutput | None = None,
        cancel: asyncio.Event | None = None,
        command: list[str] | None = None,
    ) -> RunResult:
        """Run `code` in a fresh hardened container; stream output as it lands.

        `on_output` receives each output line (stdout+stderr interleaved) as it
        arrives; `cancel` (set by the agent loop on Stop) kills the run.
        `command` overrides the container entrypoint (default: `python -`).
        """
        if not (code or "").strip():
            raise SandboxError("empty script")
        name = f"qcsbx-{uuid.uuid4().hex}"
        started = time.monotonic()
        out_cap = _Capture()
        err_cap = _Capture()
        timed_out = cancelled = False

        proc = await self._start(name, code, command)

        async def pump(stream: asyncio.StreamReader | None, cap: _Capture) -> None:
            if stream is None:
                return
            while True:
                chunk = await stream.readline()
                if not chunk:
                    return
                text = chunk.decode("utf-8", "replace")
                if cap.take(text) and on_output is not None:
                    await on_output(text)

        try:
            # gather() already returns a Future: cancel() on it cancels both
            # pumps, and asyncio.wait() accepts futures directly.
            read_task = asyncio.gather(pump(proc.stdout, out_cap), pump(proc.stderr, err_cap))
            stop_task = asyncio.create_task(self._stop_decision(cancel))
            done, _ = await asyncio.wait({read_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
            if read_task in done:
                # Natural exit wins (incl. the rare tie): shut the decision
                # task down. Keeping I/O OUT of _stop_decision matters here —
                # if it issued the kill while being watched, its kill would
                # EOF the streams and the reader future could win the race
                # while the watcher is still mid-kill (KILL then lost).
                stop_task.cancel()
                try:
                    await stop_task
                except asyncio.CancelledError:
                    pass
            else:
                reason = await stop_task  # "timeout" | "cancelled"
                if reason == "timeout":
                    timed_out = True
                    await self.kill(name, signal="KILL")
                else:
                    cancelled = True
                    await self.kill(name, signal="TERM")
                    await asyncio.sleep(TERM_GRACE_SECONDS)
                    await self.kill(name, signal="KILL")
                # The container is being killed; drop the reader (its streams
                # would EOF anyway) but keep everything captured so far.
                read_task.cancel()
                try:
                    await read_task
                except asyncio.CancelledError:
                    pass
        finally:
            if proc.returncode is None:
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except TimeoutError:
                    proc.kill()
                    await proc.wait()
            await self.remove(name)

        exit_code = proc.returncode if proc.returncode is not None else -1
        result = RunResult(
            stdout=out_cap.text(),
            stderr=err_cap.text(),
            exit_code=exit_code,
            timed_out=timed_out,
            cancelled=cancelled,
            stdout_truncated=out_cap.truncated,
            stderr_truncated=err_cap.truncated,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        if timed_out:
            note = f"\n[timed out after {self.timeout_seconds}s — killed]"
            result.stdout = (result.stdout or "").rstrip() + note
        elif cancelled:
            result.stdout = (result.stdout or "").rstrip() + "\n[stopped by user]"
        return result

    async def _stop_decision(self, cancel: asyncio.Event | None) -> str:
        """Race the hard timeout against a user Stop and report which fired.

        Pure decision: NO I/O after the win. That guarantees the task is
        complete (or about to be) the instant it is the first to finish, so
        `run()` can pick a race winner unambiguously and then perform the
        kill sequence itself. Cancelled by the caller when the run ends
        naturally first.
        """
        sleep_task = asyncio.create_task(asyncio.sleep(self.timeout_seconds))
        cancel_task = asyncio.create_task(cancel.wait()) if cancel is not None else None
        try:
            waiters = [t for t in (sleep_task, cancel_task) if t is not None]
            done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            return "timeout" if sleep_task in done else "cancelled"
        finally:
            for t in (sleep_task, cancel_task):
                if t is not None:
                    t.cancel()
