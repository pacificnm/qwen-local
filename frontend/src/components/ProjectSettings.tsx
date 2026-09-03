import { useEffect, useState } from "react";
import * as api from "../lib/api";
import { useModels } from "../store/models";
import { useProjects } from "../store/projects";
import { useRepos } from "../store/repos";
import TerminalLauncher from "./TerminalLauncher";

interface Props {
  projectId: string;
  projectName: string;
}

type SaveState =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved" }
  | { kind: "error"; text: string };

const BOUNDS = {
  port: { min: 1, max: 65535, name: "Port" },
  topK: { min: 1, max: 200, name: "RAG top-K" },
  maxChars: { min: 1, max: 100000, name: "RAG max chars" },
} as const;

function message(e: unknown): string {
  if (e instanceof api.ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return String(e);
}

function numField(
  value: string,
  name: string,
  min: number,
  max: number,
): { value?: number; error?: string } {
  const n = Number(value);
  if (value.trim() === "" || !Number.isInteger(n) || n < min || n > max) {
    return { error: `${name} must be an integer between ${min} and ${max}.` };
  }
  return { value: n };
}

function timeAgo(iso: string): string {
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

/** Renders the app's global status bar (terminal launcher + gear) and the
 *  modal the gear opens, to edit the project's one-to-one `project_settings`
 *  row — sandbox ports, RAG params, model/MCP defaults — plus attach/sync/
 *  detach the project's repository. */
export default function ProjectSettings({ projectId, projectName }: Props) {
  const models = useModels((s) => s.models);
  const loadModels = useModels((s) => s.load);
  const { projects, busy: repoBusy, error, attachRepo, detachRepo, syncRepo } = useProjects();
  const { progress } = useRepos();

  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [save, setSave] = useState<SaveState>({ kind: "idle" });

  const [sandboxPort, setSandboxPort] = useState("9000");
  const [sandboxContainerPort, setSandboxContainerPort] = useState("80");
  const [ragTopK, setRagTopK] = useState("8");
  const [ragMaxChars, setRagMaxChars] = useState("12000");
  const [modelDefault, setModelDefault] = useState("");
  const [mcpText, setMcpText] = useState("");

  // Repository section (moved here from the left-pane repo card).
  const [fullName, setFullName] = useState("");
  const [detaching, setDetaching] = useState(false);

  const project = projects.find((p) => p.id === projectId);
  const repo = project?.repo ?? null;
  const p = repo ? progress[repo.id] : undefined;
  const running = p !== undefined;
  const failed = repo !== null && repo.state.startsWith("error");
  const pct = p && p.files_total > 0 ? Math.round((p.files_done / p.files_total) * 100) : null;

  // When the active project changes, discard any in-flight/stale modal state.
  useEffect(() => {
    setOpen(false);
    setSave({ kind: "idle" });
    setLoading(false);
    setSandboxPort("9000");
    setSandboxContainerPort("80");
    setRagTopK("8");
    setRagMaxChars("12000");
    setModelDefault("");
    setMcpText("");
    setFullName("");
    setDetaching(false);
  }, [projectId]);

  // Reset the "Confirm?" detach prompt whenever the attached repo changes
  // (attach → repo appears, detach → repo goes null) so a stale confirm can
  // never attach/detach the wrong repo on a follow-up click.
  useEffect(() => {
    setDetaching(false);
  }, [repo?.id]);

  async function openAndLoad() {
    setOpen(true);
    void loadModels();
    setLoading(true);
    setSave({ kind: "idle" });
    try {
      const s = await api.getProjectSettings(projectId);
      setSandboxPort(String(s.sandbox_port));
      setSandboxContainerPort(String(s.sandbox_container_port));
      setRagTopK(String(s.rag_top_k));
      setRagMaxChars(String(s.rag_max_chars));
      setModelDefault(s.model_default ?? "");
      setMcpText(s.mcp_servers ? JSON.stringify(s.mcp_servers, null, 2) : "");
    } catch (e) {
      setSave({ kind: "error", text: `Couldn't load settings: ${message(e)}` });
    } finally {
      setLoading(false);
    }
  }

  async function attach() {
    if (!fullName.trim() || repoBusy) return;
    const name = fullName;
    setFullName("");
    try {
      await attachRepo(projectId, name);
    } catch {
      setFullName(name); // let the user fix + retry (error shows on the panel)
    }
  }

  async function toggleDetach() {
    if (repoBusy || running) return;
    if (detaching) {
      setDetaching(false);
      await detachRepo(projectId).catch(() => undefined);
    } else {
      setDetaching(true);
    }
  }

  async function saveSettings() {
    const sp = numField(sandboxPort, "Sandbox host port", BOUNDS.port.min, BOUNDS.port.max);
    const cp = numField(
      sandboxContainerPort,
      "Sandbox container port",
      BOUNDS.port.min,
      BOUNDS.port.max,
    );
    const k = numField(ragTopK, BOUNDS.topK.name, BOUNDS.topK.min, BOUNDS.topK.max);
    const ch = numField(ragMaxChars, BOUNDS.maxChars.name, BOUNDS.maxChars.min, BOUNDS.maxChars.max);
    const first = sp.error ?? cp.error ?? k.error ?? ch.error;
    if (first) {
      setSave({ kind: "error", text: first });
      return;
    }

    let mcpServers: Record<string, unknown>[] | null = null;
    const trimmed = mcpText.trim();
    if (trimmed) {
      let parsed: unknown;
      try {
        parsed = JSON.parse(trimmed);
      } catch {
        setSave({ kind: "error", text: "MCP servers must be a valid JSON array, or left blank." });
        return;
      }
      if (!Array.isArray(parsed)) {
        setSave({ kind: "error", text: "MCP servers must be a JSON array, or left blank." });
        return;
      }
      mcpServers = parsed as Record<string, unknown>[];
    }

    setSave({ kind: "saving" });
    try {
      await api.updateProjectSettings(projectId, {
        sandbox_port: sp.value!,
        sandbox_container_port: cp.value!,
        rag_top_k: k.value!,
        rag_max_chars: ch.value!,
        mcp_servers: mcpServers,
        model_default: modelDefault || null,
      });
      setSave({ kind: "saved" });
      window.setTimeout(() => setOpen(false), 650);
    } catch (e) {
      setSave({ kind: "error", text: `Save failed: ${message(e)}` });
    }
  }

  const busy = loading || save.kind === "saving";

  const repoSection = !repo ? (
    <>
      <p className="repo-hint">
        General chat — no repository attached. Attach one to enable RAG + code tools.
      </p>
      <form
        className="repo-link"
        onSubmit={(e) => {
          e.preventDefault();
          void attach();
        }}
      >
        <label htmlFor={`proj-set-repo-${projectId}`}>GitHub repository</label>
        <input
          id={`proj-set-repo-${projectId}`}
          placeholder="owner/name"
          value={fullName}
          onChange={(e) => setFullName(e.target.value)}
          spellCheck={false}
          autoComplete="off"
        />
        <button className="primary" type="submit" disabled={repoBusy || !fullName.trim()}>
          {repoBusy ? "Working…" : "Attach & index"}
        </button>
      </form>
      {error && <p className="repo-error">{error}</p>}
    </>
  ) : (
    <>
      <div className={failed ? "repo-card repo-card-error" : "repo-card"}>
        <div className="repo-name">{repo.github_full_name}</div>

        {running && p ? (
          <>
            <div className="repo-stage">
              {p.stage}
              {p.files_total > 0 ? ` · ${p.files_done}/${p.files_total} files` : ""}
              {p.chunks_written > 0 ? ` · ${p.chunks_written} chunks` : ""}
            </div>
            <div className="repo-progress">
              <div
                className={
                  pct === null ? "repo-progress-fill repo-progress-indeterminate" : "repo-progress-fill"
                }
                style={pct === null ? undefined : { width: `${pct}%` }}
              />
            </div>
          </>
        ) : (
          <>
            <div className={failed ? "repo-error-msg" : "repo-meta"}>
              {repo.state}
              {` · ${repo.file_count} files · ${repo.chunk_count} chunks`}
              {repo.last_synced_at ? ` · ${timeAgo(repo.last_synced_at)}` : ""}
            </div>
            {repo.last_commit_sha && (
              <div className="repo-meta">
                {repo.default_branch} · {repo.last_commit_sha.slice(0, 7)}
              </div>
            )}
          </>
        )}

        <div className="repo-actions">
          <button disabled={repoBusy || running} onClick={() => void syncRepo(projectId)}>
            Sync
          </button>
          <button
            className={detaching ? "danger" : undefined}
            disabled={repoBusy || running}
            onClick={() => void toggleDetach()}
          >
            {detaching ? "Confirm?" : "Detach"}
          </button>
        </div>
      </div>
      {error && <p className="repo-error">{error}</p>}
    </>
  );

  return (
    <>
      <footer className="statusbar">
        <TerminalLauncher />
        <span className="foot-label">{busy ? "Loading…" : "Settings"}</span>
        {save.kind === "saved" && <span className="foot-saved">Saved ✓</span>}
        <button
          className="gear"
          type="button"
          onClick={() => void openAndLoad()}
          aria-label={`Open settings for ${projectName}`}
        >
          <i className="codicon codicon-gear" aria-hidden="true" />
        </button>
      </footer>

      {open && (
        <div className="modal-backdrop" onMouseDown={() => setOpen(false)}>
          <div
            className="modal"
            onMouseDown={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
            aria-label={`Settings for ${projectName}`}
          >
            <div className="modal-head">
              <h2 className="modal-title">Project settings</h2>
              <button className="modal-close" type="button" onClick={() => setOpen(false)} aria-label="Close">
                ×
              </button>
            </div>
            <p className="modal-sub">{projectName}</p>

            <section className="settings-section">
              <h3>Repository</h3>
              {repoSection}
            </section>

            {save.kind === "error" && <div className="modal-error">{save.text}</div>}

            <section className="settings-section">
              <h3>RAG &amp; sandbox</h3>
              <div className="settings-grid">
                <label>
                  Sandbox host port
                  <input
                    type="number"
                    inputMode="numeric"
                    min={BOUNDS.port.min}
                    max={BOUNDS.port.max}
                    value={sandboxPort}
                    onChange={(e) => setSandboxPort(e.target.value)}
                  />
                </label>
                <label>
                  Sandbox container port
                  <input
                    type="number"
                    inputMode="numeric"
                    min={BOUNDS.port.min}
                    max={BOUNDS.port.max}
                    value={sandboxContainerPort}
                    onChange={(e) => setSandboxContainerPort(e.target.value)}
                  />
                </label>
                <label>
                  RAG top-K
                  <input
                    type="number"
                    inputMode="numeric"
                    min={BOUNDS.topK.min}
                    max={BOUNDS.topK.max}
                    value={ragTopK}
                    onChange={(e) => setRagTopK(e.target.value)}
                  />
                </label>
                <label>
                  RAG max chars
                  <input
                    type="number"
                    inputMode="numeric"
                    min={BOUNDS.maxChars.min}
                    max={BOUNDS.maxChars.max}
                    value={ragMaxChars}
                    onChange={(e) => setRagMaxChars(e.target.value)}
                  />
                </label>
              </div>
            </section>

            <section className="settings-section">
              <h3>Defaults</h3>
              <label>
                Default model
                <select value={modelDefault} onChange={(e) => setModelDefault(e.target.value)}>
                  <option value="">None — use conversation default</option>
                  {models.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.label}
                    </option>
                  ))}
                </select>
              </label>

              <label>
                MCP servers <em className="dim">JSON array — blank for none</em>
                <textarea
                  rows={6}
                  value={mcpText}
                  spellCheck={false}
                  placeholder='[{"name":"notion","type":"http","config":{"api_key":"•••","base_id":"•••","tables":["pages","docs"]}}]'
                  onChange={(e) => setMcpText(e.target.value)}
                />
              </label>
            </section>

            <div className="modal-foot">
              <button type="button" onClick={() => setOpen(false)} disabled={busy}>
                Cancel
              </button>
              <button className="primary" type="button" onClick={() => void saveSettings()} disabled={busy}>
                {save.kind === "saving" ? "Saving…" : "Save settings"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
