"""Health endpoint: app + dependencies (Ollama, Postgres, SearXNG)."""

import httpx
from fastapi import APIRouter
from sqlalchemy import text

from app.core.settings import get_settings
from app.db.session import get_engine

router = APIRouter(prefix="/api", tags=["health"])


async def _get(url: str, timeout: float = 3.0) -> bool:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
            return resp.status_code < 500
    except httpx.HTTPError:
        return False


@router.get("/health")
async def health():
    settings = get_settings()

    db_ok = False
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    ollama_ok = await _get(f"{settings.effective_ollama_host.rstrip('/')}/api/tags")
    searxng_ok = await _get(settings.searxng_url.rstrip("/"))

    checks = {
        "db": "ok" if db_ok else "down",
        "ollama": "ok" if ollama_ok else "down",
        "searxng": "ok" if searxng_ok else "down",
    }
    return {"status": "ok" if db_ok else "degraded", "checks": checks}
