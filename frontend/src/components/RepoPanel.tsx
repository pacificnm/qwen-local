import { useEffect, useState } from "react";
import { useRepos } from "../store/repos";

function timeAgo(iso: string): string {
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export default function RepoPanel() {
  const { repos, progress, loaded, busy, error, load, link, sync, unlink } = useRepos();
  const [name, setName] = useState("");
  const [confirming, setConfirming] = useState<string | null>(null);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="repo-panel">
      <form
        className="repo-link"
        onSubmit={(e) => {
          e.preventDefault();
          if (!name.trim() || busy) return;
          setName("");
          void link(name);
        }}
      >
        <label htmlFor="repo-name">GitHub repository</label>
        <input
          id="repo-name"
          placeholder="owner/name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          spellCheck={false}
          autoComplete="off"
        />
        <button className="primary" type="submit" disabled={busy || !name.trim()}>
          {busy ? "Working…" : "Link repository"}
        </button>
      </form>

      {error && <p className="repo-error">{error}</p>}

      {!loaded ? (
        <p className="repo-hint">Loading repositories…</p>
      ) : repos.length === 0 ? (
        <p className="repo-hint">
          Link a repository to index its code. Cloned shallowly from GitHub — private
          repos need a PAT configured on the server.
        </p>
      ) : (
        <ul className="repo-list">
          {repos.map((r) => {
            const p = progress[r.id];
            const running = p !== undefined;
            const failed = r.state.startsWith("error");
            const pct = p && p.files_total > 0 ? Math.round((p.files_done / p.files_total) * 100) : null;
            return (
              <li key={r.id} className={failed ? "repo-card repo-card-error" : "repo-card"}>
                <div className="repo-name">{r.github_full_name}</div>

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
                      {r.state}
                      {` · ${r.file_count} files · ${r.chunk_count} chunks`}
                      {r.last_synced_at ? ` · ${timeAgo(r.last_synced_at)}` : ""}
                    </div>
                    {r.last_commit_sha && (
                      <div className="repo-meta">
                        {r.default_branch} · {r.last_commit_sha.slice(0, 7)}
                      </div>
                    )}
                  </>
                )}

                <div className="repo-actions">
                  <button disabled={busy || running} onClick={() => void sync(r.id)}>
                    Sync
                  </button>
                  <button
                    className={confirming === r.id ? "danger" : undefined}
                    disabled={busy || running}
                    onClick={() => {
                      if (confirming === r.id) {
                        setConfirming(null);
                        void unlink(r.id);
                      } else {
                        setConfirming(r.id);
                      }
                    }}
                  >
                    {confirming === r.id ? "Confirm?" : "Unlink"}
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
