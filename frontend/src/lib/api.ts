/**
 * Minimal same-origin API client. The nginx (or Vite dev) proxy serves /api/*
 * from the backend container, so no CORS and no base-URL configuration.
 * Auth is the HttpOnly `session` cookie — fetch with credentials: same-origin.
 */

const BASE = "/api";

export interface Model {
  id: string;
  label: string;
  is_default: boolean;
}

export interface AuthUser {
  username: string;
}

export interface HealthReport {
  status: string;
  checks: Record<string, string>;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail: unknown = `HTTP ${res.status}`;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (body && typeof body.detail === "string") detail = body.detail;
    } catch {
      /* error body was not JSON */
    }
    throw new ApiError(res.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return (await res.json()) as T;
}

export async function login(username: string, password: string): Promise<AuthUser> {
  const res = await fetch(`${BASE}/auth/login`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  return handle<AuthUser>(res);
}

export async function me(): Promise<AuthUser> {
  const res = await fetch(`${BASE}/auth/me`, { credentials: "same-origin" });
  return handle<AuthUser>(res);
}

export async function logout(): Promise<{ ok: boolean }> {
  const res = await fetch(`${BASE}/auth/logout`, { method: "POST", credentials: "same-origin" });
  return handle<{ ok: boolean }>(res);
}

export async function models(): Promise<Model[]> {
  const res = await fetch(`${BASE}/models`, { credentials: "same-origin" });
  return handle<Model[]>(res);
}

export async function health(): Promise<HealthReport> {
  const res = await fetch(`${BASE}/health`, { credentials: "same-origin" });
  return handle<HealthReport>(res);
}

/* --- repositories (Phase 2: GitHub ingestion) --- */

export interface Repo {
  id: string;
  github_full_name: string;
  default_branch: string;
  last_synced_at: string | null;
  last_commit_sha: string | null;
  file_count: number;
  chunk_count: number;
  /** running | queued | cloning | scanning | processing | done | idle | "error: …" */
  state: string;
}

export interface RepoSyncStatus {
  repo_id: string;
  state: string;
  stage: string;
  files_total: number;
  files_done: number;
  files_added: number;
  files_removed: number;
  chunks_written: number;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  last_synced_at: string | null;
  last_commit_sha: string | null;
}

export async function listRepos(): Promise<Repo[]> {
  const res = await fetch(`${BASE}/repos`, { credentials: "same-origin" });
  return handle<Repo[]>(res);
}

export async function linkRepo(fullName: string): Promise<{ id: string; state: string }> {
  const res = await fetch(`${BASE}/repos`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ full_name: fullName }),
  });
  return handle<{ id: string; state: string }>(res);
}

export async function syncRepo(id: string): Promise<{ repo_id: string; state: string }> {
  const res = await fetch(`${BASE}/repos/${id}/sync`, {
    method: "POST",
    credentials: "same-origin",
  });
  return handle<{ repo_id: string; state: string }>(res);
}

export async function repoSyncStatus(id: string): Promise<RepoSyncStatus> {
  const res = await fetch(`${BASE}/repos/${id}/sync-status`, { credentials: "same-origin" });
  return handle<RepoSyncStatus>(res);
}

export interface RepoFileEntry {
  path: string;
}

export async function listRepoFiles(repoId: string): Promise<{ repo_id: string; files: RepoFileEntry[] }> {
  const res = await fetch(`${BASE}/repos/${repoId}/files`, { credentials: "same-origin" });
  return handle<{ repo_id: string; files: RepoFileEntry[] }>(res);
}

export async function getRepoFile(repoId: string, path: string): Promise<{ path: string; content: string }> {
  const qs = `?path=${encodeURIComponent(path)}`;
  const res = await fetch(`${BASE}/repos/${repoId}/file${qs}`, { credentials: "same-origin" });
  return handle<{ path: string; content: string }>(res);
}

export interface CommitBody {
  repo_id: string;
  file_path: string;
  content: string;
  base_ref?: string | null;
  branch?: string;
  commit_message: string;
  open_pr?: boolean;
  pr_title?: string | null;
  pr_body?: string | null;
}

export interface CommitResult {
  branch: string;
  commit_sha: string;
  pr_url: string | null;
}

export async function commitToFile(body: CommitBody): Promise<CommitResult> {
  const res = await fetch(`${BASE}/commit`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return handle<CommitResult>(res);
}

export async function recordPrNote(
  convId: string,
  body: { text: string; tool?: Record<string, unknown> | null },
): Promise<ChatMessage> {
  const res = await fetch(`${BASE}/conversations/${convId}/notes`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return handle<ChatMessage>(res);
}

export async function unlinkRepo(id: string): Promise<void> {
  const res = await fetch(`${BASE}/repos/${id}`, { method: "DELETE", credentials: "same-origin" });
  if (!res.ok) {
    let detail: unknown = `HTTP ${res.status}`;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (body && typeof body.detail === "string") detail = body.detail;
    } catch {
      /* error body was not JSON */
    }
    throw new ApiError(res.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
}

/* --- conversations + chat (Phase 3) --- */

export interface Conversation {
  id: string;
  title: string;
  repo_id: string | null;
  repo_name?: string | null;
  model_default: string | null;
  created_at: string;
  updated_at: string;
}

export interface ToolCallInfo {
  name: string;
  arguments?: unknown;
  ok?: boolean;
  duration_ms?: number;
  /** Streamed `tool_output` chunks, appended live (not persisted by the backend). */
  output?: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  model?: string | null;
  sequence: number;
  tool_calls?: ToolCallInfo[] | null;
}

export interface ConversationListPage {
  items: Conversation[];
  next_cursor: string | null;
  has_more: boolean;
}

export interface ConversationDetail {
  conversation: Conversation;
  messages: ChatMessage[];
}

export async function listConversations(opts?: {
  q?: string;
  cursor?: string;
  limit?: number;
}): Promise<ConversationListPage> {
  const params = new URLSearchParams();
  if (opts?.q) params.set("q", opts.q);
  if (opts?.cursor) params.set("cursor", opts.cursor);
  if (opts?.limit) params.set("limit", String(opts.limit));
  const qs = params.toString();
  const res = await fetch(`${BASE}/conversations${qs ? `?${qs}` : ""}`, {
    credentials: "same-origin",
  });
  return handle<ConversationListPage>(res);
}

export async function createConversation(body: {
  repo_id?: string | null;
  model_default?: string | null;
  title?: string;
}): Promise<Conversation> {
  const res = await fetch(`${BASE}/conversations`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return handle<Conversation>(res);
}

export async function getConversation(id: string, afterSequence = 0): Promise<ConversationDetail> {
  const res = await fetch(`${BASE}/conversations/${id}?after_sequence=${afterSequence}`, {
    credentials: "same-origin",
  });
  return handle<ConversationDetail>(res);
}

export async function renameConversation(id: string, title: string): Promise<Conversation> {
  const res = await fetch(`${BASE}/conversations/${id}`, {
    method: "PATCH",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  return handle<Conversation>(res);
}

export async function deleteConversation(id: string): Promise<void> {
  const res = await fetch(`${BASE}/conversations/${id}`, {
    method: "DELETE",
    credentials: "same-origin",
  });
  if (!res.ok && res.status !== 204) {
    let detail: unknown = `HTTP ${res.status}`;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (body && typeof body.detail === "string") detail = body.detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
}

/** Reasoning-effort levels, matching the backend's `EFFORT_LEVELS`. */
export const EFFORT_LEVELS = ["low", "medium", "high", "xhigh"] as const;
export type Effort = (typeof EFFORT_LEVELS)[number];

export interface ChatStartPayload {
  conversation_id: string;
  message: string;
  model?: string;
  effort?: Effort;
}

/** POST /chat/stream is SSE; EventSource can't POST, so we decode frames by hand. */
export async function streamChat(
  body: ChatStartPayload,
  onEvent: (event: string, data: Record<string, unknown>) => void,
  signal: AbortSignal,
): Promise<void> {
  const res = await fetch(`${BASE}/chat/stream`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) {
    let detail = `HTTP ${res.status}`;
    try {
      const b = (await res.json()) as { detail?: unknown };
      if (b && typeof b.detail === "string") detail = b.detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let event = "message";
  const dataLines: string[] = [];

  const dispatch = () => {
    if (dataLines.length === 0) {
      event = "message";
      return;
    }
    const raw = dataLines.join("\n");
    dataLines.length = 0;
    let data: Record<string, unknown> = {};
    try {
      data = JSON.parse(raw) as Record<string, unknown>;
    } catch {
      data = { text: raw };
    }
    onEvent(event, data);
    event = "message";
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let nl: number;
    while ((nl = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, nl).replace(/\r$/, "");
      buf = buf.slice(nl + 1);
      if (line === "") {
        dispatch();
      } else if (line.startsWith("event:")) {
        event = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trimStart());
      }
      // comment/pong lines (": ping") are ignored
    }
  }
  dispatch();
}

export async function cancelChat(requestId: string): Promise<{ ok: boolean; status: string }> {
  const res = await fetch(`${BASE}/chat/cancel`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ request_id: requestId }),
  });
  return handle<{ ok: boolean; status: string }>(res);
}
