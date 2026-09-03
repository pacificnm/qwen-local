"""Environment-driven application settings (pydantic-settings).

Precedence (pydantic-settings): init kwargs > process env > `.env` file > defaults.

- The repo-root `.env` file is the single source of truth for this app. The host's
  shells export their own `DATABASE_URL` / `ADMIN_PASSWORD` etc. for other projects;
  `scripts/dev_server.py` scrubs them from the environment before import so the
  file's values resolve.
- Inside docker-compose, `HOST_OVERRIDE` (container process env) re-points the
  Postgres/Ollama URLs at the host's LAN IP (192.168.88.10). `.env` itself keeps the
  `localhost` values for on-host runs. Credentials are never duplicated outside
  `.env`.
"""

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from pydantic_settings import BaseSettings, SettingsConfigDict

# Absolute so settings resolve the repo .env regardless of the CWD.
_ENV_FILE = str(Path(__file__).resolve().parents[3] / ".env")


def _swap_host(url: str, host: str) -> str:
    """Point `url` at `host`, preserving scheme, userinfo, port, and path."""
    parts = urlsplit(url)
    if not parts.hostname:
        return url
    userinfo = ""
    if parts.username:
        userinfo = parts.username
        if parts.password:
            userinfo += f":{parts.password}"
        userinfo += "@"
    port = f":{parts.port}" if parts.port else ""
    return urlunsplit((parts.scheme, f"{userinfo}{host}{port}", parts.path, parts.query, parts.fragment))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    # --- Application ---
    app_env: str = "development"
    # Signs/validates the session cookie; must be a long random string in production.
    secret_key: str = "dev-secret-key-change-me"
    # Single shared account seeded by scripts/init_db.py (argon2id).
    admin_username: str = "admin"
    admin_password: str = "change-me-on-first-boot"
    session_max_age_days: int = 30
    login_rate_limit_per_min: int = 10
    cookie_name: str = "session"

    # --- Database (host PostgreSQL + pgvector; on-host runs use localhost) ---
    database_url: str = "postgresql+asyncpg://qwen_chat:qwen_chat@localhost:5432/qwen_chat_db"

    # --- Ollama (pre-existing; the app only consumes this endpoint) ---
    ollama_host: str = "http://localhost:11434"
    ollama_fast_model: str = "qwen3.5:4b-compress"
    ollama_strong_model: str = "qwen3.8:27b-longctx"
    ollama_compaction_model: str = "qwen3.5:4b-compress"
    ollama_embed_model: str = "nomic-embed-text"
    ollama_keep_alive: str = "60s"

    # --- GitHub / search / sandbox ---
    github_pat: str = ""
    # Identity that Phase 4 commits are authored under. The backend runs as an
    # unprivileged container user with no ambient git config, so it must set
    # the committer explicitly (otherwise `git commit` fails with
    # "Author identity unknown"). Overridable via GIT_AUTHOR_NAME /
    # GIT_AUTHOR_EMAIL (.env or process env); the defaults attribute the change
    # to the assistant.
    git_author_name: str = "Qwen Assist"
    git_author_email: str = "qwen-assist@users.noreply.github.com"
    # Workspace for git clones (relative to the repo root; docker-compose
    # bind-mounts ./workspace over /srv/app/workspace for persistence).
    workspace_dir: str = "workspace"
    searxng_url: str = "http://searxng:8080"
    sandbox_image_name: str = "qwen-code-sandbox:latest"
    sandbox_timeout_seconds: int = 120
    sandbox_memory: str = "1g"
    sandbox_cpus: str = "2"
    sandbox_network: str = "none"
    # Hardened `docker run` flags (spec §4.5 defaults; overridable per host).
    sandbox_user: str = "65534:65534"
    sandbox_tmpfs: str = "/tmp:rw,size=64m"
    sandbox_pids_limit: int = 256
    # `docker build` fallback context for ensure_image(); repo-relative on host
    # dev runs (in compose the image is built by the `sandbox-image` service).
    sandbox_build_context: str = "sandbox"

    # --- Terminal sandbox (interactive, persistent; spec: VSCode-style dock) ---
    # Separate profile from the one-shot Code Interpreter sandbox: the terminal
    # container stays alive, is network-enabled, and gets a writable workspace
    # so it can be a real shell. It is containerized and never touches the host.
    # Reuses the sandbox runtime image (which gains bash + common tools).
    terminal_idle_seconds: int = 1800  # reap a sandbox with no live sessions after this long
    terminal_memory: str = "1g"
    terminal_cpus: str = "2"
    # "bridge" gives the terminal internet access (the Code Interpreter keeps
    # `sandbox_network=none`); "none" reverts it to offline.
    terminal_network: str = "bridge"
    # Runs as the same UID that owns the host clone (appuser=1000) so the
    # mounted /repo is read+write from inside the container.
    terminal_user: str = "1000:1000"
    # Host-side path of the workspace dir, for the `/repo` bind mount. The
    # backend runs *in* a container (workspace at /srv/app/workspace) while the
    # docker daemon (on the host) resolves bind sources against HOST paths, so
    # the container path is unusable there. Empty (default) falls back to the
    # `workspace_dir` path — correct for host-local dev runs where the backend
    # and daemon share a filesystem. docker-compose sets it to the host path.
    workspace_host_dir: str = ""

    # --- Container -> host reachability ---
    # Empty on the host itself (the localhost values above are correct there).
    # docker-compose sets this to the host's LAN IP (192.168.88.10).
    host_override: str = ""

    @property
    def effective_database_url(self) -> str:
        """Database URL as this process should actually connect (container-aware)."""
        if not self.host_override:
            return self.database_url
        return _swap_host(self.database_url, self.host_override)

    @property
    def effective_ollama_host(self) -> str:
        """Ollama base URL as this process should actually connect (container-aware)."""
        if not self.host_override:
            return self.ollama_host
        return _swap_host(self.ollama_host, self.host_override)


@lru_cache
def get_settings() -> Settings:
    return Settings()
