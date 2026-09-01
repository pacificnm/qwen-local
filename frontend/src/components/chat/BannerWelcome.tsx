import { useChat } from "../../store/chat";
import { useRepos } from "../../store/repos";

/**
 * Shown when the conversation has no messages yet and no turn is in flight.
 * Displays a hint about RAG based on whether a repo is attached.
 */
export function BannerWelcome() {
  const { messages, assistantText, phase } = useChat();
  const conv = useChat((s) => s.conversations.find((c) => c.id === s.activeId)) ?? null;
  const { repos } = useRepos();

  if (messages.length > 0 || assistantText || phase === "thinking") return null;

  const repoName = conv?.repo_name ?? repos.find((r) => r.id === conv?.repo_id)?.github_full_name;

  return (
    <div className="chat-welcome">
      <p>Ask anything about your codebase.</p>
      {repoName ? (
        <p className="dim">
          This conversation reads <strong>{repoName}</strong> (RAG top-8).
        </p>
      ) : (
        <p className="dim">
          This project has no repository yet — attach one in the left pane to enable RAG.
        </p>
      )}
    </div>
  );
}
