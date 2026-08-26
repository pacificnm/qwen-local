import { useState } from "react";
import { useProjects } from "../store/projects";
import { useRepos } from "../store/repos";

function timeAgo(iso: string): string {
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

/** Repository card for the active project: sync progress/status and
 *  attach / detach actions. Repo-less projects are general chat. */
export default function ProjectRepoCard({ projectId }: { projectId: string }) {
  const { projects, busy, error, attachRepo, detachRepo, syncRepo } = useProjects();
  const { progress } = useRepos();
  const [fullName, setFullName] = useState("");
  const [detaching, setDetaching] = useState(false);

  const project = projects.find((p) => p.id === projectId);
  const repo = project?.repo ?? null;
  const p = repo ? progress[repo.id] : undefined;
  const running = p !== undefined;
  const failed = repo !== null && repo.state.startsWith("error");
  const pct = p && p.files_total > 0 ? Math.round((p.files_done / p.files_total) * 100) : null;

  async function attach() {
    if (!fullName.trim() || busy) return;
    const name = fullName;
    setFullName("");
    try {
      await attachRepo(projectId, name);
    } catch {
      setFullName(name); // let the user fix + retry (error shows below)
    }
  }

  if (!repo) {
    return (
      <div className="repo-panel proj-repo">
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
          <label htmlFor={`proj-repo-${projectId}`}>GitHub repository</label>
          <input
            id={`proj-repo-${projectId}`}
            placeholder="owner/name"
            value={fullName}
            onChange={(e) => setFullName(e.target.value)}
            spellCheck={false}
            autoComplete="off"
          />
          <button className="primary" type="submit" disabled={busy || !fullName.trim()}>
            {busy ? "Working…" : "Attach & index"}
          </button>
        </form>
        {error && <p className="repo-error">{error}</p>}
      </div>
    );
  }

  return (
    <div className="repo-panel proj-repo">
      <ul className="repo-list">
        <li key={repo.id} className={failed ? "repo-card repo-card-error" : "repo-card"}>
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
            <button disabled={busy || running} onClick={() => void syncRepo(projectId)}>
              Sync
            </button>
            <button
              className={detaching ? "danger" : undefined}
              disabled={busy || running}
              onClick={() => {
                if (detaching) {
                  setDetaching(false);
                  void detachRepo(projectId).catch(() => undefined);
                } else {
                  setDetaching(true);
                }
              }}
            >
              {detaching ? "Confirm?" : "Detach"}
            </button>
          </div>
        </li>
      </ul>
      {error && <p className="repo-error">{error}</p>}
    </div>
  );
}
