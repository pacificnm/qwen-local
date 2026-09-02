import { useState } from "react";
import { useIssues } from "../../store/issues";
import { AssigneePicker, LabelPicker } from "./IssuePickers";

export function IssueCreateForm({ repoId, onDone }: { repoId: string; onDone: () => void }) {
  const meta = useIssues((s) => s.meta);
  const busy = useIssues((s) => s.busy);
  const createIssue = useIssues((s) => s.createIssue);

  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [labels, setLabels] = useState<string[]>([]);
  const [assignees, setAssignees] = useState<string[]>([]);

  return (
    <div className="issue-create-form">
      <div className="issue-sec-head">
        <span className="issue-sec-title">New issue</span>
        <button type="button" className="git-sec-action" onClick={onDone} disabled={busy === "create"}>
          Cancel
        </button>
      </div>
      <input
        autoFocus
        placeholder="Title"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        maxLength={300}
      />
      <textarea
        rows={6}
        placeholder="Describe the issue (Markdown supported)…"
        value={body}
        onChange={(e) => setBody(e.target.value)}
      />
      <div className="issue-section">
        <span className="issue-sec-title">Labels</span>
        <LabelPicker
          options={meta?.labels ?? []}
          selected={labels}
          disabled={busy === "create"}
          onToggle={(name) =>
            setLabels((cur) => (cur.includes(name) ? cur.filter((n) => n !== name) : [...cur, name]))
          }
        />
      </div>
      <div className="issue-section">
        <span className="issue-sec-title">Assignees</span>
        <AssigneePicker
          options={meta?.assignees ?? []}
          selected={assignees}
          disabled={busy === "create"}
          onToggle={(login) =>
            setAssignees((cur) => (cur.includes(login) ? cur.filter((n) => n !== login) : [...cur, login]))
          }
        />
      </div>
      <div className="commit-actions">
        <button
          type="button"
          className="primary"
          disabled={busy === "create" || !title.trim()}
          onClick={() =>
            void createIssue(repoId, { title: title.trim(), body: body.trim(), labels, assignees }).then(
              (issue) => {
                if (issue) onDone();
              },
            )
          }
        >
          {busy === "create" ? "Creating…" : "Create issue"}
        </button>
      </div>
    </div>
  );
}
