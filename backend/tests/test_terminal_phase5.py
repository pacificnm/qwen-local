"""Phase 5 (terminal): TerminalManager lifecycle + bridge framing.

The manager's docker-CLI seams (``_exec``, ``_start``) are replaced with fakes,
so no real daemon is needed — same strategy as test_sandbox_phase5.py.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from sqlalchemy.dialects import postgresql

from app.sandbox.terminal import (
    CHUNK,
    OP_INPUT,
    OP_RESIZE,
    TerminalError,
    TerminalManager,
    _safe_tag,
    encode_input,
    encode_resize,
    proc_id,
)
from app.api.terminals import _frame, session_by_token

TAG = "owner-repo"


# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #
class FakeLiveProc:
    """Captures bytes written by feed/resize and offers a small output stream."""

    def __init__(self, out: list[bytes] = (b"banner\n", b"$ ")):
        self.out = list(out)
        self.returncode: int | None = None
        self.written: list[bytes] = []
        self._eof = False
        self.killed = False

    @property
    def alive(self) -> bool:
        return self.returncode is None

    def write(self, data: bytes) -> None:
        self.written.append(data)

    async def drain(self) -> None:
        pass

    async def read(self, n: int = CHUNK) -> bytes:
        if not self._eof:
            self._eof = True
            return b"".join(self.out)
        if self.returncode is None:
            self.returncode = 0
        return b""

    def kill(self) -> None:
        if self.alive:
            self.returncode = 137

    async def wait(self) -> int | None:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def frames_written(self) -> list[list[int]]:
        """Decode each written frame as [op, ...] for assertions."""
        out = []
        for frame in self.written:
            out.append(list(frame))
        return out


class FakeTerminalDocker:
    """Fake docker CLI backend for the manager's _exec / _start seams."""

    def __init__(self, *, image_exists_rc: int = 0, run_rc: int = 0):
        self.image_exists_rc = image_exists_rc
        self.run_rc = run_rc
        self.exec_calls: list[list[str]] = []
        self.start_calls: list[tuple[str, str]] = []
        self.rm_calls: list[str] = []
        self.running: set[str] = set()
        self.last_proc: FakeLiveProc | None = None
        # name -> qcterm.tag label value; name -> has a /repo mount.
        self.labels: dict[str, str] = {}
        self.repo_mounted: set[str] = set()

    async def fake_exec(
        self, args: list[str], *, input_bytes: bytes | None = None, timeout: float = 60.0
    ) -> tuple[int, str, str]:
        self.exec_calls.append(list(args))
        verb = args[0]
        if verb == "image":  # image inspect <name>
            return self.image_exists_rc, "", ""
        if verb == "build":
            return 0, "built\n", ""
        if verb == "volume":  # volume create <name>
            return 0, "", ""
        if verb == "run":
            if self.run_rc != 0:
                return 1, "", "no such image\n"
            name = args[args.index("--name") + 1]
            self.running.add(name)
            if "--label" in args:
                label = args[args.index("--label") + 1]
                _, _, tag = label.partition("=")
                self.labels[name] = tag
            if "/repo" in args:
                self.repo_mounted.add(name)
            return 0, "containerid\n", ""
        if verb == "ps":  # ps --filter label=qcterm.tag --filter status=running --format {{.Names}}
            names = [n for n in self.labels if n in self.running]
            return 0, "\n".join(names), ""
        if verb == "inspect" and "-f" in args and "State.Running" in args[args.index("-f") + 1]:
            name = args[-1]
            if name in self.running:
                return 0, "true\n", ""
            return 1, "", "no such container\n"
        if verb == "inspect":  # inspect -f <label>\t<repo-mount> <name>  (reconcile)
            name = args[-1]
            tag = self.labels.get(name, "")
            has_repo = "1" if name in self.repo_mounted else ""
            return 0, f"{tag}\t{has_repo}", ""
        if verb == "rm":
            name = args[-1]
            self.rm_calls.append(name)
            self.running.discard(name)
            return 0, "", ""
        raise AssertionError(f"unexpected docker args: {args}")

    async def fake_start(self, name: str, cwd: str) -> FakeLiveProc:
        self.start_calls.append((name, cwd))
        proc = FakeLiveProc()
        self.last_proc = proc
        return proc


def make_manager(fd: FakeTerminalDocker) -> TerminalManager:
    mgr = TerminalManager(
        image_name="qwen-code-sandbox:latest",
        network="bridge",
        memory="1g",
        cpus="2",
        user="1000:1000",
        idle_seconds=1800,
        build_dir="sandbox",
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
# framing (shared by the manager, the WS layer, and the bridge)
# --------------------------------------------------------------------------- #
def test_encode_input_frame_layout():
    frame = encode_input(b"ls\n")
    assert frame[0] == OP_INPUT
    assert int.from_bytes(frame[1:5], "big") == len(b"ls\n")
    assert frame[5:] == b"ls\n"


def test_encode_empty_input_is_empty_frame():
    assert encode_input(b"") == b""


def test_encode_resize_frame_layout():
    frame = encode_resize(30, 100)
    assert frame[0] == OP_RESIZE
    assert int.from_bytes(frame[1:3], "big") == 30
    assert int.from_bytes(frame[3:5], "big") == 100


def test_frame_reads_raw_asgi_binary_and_text():
    # Starlette >= 1.0 delivers raw ASGI frames keyed "bytes" / "text".
    assert _frame({"type": "websocket.receive", "bytes": b"pwd\n"}) == (None, b"pwd\n")
    assert _frame({"type": "websocket.receive", "text": "echo hi"}) == ("echo hi", None)


def test_frame_falls_back_to_legacy_data_key():
    # Pre-1.0 Starlette used to expose frames under a single "data" key.
    assert _frame({"type": "websocket.receive", "data": b"ls\n"}) == (None, b"ls\n")
    assert _frame({"type": "websocket.receive", "data": "ls\n"}) == ("ls\n", None)


def test_frame_empty_message_is_all_none():
    # A control-only message (e.g. ping) carries neither payload key.
    assert _frame({"type": "websocket.ping"}) == (None, None)


def test_safe_tag_slug():
    assert _safe_tag("Owner/Repo") == "owner-repo"
    assert _safe_tag("a/b.c_d") == "a-b-c-d"
    assert _safe_tag("____") == "repo"


# --------------------------------------------------------------------------- #
# docker args + container lifecycle
# --------------------------------------------------------------------------- #
def test_create_args_uses_bridge_profile_and_workspace_volume():
    mgr = make_manager(FakeTerminalDocker())
    args = mgr._create_args(TAG, "qcterm-abc", repo_mount=None)
    assert args[:3] == ["run", "-d", "--name"]
    joined = " ".join(args)
    assert "--network=bridge" in joined
    assert "--user 1000:1000" in joined
    assert "-v qcterm-ws-owner-repo:/workspace" in joined
    assert "/repo" not in joined
    assert args[-3:] == ["qwen-code-sandbox:latest", "sleep", "infinity"]


def test_create_args_mounts_repo_when_requested():
    mgr = make_manager(FakeTerminalDocker())
    args = mgr._create_args(TAG, "qcterm-abc", repo_mount="/data/projects/x/workspace/owner__repo")
    assert "-v" in args
    joined = " ".join(args)
    assert "-v /data/projects/x/workspace/owner__repo:/repo" in joined


def test_create_args_binds_port_when_pair_supplied():
    mgr = make_manager(FakeTerminalDocker())
    args = mgr._create_args(TAG, "qcterm-abc", repo_mount=None, host_port=3000, container_port=80)
    assert "-p" in args
    # the bind sits before the image and is exactly the project's pair
    assert args.index("-p") < args.index("qwen-code-sandbox:latest")
    assert args[args.index("-p") + 1] == "3000:80"


def test_create_args_uses_explicit_override_pair():
    mgr = make_manager(FakeTerminalDocker())
    args = mgr._create_args(
        TAG, "qcterm-abc", repo_mount="/data/x/workspace/owner__repo",
        host_port=8080, container_port=3000,
    )
    assert args[args.index("-p") + 1] == "8080:3000"


def test_create_args_omits_bind_when_pair_incomplete_or_invalid():
    mgr = make_manager(FakeTerminalDocker())
    cases = (
        {"host_port": 3000},                          # container side missing
        {"container_port": 80},                       # host side missing
        {"host_port": 0, "container_port": 80},       # host out of range
        {"host_port": "nope", "container_port": 80},  # non-int
    )
    for kwargs in cases:
        args = mgr._create_args(TAG, "qcterm-abc", repo_mount=None, **kwargs)
        assert "-p" not in args, kwargs


def test_port_binding_normalization():
    bind = TerminalManager._port_binding
    assert bind(3000, 80) == "3000:80"
    assert bind(8080, 3000) == "8080:3000"
    assert bind(3000, None) is None
    assert bind(None, 80) is None
    assert bind(0, 80) is None
    assert bind(3000, 999_999_999) is None


def test_spawn_threads_ports_into_run_args():
    fd = FakeTerminalDocker()
    mgr = make_manager(fd)
    _sync(mgr.spawn(TAG, repo_host_dir=None, host_port=3000, container_port=80))
    run = next(a for a in fd.exec_calls if a[0] == "run")
    assert run[run.index("-p") + 1] == "3000:80"


def test_spawn_without_ports_omits_bind():
    fd = FakeTerminalDocker()
    mgr = make_manager(fd)
    _sync(mgr.spawn(TAG, repo_host_dir=None, cols=80, rows=24))
    run = next(a for a in fd.exec_calls if a[0] == "run")
    assert "-p" not in run


def test_spawn_primes_resize_and_tracks_session():
    fd = FakeTerminalDocker()
    mgr = make_manager(fd)
    sess = _sync(mgr.spawn(TAG, repo_host_dir=None, cols=80, rows=24))

    # container created (run + volume create) and a session started on the right cwd
    assert any(a[0] == "run" for a in fd.exec_calls)
    assert any(a[0] == "volume" for a in fd.exec_calls)
    name, cwd = fd.start_calls[0]
    assert cwd == "/workspace"  # no repo -> default scratch cwd
    assert name.startswith("qcterm-")

    # the session is tracked and primed with exactly one RESIZE frame
    assert proc_id(sess) in mgr.tracked()[TAG].sessions
    frames = fd.last_proc.frames_written()
    assert [f[0] for f in frames] == [OP_RESIZE]
    assert int.from_bytes(bytes(frames[0][1:3]), "big") == 24
    assert int.from_bytes(bytes(frames[0][3:5]), "big") == 80


def test_spawn_repo_default_is_repo_dir():
    fd = FakeTerminalDocker()
    mgr = make_manager(fd)
    sess = _sync(mgr.spawn(TAG, repo_host_dir="/data/x/workspace/owner__repo", cols=80, rows=24))
    assert sess.cwd == "/repo"
    name, cwd = fd.start_calls[0]
    assert cwd == "/repo"


def test_ensure_container_reuses_running_session():
    fd = FakeTerminalDocker()
    mgr = make_manager(fd)
    first_name, _cwd = _sync(mgr.ensure_container(TAG))
    second_name, _cwd2 = _sync(mgr.ensure_container(TAG))
    # no second `run` — the live container is reused (volume create is idempotent)
    runs = [a for a in fd.exec_calls if a[0] == "run"]
    assert len(runs) == 1
    assert second_name == first_name


def test_reconcile_adopts_orphaned_labeled_container():
    # Simulate a container left running by a prior (now-dead) process: it
    # exists in the fake daemon but no manager has ever tracked it in-memory.
    fd = FakeTerminalDocker()
    fd.running.add("qcterm-orphan1")
    fd.labels["qcterm-orphan1"] = TAG

    mgr = make_manager(fd)
    adopted = _sync(mgr.reconcile())

    assert adopted == 1
    tracked = mgr.tracked()[TAG]
    assert tracked.name == "qcterm-orphan1"
    assert tracked.cwd == "/workspace"  # no /repo mount recorded


def test_reconcile_recovers_repo_cwd_from_mount():
    fd = FakeTerminalDocker()
    fd.running.add("qcterm-orphan2")
    fd.labels["qcterm-orphan2"] = TAG
    fd.repo_mounted.add("qcterm-orphan2")

    mgr = make_manager(fd)
    _sync(mgr.reconcile())

    assert mgr.tracked()[TAG].cwd == "/repo"


def test_reconcile_skips_tags_already_tracked():
    fd = FakeTerminalDocker()
    mgr = make_manager(fd)
    live_name, _cwd = _sync(mgr.ensure_container(TAG))

    # An orphan sharing the same tag must not clobber the live entry.
    fd.running.add("qcterm-orphan3")
    fd.labels["qcterm-orphan3"] = TAG
    adopted = _sync(mgr.reconcile())

    assert adopted == 0
    assert mgr.tracked()[TAG].name == live_name


def test_reconcile_then_ensure_container_reuses_adopted_container():
    fd = FakeTerminalDocker()
    fd.running.add("qcterm-orphan4")
    fd.labels["qcterm-orphan4"] = TAG

    mgr = make_manager(fd)
    _sync(mgr.reconcile())
    name, _cwd = _sync(mgr.ensure_container(TAG))

    assert name == "qcterm-orphan4"
    assert not [a for a in fd.exec_calls if a[0] == "run"]  # no new container created


def test_feed_writes_input_frames_for_each_chunk():
    fd = FakeTerminalDocker()
    mgr = make_manager(fd)
    sess = _sync(mgr.spawn(TAG))
    _sync(mgr.feed(sess, b"echo hello\n"))
    frames = fd.last_proc.frames_written()
    ops = [f[0] for f in frames]
    assert ops[-1] == OP_INPUT
    # payload after the 4-byte length
    last = bytes(frames[-1])
    assert last[5:] == b"echo hello\n"


def test_resize_writes_a_resize_frame():
    fd = FakeTerminalDocker()
    mgr = make_manager(fd)
    sess = _sync(mgr.spawn(TAG))
    _sync(mgr.resize(sess, 40, 120))
    frames = fd.last_proc.frames_written()
    last = bytes(frames[-1])
    assert last[0] == OP_RESIZE
    assert int.from_bytes(last[1:3], "big") == 40
    assert int.from_bytes(last[3:5], "big") == 120


def test_read_chunk_streams_output_then_eof():
    fd = FakeTerminalDocker()
    mgr = make_manager(fd)
    sess = _sync(mgr.spawn(TAG))

    async def _read_two() -> tuple[bytes, bytes]:
        first = await mgr.read_chunk(sess)
        second = await mgr.read_chunk(sess)
        return first, second

    first, second = _sync(_read_two())
    assert first == b"banner\n$ "  # initial pty output
    assert second == b""  # subsequent read hits EOF


def test_close_session_kills_and_untracks_but_keeps_container():
    fd = FakeTerminalDocker()
    mgr = make_manager(fd)
    sess = _sync(mgr.spawn(TAG))
    name, _ = fd.start_calls[0]
    _sync(mgr.close_session(sess))
    # session dropped from tracking, proc killed, container NOT removed
    assert proc_id(sess) not in mgr.tracked()[TAG].sessions
    assert fd.last_proc.alive is False
    assert name in fd.running  # container still up
    assert name not in fd.rm_calls


def test_reap_idle_drops_stale_containers_only():
    fd = FakeTerminalDocker()
    mgr = make_manager(fd)
    _sync(mgr.spawn(TAG))           # fresh (tag owner-repo)
    _sync(mgr.spawn("other-repo"))  # will be aged stale
    # force the second tracked container to look stale
    mgr.tracked()["other-repo"].last_active = time.time() - 9999
    n = _sync(mgr.reap_idle())
    assert n == 1
    assert "other-repo" not in mgr.tracked()
    assert TAG in mgr.tracked()
    # exactly one container removed — the stale one, not the fresh one
    assert len(fd.rm_calls) == 1
    fresh_name = fd.start_calls[0][0]
    assert fresh_name not in fd.rm_calls
    assert fresh_name in fd.running


def test_aclose_removes_every_container():
    fd = FakeTerminalDocker()
    mgr = make_manager(fd)
    _sync(mgr.spawn(TAG))
    _sync(mgr.spawn("other-repo"))
    fd.rm_calls.clear()
    _sync(mgr.aclose())
    # both containers removed at shutdown, and tracking emptied
    assert len(fd.rm_calls) == 2
    assert mgr.tracked() == {}


def test_container_create_failure_raises():
    fd = FakeTerminalDocker(run_rc=1)
    mgr = make_manager(fd)
    with pytest.raises(TerminalError, match="create failed"):
        _sync(mgr.spawn(TAG))


# --------------------------------------------------------------------------- #
# WS auth: the session -> user lookup must be eager (asyncpg forbids lazy loads)
# --------------------------------------------------------------------------- #
def test_authorize_eager_loads_user_for_asyncpg():
    # Regression: the WS handshake 500'd with ``MissingGreenlet`` because it
    # lazy-loaded ``Session.user`` under asyncpg. The lookup must JOIN the user
    # in so the handshake succeeds against a real Postgres.
    stmt = session_by_token("tok")
    sql = str(
        stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )
    # A single round trip (LEFT JOIN users) — not a follow-up lazy SELECT.
    assert "JOIN users" in sql


# --------------------------------------------------------------------------- #
# _resolve_repo_host_dir: host path of the clone for the /repo bind mount
#
# Regression for the "empty /repo" bug: a naive `Path(workspace_host_dir).is_dir()`
# is evaluated against the CONTAINER filesystem, so it always missed (the host
# path is not present in the backend container) and silently fell through to the
# in-container path — which the host docker daemon cannot see, so it auto-created
# an empty dir. The resolver must (a) trust an explicit workspace_host_dir, and
# (b) still report "not cloned" for uncloned repos via the in-container bind
# target, which IS present for every cloned repo.
# --------------------------------------------------------------------------- #
import app.repos.sync as _repo_sync
from app.core.settings import Settings as _Settings

_FULL = "owner/repo"
_SLUG = "owner__repo"


def test_repo_host_dir_host_dir_set_clone_exists(tmp_path, monkeypatch):
    """workspace_host_dir set + clone present → the HOST bind source."""
    (tmp_path / _SLUG).mkdir(parents=True)
    host_dir = str(Path(tmp_path) / "host")
    monkeypatch.setattr(_repo_sync, "get_settings", lambda: _Settings(workspace_host_dir=host_dir))
    monkeypatch.setattr(_repo_sync, "workspace", lambda: tmp_path)
    assert _repo_sync.resolve_repo_host_dir(_FULL) == str(Path(host_dir) / _SLUG)


def test_repo_host_dir_host_dir_set_clone_absent(tmp_path, monkeypatch):
    """workspace_host_dir set + NOT cloned → None (fall back to /workspace)."""
    monkeypatch.setattr(
        _repo_sync, "get_settings", lambda: _Settings(workspace_host_dir=str(tmp_path / "host"))
    )
    monkeypatch.setattr(_repo_sync, "workspace", lambda: tmp_path)
    assert _repo_sync.resolve_repo_host_dir(_FULL) is None


def test_repo_host_dir_no_host_dir_clone_exists(tmp_path, monkeypatch):
    """No workspace_host_dir (host-local dev) + clone present → in-container path."""
    (tmp_path / _SLUG).mkdir(parents=True)
    monkeypatch.setattr(_repo_sync, "get_settings", lambda: _Settings(workspace_host_dir=""))
    monkeypatch.setattr(_repo_sync, "workspace", lambda: tmp_path)
    assert _repo_sync.resolve_repo_host_dir(_FULL) == str(tmp_path / _SLUG)


def test_repo_host_dir_no_host_dir_clone_absent(tmp_path, monkeypatch):
    """No workspace_host_dir + NOT cloned → None."""
    monkeypatch.setattr(_repo_sync, "get_settings", lambda: _Settings(workspace_host_dir=""))
    monkeypatch.setattr(_repo_sync, "workspace", lambda: tmp_path)
    assert _repo_sync.resolve_repo_host_dir(_FULL) is None
