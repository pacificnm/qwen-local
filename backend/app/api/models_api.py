"""Model picker endpoint — models are discovered live from the Ollama server's
`/api/tags` (whatever is actually installed), not hardcoded."""

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
    #: Global-fallback markers for the other two model roles (coding/strong
    #: is `is_default` above) — lets the frontend resolve a project's
    #: unset-role model without a separate endpoint (see SelectModel.tsx).
    is_default_fast_chat: bool = False
    is_default_compaction: bool = False
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


async def _list_installed(settings) -> list[dict]:
    """Raw `/api/tags` entries, or [] when Ollama is unreachable (the UI then
    shows/disables an empty picker rather than offering models that don't
    actually work)."""
    base = settings.effective_ollama_host.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            res = await client.get(f"{base}/api/tags")
            res.raise_for_status()
            return res.json().get("models") or []
    except Exception:
        return []


def _label(entry: dict) -> str:
    """Human label from an /api/tags entry: the tag plus parameter size /
    quantization when Ollama reports them, e.g. "qwen3.8:27b (27B, Q4_K_M)"."""
    name = entry.get("model") or entry.get("name") or "unknown"
    details = entry.get("details") or {}
    bits = [b for b in (details.get("parameter_size"), details.get("quantization_level")) if b]
    return f"{name} ({', '.join(bits)})" if bits else name


@router.get("/models", response_model=list[ModelOut])
async def list_models():
    settings = get_settings()
    entries = [e for e in await _list_installed(settings) if e.get("model") or e.get("name")]
    ids = [e.get("model") or e.get("name") for e in entries]
    windows = await asyncio.gather(*(_context_window(mid, settings) for mid in ids))
    return [
        ModelOut(
            id=mid,
            label=_label(entry),
            is_default=(mid == settings.ollama_strong_model),
            is_default_fast_chat=(mid == settings.ollama_fast_model),
            is_default_compaction=(mid == settings.ollama_compaction_model),
            context_window=window,
        )
        for entry, mid, window in zip(entries, ids, windows, strict=True)
    ]
