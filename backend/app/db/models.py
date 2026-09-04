"""SQLAlchemy 2.0 ORM models — full schema for all phases.

Schema per docs/MASTER_SPEC.md §3. `init_db.py` creates the extension, tables,
HNSW + full-text indexes, and seeds the single account.
"""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # Legacy password login is retired (see app/api/auth.py) — column and
    # data stay in place, unused, rather than deleted.
    password_hash: Mapped[str | None] = mapped_column(String(512), default=None)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Set once, by a one-time data migration, linking this row to the
    # matching identity.folding-os.com account. Nullable because this
    # single-account app predates SSO; there is deliberately no
    # auto-create-on-first-login (see app/api/auth.py).
    identity_sub: Mapped[str | None] = mapped_column(String(36), unique=True, index=True, default=None)
    # Cached from the id_token on each login, purely for display (the header
    # profile menu) — never used for authorization.
    name: Mapped[str | None] = mapped_column(String(255), default=None)
    email: Mapped[str | None] = mapped_column(String(255), default=None)
    picture_url: Mapped[str | None] = mapped_column(String(1024), default=None)

    sessions: Mapped[list["Session"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )


class Session(Base):
    """Server-side killable session (logout revokes the row)."""

    __tablename__ = "sessions"

    token: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="sessions")


class Repository(Base):
    __tablename__ = "repositories"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    github_full_name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    default_branch: Mapped[str] = mapped_column(String(255))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_commit_sha: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Owning project (exclusive: a repo serves at most one project). Unassigned
    # when the project is deleted (SET NULL) or the user detaches the repo.
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), index=True
    )

    files: Mapped[list["CodeFile"]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )
    chunks: Mapped[list["CodeChunk"]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )
    project: Mapped["Project | None"] = relationship(back_populates="repository")


class CodeFile(Base):
    __tablename__ = "code_files"
    __table_args__ = (UniqueConstraint("repo_id", "file_path", name="uq_code_files_repo_path"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    repo_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    file_path: Mapped[str] = mapped_column(String(1024))
    git_blob_sha: Mapped[str] = mapped_column(String(64))
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    repository: Mapped[Repository] = relationship(back_populates="files")


class CodeChunk(Base):
    __tablename__ = "code_chunks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    repo_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    file_path: Mapped[str] = mapped_column(String(1024))
    language: Mapped[str] = mapped_column(String(32))
    start_line: Mapped[int] = mapped_column(Integer)
    end_line: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    token_estimate: Mapped[int] = mapped_column(Integer)
    # nomic-embed-text = 768 dims (locks this column width).
    embedding: Mapped[list[float] | None] = mapped_column(Vector(768))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    repository: Mapped[Repository] = relationship(back_populates="chunks")


class Project(Base):
    """A project groups a repository (RAG + agent tools), its conversations,
    and — implicitly — its commits. One repo per project (enforced by the
    unique index on repositories.project_id, see init_db.py)."""

    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    repository: Mapped["Repository | None"] = relationship(
        back_populates="project", uselist=False
    )
    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", lazy="selectin",
        order_by="Conversation.updated_at.desc()",
    )
    # One-to-one per-project configuration (ports, RAG knobs, MCP servers) —
    # see ProjectSettings. Deleting a project cascades to its settings row.
    settings: Mapped["ProjectSettings | None"] = relationship(
        back_populates="project", uselist=False
    )


class ProjectSettings(Base):
    """Per-project configuration: sandbox ports, RAG retrieval knobs, the
    project's MCP server list, and per-project model-role overrides. One row
    per project (unique ``project_id``), so users tune each project
    independently through the web UI instead of editing ``.env``. All numeric
    fields default to the spec values; ``mcp_servers`` is a nullable JSON
    array of server configs. ``coding_model``/``fast_chat_model``/
    ``compaction_model`` are optional per-project overrides of the
    corresponding global ``ollama_*_model`` env default (embedding model is
    intentionally not project-configurable — see docs/ARCHITECTURE.md on the
    fixed pgvector dimension).
    """

    __tablename__ = "project_settings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), unique=True, index=True
    )
    sandbox_port: Mapped[int] = mapped_column(Integer, default=9000)
    sandbox_container_port: Mapped[int] = mapped_column(Integer, default=80)
    rag_top_k: Mapped[int] = mapped_column(Integer, default=8)
    rag_max_chars: Mapped[int] = mapped_column(Integer, default=12000)
    mcp_servers: Mapped[list | None] = mapped_column(JSONB)
    coding_model: Mapped[str | None] = mapped_column(String(64))
    fast_chat_model: Mapped[str | None] = mapped_column(String(64))
    compaction_model: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    project: Mapped["Project"] = relationship(back_populates="settings")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # Every conversation belongs to a project (general chat = project without
    # a repo). Deleting a project removes its conversations (DB cascade).
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(255), default="New chat")
    model_default: Mapped[str | None] = mapped_column(String(64))
    # Rolling compaction summary: folds messages that have fallen out of the
    # verbatim history window (see HISTORY_LIMIT in app/api/chat.py) so they
    # aren't simply dropped. `context_summary_through_seq` is the highest
    # Message.sequence already folded in; None/0 = nothing compacted yet.
    context_summary: Mapped[str | None] = mapped_column(Text)
    context_summary_through_seq: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    project: Mapped[Project] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", lazy="selectin",
        order_by="Message.sequence",
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))  # system | user | assistant
    content: Mapped[str] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(64))
    # Chat mode this turn ran under: ask | plan | code (null for pre-migration rows).
    mode: Mapped[str | None] = mapped_column(String(16))
    # Agent tool invocation log for this turn (names, args, status).
    tool_calls: Mapped[list | None] = mapped_column(JSONB)
    sequence: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
