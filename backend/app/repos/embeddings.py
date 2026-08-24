"""Batched embeddings from the host Ollama (nomic-embed-text → 768 dims)."""

import asyncio

import httpx

from app.core.settings import get_settings

from .errors import SyncError

BATCH_SIZE = 32  # keep V100 VRAM + queue pressure low
_MAX_ATTEMPTS = 3


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed in order; raises SyncError after exhausting retries."""
    if not texts:
        return []
    settings = get_settings()
    url = f"{settings.effective_ollama_host.rstrip('/')}/v1/embeddings"
    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0)) as client:
        vectors: list[list[float]] = []
        for i in range(0, len(texts), BATCH_SIZE):
            batch = texts[i : i + BATCH_SIZE]
            data = await _post_with_retry(client, url, model=settings.ollama_embed_model, batch=batch)
            rows = sorted(data, key=lambda r: r.get("index", 0))
            vectors.extend(row["embedding"] for row in rows)
        return vectors


async def _post_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    model: str,
    batch: list[str],
) -> list[dict]:
    last: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            res = await client.post(url, json={"model": model, "input": batch})
            res.raise_for_status()
            return res.json()["data"]
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
            last = exc
            if attempt < _MAX_ATTEMPTS - 1:
                await asyncio.sleep(2.0 * (attempt + 1))
    raise SyncError(f"Ollama embedding failed: {last}") from last
