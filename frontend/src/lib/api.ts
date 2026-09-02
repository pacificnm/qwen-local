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
  /** Runtime context window in tokens (Ollama `num_ctx`), when known. */
  context_window?: number | null;
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

/* --- projects (Phase 5: a project owns one repo + its conversations) --- */

export interface Project {
  id: string;
  name: string;
  created_at: string;
  updated_at: string;
  repo: Repo | null;
  conversation_count: number;
}

export async function listProjects(): Promise<Project[]> {
  const res = await fetch(`${BASE}/projects`, { credentials: "same-origin" });
  return handle<Project[]>(res);
}

export async function createProject(body: {
  name?: string;
  full_name?: string | null;
}): Promise<Project> {
  const res = await fetch(`${BASE}/projects`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return handle<Project>(res);
}

export async function updateProject(
  id: string,
  body: { name?: string; full_name?: string | null; detach_repo?: boolean },
): Promise<Project> {
  const res = await fetch(`${BASE}/projects/${id}`, {
    method: "PATCH",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return handle<Project>(res);
}

export async function deleteProject(id: string): Promise<void> {
  const res = await fetch(`${BASE}/projects/${id}`, { method: "DELETE", credentials: "same-origin" });
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

export interface ProjectSettings {
  project_id: string;
  sandbox_port: number;
  sandbox_container_port: number;
  rag_top_k: number;
  rag_max_chars: number;
  mcp_servers: Record<string, unknown>[] | null;
  model_default: string | null;
  updated_at: string;
}

export type ProjectSettingsInput = Omit<ProjectSettings, "project_id" | "updated_at">;

export async function getProjectSettings(id: string): Promise<ProjectSettings> {
  const res = await fetch(`${BASE}/projects/${id}/settings`, { credentials: "same-origin" });
  return handle<ProjectSettings>(res);
}

export async function updateProjectSettings(
  id: string,
  body: ProjectSettingsInput,
): Promise<ProjectSettings> {
  const res = await fetch(`${BASE}/projects/${id}/settings`, {
    method: "PUT",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return handle<ProjectSettings>(res);
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

export async function renameRepoFile(
  repoId: string,
  fromPath: string,
  toPath: string,
): Promise<{ path: string }> {
  const res = await fetch(`${BASE}/repos/${repoId}/files/rename`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ from_path: fromPath, to_path: toPath }),
  });
  return handle<{ path: string }>(res);
}

export async function deleteRepoFile(repoId: string, path: string): Promise<{ deleted: string }> {
  const res = await fetch(`${BASE}/repos/${repoId}/file`, {
    method: "DELETE",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  return handle<{ deleted: string }>(res);
}

export async function createRepoFile(
  repoId: string,
  path: string,
  content: string,
): Promise<{ path: string }> {
  const res = await fetch(`${BASE}/repos/${repoId}/files/create`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, content }),
  });
  return handle<{ path: string }>(res);
}

export async function createRepoDir(repoId: string, path: string): Promise<{ path: string }> {
  const res = await fetch(`${BASE}/repos/${repoId}/folders`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  return handle<{ path: string }>(res);
}

export interface GitDirtyEntry {
  status: string;
  path: string;
}

export interface GitFileEntry {
  status: string;
  path: string;
  old_path: string | null;
}

export interface GitLogEntry {
  sha: string;
  author: string;
  date: string;
  subject: string;
}

export interface GitPrInfo {
  number: number;
  url: string;
  title?: string;
}

export interface GitState {
  repo_id: string;
  branch: string;
  default_branch: string;
  has_commits: boolean;
  head_sha: string;
  upstream: string | null;
  ahead: number | null;
  behind: number | null;
  staged: GitFileEntry[];
  changes: GitFileEntry[];
  dirty: GitDirtyEntry[];
  recent: GitLogEntry[];
  pr: GitPrInfo | null;
}

export async function getRepoGit(repoId: string): Promise<GitState> {
  const res = await fetch(`${BASE}/repos/${repoId}/git`, { credentials: "same-origin" });
  return handle<GitState>(res);
}

export async function stageFiles(repoId: string, paths: string[]): Promise<{ staged: number }> {
  const res = await fetch(`${BASE}/repos/${repoId}/git/stage`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ paths }),
  });
  return handle<{ staged: number }>(res);
}

export async function unstageFiles(repoId: string, paths: string[]): Promise<{ unstaged: number }> {
  const res = await fetch(`${BASE}/repos/${repoId}/git/unstage`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ paths }),
  });
  return handle<{ unstaged: number }>(res);
}

export async function commitStaged(repoId: string, message: string): Promise<{ commit_sha: string }> {
  const res = await fetch(`${BASE}/repos/${repoId}/git/commit`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
  return handle<{ commit_sha: string }>(res);
}

export async function pushBranch(repoId: string): Promise<{ branch: string; output: string }> {
  const res = await fetch(`${BASE}/repos/${repoId}/git/push`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
  });
  return handle<{ branch: string; output: string }>(res);
}

export async function createBranch(repoId: string, name: string): Promise<{ branch: string }> {
  const res = await fetch(`${BASE}/repos/${repoId}/git/branch`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  return handle<{ branch: string }>(res);
}

export interface OpenPrBody {
  title: string;
  body?: string;
  issue_number?: number;
}

export async function openPullRequest(repoId: string, body: OpenPrBody): Promise<GitPrInfo> {
  const res = await fetch(`${BASE}/repos/${repoId}/git/pr`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return handle<GitPrInfo>(res);
}

export async function mergePullRequest(repoId: string): Promise<{ merged: boolean; sha: string }> {
  const res = await fetch(`${BASE}/repos/${repoId}/git/pr/merge`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
  });
  return handle<{ merged: boolean; sha: string }>(res);
}

// --------------------------------------------------------------------------- //
// Issues tab
// --------------------------------------------------------------------------- //

export type IssueState = "open" | "closed";

export interface GitHubLabel {
  name: string;
  color: string;
}

export interface GitHubUser {
  login: string;
  avatar_url: string;
}

export interface GitHubIssue {
  number: number;
  title: string;
  body: string;
  state: IssueState;
  user: string | null;
  labels: GitHubLabel[];
  assignees: GitHubUser[];
  comments: number;
  created_at: string;
  updated_at: string;
  html_url: string;
}

export interface GitHubComment {
  id: number;
  body: string;
  user: string | null;
  avatar_url: string | null;
  created_at: string;
}

export interface IssuesMeta {
  labels: GitHubLabel[];
  assignees: GitHubUser[];
}

export async function listIssues(
  repoId: string,
  opts: { state?: "open" | "closed" | "all"; labels?: string; page?: number } = {},
): Promise<{ items: GitHubIssue[]; has_more: boolean }> {
  const params = new URLSearchParams();
  params.set("state", opts.state ?? "open");
  if (opts.labels) params.set("labels", opts.labels);
  if (opts.page) params.set("page", String(opts.page));
  const res = await fetch(`${BASE}/repos/${repoId}/issues?${params}`, { credentials: "same-origin" });
  return handle<{ items: GitHubIssue[]; has_more: boolean }>(res);
}

export async function getIssuesMeta(repoId: string): Promise<IssuesMeta> {
  const res = await fetch(`${BASE}/repos/${repoId}/issues/meta`, { credentials: "same-origin" });
  return handle<IssuesMeta>(res);
}

export async function getIssue(
  repoId: string,
  number: number,
): Promise<{ issue: GitHubIssue; comments: GitHubComment[] }> {
  const res = await fetch(`${BASE}/repos/${repoId}/issues/${number}`, { credentials: "same-origin" });
  return handle<{ issue: GitHubIssue; comments: GitHubComment[] }>(res);
}

export interface IssueCreateBody {
  title: string;
  body?: string;
  labels?: string[];
  assignees?: string[];
}

export async function createIssue(repoId: string, body: IssueCreateBody): Promise<GitHubIssue> {
  const res = await fetch(`${BASE}/repos/${repoId}/issues`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return handle<GitHubIssue>(res);
}

export interface IssueUpdateBody {
  title?: string;
  body?: string;
  state?: IssueState;
  labels?: string[];
  assignees?: string[];
}

export async function updateIssue(
  repoId: string,
  number: number,
  body: IssueUpdateBody,
): Promise<GitHubIssue> {
  const res = await fetch(`${BASE}/repos/${repoId}/issues/${number}`, {
    method: "PATCH",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return handle<GitHubIssue>(res);
}

export async function addIssueComment(
  repoId: string,
  number: number,
  body: string,
): Promise<GitHubComment> {
  const res = await fetch(`${BASE}/repos/${repoId}/issues/${number}/comments`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ body }),
  });
  return handle<GitHubComment>(res);
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
  project_id: string;
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

export async function listConversations(opts: {
  project_id: string;
  q?: string;
  cursor?: string;
  limit?: number;
}): Promise<ConversationListPage> {
  const params = new URLSearchParams({ project_id: opts.project_id });
  if (opts.q) params.set("q", opts.q);
  if (opts.cursor) params.set("cursor", opts.cursor);
  if (opts.limit) params.set("limit", String(opts.limit));
  const res = await fetch(`${BASE}/conversations?${params.toString()}`, {
    credentials: "same-origin",
  });
  return handle<ConversationListPage>(res);
}

export async function createConversation(body: {
  project_id: string;
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
