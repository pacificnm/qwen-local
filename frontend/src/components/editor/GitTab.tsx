import { useEffect } from "react";
import { useEditor } from "../../store/editor";
import { useRepos } from "../../store/repos";
import { CommitForm } from "./CommitForm";
import { GitCommitBox } from "./GitCommitBox";
import { GitFileRow } from "./GitFileRow";
import { NoRepoHint } from "./NoRepoHint";
import { useTabRepoId } from "./useTabRepoId";

export function GitTab() {
  const repoId = useTabRepoId();
  const git = useEditor((s) => s.git);
  const gitLoading = useEditor((s) => s.gitLoading);
  const gitError = useEditor((s) => s.gitError);
  const gitBusy = useEditor((s) => s.gitBusy);
  const gitNotice = useEditor((s) => s.gitNotice);
  const stage = useEditor((s) => s.stage);
  const unstage = useEditor((s) => s.unstage);
  const pushBranch = useEditor((s) => s.pushBranch);
  const commitResult = useEditor((s) => s.commitResult);
  const commitError = useEditor((s) => s.commitError);
  const repos = useRepos((s) => s.repos);

  useEffect(() => {
    if (repoId) void useEditor.getState().loadGit(repoId);
  }, [repoId]);

  if (!repoId) return <NoRepoHint />;

  const repoName = repos.find((r) => r.id === repoId)?.github_full_name;
  const load = () => {
    if (repoId) void useEditor.getState().loadGit(repoId);
  };

  return (
    <div className="gittab">
      <div className="git-head">
        <div className="git-head-main">
          <div className="git-repo">{repoName ?? "—"}</div>
          {git && (
            <div className="git-branchline">
              <span className="git-branch">● {git.branch || "detached HEAD"}</span>
              {git.head_sha ? (
                <code className="git-sha">{git.head_sha.slice(0, 7)}</code>
              ) : (
                <span className="git-no-commits" title="The remote has no commits yet — stage files and commit below, then push.">
                  no commits yet
                </span>
              )}
              {git.dirty.length > 0 ? (
                <span className="git-dirtycount">{git.dirty.length} dirty</span>
              ) : (
                <span className="git-clean">clean</span>
              )}
            </div>
          )}
        </div>
        <button
          type="button"
          className="filetree-refresh"
          title="Refresh git state"
          disabled={gitLoading}
          onClick={load}
        >
          ↻
        </button>
      </div>

      {gitError && <p className="banner banner-error">{gitError}</p>}
      {gitNotice && (
        <div
          className={`banner ${gitNotice.ok ? "banner-ok" : "banner-error"}`}
          role={gitNotice.ok ? "status" : "alert"}
        >
          <span>{gitNotice.text}</span>
          <button onClick={() => useEditor.getState().clearGitNotice()}>dismiss</button>
        </div>
      )}
      {gitLoading && !git && <p className="dim">Loading git state…</p>}

      {git && (
        <>
          <div className="git-section">
            <div className="git-sec-head">
              <h4 className="git-sec-title">Staged</h4>
              {git.staged.length > 0 && (
                <button
                  type="button"
                  className="git-sec-action"
                  disabled={!!gitBusy}
                  onClick={() =>
                    void unstage(
                      repoId,
                      git.staged.flatMap((f) => (f.old_path ? [f.old_path, f.path] : [f.path])),
                    )
                  }
                >
                  Unstage all
                </button>
              )}
            </div>
            {git.staged.length === 0 ? (
              <p className="dim">Nothing staged — stage files below.</p>
            ) : (
              <ul className="git-files">
                {git.staged.map((f) => (
                  <GitFileRow
                    key={`${f.status}:${f.path}`}
                    entry={f}
                    action="− Unstage"
                    disabled={!!gitBusy}
                    onAction={() =>
                      void unstage(repoId, f.old_path ? [f.old_path, f.path] : [f.path])
                    }
                  />
                ))}
              </ul>
            )}
          </div>

          <div className="git-section">
            <div className="git-sec-head">
              <h4 className="git-sec-title">Changes</h4>
              {git.changes.length > 0 && (
                <button
                  type="button"
                  className="git-sec-action"
                  disabled={!!gitBusy}
                  onClick={() => void stage(repoId, git.changes.map((f) => f.path))}
                >
                  Stage all
                </button>
              )}
            </div>
            {git.changes.length === 0 ? (
              <p className="dim">No untracked or modified files.</p>
            ) : (
              <ul className="git-files">
                {git.changes.map((f) => (
                  <GitFileRow
                    key={`${f.status}:${f.path}`}
                    entry={f}
                    action="+ Stage"
                    disabled={!!gitBusy}
                    onAction={() => void stage(repoId, [f.path])}
                  />
                ))}
              </ul>
            )}
          </div>

          <GitCommitBox repoId={repoId} stagedCount={git.staged.length} />

          <div className="git-section">
            <h4 className="git-sec-title">Push</h4>
            <div className="git-push-row">
              <div className="git-push-meta">
                {git.upstream ? (
                  <>
                    <code className="git-upstream">{git.upstream}</code>
                    {git.ahead != null && git.behind != null && (
                      <span className="git-ab" title="Ahead / behind upstream (refresh to re-fetch)">
                        {git.ahead > 0 && <span className="git-ab-ahead">↑ {git.ahead}</span>}
                        {git.behind > 0 && <span className="git-ab-behind">↓ {git.behind}</span>}
                        {git.ahead === 0 && git.behind === 0 && (
                          <span className="git-ab-level">up to date</span>
                        )}
                      </span>
                    )}
                  </>
                ) : (
                  <span className="dim">no upstream yet — the first push sets it</span>
                )}
              </div>
              <button
                type="button"
                className="primary git-push-btn"
                disabled={!!gitBusy}
                onClick={() => void pushBranch(repoId)}
              >
                {gitBusy === "push" ? "Pushing…" : `Push ${git.branch || "branch"}`}
              </button>
            </div>
          </div>

          <div className="git-section">
            <h4 className="git-sec-title">Recent commits</h4>
            {git.recent.length === 0 ? (
              <p className="dim">No commits yet.</p>
            ) : (
              <ul className="git-log">
                {git.recent.map((c) => (
                  <li key={c.sha}>
                    <code className="git-csha">{c.sha}</code>
                    <span className="git-subject" title={c.subject}>
                      {c.subject}
                    </span>
                    <span className="git-cmeta">
                      {c.author} · {c.date}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}

      {git && <CommitForm repoId={repoId} />}
      {!git && !gitError && (
        <div className="commit-card">
          <p className="dim">Git state is required to commit (the sync clone must exist).</p>
        </div>
      )}

      {commitResult && (
        <div className="banner banner-ok" role="status">
          <div>
            <strong>{commitResult.branch}</strong> · commit{" "}
            <code>{commitResult.commit_sha.slice(0, 8)}</code> pushed
            {commitResult.pr_url ? (
              <>
                {" "}
                →{" "}
                <a href={commitResult.pr_url} target="_blank" rel="noopener noreferrer" className="pr-link">
                  open PR
                </a>
              </>
            ) : (
              " (branch pushed, no PR)"
            )}
            <span className="banner-dim"> — recorded in the active conversation</span>
          </div>
          <button onClick={() => useEditor.getState().clearResult()}>dismiss</button>
        </div>
      )}
      {commitError && (
        <div className="banner banner-error" role="alert">
          <span>{commitError}</span>
          <button onClick={() => useEditor.getState().clearResult()}>dismiss</button>
        </div>
      )}
    </div>
  );
}
