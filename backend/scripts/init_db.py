"""One-time schema bootstrap + single-account seeding.

Run from a Python environment with the backend dependencies installed:

    # host: DATABASE_URL must point at the local socket/127.0.0.1
    DATABASE_URL='postgresql+asyncpg://qwen_chat:***@127.0.0.1:5432/qwen_chat_db' \
        python scripts/init_db.py

Idempotent: safe to re-run. Seeds/refreshes the ADMIN_USERNAME account
(argon2id) and creates the vector extension, tables, HNSW + FTS indexes.
NOTE: `CREATE EXTENSION vector` requires a superuser once per database —
run it via `scripts/provision_db.sh` (or ask your DBA) before this script.
"""

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND_DIR.parent

# Allow running as `python scripts/init_db.py` from the backend/ directory.
sys.path.insert(0, str(_BACKEND_DIR))

from dotenv import dotenv_values  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.core.security import hash_password  # noqa: E402
from app.core.settings import Settings  # noqa: E402
from app.db.base import Base  # noqa: E402


def load_settings() -> Settings:
    """The repo .env file is the single source of truth for this app.

    This host runs several services whose shells export their own variables;
    inheriting them breaks the app, so .env values are passed as init
    arguments (highest precedence in pydantic-settings) and win over env.
    """
    values = dotenv_values(_REPO_ROOT / ".env")
    fields = set(Settings.model_fields)
    return Settings(**{k.lower(): str(v) for k, v in values.items() if k.lower() in fields})

HNSW_INDEX = """
CREATE INDEX IF NOT EXISTS idx_code_chunks_embedding_hnsw
    ON code_chunks USING hnsw (embedding vector_cosine_ops)
"""

FTS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_conversations_title_fts"
    " ON conversations USING gin (to_tsvector('english', title))",
    "CREATE INDEX IF NOT EXISTS idx_messages_content_fts"
    " ON messages USING gin (to_tsvector('english', content))",
]


async def main() -> None:
    settings = load_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)

    # 1) pgvector extension — isolated transaction: if this fails (needs
    # superuser), the remaining DDL must not be aborted along with it.
    ext_ok = False
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        ext_ok = True
    except Exception as exc:  # noqa: BLE001
        print(f"⚠ CREATE EXTENSION vector failed: {exc}")
    try:
        async with engine.connect() as conn:
            row = await conn.execute(text("SELECT extversion FROM pg_extension WHERE extname = 'vector'"))
            ext_ok = row.scalar_one() is not None or ext_ok
    except Exception:  # noqa: BLE001
        pass
    print("✓ vector extension present" if ext_ok else "⚠ vector extension MISSING — installing as superuser")

    # 2) Tables (idempotent via checkfirst).
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, checkfirst=True)
    print("✓ tables created/verified")

    # 3) HNSW index on embeddings.
    async with engine.begin() as conn:
        await conn.execute(text(HNSW_INDEX))
    print("✓ hnsw index on code_chunks.embedding")

    # 4) Full-text indexes.
    for ddl in FTS_INDEXES:
        async with engine.begin() as conn:
            await conn.execute(text(ddl))
    print("✓ full-text indexes (conversations.title, messages.content)")

    # 5) Seed the single shared account (upsert by username).
    username = settings.admin_username.lower()
    password_hash = hash_password(settings.admin_password)
    async with engine.begin() as conn:
        row = await conn.execute(text("SELECT id FROM users WHERE username = :u"), {"u": username})
        existing = row.scalar_one_or_none()
        if existing is None:
            await conn.execute(
                text("INSERT INTO users (id, username, password_hash, active, created_at) "
                     "VALUES (gen_random_uuid(), :u, :h, true, now())"),
                {"u": username, "h": password_hash},
            )
            print(f"✓ seeded account '{username}' (argon2id)")
        else:
            await conn.execute(
                text("UPDATE users SET password_hash = :h WHERE id = :i"),
                {"h": password_hash, "i": existing},
            )
            print(f"✓ refreshed password hash for existing account '{username}'")

    # 6) Report.
    async with engine.connect() as conn:
        tables = sorted(
            r[0]
            for r in await conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")
            )
        )
        counts = {
            t: (await conn.execute(text(f"SELECT count(*) FROM {t}"))).scalar_one()
            for t in ("users", "sessions", "repositories", "code_chunks", "conversations", "messages")
            if t in tables
        }
    print(f"✓ done. tables={tables}")
    print(f"  counts={counts}  at {datetime.now(UTC).isoformat()}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
