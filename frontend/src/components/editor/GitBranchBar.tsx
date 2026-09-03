import { useEffect, useState } from "react";
import type { GitState } from "../../lib/api";
import { slugifyText } from "../../lib/slugify";
import { useEditor } from "../../store/editor";

/** Current branch + ahead/behind + push, plus "+ New branch" — the start of
 * the issue → branch → PR → merge workflow. */
export function GitBranchBar({ repoId, git }: { repoId: string; git: GitState }) {
  const gitBusy = useEditor((s) => s.gitBusy);
  const createBranch = useEditor((s) => s.createBranch);
  const pushBranch = useEditor((s) => s.pushBranch);
  const pendingBranchIssue = useEditor((s) => s.pendingBranchIssue);
  const setPendingBranchIssue = useEditor((s) => s.setPendingBranchIssue);

  const [creating, setCreating] = useState(false);
  const [issueNumber, setIssueNumber] = useState("");
  const [description, setDescription] = useState("");

  // Consumed once from the Issues tab's "Start branch" action, then cleared.
  useEffect(() => {
    if (!pendingBranchIssue) return;
    setIssueNumber(String(pendingBranchIssue.number));
    setDescription(pendingBranchIssue.title);
    setCreating(true);
    setPendingBranchIssue(null);
  }, [pendingBranchIssue, setPendingBranchIssue]);

  const slug = slugifyText(description);
  const computedName = issueNumber.trim() ? `issue-${issueNumber.trim()}-${slug}` : slug;

  // Nothing to push: no commits yet, or (with an upstream already set) fully
  // up to date with it. Without an upstream, any commit is pushable — that's
  // what establishes the tracking branch.
  const nothingToPush = !git.has_commits || (!!git.upstream && (git.ahead ?? 0) === 0);
  const pushTitle = !git.has_commits
    ? "Nothing to push yet — commit changes first"
    : nothingToPush
      ? `Already up to date with ${git.upstream}`
      : undefined;

  function cancelCreate() {
    setCreating(false);
    setIssueNumber("");
    setDescription("");
  }

  return (
    <div className="git-branchbar">
      <div className="git-push-row">
        <div className="git-push-meta">
          <span className="git-branch">● {git.branch || "detached HEAD"}</span>
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
            <span className="dim">no upstream yet — push sets it</span>
          )}
        </div>
        <div className="git-branchbar-actions">
          {!creating && (
            <button
              type="button"
              className="git-sec-action"
              disabled={!!gitBusy}
              onClick={() => setCreating(true)}
            >
              + New branch
            </button>
          )}
          <button
            type="button"
            className="primary git-push-btn"
            disabled={!!gitBusy || nothingToPush}
            title={pushTitle}
            onClick={() => void pushBranch(repoId)}
          >
            {gitBusy === "push" ? "Pushing…" : `Push ${git.branch || "branch"}`}
          </button>
        </div>
      </div>

      {creating && (
        <div className="git-newbranch">
          <div className="git-newbranch-fields">
            <label>
              Issue # (optional)
              <input
                inputMode="numeric"
                placeholder="42"
                value={issueNumber}
                onChange={(e) => setIssueNumber(e.target.value.replace(/[^0-9]/g, ""))}
              />
            </label>
            <label className="git-newbranch-desc">
              Description
              <input
                autoFocus
                placeholder="fix the login bug"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Escape") cancelCreate();
                }}
              />
            </label>
          </div>
          <div className="git-newbranch-preview">
            <span className="dim">Branch name:</span> <code>{computedName}</code>
          </div>
          <div className="git-newbranch-actions">
            <button type="button" onClick={cancelCreate} disabled={!!gitBusy}>
              Cancel
            </button>
            <button
              type="button"
              className="primary"
              disabled={!!gitBusy || !description.trim()}
              onClick={() =>
                void createBranch(repoId, computedName).then((ok) => {
                  if (ok) cancelCreate();
                })
              }
            >
              {gitBusy === "branch" ? "Creating…" : "Create & switch"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
