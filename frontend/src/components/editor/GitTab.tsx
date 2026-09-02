import { useEffect } from "react";
import { useEditor } from "../../store/editor";
import { useRepos } from "../../store/repos";
import { CommitForm } from "./CommitForm";
import { GitBranchBar } from "./GitBranchBar";
import { GitCommitBox } from "./GitCommitBox";
import { GitFileRow } from "./GitFileRow";
import { GitPullRequestBox } from "./GitPullRequestBox";
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
            <div className="git-statusline">
              {git.head_sha ? (
                <code className="git-sha">{git.head_sha.slice(0, 7)}</code>
              ) : (
                <span
                  className="git-no-commits"
                  title="The remote has no commits yet — stage files and commit below, then push."
                >
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
          <GitBranchBar repoId={repoId} git={git} />

          <div className="git-section git-worktree">
            <h4 className="git-sec-title">Working tree</h4>

            <div className="git-worktree-group">
              <div className="git-sec-head git-sec-subhead">
                <span className="git-sec-subtitle">
                  Staged{git.staged.length > 0 ? ` (${git.staged.length})` : ""}
                </span>
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

            <div className="git-worktree-group">
              <div className="git-sec-head git-sec-subhead">
                <span className="git-sec-subtitle">
                  Changes{git.changes.length > 0 ? ` (${git.changes.length})` : ""}
                </span>
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
          </div>

          <GitCommitBox repoId={repoId} stagedCount={git.staged.length} />

          <GitPullRequestBox repoId={repoId} git={git} />

          <details className="git-section git-recent">
            <summary className="git-sec-title">Recent commits</summary>
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
          </details>
        </>
      )}

      {git && (
        <div className="git-quickcommit">
          <h4 className="git-sec-title">Quick commit — open file</h4>
          <p className="dim git-quickcommit-hint">
            Publishes the file open in the editor directly, on its own new branch (with an
            optional PR) — independent of staging above. Use this for a one-off edit; use the
            branch workflow above for anything you'll keep working on.
          </p>
          <CommitForm repoId={repoId} />
        </div>
      )}
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
