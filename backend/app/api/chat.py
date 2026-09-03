"""SSE chat endpoint + cancel (Phase 3, docs/API.md).

Concurrency: one in-flight generation per conversation, tracked in an
in-process registry (single uvicorn worker). Stop = set the run's
asyncio.Event; the agent loop and the LLM stream observe it and exit fast.
Partial assistant text + tool log are always persisted — in the stream
generator's finally on its own DB session, so it also runs on client
disconnect.
"""

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.agents import compaction, qwen_assistant
from app.api.conversations import _get_owned_conv
from app.api.deps import get_current_user, get_db
from app.core.settings import get_settings
from app.db.models import Conversation, Message, Project, ProjectSettings, Repository, User
from app.db.session import get_session_factory
from app.llm.prompts import BASE_SYSTEM, append_context_summary, append_mode_instructions, build_system
from app.repos.errors import SyncError
from app.repos.retrieval import retrieve_chunks

router = APIRouter(prefix="/api/chat", tags=["chat"])
_logger = logging.getLogger("app.chat")

HISTORY_LIMIT = 40  # recent messages kept in context (oldest dropped first)
AUTO_TITLE_CHARS = 60
# Each individual tool_output chunk is already capped (tools.TOOL_OUTPUT_CAP);
# this bounds the ACCUMULATED total for a streaming tool's many small chunks
# before it lands in the persisted Message.tool_calls JSONB.
TOOL_OUTPUT_PERSIST_CAP = 64 * 1024


class ChatIn(BaseModel):
    conversation_id: uuid.UUID
    message: str = Field(min_length=1, max_length=64 * 1024)
    model: str | None = None  # absent → conversation default → strong model
    effort: str | None = None  # absent → DEFAULT_EFFORT; xhigh|high|medium|low
    mode: str | None = None  # absent → "code"; ask|plan|code


class CancelIn(BaseModel):
    request_id: str


@dataclass
class _ActiveRun:
    request_id: str
    conversation_id: uuid.UUID
    cancel: asyncio.Event = field(default_factory=asyncio.Event)


@dataclass
class _TurnState:
    """What the endpoint knows about the run, regardless of the task's fate."""

    text_parts: list[str] = field(default_factory=list)
    tools: list[dict] = field(default_factory=list)
    status: str = "running"  # running | done | cancelled | error


_active: dict[uuid.UUID, _ActiveRun] = {}
_by_request: dict[str, _ActiveRun] = {}


def _release(conv_id: uuid.UUID, run: _ActiveRun) -> None:
    if _active.get(conv_id) is run:
        _active.pop(conv_id, None)
    if _by_request.get(run.request_id) is run:
        _by_request.pop(run.request_id, None)


@router.post("/stream")
async def chat_stream(
    body: ChatIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conv = await _get_owned_conv(db, body.conversation_id, user)
    if conv.id in _active:
        raise HTTPException(
            status_code=409, detail="A generation is already running in this conversation"
        )

    project_settings = await db.scalar(
        select(ProjectSettings).where(ProjectSettings.project_id == conv.project_id)
    )
    model = (
        body.model
        or conv.model_default
        or (project_settings.coding_model if project_settings else None)
        or get_settings().ollama_strong_model
    )
    if not model or not model.strip():
        raise HTTPException(status_code=422, detail="model is required")

    effort = body.effort or qwen_assistant.DEFAULT_EFFORT
    if effort not in qwen_assistant.EFFORT_LEVELS:
        raise HTTPException(
            status_code=422,
            detail=f"unknown effort: {effort!r} (expected low|medium|high|xhigh)",
        )

    mode = body.mode or "code"
    if mode not in ("ask", "plan", "code"):
        raise HTTPException(
            status_code=422, detail=f"unknown mode: {mode!r} (expected ask|plan|code)"
        )

    # Persist the user message BEFORE the stream opens (spec: always kept).
    next_seq = (
        await db.scalar(
            select(func.coalesce(func.max(Message.sequence), 0) + 1).where(
                Message.conversation_id == conv.id
            )
        )
    )
    prior_count = await db.scalar(select(func.count(Message.id)).where(Message.conversation_id == conv.id))
    db.add(
        Message(conversation_id=conv.id, role="user", content=body.message, sequence=next_seq)
    )
    await db.commit()
    assistant_seq = int(next_seq) + 1
    is_first_exchange = int(prior_count) == 0

    # Fold any messages about to fall out of the verbatim history window into
    # the conversation's rolling summary (non-fatal on failure — see module).
    summary = await compaction.maybe_compact(
        db, conv, next_seq=int(next_seq), history_limit=HISTORY_LIMIT
    )

    # The conversation's project (when it has one) serves two purposes here:
    # its repo provides RAG context (top-8 chunks) and the target of the
    # agent's repo tools (read/write/edit/commit). Repo-less project = general.
    system = BASE_SYSTEM
    repo_obj = None
    project = await db.get(Project, conv.project_id)
    if project is not None:
        repo_obj = (
            await db.execute(select(Repository).where(Repository.project_id == project.id))
        ).scalar_one_or_none()
    if repo_obj is not None:
        try:
            chunks = await retrieve_chunks(db, repo_id=repo_obj.id, query=body.message)
        except SyncError as exc:
            raise HTTPException(
                status_code=502, detail=f"search index unavailable: {exc}"
            ) from exc
        system = build_system(repo_obj.github_full_name, chunks)
    # repo vanished (FK SET NULL) → general chat without repo tools
    system = append_context_summary(system, summary)
    system = append_mode_instructions(system, mode)

    history = (
        (
            await db.execute(
                select(Message)
                # <= next_seq: includes the user message persisted above.
                .where(Message.conversation_id == conv.id, Message.sequence <= next_seq)
                .order_by(Message.sequence.desc())
                .limit(HISTORY_LIMIT)
            )
        )
        .scalars()
        .all()[::-1]
    )
    chat_history = [
        {"role": m.role, "content": m.content}
        for m in history
        if m.role in ("user", "assistant") and m.content
    ]

    run = _ActiveRun(request_id=str(uuid.uuid4()), conversation_id=conv.id)
    _active[conv.id] = run
    _by_request[run.request_id] = run

    return EventSourceResponse(
        _stream(
            conv_id=conv.id,
            model=model,
            effort=effort,
            mode=mode,
            system=system,
            chat_history=chat_history,
            repo=repo_obj,
            state=_TurnState(),
            run=run,
            is_first_exchange=is_first_exchange,
            user_text=body.message,
            assistant_seq=assistant_seq,
        ),
        ping=15,  # keep proxies and the tunnel from idling the connection
    )


def _apply_tool_event(tools: list[dict], name: str, data: dict) -> None:
    """Fold one tool_start/tool_output/tool_end SSE event into `tools`
    (mutated in place) — the accumulator behind the persisted
    `Message.tool_calls`. Split out of `_stream`'s `emit` closure so it's
    unit-testable without the SSE/queue machinery around it.

    "_index" is bookkeeping only (matches tool_output/tool_end to the right
    call — MONOTONIC per turn, so it also survives concurrent/interleaved
    tool calls); "_capped" marks an entry whose output already hit
    TOOL_OUTPUT_PERSIST_CAP so further chunks are dropped instead of
    re-truncating on every one. Both are stripped before persisting
    (see `_persist_turn`).
    """
    if name == "tool_start":
        tools.append({"name": data.get("tool"), "arguments": data.get("arguments"), "_index": data.get("index")})
    elif name == "tool_output":
        idx = data.get("index")
        for t in tools:
            if t.get("_index") == idx and not t.get("_capped"):
                combined = t.get("output", "") + str(data.get("text", ""))
                if len(combined) > TOOL_OUTPUT_PERSIST_CAP:
                    note = f"\n… [truncated to {TOOL_OUTPUT_PERSIST_CAP // 1024} KB]"
                    combined = combined[: TOOL_OUTPUT_PERSIST_CAP - len(note)] + note
                    t["_capped"] = True
                t["output"] = combined
                break
    elif name == "tool_end":
        idx = data.get("index")
        for t in tools:
            if t.get("_index") == idx:
                t["ok"] = bool(data.get("ok"))
                t["duration_ms"] = int(data.get("duration_ms", 0))
                break


async def _stream(
    *,
    conv_id: uuid.UUID,
    model: str,
    effort: str,
    mode: str,
    system: str,
    chat_history: list[dict],
    repo: Repository | None,
    state: _TurnState,
    run: _ActiveRun,
    is_first_exchange: bool,
    user_text: str,
    assistant_seq: int,
):
    q: asyncio.Queue = asyncio.Queue()
    sentinel = object()

    async def emit(name: str, data: dict) -> None:
        if name == "token":
            state.text_parts.append(str(data.get("text", "")))
        elif name in ("tool_start", "tool_output", "tool_end"):
            _apply_tool_event(state.tools, name, data)
        elif name in ("done", "cancelled", "error"):
            state.status = name
        q.put_nowait((name, data))

    async def run_turn_wrapper():
        try:
            await qwen_assistant.run_turn(
                model=model,
                system=system,
                history=chat_history,
                repo=repo,
                emit=emit,
                cancel=run.cancel,
                effort=effort,
                mode=mode,
            )
        finally:
            q.put_nowait(sentinel)

    task = asyncio.create_task(run_turn_wrapper())
    try:
        yield {
            "event": "session",
            "data": json.dumps(
                {
                    "request_id": run.request_id,
                    "conversation_id": str(conv_id),
                    "model": model,
                    "effort": effort,
                    "mode": mode,
                }
            ),
        }
        while True:
            item = await q.get()
            if item is sentinel:
                break
            name, data = item
            yield {"event": name, "data": json.dumps(data, ensure_ascii=False)}
    finally:
        if not task.done():
            task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        _release(conv_id, run)
        await _persist_turn(
            conv_id=conv_id,
            model=model,
            mode=mode,
            assistant_seq=assistant_seq,
            state=state,
            is_first_exchange=is_first_exchange,
            user_text=user_text,
        )


async def _persist_turn(
    *,
    conv_id: uuid.UUID,
    model: str,
    mode: str,
    assistant_seq: int,
    state: _TurnState,
    is_first_exchange: bool,
    user_text: str,
) -> None:
    """Persist the (possibly partial) assistant turn on its own session."""
    text = "".join(state.text_parts)
    if not (text.strip() or state.tools):
        return
    # Strip internal bookkeeping (_index/_capped — used only to route
    # tool_output/tool_end to the right entry while the turn is live).
    public_tools = [{k: v for k, v in t.items() if not k.startswith("_")} for t in state.tools]
    async with get_session_factory()() as db:
        conv = await db.get(Conversation, conv_id)
        if conv is None:
            return
        db.add(
            Message(
                conversation_id=conv_id,
                role="assistant",
                content=text,
                model=model,
                mode=mode,
                tool_calls=public_tools if public_tools else None,  # JSONB takes the list
                sequence=assistant_seq,
            )
        )
        if conv.title == "New chat" and is_first_exchange and text.strip():
            title = " ".join(user_text.split())[:AUTO_TITLE_CHARS]
            if title:
                conv.title = title
        try:
            await db.commit()
        except Exception:
            await db.rollback()
            # Persistence failure must not mask the turn; log for the operator.
            _logger.exception("failed to persist assistant turn %s", conv_id)


@router.post("/cancel")
async def chat_cancel(
    body: CancelIn,
    _user: User = Depends(get_current_user),
):
    run = _by_request.get(body.request_id)
    if run is None or run.cancel.is_set():
        raise HTTPException(status_code=404, detail="Unknown or already-finished request")
    run.cancel.set()
    return {"ok": True, "status": "stopping"}
