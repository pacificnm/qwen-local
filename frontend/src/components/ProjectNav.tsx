import { useState } from "react";
import { useProjects } from "../store/projects";

/** Left-pane "Projects" area: folder list + inline new-project form.
 *  Selecting a folder reveals its repo (ProjectRepoCard) and its
 *  conversations (ConversationList) in the parent Shell. */
export default function ProjectNav() {
  const { projects, activeId, loaded, busy, error, setActive, create, rename, remove } =
    useProjects();
  const [showForm, setShowForm] = useState(false);
  const [draftName, setDraftName] = useState("");
  const [draftRepo, setDraftRepo] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  function message(e: unknown): string {
    return e instanceof Error ? e.message : String(e);
  }

  async function submitNew() {
    const p = await create(draftName, draftRepo || null);
    if (p) {
      setDraftName("");
      setDraftRepo("");
      setShowForm(false);
    }
  }

  async function commitRename(id: string) {
    setEditingId(null);
    const title = draft.trim();
    if (!title) return;
    try {
      await rename(id, title);
    } catch (e) {
      window.alert(message(e));
    }
  }

  return (
    <div className="proj-panel">
      <div className="proj-head">
        <span>Projects</span>
        <button
          className="proj-toggle"
          onClick={() => setShowForm((v) => !v)}
          title={showForm ? "Hide" : "New project"}
        >
          {showForm ? "×" : "+"}
        </button>
      </div>

      {showForm && (
        <form
          className="proj-new"
          onSubmit={(e) => {
            e.preventDefault();
            void submitNew();
          }}
        >
          <label>
            Name
            <input
              placeholder="e.g. My app"
              value={draftName}
              onChange={(e) => setDraftName(e.target.value)}
              maxLength={255}
            />
          </label>
          <label>
            GitHub repository <em className="dim">(optional)</em>
            <input
              placeholder="owner/name — attach + index"
              value={draftRepo}
              onChange={(e) => setDraftRepo(e.target.value)}
              spellCheck={false}
              autoComplete="off"
            />
          </label>
          <div className="proj-new-actions">
            <button
              className="primary"
              type="submit"
              disabled={busy || (!draftName.trim() && !draftRepo.trim())}
            >
              {busy ? "Working…" : "Create"}
            </button>
            <button type="button" onClick={() => setShowForm(false)}>
              Cancel
            </button>
          </div>
        </form>
      )}

      {error && <p className="repo-error">{error}</p>}

      {!loaded ? (
        <p className="repo-hint">Loading projects…</p>
      ) : (
        <ul className="proj-list">
          {projects.map((p) => (
            <li
              key={p.id}
              className={`proj-item${p.id === activeId ? " active" : ""}`}
              onClick={() => setActive(p.id)}
            >
              {editingId === p.id ? (
                <input
                  autoFocus
                  className="proj-rename"
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onClick={(e) => e.stopPropagation()}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void commitRename(p.id);
                    if (e.key === "Escape") setEditingId(null);
                  }}
                  aria-label="Rename project"
                />
              ) : (
                <div className="proj-row">
                  <span className="proj-folder" aria-hidden>
                    📁
                  </span>
                  <div className="proj-text">
                    <span className="proj-name">{p.name}</span>
                    <span className="proj-meta">
                      {p.repo ? p.repo.github_full_name : "no repo"} · {p.conversation_count} chats
                    </span>
                  </div>
                  <div className="proj-actions" onClick={(e) => e.stopPropagation()}>
                    <button
                      title="Rename"
                      aria-label={`Rename ${p.name}`}
                      onClick={() => {
                        setEditingId(p.id);
                        setDraft(p.name);
                      }}
                    >
                      ✎
                    </button>
                    <button
                      title="Delete"
                      aria-label={`Delete ${p.name}`}
                      className="danger"
                      onClick={() => {
                        if (
                          window.confirm(
                            `Delete project "${p.name}" and ALL its conversations?` +
                              (p.repo ? " (the repository itself is kept.)" : ""),
                          )
                        ) {
                          void remove(p.id).catch((e: unknown) => window.alert(message(e)));
                        }
                      }}
                    >
                      ✕
                    </button>
                  </div>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
