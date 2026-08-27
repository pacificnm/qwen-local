"""FastAPI application entry point."""

import asyncio
import logging

from fastapi import FastAPI

from app.api import auth, chat, conversations, edits, health, models_api, projects, repos, terminals
from app.db.session import dispose_engine
from app.sandbox.terminal import get_terminal_manager

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
    app.include_router(projects.router)
    app.include_router(repos.router)
    app.include_router(edits.router)
    app.include_router(conversations.router)
    app.include_router(chat.router)
    app.include_router(terminals.router)
    app.include_router(health.router)

    @app.on_event("startup")
    async def _start_terminal_reaper() -> None:
        app.state.terminal_reaper = asyncio.create_task(_terminal_reaper_loop())

    @app.on_event("shutdown")
    async def shutdown() -> None:
        task = getattr(app.state, "terminal_reaper", None)
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - cleanup
                pass
        await get_terminal_manager().aclose()
        await dispose_engine()

    return app


async def _terminal_reaper_loop(interval: float = 60.0) -> None:
    """Periodically drop idle terminal containers (bounded resource use)."""
    manager = get_terminal_manager()
    while True:
        try:
            await asyncio.sleep(interval)
            await manager.reap_idle()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a bad tick must not kill the loop
            logger.exception("terminal reaper tick failed")


app = create_app()


if __name__ == "__main__":
    import uvicorn

    # Phase 1 runs a single worker: the login rate limiter is in-process.
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, workers=1)
