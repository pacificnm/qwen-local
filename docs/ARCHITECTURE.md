# Architecture

Companion to [MASTER_SPEC.md](MASTER_SPEC.md). All decisions below are locked (see decision log in the spec, v2.0, 2026-08-23).

## 1. Deployment Topology

```
   chat.folding-os.com (Cloudflare edge, TLS)
        │
        ▼
   ┌─────────────┐  existing remote-managed tunnel (cloudflared token mode)
   │ CLOUDFLARE  │  Public hostname: chat.folding-os.com → http://frontend:3000
   └──────┬──────┘
          │  (shared Docker network: cloudflared_default)
   ┌──────▼─────────────────────────────────────────────┐
   │              HOST (bare metal, single V100)         │
   │                                                    │
   │  ┌────────────┐  ┌───────────┐  ┌────────────────┐ │
   │  │ PostgreSQL │  │  Ollama   │  │ cloudflared    │ │
   │  │ 17+pgvector│  │ (existing│  │  (pre-existing)│ │
   │  │ :5432      │  │  :11434  │  └────────────────┘ │
   │  │ users,     │  │ qwen3:14b│                     │
   │  │ repos,     │  │ qwen3:27b│                     │
   │  │ chunks,    │  │ nomic-   │                     │
   │  │ files,     │  │ embed-text│                    │
   │  │ convos,    │  └───────────┘                    │
   │  │ messages   │                                    │
   │  └────────────┘                                    │
   │                                                    │
   │  DOCKER COMPOSE (no host ports published)           │
   │  ┌──────────┐  /api same-origin  ┌──────────┐     │
   │  │ frontend │◄───── proxy ──────►│ backend  │     │
   │  │ nginx    │  (nginx location  │ FastAPI  │     │
   │  │ SPA :3000│   /api → :8000)   │ :8000    │     │
   │  └──────────┘                    └────┬─────┘     │
   │        ▲                              │           │
   │        │            docker socket◄────┤   searxng │
   │  ┌─────────────┐              ┌───────▼──────┐    │
   │  │ sandbox*N   │              │   searxng    │    │
   │  │ --network   │              │ (JSON API)   │    │
   │  │   none      │              └──────────────┘    │
   │  └─────────────┘        ┌─────────────────────┐   │
   │                         │  GitHub.com         │   │
   │                         │ (GITHUB_PAT:        │   │
   │                         │  clone/branch/PR)   │   │
   │                         └─────────────────────┘   │
   └────────────────────────────────────────────────────┘
```

*   **Host:** single Tesla V100; Ollama + PostgreSQL run natively (out of scope to configure).
*   **Containers:** `frontend` (nginx), `backend` (FastAPI), `searxng`, plus ephemeral `qcsbx-*` containers created per code-interpreter call.
*   **Docker access (Phase 5):** `backend` mounts the host socket `/var/run/docker.sock` (rw — the CLI needs `open()` rw) and speaks to the daemon with a static `docker` CLI baked into the image (app image is `python:3.12-slim` + that CLI; the runtime user is in the host `docker` gid, 988). The sandbox runtime image `qwen-code-sandbox:latest` (from `sandbox/`) is built by the profiled `sandbox-image` service — refresh with `docker compose build sandbox-image`; the backend also auto-builds on first run if the image is missing.
*   **Host bridging:** backend sets `HOST_OVERRIDE=192.168.88.10` (host LAN IP) in docker-compose; `settings.effective_database_url` / `effective_ollama_host` re-point the `.env` URLs at it while preserving credentials/ports. `pg_hba` allows `192.168.88.0/24`.
*   **Public access (locked):** TLS + routing via the pre-existing Cloudflare Tunnel. `frontend`/`backend` attach to the tunnel's external network (`cloudflared_default`) so the dashboard ingress `chat.folding-os.com → http://frontend:3000` resolves. The frontend nginx proxies `/api/*` to `backend:8000` (same-origin; SSE unbuffered). LAN devices reach the app directly via host port **3000** (`http://192.168.88.10:3000`, published on `frontend`; host 3000 was freed when the old open-webui/cptr/open-terminal stack was decommissioned — this system replaces it — and 8000/8080 are already taken by other host apps).

## 2. Component Responsibilities

| Component | Responsibility |
|-----------|----------------|
| `frontend` | 3-pane SPA; auth shell; chat/stream rendering; editor/diff; repo panel; SSE client |
| `backend` | Sessions, REST API, RAG pipeline, Qwen-Agent orchestration, SSE hub, GitHub client, sandbox manager |
| `backend/app/llm` | `client.py` (Ollama streaming client), `agent.py` (turn loop: ≤3 tool rounds, forced answer on budget exhaustion), `tools.py` (`web_search`→SearXNG, `code_interpreter`→sandbox manager), `prompts.py` |
| `backend/app/repos` | `sync.py` (clone/scan/incremental), `chunking.py` (tree-sitter), `embeddings.py` (Ollama embed), `retrieval.py` (pgvector top-8), `gitops.py` (branch/commit/PR) |
| `backend/app/sandbox` | `manager.py` (ephemeral hardened Docker runs — see §3.5); driven by `app/llm/tools.py` |
| `searxng` | Local web search (JSON API) for the agent |
| `qcsbx-*` | Ephemeral Python execution, no network, 120 s hard timeout, destroyed after every exit path |
| Host Ollama | Inference + embeddings (existing; app only consumes) |
| Host PostgreSQL | Durable state + vector search |

## 3. Data Flows

### 3.1 Repository Ingestion
```
UI "link repo" → backend /api/repos
  → clone (--depth 1, GITHUB_PAT) → filter (skip rules)
  → tree-sitter AST split (500–1000 tok, overlap, JS/TS/Py/SQL)
  → Ollama nomic-embed-text (batches 8–16)
  → code_files (blob sha) + code_chunks (vector 768, HNSW)
  → UI sync status (completed/failed/progress)
Re-sync: diff blob shas → re-embed changed only, delete removed.
```

### 3.2 Chat Turn (happy path)
```
user message → /api/chat/stream (SSE)
  → bind conversation.repo_id? → embed query → pgvector Top-8
  → build system prompt (repo ctx + chunks + style rules)
  → Qwen-Agent (model = request.model, keep_alive=60s)
      ├── plain reply          → text_delta* → done
      ├── web_search (SearXNG) → tool_start → tool_output(results) → agent continues
      └── code_interpreter     → tool_start → sandbox run
                                 (tool_output stdout/stderr*) → tool_end
  → assistant message persisted (full content + tool_calls JSONB)
  → done {request_id, tokens?}
```

### 3.3 Stop (full stop)
```
UI Stop → POST /api/chat/cancel {request_id}
  → asyncio task cancel → Ollama request aborted (client-side HTTP cancel)
  → if sandbox running: SIGTERM → 2 s → SIGKILL; container removed
  → partial assistant text kept & persisted
  → SSE `cancelled` event → stream closes
```

### 3.4 Edit & Push
```
AI code block → right pane Monaco (or repo file opened for diff)
user edits → "Commit to GitHub"
  → POST /api/commit {repo, path, base_ref, content, branch?, commit_msg, pr?}
  → GitHub API: branch qwen-assist/<slug> → commit → PR (generated body)
  → {pr_url} → UI toast + link; recorded on the source message
```

### 3.5 Code Interpreter Run (ephemeral sandbox, spec §4.5)
```
AI emits code_interpreter {code}
  → app/llm/tools.py → app/sandbox/manager.run()
  → ensure image qwen-code-sandbox:latest (auto-build once if missing)
  → docker run -i --rm --name qcsbx-<uuid>
        --read-only --tmpfs=/tmp:rw,size=64m
        --memory=1g --memory-swap=1g --cpus=2 --pids-limit=256
        --network=none --cap-drop=ALL --security-opt=no-new-privileges
        --user 65534:65534  qwen-code-sandbox:latest  python -
     (script on stdin — no host↔container file paths)
  → stdout/stderr pump: each line → SSE tool_output {index, text}
  → terminal: natural exit | 120 s → docker kill -s KILL
                     | user Stop → SIGTERM → 2 s grace → SIGKILL
  → docker rm -f (best-effort, on every path incl. crash/cancel/exception)
  → model gets "exit code: N" + captured stdout/stderr (≤1 MiB per stream;
    32 KB model-visible) or the timeout/stop notice → tool_end {index, ok, duration_ms}
```
Manager seams (`_exec` one-shot CLI ops, `_start` streaming run) are
monkey-patched in `backend/tests/test_sandbox_phase5.py` — the suite never
touches a real daemon.

## 4. Streaming Protocol (SSE)

Transport: `text/event-stream`. One `EventSource`-style stream per turn, multiplexed events:

| Event | Payload | Meaning |
|-------|---------|---------|
| `session` | `{request_id, conversation_id, model}` | Turn started |
| `token` | `{text}` | Incremental text |
| `thinking` | `{text}` | Reasoning (if the model emits it) |
| `tool_start` | `{tool, index, arguments}` | `tool` ∈ `web_search`, `code_interpreter`; `index` = 0-based tool-call slot for the turn |
| `tool_output` | `{index, text}` | Non-streaming tools: exactly one (final result). Streaming (sandbox): one per output line as it lands. A failed streaming tool still gets one final event with the error text. |
| `tool_end` | `{index, ok, duration_ms}` | Slot finished — no name, match by `index` |
| `done` | `{text}` | Turn complete (full assistant text) |
| `error` | `{message}` | Non-recoverable |
| `cancelled` | `{text}` | Client Stop accepted (partial text so far) |

(`code_block` is reserved for editor-population suggestions; the current
build does not emit it — the UI offers "Open in editor" on every fenced
code block instead.)

Rules:
*   Every stream terminates with exactly one of `done` / `error` / `cancelled`.
*   `tool_output.text` is capped at 64 KB per event (longer single chunks are truncated with a note); streaming tools chunk naturally into multiple events.
*   Output fed back to the model is capped at 32 KB per tool; the backend accumulates at most 1 MiB per stream (memory guard against run-away `print` loops).
*   Tool events are correlated by `index` (per turn); `tool_end` carries no name.
*   Events are ordered by server emission; the client must render in arrival order, appending `tool_output` per `index`.

## 5. Key Invariants
1.  The app never modifies Ollama state (no pulls, no model management) — it only issues requests with `keep_alive=60s`.
2.  One PAT, env-only: `GITHUB_PAT` is read by the backend process only; it must not appear in responses, SSE payloads, or logs.
3.  Sandbox containers are short-lived and network-less; nothing persists from a run. `backend` is the only component holding the Docker socket; sandbox runs are unprivileged (user 65534, no capabilities, read-only root) and are destroyed on every exit path — success, timeout, Stop, or crash.
4.  Embedding dimension is 768 (`nomic-embed-text`) — changing the embedding model later requires a full re-embed of every repo.
5.  Conversation + message state is the source of truth for UI hydration; SSE is transport only.
