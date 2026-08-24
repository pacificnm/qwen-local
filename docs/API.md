# API Reference

REST + SSE. Base URL: `https://chat.folding-os.com/api` — backend listens on `:8000` inside the compose network; the public hostname is served by the pre-existing Cloudflare Tunnel into `frontend:3000`, whose nginx proxies `/api/*` → `backend:8000` (same-origin, SSE unbuffered — so `Accept: text/event-stream` streams work through the edge).
Auth: session cookie from `POST /auth/login` (except that endpoint itself).
Errors: JSON `{ "error": { "code": string, "message": string } }` with an appropriate 4xx/5xx status.

## Auth
| Method | Path | Description |
|--------|------|-------------|
| POST | `/auth/login` | `{username, password}` → 200 + `Set-Cookie: session=…` (HttpOnly, Secure, 30 d). 401 invalid; 429 rate limit (10/min). |
| POST | `/auth/logout` | Revokes the session cookie. |
| GET | `/auth/me` | `{username}` (401 if unauthenticated). |

## Models
| Method | Path | Description |
|--------|------|-------------|
| GET | `/models` | `[{id: "qwen3.5:4b", label: "Qwen 3.5 4B (fast)"}, {id: "qwen3.8:27b-longctx", label: "Qwen 3.8 27B (default)", is_default: true}]` from env config. |

## Repositories
| Method | Path | Description |
|--------|------|-------------|
| GET | `/repos` | `[{id, full_name, default_branch, last_synced_at, chunk_count, file_count, sync_state}]` |
| POST | `/repos` | `{full_name: "org/repo"}` → clone + ingest kick-off. 202 `{id, sync_state: "in_progress"}`. 404 unknown repo / bad PAT. |
| DELETE | `/repos/{id}` | Unlink: deletes repo, files, chunks, keeps conversations (repo_id → null). |
| POST | `/repos/{id}/sync` | Re-run incremental sync. 202. |
| GET | `/repos/{id}/sync` | `{state: idle\|in_progress\|done\|error, files_total, files_done, chunks, error?}` |

## Conversations
| Method | Path | Description |
|--------|------|-------------|
| GET | `/conversations?limit=&cursor=` | Newest-first; optional `?q=` searches title + message bodies (PG FTS). |
| POST | `/conversations` | `{repo_id?, title?, model?}` → 201. |
| GET | `/conversations/{id}` | Detail + `messages` (paged by `sequence`). |
| PATCH | `/conversations/{id}` | `{title?}` rename. |
| DELETE | `/conversations/{id}` | Delete conversation + messages. |

## Chat (SSE)
**POST `/chat/stream`**
```json
{ "conversation_id": "…", "message": "…", "model": "qwen3.8:27b-longctx" }
```
Response: `Content-Type: text/event-stream`, one stream per turn.
`409` while another run is already in progress for the same conversation.

SSE events (payload fields are exact — match tool events by `index`, not name):

| Event | Payload | Meaning |
|-------|---------|---------|
| `session` | `{request_id, conversation_id, model}` | Turn started; keep `request_id` for `/chat/cancel`. |
| `token` | `{text}` | Incremental assistant text. |
| `thinking` | `{text}` | Reasoning text (when the model emits it). |
| `tool_start` | `{tool, index, arguments}` | Tool call started. `tool` ∈ `web_search`, `code_interpreter`; `index` is the 0-based tool-call slot for this turn. |
| `tool_output` | `{index, text}` | Output for slot `index`. Non-streaming tools (`web_search`) emit exactly one (final result). Streaming tools (`code_interpreter`) emit **multiple** — stdout/stderr lines as they land — until `tool_end`. A failed streaming tool still gets one final event carrying the error text. |
| `tool_end` | `{index, ok, duration_ms}` | Slot finished (no `tool` name). `ok: false` only when the call raised (unknown tool, empty `code`, sandbox start failure). A timeout or user Stop still "succeeds": the model-visible result carries an explicit `TIMEOUT:` / `CANCELLED:` note, and the turn then terminates with `cancelled` (Stop) or continues. |
| `done` | `{text}` | Turn complete; full assistant text. |
| `cancelled` | `{text}` | Stop accepted; partial text so far (persisted). |
| `error` | `{message}` | Non-recoverable error. |

Every stream ends with exactly one of `done` / `cancelled` / `error`.
`tool_output.text` is capped at 64 KB per event (a longer single chunk is
truncated with a note); clients append per `index` in arrival order.

**Tools** (invoked by the model, not by the client):

* `web_search {query: string}` — self-hosted SearXNG JSON search; non-streaming; returns the top results as text.
* `code_interpreter {code: string}` — runs `code` in a fresh, hardened, network-less Docker container (spec §4.5): read-only root + 64 MB tmpfs `/tmp`, 1 GiB memory, 2 CPUs, 256-pid cap, all capabilities dropped, unprivileged user 65534, hard 120 s SIGKILL timeout, destroyed after every exit path. Script is fed on stdin; stdout/stderr stream live as `tool_output`; the model sees exit code + output (or the timeout/stop notice).

**POST `/chat/cancel`** → `{ "request_id": "…" }` — full stop: aborts generation + active tool (sandbox gets SIGTERM → 2 s grace → SIGKILL + container removal); partial text is kept. 404 if the request is unknown/finished.

## Commit / PR
**POST `/commit`**
```json
{
  "repo_id": "…",
  "file_path": "src/auth.py",
  "base_ref": "main",
  "content": "…",
  "branch": "qwen-assist/fix-auth" ,
  "commit_message": "fix(auth): correct PAT scope check",
  "open_pr": true,
  "pr_title": "…", "pr_body": "…"
}
```
→ 201 `{branch, commit_sha, pr_url?}`. 409 branch collision (server appends timestamp suffix + re-tries once). 422 empty content / missing file on base ref.

## Health
| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | `{status: "ok", ollama: "ok"\|"down", postgres: "ok"\|"down", searxng: "ok"\|"down"}` |

## Conventions
*   All timestamps ISO-8601 UTC.
*   IDs are UUID strings.
*   List endpoints: `limit` (default 50, max 200) + opaque `cursor`.
*   The backend never echoes secrets; SSE `tool_output` is capped at 64 KB per event.
