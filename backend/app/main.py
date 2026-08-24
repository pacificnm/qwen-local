"""FastAPI application entry point."""

import logging

from fastapi import FastAPI

from app.api import auth, chat, conversations, edits, health, models_api, repos
from app.db.session import dispose_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("qwen-chat")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Qwen Chat Backend",
        description="Self-hosted AI codebase assistant (Phase 4: + editor, commit, PR)",
        version="0.4.0",
    )

    app.include_router(auth.router)
    app.include_router(models_api.router)
    app.include_router(repos.router)
    app.include_router(edits.router)
    app.include_router(conversations.router)
    app.include_router(chat.router)
    app.include_router(health.router)

    @app.on_event("shutdown")
    async def shutdown() -> None:
        await dispose_engine()

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    # Phase 1 runs a single worker: the login rate limiter is in-process.
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, workers=1)
