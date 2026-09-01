/** Shown in the Code / Git tabs when no repository is available. */
export function NoRepoHint() {
  return (
    <div className="editor-empty">
      <p className="dim">
        No repository yet — select a project that has a repo, or link one in
        the project card on the left.
      </p>
    </div>
  );
}
