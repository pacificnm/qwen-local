import { useEffect, useState } from "react";
import type { GitState } from "../../lib/api";
import { useEditor } from "../../store/editor";

/** "issue-42-fix-login" → "Fix login" (drop the issue prefix, title-case the rest). */
function titleFromBranch(branch: string): string {
  const stripped = branch.replace(/^issue-\d+-/, "");
  const words = stripped.split(/[-_]+/).filter(Boolean);
  if (words.length === 0) return "";
  return words.map((w) => w[0].toUpperCase() + w.slice(1)).join(" ");
}

function issueNumberFromBranch(branch: string): string {
  return /^issue-(\d+)-/.exec(branch)?.[1] ?? "";
}

/** Steps 5–6 of the issue → branch → PR → merge workflow. Hidden while on
 * the repo's default branch — there is nothing to PR/merge from there. */
export function GitPullRequestBox({ repoId, git }: { repoId: string; git: GitState }) {
  const gitBusy = useEditor((s) => s.gitBusy);
  const openPr = useEditor((s) => s.openPr);
  const mergePr = useEditor((s) => s.mergePr);

  const [title, setTitle] = useState(() => titleFromBranch(git.branch));
  const [body, setBody] = useState("");
  const [issueNumber, setIssueNumber] = useState(() => issueNumberFromBranch(git.branch));

  useEffect(() => {
    setTitle(titleFromBranch(git.branch));
    setIssueNumber(issueNumberFromBranch(git.branch));
  }, [git.branch]);

  if (git.branch === git.default_branch) return null;

  if (git.pr) {
    const pr = git.pr;
    return (
      <div className="git-section git-pr-box git-pr-open">
        <h4 className="git-sec-title">Pull request</h4>
        <div className="git-pr-summary">
          <a href={pr.url} target="_blank" rel="noopener noreferrer" className="pr-link">
            #{pr.number}
            {pr.title ? ` — ${pr.title}` : ""}
          </a>
        </div>
        <div className="commit-actions">
          <button
            type="button"
            className="primary"
            disabled={!!gitBusy}
            onClick={() => {
              if (
                window.confirm(
                  `Merge PR #${pr.number} into ${git.default_branch}? This cannot be undone from here.`,
                )
              ) {
                void mergePr(repoId);
              }
            }}
          >
            {gitBusy === "merge" ? "Merging…" : `Merge into ${git.default_branch}`}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="git-section git-pr-box git-pr-none">
      <h4 className="git-sec-title">Pull request</h4>
      <input
        className="git-pr-title"
        placeholder="PR title"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        maxLength={300}
      />
      <textarea
        rows={3}
        placeholder="PR body (optional)"
        value={body}
        onChange={(e) => setBody(e.target.value)}
      />
      <input
        className="git-pr-issue"
        inputMode="numeric"
        placeholder="Closes issue # (optional)"
        value={issueNumber}
        onChange={(e) => setIssueNumber(e.target.value.replace(/[^0-9]/g, ""))}
      />
      <div className="commit-actions">
        <button
          type="button"
          className="primary"
          disabled={!!gitBusy || !title.trim()}
          onClick={() =>
            void openPr(repoId, {
              title: title.trim(),
              body: body.trim() || undefined,
              issue_number: issueNumber.trim() ? Number(issueNumber.trim()) : undefined,
            }).then((ok) => {
              if (ok) setBody("");
            })
          }
        >
          {gitBusy === "pr" ? "Opening…" : "Open pull request"}
        </button>
      </div>
    </div>
  );
}
