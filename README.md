# Local Qwen-Powered AI Codebase Assistant

**Project:** `chat.folding-os.com`

This is a custom, self-hosted AI coding assistant powered by local Qwen models running on our internal infrastructure (single Tesla V100, existing Ollama install). It provides a modern chat interface (chat.qwen.ai-inspired, dark 3-pane UI) tailored for software engineers, featuring deep GitHub integration, language-aware code retrieval (RAG), an in-browser code editor, and the ability to push AI-assisted code changes directly back to GitHub via Pull Requests.

## Key Capabilities

- **100% Local & Private:** All inference, embeddings, and data storage remain on our internal servers; single shared account (username + password).
- **Dual Qwen Models:** `qwen3.5:4b` (fast) and `qwen3.8:27b-longctx` (strong) served by the existing Ollama install, switchable per conversation.
- **GitHub Integration:** Securely link and sync private repositories via a single PAT; incremental Tree-sitter ingestion.
- **Language-Aware RAG:** Uses Tree-sitter to parse JavaScript, TypeScript, Python, and SQL into whole functions/classes before embedding, ensuring high-quality context retrieval (pgvector, top-8).
- **Bi-Directional Sync:** Review, edit in Monaco, and commit AI-generated code directly to GitHub branches and Pull Requests from the browser.
- **Agentic Execution:** Sandboxed code execution (no network, 120 s) and self-hosted web search (SearXNG) via Qwen-Agent, streamed over SSE with a full-stop button.

## Documentation

For detailed technical specifications, architecture diagrams, database schemas, and implementation guides, please refer to the documentation folder:

- **[Master Specification](docs/MASTER_SPEC.md)**: Complete system requirements and phased roadmap.
- **[Architecture](docs/ARCHITECTURE.md)**: Data flow, deployment topology, and component interactions.
- **[API Reference](docs/API.md)**: Endpoint details and streaming event formats.

## Running (Phase 1)

The root **`.env` is the single source of truth** for all values (secrets included). On this
host the shells export unrelated `DATABASE_URL`/`ADMIN_PASSWORD`/etc. for other projects, so
always start the backend through `backend/scripts/dev_server.py` (it scrubs the inherited
environment first) — never via a bare `uvicorn` in a live shell.

Host development (API only, on `:8000`):

```sh
cd backend
python scripts/init_db.py        # idempotent: creates schema, indexes, seeds admin
python scripts/dev_server.py
```

Docker deployment (the real stack — LAN: `http://192.168.88.10:3000`, remote: the Cloudflare
tunnel at `chat.folding-os.com`):

```sh
docker compose build
docker compose up -d
```

- `chat` (backend) — FastAPI, reads `.env`; reaches host Postgres/Ollama via
  `HOST_OVERRIDE=192.168.88.10` (set in `docker-compose.yml`; on-host runs use the
  `.env` `localhost` values).
- `chat-frontend` — nginx serving the React app on **3000** (the tunnel target);
  proxies `/api/*` → `backend:8000` with SSE unbuffering.
- `chat-searxng` — local web search for the assistant (JSON enabled), backend-only.

Public hostname (Cloudflare Zero Trust → Tunnels → Public Hostname):
`chat.folding-os.com` → `http://frontend:3000`.

Phase 1 API surface: `POST /api/auth/login`, `GET /api/auth/me`, `POST /api/auth/logout`,
`GET /api/models`, `GET /api/health` (db / ollama / searxng).

## Contributing
New contributors: set up a venv and run the backend test suite before opening a PR —
`cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/python -m pytest tests -q`.
See `backend/tests/` for the expected behavior of each module.
