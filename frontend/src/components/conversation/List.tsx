import { useState } from "react";
import { useChat } from "../../store/chat";

function relTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const s = Math.max(0, (Date.now() - then) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return new Date(iso).toLocaleDateString();
}

export default function ConversationList() {
  const {
    conversations,
    hasMore,
    activeId,
    search,
    setSearch,
    loadMore,
    openConversation,
    rename,
    remove,
  } = useChat();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

  async function act(fn: () => Promise<void>) {
    setErr(null);
    try {
      await fn();
    } catch (e) {
      setErr(message(e));
    }
  }

  function startRename(id: string, title: string) {
    setEditingId(id);
    setDraft(title);
  }

  async function commitRename() {
    if (!editingId) return;
    const id = editingId;
    const title = draft.trim();
    setEditingId(null);
    if (!title) return;
    await act(async () => {
      await rename(id, title);
    });
  }

  return (
    <div className="conv-panel">
      <input
        placeholder="Search conversations…"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        aria-label="Search conversations"
      />

      {err && <p className="repo-error-msg">{err}</p>}

      <ul className="conv-list">
        {conversations.length === 0 && (
          <li className="conv-empty">
            {search ? "No matches." : "No conversations yet — start one below."}
          </li>
        )}
        {conversations.map((c) => (
          <li
            key={c.id}
            className={`conv-item${c.id === activeId ? " active" : ""}`}
            onClick={() =>
              act(async () => {
                await openConversation(c.id);
              })
            }
          >
            {editingId === c.id ? (
              <input
                autoFocus
                className="conv-rename"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onClick={(e) => e.stopPropagation()}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void commitRename();
                  if (e.key === "Escape") setEditingId(null);
                }}
                aria-label="Rename conversation"
              />
            ) : (
              <div className="conv-row">
                <div className="conv-text">
                  <span className="conv-title">{c.title || "Untitled"}</span>
                  <span className="conv-meta">{relTime(c.updated_at)}</span>
                </div>
                <div className="conv-actions" onClick={(e) => e.stopPropagation()}>
                  <button
                    title="Rename"
                    aria-label={`Rename ${c.title}`}
                    onClick={() => startRename(c.id, c.title)}
                  >
                    ✎
                  </button>
                  <button
                    title="Delete"
                    aria-label={`Delete ${c.title}`}
                    className="danger"
                    onClick={() => {
                      if (window.confirm(`Delete "${c.title}" and all its messages?`)) {
                        void act(async () => {
                          await remove(c.id);
                        });
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

      {hasMore && (
        <button onClick={() => void loadMore().catch(() => undefined)}>Load older…</button>
      )}
    </div>
  );
}
