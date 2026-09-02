import type { GitHubIssue } from "../../lib/api";

export function IssueRow({ issue, onClick }: { issue: GitHubIssue; onClick: () => void }) {
  return (
    <li
      className="issue-item"
      onClick={onClick}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") onClick();
      }}
    >
      <div className="issue-row">
        <span className={`issue-state-dot issue-state-${issue.state}`} title={issue.state} />
        <div className="issue-text">
          <span className="issue-title">
            {issue.title} <span className="issue-number">#{issue.number}</span>
          </span>
          {issue.labels.length > 0 && (
            <span className="issue-label-row">
              {issue.labels.map((lb) => (
                <span key={lb.name} className="issue-label-dot-sm" style={{ background: `#${lb.color}` }} />
              ))}
            </span>
          )}
        </div>
        {issue.comments > 0 && (
          <span className="issue-comment-count" title={`${issue.comments} comments`}>
            💬 {issue.comments}
          </span>
        )}
      </div>
    </li>
  );
}
