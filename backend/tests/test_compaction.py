"""Rolling-summary compaction tests (no DB, no network).

`maybe_compact` is exercised against a fake AsyncSession-like object whose
`execute`/`scalar`/`commit` are AsyncMocks — this repo has no DB-fixture
precedent (all existing tests are fake/mock-driven, see
test_qwen_agent_adapter.py), so the DB layer is stubbed the same way rather
than standing up a real engine. `_summarize` (the one real HTTP call) is
monkeypatched to keep these tests offline and fast.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.agents.compaction as compaction
import app.core.settings as cs
from app.db.models import Conversation, Message


def make_conv(*, context_summary=None, through=None) -> Conversation:
    conv = Conversation(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        title="t",
    )
    conv.context_summary = context_summary
    conv.context_summary_through_seq = through
    return conv


def make_message(seq: int, role: str = "user", content: str = "hi") -> Message:
    return Message(id=uuid.uuid4(), conversation_id=uuid.uuid4(), role=role, content=content, sequence=seq)


def fake_db(*, batch: list[Message] | None = None, project_settings=None) -> MagicMock:
    # `db` is a plain MagicMock (not AsyncMock) so attribute chains off its
    # awaited results (e.g. `.execute(...).scalars().all()`) stay ordinary
    # sync MagicMocks instead of accidentally propagating AsyncMock-ness —
    # only `execute`/`scalar`/`commit` themselves need to be awaitable.
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock())
    db.execute.return_value.scalars.return_value.all.return_value = batch or []
    db.scalar = AsyncMock(return_value=project_settings)
    db.commit = AsyncMock()
    return db


async def test_below_history_limit_is_noop():
    conv = make_conv(context_summary="old summary")
    db = fake_db()
    result = await compaction.maybe_compact(db, conv, next_seq=10, history_limit=40)
    assert result == "old summary"
    db.execute.assert_not_called()
    db.commit.assert_not_called()


async def test_nothing_new_beyond_prior_cutoff_short_circuits():
    # 50 messages total, limit 40 -> cutoff 10; already compacted through 10.
    conv = make_conv(context_summary="old summary", through=10)
    db = fake_db()
    result = await compaction.maybe_compact(db, conv, next_seq=50, history_limit=40)
    assert result == "old summary"
    db.execute.assert_not_called()
    db.commit.assert_not_called()


async def test_folds_new_batch_and_advances_cutoff(monkeypatch):
    settings = cs.Settings(ollama_compaction_model="global-compaction-model")
    monkeypatch.setattr(compaction, "get_settings", lambda: settings)

    async def fake_summarize(model, existing_summary, batch):
        assert model == "global-compaction-model"
        assert existing_summary is None
        assert [m.sequence for m in batch] == [1, 2]
        return "new summary"

    monkeypatch.setattr(compaction, "_summarize", fake_summarize)

    conv = make_conv()
    batch = [make_message(1), make_message(2, role="assistant")]
    db = fake_db(batch=batch)

    result = await compaction.maybe_compact(db, conv, next_seq=42, history_limit=40)

    assert result == "new summary"
    assert conv.context_summary == "new summary"
    assert conv.context_summary_through_seq == 2
    db.commit.assert_awaited_once()


async def test_project_setting_overrides_global_compaction_model(monkeypatch):
    settings = cs.Settings(ollama_compaction_model="global-compaction-model")
    monkeypatch.setattr(compaction, "get_settings", lambda: settings)

    seen_models = []

    async def fake_summarize(model, existing_summary, batch):
        seen_models.append(model)
        return "new summary"

    monkeypatch.setattr(compaction, "_summarize", fake_summarize)

    class FakeSettingsRow:
        compaction_model = "project-compaction-model"

    conv = make_conv()
    batch = [make_message(1)]
    db = fake_db(batch=batch, project_settings=FakeSettingsRow())

    await compaction.maybe_compact(db, conv, next_seq=41, history_limit=40)

    assert seen_models == ["project-compaction-model"]


async def test_summarize_failure_leaves_state_unchanged(monkeypatch):
    settings = cs.Settings()
    monkeypatch.setattr(compaction, "get_settings", lambda: settings)

    async def boom(model, existing_summary, batch):
        raise RuntimeError("ollama unreachable")

    monkeypatch.setattr(compaction, "_summarize", boom)

    conv = make_conv(context_summary="old summary", through=0)
    batch = [make_message(1)]
    db = fake_db(batch=batch)

    result = await compaction.maybe_compact(db, conv, next_seq=41, history_limit=40)

    assert result == "old summary"
    assert conv.context_summary == "old summary"
    assert conv.context_summary_through_seq == 0
    db.commit.assert_not_called()


async def test_empty_batch_advances_cutoff_without_summarizing(monkeypatch):
    settings = cs.Settings()
    monkeypatch.setattr(compaction, "get_settings", lambda: settings)

    called = False

    async def fake_summarize(model, existing_summary, batch):
        nonlocal called
        called = True
        return "should not be reached"

    monkeypatch.setattr(compaction, "_summarize", fake_summarize)

    conv = make_conv()
    db = fake_db(batch=[])  # e.g. only system-role rows in range, filtered out upstream

    result = await compaction.maybe_compact(db, conv, next_seq=41, history_limit=40)

    assert called is False
    assert result is None
    assert conv.context_summary_through_seq == 1
    db.commit.assert_awaited_once()
