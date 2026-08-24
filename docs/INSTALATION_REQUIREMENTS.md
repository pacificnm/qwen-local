# Installation Requirements (Current as of August 2026)

> Single-user deployment: one shared username/password account (no GitHub OAuth).
> GitHub access via a single static PAT (`GITHUB_PAT`, `repo` scope). See MASTER_SPEC.md §1.1 for locked decisions.

## Public Access

- **TLS + routing:** the pre-existing `cloudflared` container (Cloudflare Tunnel, remote-managed token). Add one public hostname in the Zero Trust dashboard: `chat.folding-os.com` → `http://frontend:3000`.
- **Networking:** the compose file attaches `frontend`/`backend` to the tunnel's existing external network (`cloudflared_default`) so service-name DNS works; frontend nginx proxies `/api/*` → `backend:8000`.
- **Host ports:** frontend publishes `3000` for direct LAN access (`http://192.168.88.10:3000`); backend `8000` and the rest stay inside the compose network.

## Host System (Bare Metal)

### Core Services
- **Docker** and **Docker Compose** (Latest stable release, v26+)
- **Ollama** (running natively on host, port 11434 — **pre-existing and out of scope to configure**)
- **PostgreSQL 17+** with **pgvector** extension enabled (running natively on host, port 5432)
- **Git** (v2.45+)
- **SearXNG** (runs as a Docker container in the compose stack, JSON API enabled — no external search keys)

### Ollama Models (expected already pulled — the app never pulls or tunes models)
- **Chat Models (dual, in-UI picker):** `qwen3.5:4b` (fast) and `qwen3.8:27b-longctx` (strong, default)
- **Embedding Model:** `nomic-embed-text` (768 dims — locks the pgvector column dimension)
- **Residency:** requests pass `keep_alive=60s`; the idle model unloads after 60 s to free V100 VRAM

### System Tools
- **NVIDIA Drivers** (v550+) for the single Tesla V100
- **Python 3.12+** (or 3.13, for backend development and sandbox)
- **Node.js 22 LTS** (or 20 LTS) and **npm/pnpm** (for frontend development)
- **Tree-sitter CLI** (for grammar compilation)

---

## Backend (Python)

### Core Framework
- `fastapi` (v0.115+)
- `uvicorn[standard]`
- `sse-starlette` (for Server-Sent Events streaming)

### AI & Orchestration
- `qwen-agent` (Alibaba's official agent framework)
- `openai` (Python SDK, for Ollama's OpenAI-compatible endpoint)

### Database
- `sqlalchemy` (v2.0+)
- `asyncpg` (Preferred async PostgreSQL driver) or `psycopg[binary,pool]`
- `pgvector` (Python bindings, e.g., `pgvector[sqlalchemy]`)
- `alembic` (for database migrations)

### GitHub Integration
- `PyGithub` or `httpx` (for async GitHub API calls)
- `cryptography` (for encrypting PATs at rest)

### Code Parsing & RAG
- `tree-sitter` (core parser)
- `tree-sitter-javascript`
- `tree-sitter-typescript`
- `tree-sitter-python`
- `tree-sitter-sql`

### Utilities
- `python-dotenv`
- `python-multipart` (for file uploads)
- `pydantic` (v2.x, for data validation)
- `httpx` (async HTTP client)

---

## Frontend (Node.js)

### Core Framework (Choose one based on team preference)
- **React:** `react` (v18/v19), `react-dom`, `typescript`
- **Vue:** `vue` (v3.5+), `typescript`
- **Svelte:** `svelte` (v5+), `typescript`

### Build Tools
- `vite` (v5/v6)
- `@vitejs/plugin-react` (or Vue/Svelte equivalent)
- `typescript` (v5.x)

### UI & Editor
- `monaco-editor` (v0.50+)
- `@monaco-editor/react` (or equivalent wrapper for Vue/Svelte)
- `react-markdown` + `remark-gfm` (or `@joplin/turndown-plugin-gfm` for Vue/Svelte)
- `shiki` (v1.x, for fast, accurate code block highlighting)
- `katex` (for LaTeX/Math rendering)

### State & API
- `zustand` (React) / `pinia` (Vue) / native stores (Svelte)
- `eventsource-parser` (for robust SSE stream handling)

### Utilities
- `date-fns` (v3/v4, for date formatting)
- `lucide-react` / `lucide-vue-next` / `lucide-svelte` (modern icon library)

---

## Docker Images

### Application Containers
- **Backend Base:** `python:3.12-slim` (or `3.13-slim`)
- **Frontend Build:** `node:22-alpine`
- **Frontend Serve:** `nginx:alpine` (or `caddy` for simpler config)
- **Search:** `searxng/searxng` (JSON API enabled via `settings.yml`)

### Ephemeral Sandbox
- **Code Execution:** `python:3.12-slim` (with common data science libs like `pandas`, `numpy`, `matplotlib` pre-installed)

---

## Development Tools (Recommended)

- **Backend:** `uv` or `poetry` (modern dependency management), `ruff` (linting/formatting), `pytest` + `pytest-asyncio`
- **Frontend:** `eslint`, `prettier`, `vitest`
- **Database:** `DBeaver` or `pgAdmin 4` (PostgreSQL GUI)
- **API Testing:** `Postman`, `Insomnia`, or `Bruno`
