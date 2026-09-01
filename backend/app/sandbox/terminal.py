"""Persistent, per-repo interactive-terminal sandbox manager.

Distinct from the one-shot, hardened ``SandboxManager`` (code interpreter):
the terminal container STAYS ALIVE (``sleep infinity``), is network-enabled
(bridge), gets a writable ``/workspace`` (named volume) and an optional
read+write bind of the project repo at ``/repo``, and runs a REAL shell via
the baked PTY bridge (``python3 /ptymaster.py``).

Lifecycle per ``docker`` invocation (the backend drives the host docker socket
through the CLI baked into its image — same seam style as manager.py):

    docker run -d --name qcterm-<x> --memory .. --cpus .. --network bridge
        --user 1000:1000 -v vol:/workspace [-v <host>:/repo]
        [-p HOST_PORT:CONTAINER_PORT]  <image> sleep infinity
    docker exec -i qcterm-<x> python3 /ptymaster.py      (one bash, per session)

The optional ``-p`` bind exposes the sandbox to the host so dev servers (Vite/
Express) can be reached at ``http://localhost:HOST_PORT``. The pair comes from
the project's ``ProjectSettings`` (supplied by the caller at spawn time — the
manager itself stays DB-free), so it is not hardcoded here.

Framing to the bridge (over the exec stdin, fd 0) — see sandbox/bridge.py:
    0x01 <uint32 BE len> <raw>   INPUT   (write bytes into the pty)
    0x02 <uint16 BE r>  <uint16 BE c>   RESIZE  (resize the pty)
Out (exec stdout, fd 1) is raw pty output; the WS layer forwards it verbatim.

``_exec`` (one-shot CLI) and ``_start`` (the interactive exec) are the
monkey-patch seams used by the test suite — tests never touch a real daemon.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field

from ..core.settings import get_settings

logger = logging.getLogger("qwen-chat.terminal")

OP_INPUT = 0x01
OP_RESIZE = 0x02

# Terminal container name prefix (discoverable via `docker ps --filter name=qcterm-`).
NAME_PREFIX = "qcterm-"
# Docker label carrying the repo tag, so `reconcile()` can recover the
# tag->container mapping from a container's name alone (the name itself is a
# random suffix and does not encode the tag).
TAG_LABEL = "qcterm.tag"
# In-container bridge path (baked into the image via sandbox/Dockerfile).
BRIDGE_PATH = "/ptymaster.py"
# Bridge reads stdin in 64 KiB frames; cap our writes to keep chunks bounded.
CHUNK = 64 * 1024


class TerminalError(RuntimeError):
    """Raised when the terminal image is missing/unbuildable or a session can't start."""


# --------------------------------------------------------------------------- #
# wire framing (shared by the manager, the WS layer, and the tests)
# --------------------------------------------------------------------------- #
def encode_input(data: bytes) -> bytes:
    if not data:
        return b""
    return bytes([OP_INPUT]) + len(data).to_bytes(4, "big") + data


def encode_resize(rows: int, cols: int) -> bytes:
    return bytes([OP_RESIZE]) + int(rows).to_bytes(2, "big") + int(cols).to_bytes(2, "big")


def proc_id(sess: TerminalSession) -> str:
    """Stable string key for one live session (identity of its backing proc)."""
    return str(id(sess.proc))


def _safe_tag(full_name: str) -> str:
    """Filesystem/volume-safe slug from a GitHub full_name (owner/name)."""
    slug = re.sub(r"[^a-z0-9]+", "-", full_name.lower()).strip("-")
    return slug or "repo"


class LiveProc:
    """A live ``docker exec`` process with "read-when-available" semantics.

    The asyncio ``StreamReader.read(n)`` blocks for ``n`` bytes or EOF, which is
    wrong for a live terminal (it would buffer up to 64 KiB before flushing).
    Instead a background thread does blocking ``os.read`` on the raw stdout pipe
    — which returns as soon as ANY bytes are available — and pushes each chunk
    into an ``asyncio.Queue``. ``read()`` then returns the next chunk promptly
    (``b""`` on EOF). Writes go to the stdin pipe via the executor (off the loop).
    """

    __slots__ = ("_proc", "_loop", "_queue", "_pending_write", "_thread")

    def __init__(self, proc: subprocess.Popen, loop: asyncio.AbstractEventLoop) -> None:
        self._proc = proc
        self._loop = loop
        self._queue: asyncio.Queue = asyncio.Queue()
        self._pending_write: asyncio.Future | None = None
        self._thread = threading.Thread(target=self._pump_stdout, name="term-stdout", daemon=True)
        self._thread.start()

    # -- stdout pump (background thread) ---------------------------------- #
    def _pump_stdout(self) -> None:
        fd = self._proc.stdout.fileno()
        try:
            while True:
                # Blocking os.read: returns when >=1 byte is available (or EOF);
                # it does NOT wait for the full CHUNK like StreamReader.read(n) does.
                data = os.read(fd, CHUNK)
                if not data:  # EOF: the bridge process exited
                    break
                self._loop.call_soon_threadsafe(self._queue.put_nowait, data)
        except (OSError, ValueError):
            pass  # pipe closed / process gone — fall through to EOF sentinel
        finally:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, b"")

    # -- stable interface used by the manager + tests --------------------- #
    @property
    def alive(self) -> bool:
        return self._proc.poll() is None

    def write(self, data: bytes) -> None:
        if not data:
            self._pending_write = None
            return
        self._pending_write = self._loop.run_in_executor(None, self._flush_write, data)

    def _flush_write(self, data: bytes) -> None:
        self._proc.stdin.write(data)
        self._proc.stdin.flush()

    async def drain(self) -> None:
        if self._pending_write is not None:
            fut, self._pending_write = self._pending_write, None
            await fut

    async def read(self, n: int = CHUNK) -> bytes:
        data = await self._queue.get()
        return data[:n] if data else data  # b"" on EOF; else the queued chunk

    def kill(self) -> None:
        if self.alive:
            try:
                self._proc.kill()
            except OSError:
                pass

    async def wait(self) -> int | None:
        if self.alive:
            await self._loop.run_in_executor(None, self._proc.wait)
        return self._proc.returncode


@dataclass
class TerminalSession:
    """One bash PTY inside a terminal container, reachable over the WS."""

    tag: str
    container: str
    cwd: str
    proc: LiveProc
    last_active: float = field(default_factory=time.time)

    @property
    def alive(self) -> bool:
        return self.proc.alive


@dataclass
class _Container:
    name: str
    tag: str
    cwd: str
    last_active: float = field(default_factory=time.time)
    sessions: dict[str, LiveProc] = field(default_factory=dict)


class TerminalManager:
    """Drives the Docker CLI for persistent interactive terminal containers."""

    def __init__(
        self,
        image_name: str | None = None,
        network: str | None = None,
        memory: str | None = None,
        cpus: str | None = None,
        user: str | None = None,
        idle_seconds: int | None = None,
        build_dir: str | None = None,
        docker_cli: str = "docker",
    ) -> None:
        s = get_settings()
        # Reuses the shared code-interpreter image (which now carries bash + tools).
        self.image_name = image_name or s.sandbox_image_name
        self.network = network or s.terminal_network
        self.memory = memory or s.terminal_memory
        self.cpus = cpus or s.terminal_cpus
        self.user = user or s.terminal_user
        self.idle_seconds = idle_seconds if idle_seconds is not None else s.terminal_idle_seconds
        self.build_dir = build_dir if build_dir is not None else s.sandbox_build_context
        self.docker_cli = docker_cli
        self._containers: dict[str, _Container] = {}
        self._lock = asyncio.Lock()

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
            raise TerminalError(f"`docker {' '.join(args[:2])}` timed out after {timeout:.0f}s") from None
        return proc.returncode or 0, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")

    async def _start(self, name: str, cwd: str) -> LiveProc:
        """Spawn the interactive `docker exec -i <name> python3 /ptymaster.py`.

        Uses a plain ``subprocess.Popen`` (not the asyncio subprocess) so the
        raw stdout file descriptor is available for the thread-pumped,
        read-when-available reader in ``LiveProc``.
        """
        loop = asyncio.get_running_loop()
        proc = subprocess.Popen(
            [self.docker_cli, "exec", "-i", "-w", cwd, name, "python3", BRIDGE_PATH],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        return LiveProc(proc, loop)

    # ------------------------------------------------------------------ #
    # image management
    # ------------------------------------------------------------------ #
    async def image_exists(self) -> bool:
        rc, _, _ = await self._exec(["image", "inspect", self.image_name], timeout=30)
        return rc == 0

    async def ensure_image(self) -> None:
        if await self.image_exists():
            return
        rc, out, err = await self._exec(["build", "-t", self.image_name, self.build_dir], timeout=1800)
        if rc != 0:
            tail = (err or out).strip().splitlines()[-5:]
            raise TerminalError(
                f"terminal image build failed (exit {rc}): {' | '.join(tail)} "
                "(in compose, run: docker compose build sandbox-image)"
            )

    # ------------------------------------------------------------------ #
    # container + session lifecycle
    # ------------------------------------------------------------------ #
    async def _is_running(self, name: str) -> bool:
        rc, out, _ = await self._exec(
            ["inspect", "-f", "{{.State.Running}}", name], timeout=30
        )
        return rc == 0 and out.strip() == "true"

    @staticmethod
    def _port_binding(host_port: int | None, container_port: int | None) -> str | None:
        """Normalize a per-project port pair to a ``-p host:container`` binding.

        Returns ``None`` unless BOTH sides are supplied and each is a valid
        1-65535 port — a half pair (or an out-of-range value) is better left
        unbound than bound to a half-configured mapping.
        """
        if host_port is None or container_port is None:
            return None
        try:
            host = int(host_port)
            cont = int(container_port)
        except (TypeError, ValueError):
            return None
        if not (1 <= host <= 65535 and 1 <= cont <= 65535):
            return None
        return f"{host}:{cont}"

    def _create_args(
        self,
        tag: str,
        name: str,
        repo_mount: str | None,
        host_port: int | None = None,
        container_port: int | None = None,
    ) -> list[str]:
        # /workspace volume is keyed by the REPO tag (not the random container
        # name) so scratch state is preserved whenever the tag's container is
        # reaped and re-created.
        workspace_volume = f"qcterm-ws-{_safe_tag(tag)}"
        args = [
            "run",
            "-d",
            "--name",
            name,
            # Recovers tag->container mapping in `reconcile()` after an
            # ungraceful restart wipes the in-memory `_containers` map.
            "--label",
            f"{TAG_LABEL}={tag}",
            f"--memory={self.memory}",
            f"--memory-swap={self.memory}",
            f"--cpus={self.cpus}",
            f"--network={self.network}",
            "--user",
            self.user,
            "-v",
            f"{workspace_volume}:/workspace",
        ]
        if repo_mount:
            args += ["-v", f"{repo_mount}:/repo"]
        binding = self._port_binding(host_port, container_port)
        if binding is not None:
            args += ["-p", binding]
        args += [self.image_name, "sleep", "infinity"]
        return args

    async def ensure_container(
        self,
        tag: str,
        repo_host_dir: str | None = None,
        host_port: int | None = None,
        container_port: int | None = None,
    ) -> tuple[str, str]:
        """Return (container_name, cwd) for `tag`, creating the container if needed.

        Reuses a live tracked container; recreates if the tracked one died. The
        same `tag` (repo) always reuses both its container and its /workspace
        volume, so scratch state survives across idle reaps and restarts.

        ``host_port``/``container_port`` (the project's sandbox ``-p`` pair) are
        applied at container creation; an already-running tracked container was
        bound when it started, so a later changed port only takes effect on the
        next creation — the manager can't re-port a live container.
        """
        async with self._lock:
            tracked = self._containers.get(tag)
            if tracked is not None and await self._is_running(tracked.name):
                for sess in tracked.sessions.values():
                    sess.kill()
                tracked.sessions.clear()
                return tracked.name, tracked.cwd

            # Reuse the tag's existing /workspace volume (idempotent create; the
            # image seeds it with 1000:1000 ownership on first use).
            await self._exec(["volume", "create", f"qcterm-ws-{_safe_tag(tag)}"], timeout=30)
            name = f"{NAME_PREFIX}{uuid.uuid4().hex[:12]}"
            args = self._create_args(tag, name, repo_host_dir, host_port, container_port)
            rc, out, err = await self._exec(args, timeout=120)
            if rc != 0:
                raise TerminalError(f"terminal container create failed (exit {rc}): {err or out}")
            cwd = "/repo" if repo_host_dir else "/workspace"
            self._containers[tag] = _Container(name=name, tag=tag, cwd=cwd)
            return name, cwd

    async def reconcile(self) -> int:
        """Adopt `qcterm-*` containers left running by an ungraceful restart.

        `_containers` is purely in-memory; a crash or a hard kill (as opposed
        to a graceful `docker compose stop`, which drains via `aclose()`)
        skips shutdown cleanup entirely. The next process then starts with an
        empty map, so `ensure_container` can't see the old container and
        spins up a brand new one for the same tag — leaking the orphan
        forever, since nothing else ever discovers or removes it by name.

        Reads the `TAG_LABEL` baked in at creation (see `_create_args`) to
        recover each container's tag, and whether it has a `/repo` mount to
        recover `cwd`. Adopted containers get a fresh `last_active` so the
        idle reaper still governs them going forward. Returns the number of
        containers adopted.
        """
        rc, out, _ = await self._exec(
            ["ps", "--filter", f"label={TAG_LABEL}", "--filter", "status=running", "--format", "{{.Names}}"],
            timeout=30,
        )
        if rc != 0:
            return 0
        adopted = 0
        async with self._lock:
            for name in (n.strip() for n in out.splitlines() if n.strip()):
                rc2, out2, _ = await self._exec(
                    [
                        "inspect",
                        "-f",
                        f'{{{{index .Config.Labels "{TAG_LABEL}"}}}}\t'
                        '{{range .Mounts}}{{if eq .Destination "/repo"}}1{{end}}{{end}}',
                        name,
                    ],
                    timeout=30,
                )
                if rc2 != 0:
                    continue
                tag, _, has_repo = out2.strip("\n").partition("\t")
                if not tag or tag in self._containers:
                    continue
                cwd = "/repo" if has_repo else "/workspace"
                self._containers[tag] = _Container(name=name, tag=tag, cwd=cwd)
                adopted += 1
        return adopted

    async def spawn(
        self,
        tag: str,
        repo_host_dir: str | None = None,
        cols: int = 80,
        rows: int = 24,
        host_port: int | None = None,
        container_port: int | None = None,
    ) -> TerminalSession:
        """Create/attach a container and start a fresh bash session in it.

        ``host_port``/``container_port`` bind the container to the host at
        creation (``-p host:container``) so in-sandbox dev servers are reachable
        at ``http://localhost:host_port``; pass the project's ``ProjectSettings``
        values. Omit both to leave the container unbound.
        """
        name, cwd = await self.ensure_container(
            tag, repo_host_dir, host_port, container_port
        )
        proc = await self._start(name, cwd)
        sess = TerminalSession(tag=tag, container=name, cwd=cwd, proc=proc)
        async with self._lock:
            self._containers[tag].sessions[proc_id(sess)] = proc
            self._containers[tag].last_active = time.time()
        # Prime the pty with the caller's size (the client re-sends on ready).
        await self.resize(sess, rows, cols)
        return sess

    async def feed(self, sess: TerminalSession, data: bytes) -> None:
        """Write user/terminal bytes into the pty."""
        if not data or not sess.alive:
            logger.debug("feed skip: alive=%s len=%s", sess.alive, len(data))
            return
        self._touch(sess.tag)
        for i in range(0, len(data), CHUNK):
            sess.proc.write(encode_input(data[i:i + CHUNK]))
            await sess.proc.drain()
        logger.debug("feed wrote %d bytes (alive=%s)", len(data), sess.alive)

    async def resize(self, sess: TerminalSession, rows: int, cols: int) -> None:
        if not sess.alive or rows is None or cols is None or rows <= 0 or cols <= 0:
            return
        self._touch(sess.tag)
        sess.proc.write(encode_resize(rows, cols))
        await sess.proc.drain()
        logger.debug("resize wrote rows=%s cols=%s (alive=%s)", rows, cols, sess.alive)

    def read_chunk(self, sess: TerminalSession, n: int = CHUNK) -> asyncio.Task[bytes]:
        """Schedule reading the next pty output chunk (empty bytes => EOF)."""
        return asyncio.ensure_future(sess.proc.read(n))

    def _touch(self, tag: str) -> None:
        tracked = self._containers.get(tag)
        if tracked is not None:
            tracked.last_active = time.time()

    async def close_session(self, sess: TerminalSession) -> None:
        """Kill one bash session and drop it from tracking (container persists)."""
        try:
            sess.proc.kill()
            await sess.proc.wait()
        except (ProcessLookupError, OSError):
            pass
        async with self._lock:
            c = self._containers.get(sess.tag)
            if c is not None:
                c.sessions.pop(proc_id(sess), None)
                c.last_active = time.time()

    async def reap_idle(self, now: float | None = None) -> int:
        """Kill + remove containers idle longer than `idle_seconds`.

        Idle = no input/resize within the window (the WS may still be open on a
        collapsed tab). Returns the number of containers reaped.
        """
        now = time.time() if now is None else now
        async with self._lock:
            stale = [
                self._containers.pop(tag)
                for tag, c in list(self._containers.items())
                if now - c.last_active > self.idle_seconds
            ]
        for c in stale:
            for p in c.sessions.values():
                try:
                    p.kill()
                except (ProcessLookupError, OSError):
                    pass
            await self._remove_container(c.name)
        return len(stale)

    async def aclose(self) -> None:
        """Kill every tracked session and remove every tracked container (shutdown)."""
        async with self._lock:
            stale = list(self._containers.values())
            self._containers.clear()
        for c in stale:
            for p in c.sessions.values():
                try:
                    p.kill()
                except (ProcessLookupError, OSError):
                    pass
            await self._remove_container(c.name)

    async def _remove_container(self, name: str) -> None:
        try:
            await self._exec(["rm", "-f", name], timeout=30)
        except (TerminalError, FileNotFoundError):
            pass

    def tracked(self) -> dict[str, _Container]:
        return dict(self._containers)


# --------------------------------------------------------------------------- #
# process-wide singleton (created in app lifespan; tests build their own)
# --------------------------------------------------------------------------- #
_manager: TerminalManager | None = None


def get_terminal_manager() -> TerminalManager:
    global _manager
    if _manager is None:
        _manager = TerminalManager()
    return _manager


def set_terminal_manager(manager: TerminalManager | None) -> None:
    """Test seam: override (or reset with None) the process-wide manager."""
    global _manager
    _manager = manager


async def resolve_project_container(full_name: str) -> tuple[str, str]:
    """Ensure (creating if needed) and return `(name, cwd)` for the repo's own
    persistent sandbox container — the SAME `qcterm-*` container backing the
    interactive Terminal Dock for this repo. `cwd` is `/repo` when the repo is
    cloned, else `/workspace` (see TerminalManager.ensure_container).

    The single point every model-facing docker_*/check tool goes through to
    reach "the project's container" — none of them accept a container name
    from the model.
    """
    from app.repos.sync import resolve_repo_host_dir

    repo_host_dir = resolve_repo_host_dir(full_name)
    return await get_terminal_manager().ensure_container(full_name, repo_host_dir)


if __name__ == "__main__":  # pragma: no cover - local sanity check
    mg = TerminalManager(docker_cli="docker")
    print("image:", mg.image_name, "network:", mg.network, "user:", mg.user, "idle:", mg.idle_seconds)
