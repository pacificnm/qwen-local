"""Rolling-summary compaction: folds messages about to fall out of the
verbatim history window (see HISTORY_LIMIT in app/api/chat.py) into a running
per-conversation summary via one cheap, tool-free LLM call, instead of just
dropping them. Failure is non-fatal — it just means this turn's
newly-dropped messages get folded in on a later turn instead of blocking the
user's actual chat turn.
"""

import logging

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.db.models import Conversation, Message, ProjectSettings

logger = logging.getLogger("app.compaction")

_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
_MAX_ATTEMPTS = 2

COMPACTION_SYSTEM = (
    "You maintain a running summary of an ongoing coding-assistant conversation "
    "so older turns can be dropped from context without losing important state. "
    "Given the CURRENT SUMMARY (may be empty) and a batch of NEW MESSAGES that are "
    "about to leave the visible context window, produce an UPDATED SUMMARY that "
    "folds the new messages in.\n"
    "Rules:\n"
    "- Preserve concrete facts: file paths, function/class names, decisions made, "
    "commands run, errors encountered and their resolutions, and open TODOs/questions.\n"
    "- Drop pleasantries and anything superseded by a later message in the batch.\n"
    "- Write concise prose or short bullet points, third person, past tense.\n"
    "- Target under 600 words; trim the least-important older material first if you "
    "would exceed it.\n"
    "- Output ONLY the updated summary text, no preamble or headers."
)


async def _resolve_model(db: AsyncSession, conv: Conversation) -> str:
    settings = get_settings()
    row = await db.scalar(
        select(ProjectSettings).where(ProjectSettings.project_id == conv.project_id)
    )
    return (row.compaction_model if row else None) or settings.ollama_compaction_model


async def _summarize(model: str, existing_summary: str | None, batch: list[Message]) -> str:
    settings = get_settings()
    base = settings.effective_ollama_host.rstrip("/")
    transcript = "\n".join(f"[{m.role}] {m.content}" for m in batch)
    user_content = f"CURRENT SUMMARY:\n{existing_summary or '(none yet)'}\n\nNEW MESSAGES:\n{transcript}"
    payload = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": COMPACTION_SYSTEM},
            {"role": "user", "content": user_content},
        ],
    }
    last: Exception | None = None
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for attempt in range(_MAX_ATTEMPTS):
            try:
                res = await client.post(f"{base}/api/chat", json=payload)
                res.raise_for_status()
                return res.json()["message"]["content"].strip()
            except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
                last = exc
    raise RuntimeError(f"compaction summarize failed: {last}") from last


async def maybe_compact(
    db: AsyncSession, conv: Conversation, *, next_seq: int, history_limit: int
) -> str | None:
    """Return the summary text to prepend to this turn's system prompt
    (existing, freshly updated, or None), folding in any messages that are
    about to fall out of the verbatim `history_limit` window. Non-fatal on
    any error: logs and returns the unchanged existing summary, leaving
    `context_summary_through_seq` untouched so the same batch is retried
    next turn."""
    total = next_seq  # sequence numbers are 1-based/contiguous; next_seq == total messages now
    if total <= history_limit:
        return conv.context_summary
    cutoff = total - history_limit
    through = conv.context_summary_through_seq or 0
    if cutoff <= through:
        return conv.context_summary

    batch = (
        (
            await db.execute(
                select(Message)
                .where(
                    Message.conversation_id == conv.id,
                    Message.sequence > through,
                    Message.sequence <= cutoff,
                    Message.role.in_(("user", "assistant")),
                )
                .order_by(Message.sequence)
            )
        )
        .scalars()
        .all()
    )
    if not batch:
        conv.context_summary_through_seq = cutoff  # nothing summarizable in range; skip it forever
        await db.commit()
        return conv.context_summary

    model = await _resolve_model(db, conv)
    try:
        new_summary = await _summarize(model, conv.context_summary, batch)
    except Exception:
        logger.warning(
            "compaction failed for conversation %s (model=%s); will retry next turn",
            conv.id, model, exc_info=True,
        )
        return conv.context_summary

    conv.context_summary = new_summary
    conv.context_summary_through_seq = cutoff
    await db.commit()
    return new_summary
