"""RAG: top-k nearest code chunks for a conversation's repo (pgvector cosine)."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import CodeChunk
from .embeddings import embed_texts

RAG_TOP_K = 8  # spec §4.3: top-8 injection
MAX_CHUNK_CHARS = 12_000  # safety: never feed an outsized chunk into the prompt


async def retrieve_chunks(
    db: AsyncSession,
    *,
    repo_id: UUID,
    query: str,
    k: int = RAG_TOP_K,
) -> list[dict]:
    """Embed the query and return the k nearest chunks as dicts, best first.

    Raises SyncError (via embed_texts) on Ollama failure — the caller fails the
    request *before* opening the SSE stream.
    """
    vectors = await embed_texts([query])
    vec = vectors[0]

    distance = CodeChunk.embedding.cosine_distance(vec)
    stmt = (
        select(CodeChunk, distance)
        .where(CodeChunk.repo_id == repo_id)
        .order_by(distance)
        .limit(k)
    )
    rows = (await db.execute(stmt)).all()
    return [
        {
            "file_path": chunk.file_path,
            "start_line": chunk.start_line,
            "end_line": chunk.end_line,
            "language": chunk.language,
            "content": chunk.content[:MAX_CHUNK_CHARS],
            "score": 1.0 - float(score),
        }
        for chunk, score in rows
    ]
