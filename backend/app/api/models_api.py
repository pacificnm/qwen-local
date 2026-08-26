"""Model picker endpoint — driven entirely by env config (MASTER_SPEC §1.1 #5)."""

import asyncio
import re

import httpx
from fastapi import APIRouter
from pydantic import BaseModel

from app.core.settings import get_settings

router = APIRouter(prefix="/api", tags=["models"])

#: name → context window, resolved from Ollama and cached for the process
#: lifetime (a model's runtime context is fixed by the deployment).
_CONTEXT_CACHE: dict[str, int] = {}


class ModelOut(BaseModel):
    id: str
    label: str
    is_default: bool = False
    context_window: int | None = None


def _parse_num_ctx(parameters: str) -> int | None:
    match = re.search(r"num_ctx\s+(\d+)", parameters or "")
    return int(match.group(1)) if match else None


async def _context_window(model_id: str, settings) -> int | None:
    """The model's runtime context window (Ollama `num_ctx`), or None when
    Ollama is unreachable or the info is missing (the UI then hides the
    context-used figure instead of guessing)."""
    if model_id in _CONTEXT_CACHE:
        return _CONTEXT_CACHE[model_id]
    base = settings.effective_ollama_host.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            res = await client.post(f"{base}/api/show", json={"name": model_id})
            res.raise_for_status()
            body = res.json()
    except Exception:
        return None
    window = _parse_num_ctx(body.get("parameters") or "")
    if window is not None and window > 0:
        _CONTEXT_CACHE[model_id] = window
        return window
    return None


@router.get("/models", response_model=list[ModelOut])
async def list_models():
    settings = get_settings()
    fast_window, strong_window = await asyncio.gather(
        _context_window(settings.ollama_fast_model, settings),
        _context_window(settings.ollama_strong_model, settings),
    )
    return [
        ModelOut(id=settings.ollama_fast_model, label="Qwen 3.5 4B (fast)", context_window=fast_window),
        ModelOut(
            id=settings.ollama_strong_model,
            label="Qwen 3.8 27B (default)",
            is_default=True,
            context_window=strong_window,
        ),
    ]
