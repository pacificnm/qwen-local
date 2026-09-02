import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { markdownComponents } from "../chat/markdownComponents";
import { useEditor } from "../../store/editor";
import { useIssues } from "../../store/issues";
import { AssigneePicker, LabelPicker } from "./IssuePickers";

function relTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const s = Math.max(0, (Date.now() - then) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return new Date(iso).toLocaleDateString();
}

export function IssueDetail({ repoId }: { repoId: string }) {
  const selectedNumber = useIssues((s) => s.selectedNumber);
  const selected = useIssues((s) => s.selected);
  const selectedLoading = useIssues((s) => s.selectedLoading);
  const selectedError = useIssues((s) => s.selectedError);
  const meta = useIssues((s) => s.meta);
  const busy = useIssues((s) => s.busy);
  const selectIssue = useIssues((s) => s.selectIssue);
  const updateIssue = useIssues((s) => s.updateIssue);
  const addComment = useIssues((s) => s.addComment);
  const setTab = useEditor((s) => s.setTab);
  const setPendingBranchIssue = useEditor((s) => s.setPendingBranchIssue);

  const [editingBody, setEditingBody] = useState(false);
  const [bodyDraft, setBodyDraft] = useState("");
  const [comment, setComment] = useState("");

  useEffect(() => {
    setEditingBody(false);
    setComment("");
  }, [selectedNumber]);

  if (selectedLoading) return <p className="dim">Loading issue…</p>;
  if (selectedError) return <p className="banner banner-error">{selectedError}</p>;
  if (!selected) return null;

  const { issue, comments } = selected;

  return (
    <div className="issue-detail">
      <button type="button" className="issue-back" onClick={() => void selectIssue(repoId, null)}>
        ← Back to list
      </button>

      <div className="issue-detail-head">
        <h3 className="issue-detail-title">
          {issue.title} <span className="issue-number">#{issue.number}</span>
        </h3>
        <div className="issue-detail-meta">
          <span className={`issue-state-badge issue-state-${issue.state}`}>{issue.state}</span>
          <span className="dim">
            opened by {issue.user ?? "unknown"} · {relTime(issue.created_at)}
          </span>
        </div>
      </div>

      <div className="issue-detail-actions">
        <button
          type="button"
          disabled={busy === "update"}
          onClick={() => void updateIssue(repoId, issue.number, { state: issue.state === "open" ? "closed" : "open" })}
        >
          {issue.state === "open" ? "Close issue" : "Reopen issue"}
        </button>
        <button
          type="button"
          className="primary"
          onClick={() => {
            setPendingBranchIssue({ number: issue.number, title: issue.title });
            setTab("git");
          }}
        >
          Start branch
        </button>
        <a href={issue.html_url} target="_blank" rel="noopener noreferrer" className="pr-link">
          Open on GitHub
        </a>
      </div>

      <div className="issue-section">
        <div className="issue-sec-head">
          <span className="issue-sec-title">Description</span>
          {!editingBody && (
            <button
              type="button"
              className="git-sec-action"
              onClick={() => {
                setBodyDraft(issue.body);
                setEditingBody(true);
              }}
            >
              Edit
            </button>
          )}
        </div>
        {editingBody ? (
          <div className="issue-edit-body">
            <textarea rows={8} value={bodyDraft} onChange={(e) => setBodyDraft(e.target.value)} />
            <div className="commit-actions">
              <button type="button" onClick={() => setEditingBody(false)} disabled={busy === "update"}>
                Cancel
              </button>
              <button
                type="button"
                className="primary"
                disabled={busy === "update"}
                onClick={() =>
                  void updateIssue(repoId, issue.number, { body: bodyDraft }).then((ok) => {
                    if (ok) setEditingBody(false);
                  })
                }
              >
                Save
              </button>
            </div>
          </div>
        ) : issue.body.trim() ? (
          <div className="md-body">
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
              {issue.body}
            </ReactMarkdown>
          </div>
        ) : (
          <p className="dim">No description.</p>
        )}
      </div>

      <div className="issue-section">
        <span className="issue-sec-title">Labels</span>
        <LabelPicker
          options={meta?.labels ?? []}
          selected={issue.labels.map((l) => l.name)}
          disabled={busy === "update"}
          onToggle={(name) => {
            const current = issue.labels.map((l) => l.name);
            const next = current.includes(name) ? current.filter((n) => n !== name) : [...current, name];
            void updateIssue(repoId, issue.number, { labels: next });
          }}
        />
      </div>

      <div className="issue-section">
        <span className="issue-sec-title">Assignees</span>
        <AssigneePicker
          options={meta?.assignees ?? []}
          selected={issue.assignees.map((a) => a.login)}
          disabled={busy === "update"}
          onToggle={(login) => {
            const current = issue.assignees.map((a) => a.login);
            const next = current.includes(login) ? current.filter((n) => n !== login) : [...current, login];
            void updateIssue(repoId, issue.number, { assignees: next });
          }}
        />
      </div>

      <div className="issue-section">
        <span className="issue-sec-title">
          Comments{comments.length > 0 ? ` (${comments.length})` : ""}
        </span>
        {comments.length === 0 ? (
          <p className="dim">No comments yet.</p>
        ) : (
          <ul className="issue-comments">
            {comments.map((c) => (
              <li key={c.id} className="issue-comment">
                <div className="issue-comment-head">
                  {c.avatar_url && <img src={c.avatar_url} alt="" className="issue-avatar" />}
                  <strong>{c.user ?? "unknown"}</strong>
                  <span className="dim">{relTime(c.created_at)}</span>
                </div>
                <div className="md-body">
                  <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
                    {c.body}
                  </ReactMarkdown>
                </div>
              </li>
            ))}
          </ul>
        )}
        <textarea
          rows={3}
          placeholder="Write a comment…"
          value={comment}
          onChange={(e) => setComment(e.target.value)}
        />
        <div className="commit-actions">
          <button
            type="button"
            className="primary"
            disabled={busy === "comment" || !comment.trim()}
            onClick={() =>
              void addComment(repoId, issue.number, comment.trim()).then((ok) => {
                if (ok) setComment("");
              })
            }
          >
            {busy === "comment" ? "Posting…" : "Comment"}
          </button>
        </div>
      </div>
    </div>
  );
}
