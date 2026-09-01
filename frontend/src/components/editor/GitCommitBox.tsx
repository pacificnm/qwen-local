import { useState } from "react";
import { useEditor } from "../../store/editor";

export function GitCommitBox({ repoId, stagedCount }: { repoId: string; stagedCount: number }) {
  const gitBusy = useEditor((s) => s.gitBusy);
  const commitStaged = useEditor((s) => s.commitStaged);
  const [message, setMessage] = useState("");

  const busy = gitBusy === "commit";
  const disabled = !!gitBusy || stagedCount === 0 || !message.trim();

  return (
    <div className="git-section git-commit-sec">
      <h4 className="git-sec-title">
        Commit{stagedCount > 0 ? ` · ${stagedCount} staged` : " · nothing staged yet"}
      </h4>
      <textarea
        rows={3}
        maxLength={500}
        placeholder="commit message — e.g. feat(chunking): tree-sitter aware splits"
        value={message}
        disabled={!!gitBusy}
        onChange={(e) => setMessage(e.target.value)}
      />
      <div className="commit-actions">
        <button
          type="button"
          className="primary"
          disabled={disabled}
          onClick={() =>
            void commitStaged(repoId, message.trim()).then((ok) => {
              if (ok) setMessage("");
            })
          }
        >
          {busy ? "Committing…" : "Commit to current branch"}
        </button>
      </div>
    </div>
  );
}
