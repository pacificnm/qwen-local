"""Run the API on this host with the repo .env as the single source of truth.

This server's shells export their own DATABASE_URL / ADMIN_PASSWORD etc. for
other services; inheriting them silently breaks this app. We drop this app's
own variables from the inherited environment before FastAPI is imported, so
every value resolves from <repo>/.env (or the built-in defaults when absent).
"""

import os
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_DIR))

# Every settings field this app reads (uppercased) — see app/core/settings.py.
_APP_ENV_VARS = {
    "APP_ENV",
    "SECRET_KEY",
    "ADMIN_USERNAME",
    "ADMIN_PASSWORD",
    "SESSION_MAX_AGE_DAYS",
    "LOGIN_RATE_LIMIT_PER_MIN",
    "COOKIE_NAME",
    "DATABASE_URL",
    "OLLAMA_HOST",
    "OLLAMA_FAST_MODEL",
    "OLLAMA_STRONG_MODEL",
    "OLLAMA_EMBED_MODEL",
    "OLLAMA_KEEP_ALIVE",
    "GITHUB_PAT",
    "SEARXNG_URL",
    "SANDBOX_IMAGE_NAME",
    "SANDBOX_TIMEOUT_SECONDS",
    "SANDBOX_MEMORY",
    "SANDBOX_CPUS",
    "SANDBOX_NETWORK",
    "HOST_OVERRIDE",
}

for _var in _APP_ENV_VARS:
    os.environ.pop(_var, None)

if __name__ == "__main__":
    import uvicorn

    # Phase 1 runs a single worker: the login rate limiter is in-process.
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, workers=1)
