import type { GitFileEntry } from "../../lib/api";

function statusClass(code: string): string {
  if (code.includes("?")) return "git-st-new";
  if (code.includes("A")) return "git-st-add";
  if (code.includes("D")) return "git-st-del";
  if (code.includes("R")) return "git-st-ren";
  return "git-st-mod";
}

export function GitFileRow({
  entry,
  action,
  onAction,
  disabled,
}: {
  entry: GitFileEntry;
  action: string;
  onAction: () => void;
  disabled: boolean;
}) {
  return (
    <li className="git-file">
      <span className={`git-status ${statusClass(entry.status)}`}>{entry.status}</span>
      <span
        className="git-dpath"
        title={entry.old_path ? `${entry.old_path} → ${entry.path}` : entry.path}
      >
        {entry.old_path && <span className="git-oldpath">{entry.old_path} → </span>}
        {entry.path}
      </span>
      <button type="button" className="git-row-btn" disabled={disabled} onClick={onAction}>
        {action}
      </button>
    </li>
  );
}
