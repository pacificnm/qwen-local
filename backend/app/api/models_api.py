"""Model picker endpoint — driven entirely by env config (MASTER_SPEC §1.1 #5)."""

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.settings import get_settings

router = APIRouter(prefix="/api", tags=["models"])


class ModelOut(BaseModel):
    id: str
    label: str
    is_default: bool = False


@router.get("/models", response_model=list[ModelOut])
async def list_models():
    settings = get_settings()
    return [
        ModelOut(id=settings.ollama_fast_model, label="Qwen 3.5 4B (fast)"),
        ModelOut(id=settings.ollama_strong_model, label="Qwen 3.8 27B (default)", is_default=True),
    ]
