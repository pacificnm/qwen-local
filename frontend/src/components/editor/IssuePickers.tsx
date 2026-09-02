import type { CSSProperties } from "react";
import type { GitHubLabel, GitHubUser } from "../../lib/api";

/** Toggleable label chips (colored dot + name), sourced from the repo's
 * actual labels (`GET .../issues/meta`). */
export function LabelPicker({
  options,
  selected,
  onToggle,
  disabled,
}: {
  options: GitHubLabel[];
  selected: string[];
  onToggle: (name: string) => void;
  disabled?: boolean;
}) {
  if (options.length === 0) return <p className="dim">No labels on this repo.</p>;
  return (
    <div className="issue-chips">
      {options.map((lb) => {
        const active = selected.includes(lb.name);
        return (
          <button
            type="button"
            key={lb.name}
            className={`issue-label-chip${active ? " active" : ""}`}
            style={{ "--chip-color": `#${lb.color}` } as CSSProperties}
            disabled={disabled}
            onClick={() => onToggle(lb.name)}
            title={lb.name}
          >
            <span className="issue-label-dot" />
            {lb.name}
          </button>
        );
      })}
    </div>
  );
}

/** Toggleable assignee chips, sourced from the repo's assignable users
 * (`GET .../issues/meta`). */
export function AssigneePicker({
  options,
  selected,
  onToggle,
  disabled,
}: {
  options: GitHubUser[];
  selected: string[];
  onToggle: (login: string) => void;
  disabled?: boolean;
}) {
  if (options.length === 0) return <p className="dim">No assignable users found.</p>;
  return (
    <div className="issue-chips">
      {options.map((u) => {
        const active = selected.includes(u.login);
        return (
          <button
            type="button"
            key={u.login}
            className={`issue-assignee-chip${active ? " active" : ""}`}
            disabled={disabled}
            onClick={() => onToggle(u.login)}
          >
            <img src={u.avatar_url} alt="" className="issue-avatar" />
            {u.login}
          </button>
        );
      })}
    </div>
  );
}
