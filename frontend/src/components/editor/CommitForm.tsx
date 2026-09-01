import { useEffect, useState } from "react";
import { slugify } from "../../lib/slugify";
import { useEditor } from "../../store/editor";

/** Commits the focused main-pane tab (the file open in the center editor). */
export function CommitForm({ repoId }: { repoId: string }) {
  const editor = useEditor();
  const focused = useEditor((s) => s.editorTabs.find((t) => t.id === s.activeTabId) ?? null);
  const fileOpen = !!focused && focused.repoId === repoId && !!focused.path;

  const [branch, setBranch] = useState(() =>
    focused?.path ? `qwen-assist/${slugify(focused.path)}` : "qwen-assist/edits",
  );
  const [message, setMessage] = useState("");
  const [openPr, setOpenPr] = useState(true);
  const [prTitle, setPrTitle] = useState("");
  const [prBody, setPrBody] = useState("");

  useEffect(() => {
    setBranch(focused?.path ? `qwen-assist/${slugify(focused.path)}` : "qwen-assist/edits");
  }, [focused?.path]);

  const disabled =
    editor.committing || !fileOpen || !message.trim() || (focused?.working ?? "") === "";

  return (
    <div className="commit-form">
      <div className="commit-grid">
        <label>
          File
          {fileOpen ? (
            <code className="commit-file">{focused?.path}</code>
          ) : (
            <span className="commit-file-muted">open a file tab in the center pane first</span>
          )}
        </label>
        <label>
          Branch
          <input value={branch} onChange={(e) => setBranch(e.target.value)} spellCheck={false} />
        </label>
        <label>
          Commit message
          <input
            value={message}
            placeholder="fix(auth): correct PAT scope check"
            onChange={(e) => setMessage(e.target.value)}
            maxLength={500}
          />
        </label>
      </div>
      <label className="commit-pr-check">
        <input type="checkbox" checked={openPr} onChange={(e) => setOpenPr(e.target.checked)} />
        Open a pull request
      </label>
      {openPr && (
        <div className="commit-pr-extra">
          <input
            value={prTitle}
            placeholder="PR title (defaults to commit message)"
            onChange={(e) => setPrTitle(e.target.value)}
            maxLength={300}
          />
          <textarea
            rows={3}
            value={prBody}
            placeholder="PR body (optional — a summary + file list is generated)"
            onChange={(e) => setPrBody(e.target.value)}
          />
        </div>
      )}
      <div className="commit-actions">
        <button
          type="button"
          className="primary"
          disabled={disabled}
          onClick={() =>
            void editor
              .commit({
                repoId,
                filePath: focused?.path ?? "",
                message: message.trim(),
                branch: branch.trim() || "qwen-assist/edits",
                openPr,
                prTitle: prTitle.trim() || null,
                prBody: prBody.trim() || null,
              })
              .then((ok) => {
                if (ok) {
                  setMessage("");
                  setPrTitle("");
                  setPrBody("");
                }
              })
          }
        >
          {editor.committing ? "Committing…" : "↑ Commit to GitHub"}
        </button>
      </div>
    </div>
  );
}
