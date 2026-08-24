"""Conversation CRUD + full-text search (Phase 3, docs/API.md)."""

import base64
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.settings import get_settings
from app.db.models import Conversation, Message, Repository, User

router = APIRouter(prefix="/api/conversations", tags=["conversations"])

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
DEFAULT_PAGE = 100
MAX_PAGE = 500
TITLE_MAX = 255


class CreateIn(BaseModel):
    repo_id: uuid.UUID | None = None
    model_default: str | None = None
    title: str | None = Field(default=None, max_length=TITLE_MAX)


class RenameIn(BaseModel):
    title: str = Field(min_length=1, max_length=TITLE_MAX)


class NoteIn(BaseModel):
    """Append a system/assistant note to a conversation (Phase 4 PR-link persistence)."""

    text: str = Field(min_length=1, max_length=4000)
    tool: dict | None = None


def _known_models() -> set[str]:
    s = get_settings()
    return {s.ollama_fast_model, s.ollama_strong_model}


def _conv_out(c: Conversation) -> dict:
    return {
        "id": str(c.id),
        "title": c.title,
        "repo_id": str(c.repo_id) if c.repo_id else None,
        "model_default": c.model_default,
        "created_at": c.created_at,
        "updated_at": c.updated_at,
    }


def _msg_out(m: Message) -> dict:
    return {
        "id": str(m.id),
        "role": m.role,
        "content": m.content,
        "model": m.model,
        "tool_calls": m.tool_calls,
        "sequence": m.sequence,
        "created_at": m.created_at,
    }


def _encode_cursor(updated_at: datetime | str, conv_id: str) -> str:
    if isinstance(updated_at, datetime):
        updated_at = updated_at.isoformat()
    raw = f"{updated_at}|{conv_id}".encode()
    return base64.urlsafe_b64encode(raw).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        ts, uid = raw.split("|", 1)
        return datetime.fromisoformat(ts), uuid.UUID(uid)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=422, detail="invalid cursor") from exc


async def _get_owned_conv(
    db: AsyncSession, conv_id: uuid.UUID, user: User
) -> Conversation:
    conv = await db.get(Conversation, conv_id)
    if conv is None or conv.user_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@router.post("", status_code=201)
async def create_conversation(
    body: CreateIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if body.model_default is not None and body.model_default not in _known_models():
        raise HTTPException(status_code=422, detail="unknown model")
    if body.repo_id is not None and await db.get(Repository, body.repo_id) is None:
        raise HTTPException(status_code=404, detail="Repository not linked")
    conv = Conversation(
        user_id=user.id,
        repo_id=body.repo_id,
        title=(body.title or "New chat").strip() or "New chat",
        model_default=body.model_default,
    )
    db.add(conv)
    await db.commit()
    return _conv_out(conv)


@router.get("", response_model=dict)
async def list_conversations(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    limit: Annotated[int, Query(ge=1)] = DEFAULT_LIMIT,
    cursor: str | None = None,
    q: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
):
    limit = min(limit, MAX_LIMIT)
    base = select(Conversation).where(Conversation.user_id == user.id)

    if q:
        tsq = func.plainto_tsquery("english", q)
        title_hit = func.to_tsvector("english", Conversation.title).op("@@")(tsq)
        msg_hit = (
            select(Message.id)
            .where(
                Message.conversation_id == Conversation.id,
                func.to_tsvector("english", Message.content).op("@@")(tsq),
            )
            .exists()
        )
        base = base.where(or_(title_hit, msg_hit))

    if cursor:
        c_ts, c_id = _decode_cursor(cursor)
        # Keyset on (updated_at, id) desc: strictly after the cursor row.
        base = base.where(
            or_(
                Conversation.updated_at < c_ts,
                and_(Conversation.updated_at == c_ts, Conversation.id < c_id),
            )
        )

    convs = (
        (
            await db.execute(
                base.order_by(Conversation.updated_at.desc(), Conversation.id.desc()).limit(
                    limit + 1
                )
            )
        )
        .scalars()
        .all()
    )
    has_more = len(convs) > limit
    items = [_conv_out(c) for c in convs[:limit]]
    return {
        "items": items,
        "next_cursor": _encode_cursor(items[-1]["updated_at"], uuid.UUID(items[-1]["id"]))
        if has_more
        else None,
        "has_more": has_more,
    }


@router.get("/{conv_id}")
async def get_conversation(
    conv_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    after_sequence: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1)] = DEFAULT_PAGE,
):
    limit = min(limit, MAX_PAGE)
    conv = await _get_owned_conv(db, conv_id, user)
    rows = (
        (
            await db.execute(
                select(Message)
                .where(Message.conversation_id == conv.id, Message.sequence > after_sequence)
                .order_by(Message.sequence)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return {"conversation": _conv_out(conv), "messages": [_msg_out(m) for m in rows]}


@router.patch("/{conv_id}")
async def rename_conversation(
    conv_id: uuid.UUID,
    body: RenameIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conv = await _get_owned_conv(db, conv_id, user)
    conv.title = body.title.strip()
    await db.commit()
    return _conv_out(conv)


@router.post("/{conv_id}/notes", status_code=201)
async def add_note(
    conv_id: uuid.UUID,
    body: NoteIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conv = await _get_owned_conv(db, conv_id, user)
    max_seq = (
        await db.execute(
            select(func.coalesce(func.max(Message.sequence), 0)).where(
                Message.conversation_id == conv.id
            )
        )
    ).scalar_one()
    msg = Message(
        conversation_id=conv.id,
        role="assistant",
        content=body.text.strip(),
        model=None,
        tool_calls=[body.tool] if body.tool else None,
        sequence=int(max_seq) + 1,
    )
    db.add(msg)
    await db.commit()
    await db.refresh(msg)
    return _msg_out(msg)


@router.delete("/{conv_id}", status_code=204)
async def delete_conversation(
    conv_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conv = await _get_owned_conv(db, conv_id, user)
    await db.delete(conv)  # FK ON DELETE CASCADE removes messages
    await db.commit()
    return Response(status_code=204)
