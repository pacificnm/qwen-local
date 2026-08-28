"""Interactive terminal (VSCode-style) endpoints.

Two endpoints:

- ``WS  /api/terminals/{repo_id}/ws`` — the live terminal. The client speaks a
  simple protocol: **binary** frames are keystrokes (forwarded verbatim into the
  pty), **text** frames are JSON control (``{"type":"resize","rows":N,"cols":M}``).
  Output flows back as **binary** pty bytes. The backend owns the bridge framing
  (see app/sandbox/terminal.py + sandbox/bridge.py).
- ``GET /api/terminals/{repo_id}`` — status (working dir + whether a container
  is currently tracked for this repo).

Auth is the session cookie, read manually from the WS upgrade headers (WebSockets
do not run FastAPI's DI). Scoped to a linked repo: the terminal mounts that
repo read+write at ``/repo`` and gets a persistent writable ``/workspace``.
"""

import asyncio
import json
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.api.deps import get_current_user, get_db
from app.core.settings import get_settings
from app.db.models import ProjectSettings, Repository, User
from app.db.models import Session as AppSession
from app.db.session import db_session
from app.repos import gitops
from app.repos.sync import workspace
from app.sandbox.terminal import TerminalError, get_terminal_manager

logger = logging.getLogger("qwen-chat.terminal")

router = APIRouter(prefix="/api/terminals", tags=["terminals"])


def _resolve_repo_host_dir(full_name: str) -> str | None:
    """Host-side path of the repo worktree for the ``/repo`` bind mount.

    The docker daemon (on the host) resolves bind sources against HOST paths, so
    this must return a path as seen FROM THE HOST, not the backend container.

    - ``workspace_host_dir`` (set in compose) is authoritative: it is the host
      directory behind the container's workspace mount (e.g.
      ``/data/.../workspace`` behind ``/srv/app/workspace``). That host path is
      *not* present inside the backend container by construction, so it is
      returned as-is with NO existence check — a container-namespace
      ``is_dir()`` would always miss and silently fall through to the in-container
      path below, which the host daemon cannot see (→ an empty ``/repo`` mount).
    - ``workspace() / slug`` is the container view of the SAME clone (the
      ``workspace`` dir is the bind-mount target). It IS present in-container for
      a cloned repo, so ``.is_dir()`` on *it* is a valid "is it cloned?" guard in
      both compose and host-local contexts.
    - ``workspace_host_dir`` unset (host-local dev, backend and daemon share a
      filesystem) → the in-container path IS a valid host path and is used directly.

    Net: a cloned repo → the host bind source (real files at ``/repo``); an
    uncloned repo → ``None`` so the terminal falls back to its writable ``/workspace``.
    """
    settings = get_settings()
    slug = full_name.replace("/", "__")
    clone_in_container = gitops.workspace_repo_dir(workspace(), full_name)
    if settings.workspace_host_dir:
        if clone_in_container.is_dir():
            return str(Path(settings.workspace_host_dir) / slug)
        return None
    if clone_in_container.is_dir():
        return str(clone_in_container)
    return None


async def _sandbox_ports(repo_id: uuid.UUID) -> tuple[int, int]:
    """Per-project ``-p`` pair (host, container) for the terminal of ``repo_id``.

    The terminal is scoped to a repo; a repo serves at most one project, so this
    follows repo -> project -> its one-to-one settings row to read
    ``sandbox_port`` / ``sandbox_container_port`` (the source of truth for the
    binding — nothing is hardcoded in the manager). Falls back to the spec
    defaults (9000 host : 80 container) when the repo is unassigned or has no
    settings row, so a fresh project gets a reachable bind out of the box.
    """
    defaults: tuple[int, int] = (9000, 80)
    async for db in db_session():
        repo = await db.get(Repository, repo_id)
        if repo is None or repo.project_id is None:
            return defaults
        row = (
            await db.execute(
                select(ProjectSettings).where(
                    ProjectSettings.project_id == repo.project_id
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return defaults
        return (row.sandbox_port or 9000, row.sandbox_container_port or 80)
    return defaults  # pragma: no cover - db_session always yields once


def _cookie(ws: WebSocket, name: str) -> str | None:
    raw = ws.headers.get("cookie") or ""
    for part in raw.split(";"):
        part = part.strip()
        if part.startswith(name + "="):
            return part[len(name) + 1:]
    return None


def session_by_token(token: str):
    """The ``sessions -> users`` lookup as an eager-loaded statement.

    ``Session.user`` is a lazy relationship; the asyncpg dialect forbids lazy
    loads (``MissingGreenlet``), so the user must be JOINed in — same pattern as
    ``deps.get_current_user``. Factored out so tests can pin this down.
    """
    return (
        select(AppSession)
        .options(joinedload(AppSession.user))
        .where(AppSession.token == token)
    )


async def _authorize(token: str | None, repo_id: uuid.UUID) -> str:
    """Validate the session cookie and resolve the linked repo; return full_name.

    Raises HTTPException (mapped to a WS close by the caller) on failure.
    Runs inside a short DB session; the generator closes it when the loop exits.
    """
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    from datetime import UTC, datetime

    full_name: str | None = None
    ok = False
    async for db in db_session():
        row = (await db.execute(session_by_token(token))).scalar_one_or_none()
        if row is None or row.expires_at < datetime.now(UTC):
            if row is not None:
                await db.delete(row)
                await db.commit()
            raise HTTPException(status_code=401, detail="Session expired")
        user = row.user
        if user is None or not user.active:
            raise HTTPException(status_code=401, detail="Account disabled")
        repo = await db.get(Repository, repo_id)
        if repo is None or repo.id != repo_id:
            raise HTTPException(status_code=404, detail="Repository not linked")
        full_name = repo.github_full_name
        ok = True
    if not ok or full_name is None:  # pragma: no cover - defensive
        raise HTTPException(status_code=404, detail="Repository not linked")
    return full_name


def _frame(msg: dict) -> tuple[str | None, bytes | None]:
    """Split one received WS frame into ``(text, bytes)``.

    Version-agnostic across Starlette: >= 1.0 delivers RAW ASGI frames — a text
    frame as ``{"text": "..."}`` and a binary frame as ``{"bytes": b"..."}``;
    pre-1.0 merged both under a single ``"data"`` key. Returns the text if the
    frame is text, else the binary payload as ``bytes`` (at most one is set).
    """
    text = msg.get("text")
    if not isinstance(text, str):
        text = msg.get("data") if isinstance(msg.get("data"), str) else None
    payload = msg.get("bytes")
    if payload is None:
        legacy = msg.get("data")
        payload = legacy if isinstance(legacy, (bytes, bytearray)) else None
    payload = bytes(payload) if payload is not None else None
    return text, payload


@router.websocket("/ws/{repo_id}")
async def terminal_ws(repo_id: uuid.UUID, ws: WebSocket) -> None:
    settings = get_settings()
    token = _cookie(ws, settings.cookie_name)
    try:
        full_name = await _authorize(token, repo_id)
    except HTTPException as exc:
        await ws.close(code=1011 if exc.status_code != 401 else 4401)
        logger.warning("terminal ws auth failed: %s", exc.detail)
        return

    repo_host_dir = _resolve_repo_host_dir(full_name)
    host_port, container_port = await _sandbox_ports(repo_id)
    cols = _int_q(ws, "cols", 80)
    rows = _int_q(ws, "rows", 24)

    manager = get_terminal_manager()
    await ws.accept()
    try:
        sess = await manager.spawn(
            full_name,
            repo_host_dir,
            cols=cols,
            rows=rows,
            host_port=host_port,
            container_port=container_port,
        )
    except TerminalError as exc:
        await ws.send_text(json.dumps({"type": "error", "error": str(exc)}))
        await ws.close(code=1011)
        return

    await ws.send_text(
        json.dumps(
            {
                "type": "ready",
                "cwd": sess.cwd,
                "tag": full_name,
                "host_port": host_port,
            }
        )
    )

    async def to_bridge() -> None:
        try:
            while True:
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    return
                raw_text, raw_bytes = _frame(msg)
                logger.debug(
                    "ws recv: text_len=%s bytes_len=%s",
                    len(raw_text) if raw_text is not None else None,
                    len(raw_bytes) if raw_bytes is not None else None,
                )
                if raw_bytes is not None:
                    await manager.feed(sess, raw_bytes)
                elif raw_text is not None:
                    try:
                        ctrl = json.loads(raw_text)
                    except (ValueError, TypeError):
                        await manager.feed(sess, raw_text.encode("utf-8", "replace"))
                    else:
                        if ctrl.get("type") == "resize":
                            await manager.resize(
                                sess,
                                int(ctrl.get("rows") or 0),
                                int(ctrl.get("cols") or 0),
                            )
        except WebSocketDisconnect:
            return
        except Exception:  # pragma: no cover - diagnostic
            logger.exception("to_bridge failed")
            raise

    async def to_client() -> None:
        try:
            while True:
                chunk = await manager.read_chunk(sess)
                if not chunk:
                    break
                logger.debug("ws send->client: %d bytes", len(chunk))
                await ws.send_bytes(chunk)
        except Exception:  # pragma: no cover - diagnostic
            logger.exception("to_client failed")
            raise

    # Two loops: one per direction. When EITHER ends (client gone, or the pty
    # EOFed because bash exited), tear the session down and close the socket.
    t_bridge = asyncio.create_task(to_bridge())
    t_client = asyncio.create_task(to_client())
    await asyncio.wait({t_bridge, t_client}, return_when=asyncio.FIRST_COMPLETED)
    for t in (t_bridge, t_client):
        t.cancel()
    for t in (t_bridge, t_client):
        try:
            await t
        except (asyncio.CancelledError, Exception):  # noqa: BLE001 - cleanup path
            pass
    await manager.close_session(sess)
    try:
        await ws.close()
    except RuntimeError:
        pass  # already closed


@router.get("/{repo_id}")
async def terminal_status(
    repo_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    repo = await db.get(Repository, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not linked")
    manager = get_terminal_manager()
    tracked = manager.tracked()
    c = tracked.get(repo.github_full_name)
    return {
        "repo_id": str(repo.id),
        "running": bool(c is not None and c.sessions),
        "cwd": (c.cwd if c is not None else _default_cwd(repo.github_full_name)),
        "live_sessions": (len(c.sessions) if c is not None else 0),
    }


def _default_cwd(full_name: str) -> str:
    return "/repo" if _resolve_repo_host_dir(full_name) else "/workspace"


def _int_q(ws: WebSocket, key: str, default: int) -> int:
    """Best-effort int for a WS query-string param (defaults if absent/invalid).

    Starlette exposes the raw query as a plain string (``ws.url.query``), not a
    dict, so parse it explicitly rather than assuming a ``.query_params`` map.
    """
    from urllib.parse import parse_qs

    raw = ws.url.query
    if not raw:
        return default
    try:
        vals = parse_qs(raw, keep_blank_values=True).get(key, [])
        if vals:
            return int(vals[0])
    except (ValueError, TypeError):
        return default
    return default
