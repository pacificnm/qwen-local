# MASTER SPECIFICATION DOCUMENT: Local Qwen-Powered AI Codebase Assistant

**Version:** 2.0 (finalized 2026-08-23)
**Status:** Specification complete — ready for implementation (planning only, no code yet)

## 1. Executive Summary
This project builds a self-hosted AI codebase assistant with a chat.qwen.ai-inspired interface. It is powered by local Qwen models served by an **existing Ollama installation** (no Ollama setup or tuning is in scope) on a single Tesla V100. The system provides deep GitHub integration (private repos via a single PAT), language-aware code retrieval (RAG via Tree-sitter + pgvector), a 3-pane chat UI with an in-browser Monaco editor, sandboxed code execution, local web search (SearXNG), and the ability to push AI-assisted code changes to GitHub as branches and Pull Requests.

**Product scope (locked):** the codebase assistant described in this spec. chat.qwen.ai is the **UI/UX reference only** — no image generation, no personal-memory engine, no multi-user tenancy.

### 1.1 Decision Log (locked 2026-08-23)
| # | Decision | Value |
|---|----------|-------|
| 1 | Product scope | Codebase assistant per spec; chat.qwen.ai = UI reference only; no new feature categories |
| 2 | Users | Single shared account, username + password (no GitHub OAuth, no per-user PATs) |
| 3 | GitHub access | One static PAT (`repo` scope) via `GITHUB_PAT` env var |
| 4 | Web search | Self-hosted **SearXNG** container (JSON API), no hosted search keys |
| 5 | Chat models | Dual (actual Ollama tags verified 2026-08-23): `qwen3.5:4b` (fast) + `qwen3.8:27b-longctx` (strong); in-UI model picker; names from env vars. Default: `qwen3.8:27b-longctx` |
| 6 | Model residency | Ollama `keep_alive=60s` (idle model unloads; VRAM freed for active model's KV cache) |
| 7 | RAG retrieval | Top-K = 8 chunks per question |
| 8 | AST chunk size | 500–1000 tokens target (ceiling at function/class boundaries), 10–15% overlap |
| 9 | Ingestion skips | `.git`, `node_modules`, vendor/dist/build/out/target, `__pycache__`, lockfiles, LFS pointers, binaries, files > 1 MB; parse JS/TS/Python/SQL only |
| 10 | Sandbox | Ephemeral Docker, **no network**, **120 s** hard timeout, caps on CPU/RAM, destroyed after run |
| 11 | Stop button | **Full stop**: aborts in-flight generation AND cancels the active tool call (kills running sandbox); partial output kept |
| 12 | Conversation mgmt | Rename + delete + search (sidebar search over titles and message bodies) |
| 13 | Embeddings | `nomic-embed-text`, **768 dims** (locks the pgvector column dimension) |
| 14 | UI layout | 3-pane: conversations+repo sidebar / chat pane / editor-diff pane; **dark theme default**, light optional |
| 15 | Frontend stack | React 19 + TypeScript + Vite + Zustand (per PROJECT_STRUCTURE.md) |
| 16 | Ollama | Pre-existing and out of scope; app only consumes its OpenAI-compatible endpoint |
| 17 | GPU | Single Tesla V100 (Ollama already serves both Qwen models + embedding model) |
| 18 | Public access / TLS | **LAN:** frontend publishes host port `3000` → `http://192.168.88.10:3000` for direct local access (host 3000 freed when the old open-webui/cptr/open-terminal stack was decommissioned; 8000/8080 are taken by other host apps). **Remote:** existing Cloudflare Tunnel (remote-managed token already on host), dashboard hostname `chat.folding-os.com` → `http://frontend:3000`. Frontend nginx proxies `/api/*` → `backend:8000` (same-origin, no CORS). TLS is Cloudflare's; no cert management. |

## 2. Infrastructure & Deployment Topology

### 2.1 Host Machine
*   **GPU:** single Tesla V100. All model inference and embedding workloads run on the existing Ollama install — **no Ollama installation, model pulls, or tuning are part of this project**.
*   **Ollama (pre-existing):** listens on `localhost:11434`, OpenAI-compatible API. Expected models already present: `qwen3:14b`, `qwen3:27b`, `nomic-embed-text`.
*   **PostgreSQL 17+ with pgvector:** runs natively on the host, port `5432`.
*   **Docker Engine:** manages the app containers, the SearXNG container, and ephemeral sandbox containers.

### 2.2 Docker Containers (docker-compose)
| Container | Image | Port | Notes |
|-----------|-------|------|-------|
| `frontend` | nginx:alpine serving the Vite build | `3000` | Static SPA |
| `backend` | python:3.12-slim (FastAPI + uvicorn) | `8000` | All API + orchestration; granted Docker socket access for sandbox management |
| `searxng` | searxng/searxng | internal (expose `8080` to host if desired) | JSON API enabled (`formats: [html, json]`), no external engines required config |
| `sandbox-*` (ephemeral) | sandbox/Dockerfile (python:3.12-slim + pandas/numpy/matplotlib) | none | Created/killed per code-interpreter call |

*   **Networking:** containers reach host Ollama/Postgres via the host's LAN IP (`HOST_OVERRIDE=192.168.88.10`, set in docker-compose — `settings.effective_*` re-point the URLs; `pg_hba` allows `192.168.88.0/24`). Backend reaches SearXNG via the compose network.
*   **Public access (locked, decision #18):** the host already runs a `cloudflared` container (remote-managed tunnel, token mode; `folding-os.com` NS records are Cloudflare's). Setup:
    1.  compose declares an external network `cloudflare` (`name: cloudflared_default`, the tunnel's existing network) and attaches `frontend` and `backend` to it;
    2.  Cloudflare Zero Trust dashboard → Tunnels → Public Hostname: `chat.folding-os.com` → `http://frontend:3000`;
    3.  frontend nginx: `location /api/ { proxy_pass http://backend:8000; proxy_buffering off; }` (SSE-friendly: `proxy_cache off`, `X-Accel-Buffering: no`) — SPA + API same-origin, **no CORS needed**;
    4.  **No host ports are published** for this app — deliberate hardening (internet exposure only via the tunnel). The old open-webui/cptr/open-terminal stack (former owners of host 3000/8000/5173/8001/5555) is being decommissioned because this system replaces it; `3000`/`8000` used here exist only inside the compose network either way.
    *   TLS/edge security is Cloudflare's (no cert work). Optional hardening: Cloudflare Access policy on the hostname for SSO in front of the app's own username/password login.

## 3. Database Schema (PostgreSQL + pgvector)
Single-user system: all rows belong to one account. `pgvector` extension enabled.

*   **`users`**: single shared account.
    *   `id` (UUID), `username`, `password_hash` (argon2id), `created_at`
    *   Seeded by `scripts/init_db.py` from `ADMIN_USERNAME` / `ADMIN_PASSWORD` env vars.
*   **`repositories`**: linked GitHub repositories.
    *   `id`, `github_full_name` (e.g., 'org/repo'), `default_branch`, `last_synced_at`, `last_commit_sha`
*   **`code_files`**: per-file sync markers for incremental ingestion.
    *   `id`, `repo_id`, `file_path`, `git_blob_sha`, `synced_at`
    *   Diff on sync: new/changed rows → re-parse + re-embed; removed rows → delete associated chunks.
*   **`code_chunks`**: RAG corpus.
    *   `id`, `repo_id`, `file_path`, `language`, `start_line`, `end_line`, `content`, `token_estimate`, `embedding vector(768)` (`nomic-embed-text`)
    *   HNSW index on `embedding` (cosine distance).
*   **`conversations`**:
    *   `id`, `user_id`, `repo_id` (nullable — null = general chat), `title` (user-editable), `model_default`, `created_at`, `updated_at`
    *   GIN index on `to_tsvector('simple', title)` for search.
*   **`messages`**:
    *   `id`, `conversation_id`, `role` (user/assistant/tool), `content`, `model` (assistant msgs), `tool_calls` (JSONB), `sequence` (int), `created_at`
    *   GIN index on `to_tsvector('simple', content)` for message search.

## 4. Core Module Specifications

### 4.1 Authentication (single user)
*   `POST /api/auth/login` with username + password → **argon2id** verify → session cookie (`HttpOnly`, `Secure`, `SameSite=Lax`), signed with `SECRET_KEY`, 30-day rolling expiry.
*   Login rate limit: 10 attempts / minute (429 with reset hint).
*   `POST /api/auth/logout` revokes the session. Auth state kept in `sessions` table (cookie token → user, expires_at) so logout works.
*   **No GitHub OAuth.** GitHub access uses the operator's single PAT (env var `GITHUB_PAT`, `repo` scope) for clone/read/branch/commit/PR. The PAT never reaches the browser and is never persisted to the DB.

### 4.2 Code Ingestion & Language-Aware RAG Pipeline
When a user links a repository (by `owner/name`), the backend triggers:
1.  **Cloning:** shallow clone (`--depth 1`) into an ephemeral host directory using `GITHUB_PAT`.
2.  **Filtering (locked rules):** skip `.git`, `node_modules`, `vendor`, `dist`, `build`, `out`, `target`, `__pycache__`, lockfiles (`package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `poetry.lock`, `uv.lock`, `Cargo.lock`), LFS pointer files, binary files, and any file > 1 MB.
3.  **Parsing (Tree-sitter):** grammars for **JavaScript, TypeScript (+TSX), Python, SQL**. Split at function/class boundaries. Target chunk size **500–1000 tokens** (ceilings, never hard mid-function cuts); split only monoliths exceeding the ceiling at function boundaries. **10–15% overlap** for continuity. Non-grammar files are **not** embedded (out of scope). Each chunk's embeddable text is prefixed with `path:line-start-end // language` metadata.
4.  **Embedding:** batch (8–16 chunks/request) → Ollama `nomic-embed-text` (768 dims) via host endpoint.
5.  **Storage:** insert `code_files` rows (blob sha) + `code_chunks` rows.
6.  **Incremental sync:** compare current `git blob sha` per file against `code_files`; re-embed only new/changed files; delete chunks for removed files; update `repositories.last_commit_sha` / `last_synced_at`.
7.  **Retrieval (locked):** per question → embed query → pgvector top-**K = 8** (cosine) → inject into system prompt. Context budget guidance (per SKILL.md): ~60–70% of the window for retrieved code, ~20% conversation history, ~10–20% for the response.

### 4.3 Chat Orchestration (Qwen-Agent)
*   **Model selection:** UI picker offers both models; per-request model name read from env config:
    *   `OLLAMA_FAST_MODEL` = `qwen3.5:4b` — quick Q&A, small edits
    *   `OLLAMA_STRONG_MODEL` = `qwen3.8:27b-longctx` — default; architecture, refactoring, heavy code
    *   `OLLAMA_EMBED_MODEL` = `nomic-embed-text`
    *   App never pulls/loads models; `keep_alive=60s` passed on requests (idle model unloads after 60 s, freeing V100 VRAM; switching back costs a reload — accepted trade-off).
*   **RAG injection:** for conversations bound to a `repo_id`, the backend retrieves Top-8 chunks and builds the system prompt: repo name, project context, retrieved code with paths/lines, coding style rules (type hints, error handling, brief comments).
*   **Agent loop:** Qwen-Agent (OpenAI-compatible → host Ollama) decides between plain reply, **Web Search**, or **Code Interpreter**.
    *   *Web Search:* Qwen-Agent's search tool pointed at **SearXNG JSON API** (`SEARXNG_URL` + `/search?q=…&format=json`). No external API keys.
    *   *Code Interpreter:* backend spins up an ephemeral sandbox (see 4.5), streams stdout/stderr back into the agent loop, then destroys the container.
*   **Streaming:** all agent events (thinking, tool start/output/end, text deltas, code blocks) stream to the frontend via **SSE** (event schema in `API.md`).
*   **Stop (full stop, locked):** client `POST /api/chat/cancel {request_id}` → backend cancels the in-flight LLM request, interrupts the active tool call (SIGTERM→SIGKILL on the sandbox), keeps the partial assistant message in the transcript, and emits a `cancelled` event.
*   **Sampling defaults:** temperature 0.2–0.4 code / 0.6 general (per SKILL.md); max output tokens 2048–4096 for code, 1024 for chat.

### 4.4 Code Editing & GitHub Sync
*   **In-Browser Editor:** Monaco (via `@monaco-editor/react`) in the right pane.
*   **Extraction:** fenced code blocks in assistant messages are parsed (language from fence + path metadata when present) and offered to the editor pane; repo files can also be opened for comparison.
*   **Diff view:** Monaco's diff editor shows original (from repo) vs. AI/user-modified content.
*   **Push workflow:** "Commit to GitHub" → backend (with `GITHUB_PAT`):
    1.  create branch `qwen-assist/<slug>` (timestamp suffix on collision) from `default_branch`,
    2.  commit the file change (conventional commit message, editable in UI),
    3.  open a PR with a generated description (summary, file list, test notes),
    4.  return PR URL → success toast + link in chat.
*   **Conversation state:** commits are recorded on the message (`tool_calls` JSONB) so the PR link survives reloads.

### 4.5 Code Execution Sandbox (locked policy)
*   Image from `sandbox/Dockerfile`: `python:3.12-slim` + `pandas`, `numpy`, `matplotlib`, `requests` (offline-only usage).
*   Per-run container: `--network none`, `--memory 1g`, `--cpus 2`, `--pids-limit 256`, `--read-only` rootfs with `tmpfs /tmp` (64 MB), unprivileged user, `no-new-privileges`, **120 s hard timeout** (SIGKILL on exceed).
*   Script injected to a fixed path, executed, stdout/stderr captured incrementally and streamed to the SSE `tool_output` events.
*   Container destroyed immediately after execution (or on cancel).
*   Backend holds the Docker socket to manage these containers (accepted because this is a dedicated internal server).

### 4.6 Frontend (React 19 + TS + Vite + Zustand)
*   **Layout (3-pane, dark default, light optional):**
    *   *Left (≈260 px):* conversation list (new, **search box over titles + message bodies**, **rename**, **delete**), repo selector with sync status, model badge.
    *   *Center:* header (repo + **model picker**), message list (Markdown + Shiki code blocks + KaTeX), composer (Enter=send, Shift+Enter=newline) with **Stop** button replacing the send icon while streaming.
    *   *Right (toggleable, ≈40–60%):* Monaco editor, diff view, "Commit to GitHub" action, PR status.
*   **Stack:** `react-markdown` + `remark-gfm` + `shiki`, `katex`, `@monaco-editor/react`, `zustand`, `eventsource-parser`, `lucide-react`, `date-fns`.
*   **SSE consumption:** `eventsource-parser` over `fetch` streaming (allows `POST` + abort); reconnect policy: no auto-reconnect mid-generation (state is authoritative server-side).
*   **Theming:** CSS variables; dark palette inspired by chat.qwen.ai; light theme optional. Desktop-first (≥1280 px).

## 5. Implementation Phases (Project Roadmap)

### Phase 1: Infrastructure, Database & Auth
*   `pgvector` schema + HNSW index + full-text indexes; `init_db.py` seeds the single account.
*   Docker Compose: frontend, backend, **searxng**; networking to host Ollama/Postgres.
*   Login/logout/session endpoints + rate limit; frontend auth shell (login screen, session restore).

### Phase 2: GitHub Ingestion Pipeline
*   Repo link/unlink API (PAT clone, shallow); sync status endpoint.
*   Tree-sitter parser (JS/TS/TSX/Python/SQL) with boundary-aware chunking (500–1000 tok, overlap) + skip rules.
*   `code_files` blob-sha incremental sync; embedding batches → pgvector.
*   Repo panel in UI: link, sync, progress, file counts.

### Phase 3: Chat, RAG, Tools & Streaming
*   SSE endpoint (`sse-starlette`), event state machine (see API.md), full stop/cancel.
*   Qwen-Agent wired to Ollama (`keep_alive=60s`), model picker (14b/27b), RAG Top-8 injection.
*   Web search via SearXNG; conversation rename/delete/search; Markdown/Shiki/KaTeX rendering.

### Phase 4: Editor & GitHub Sync
*   Monaco pane, code-block extraction, diff view.
*   Branch → commit → PR flow via GitHub API; PR link surfaced in UI.

### Phase 5: Sandbox & Polish
*   Ephemeral sandbox manager; stdout/stderr streaming; full-stop integration.
*   E2E pass: link private repo → question → RAG answer → AI writes fix → edit in Monaco → push PR.
*   Theming polish, edge cases (empty repos, >1 MB files, unicode, long sessions), docs (ARCHITECTURE.md, API.md already drafted).

## 6. Security Posture
*   Session cookie: `HttpOnly`, `Secure`, `SameSite=Lax`; server-side killable sessions; argon2id hashing; login rate limit.
*   `GITHUB_PAT` and `SECRET_KEY` in env only; never in logs or responses.
*   Sandbox: no network, resource caps, unprivileged, ephemeral (see 4.5).
*   CORS locked to the single frontend origin; backend proxied same-origin by the operator's reverse proxy.
*   SSE streams never contain secrets; tool output is truncated to a configurable max (e.g., 64 KB).

## 7. Key Documentation Resources
*   **Qwen-Agent:** [github.com/QwenLM/Qwen-Agent](https://github.com/QwenLM/Qwen-Agent)
*   **Ollama OpenAI Compatibility:** [github.com/ollama/ollama/blob/main/docs/openai.md](https://github.com/ollama/ollama/blob/main/docs/openai.md)
*   **SearXNG:** [docs.searxng.org](https://docs.searxng.org) (JSON API: `settings.yml → search.formats: [html, json]`)
*   **FastAPI & SSE:** [fastapi.tiangolo.com](https://fastapi.tiangolo.com/) · [sse-starlette](https://github.com/sysid/sse-starlette)
*   **Tree-sitter:** [tree-sitter.github.io/tree-sitter/](https://tree-sitter.github.io/tree-sitter/)
*   **Monaco Editor:** [microsoft.github.io/monaco-editor/](https://microsoft.github.io/monaco-editor/)
*   **pgvector:** [github.com/pgvector/pgvector](https://github.com/pgvector/pgvector)

## 8. Open Items (non-blocking)
*   ~~Reverse-proxy/TLS for `chat.folding-os.com`~~ — **RESOLVED 2026-08-23:** existing Cloudflare Tunnel (decision #18); only manual step left is adding the public hostname in the Zero Trust dashboard.
*   Conversation export (Markdown/JSON) — deferred; not in locked scope.
*   Auto model routing by task complexity — deferred; user picks the model explicitly.
