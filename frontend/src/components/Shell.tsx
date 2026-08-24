import { useEffect } from "react";
import { useAuth } from "../store/auth";
import { useModels } from "../store/models";
import { useChat } from "../store/chat";
import ChatPane from "./ChatPane";
import ConversationList from "./ConversationList";
import EditorPane from "./EditorPane";
import RepoPanel from "./RepoPanel";

export default function Shell() {
  const user = useAuth((s) => s.user);
  const logout = useAuth((s) => s.logout);
  const loadModels = useModels((s) => s.load);
  const loadConversations = useChat((s) => s.loadConversations);

  useEffect(() => {
    void loadModels().catch(() => undefined);
    void loadConversations().catch(() => undefined);
  }, [loadModels, loadConversations]);

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <span className="logo" aria-hidden>
            ◈
          </span>
          Qwen Chat
        </div>
        <div className="topbar-right">
          <span className="user-chip">{user}</span>
          <button onClick={() => void logout()}>
            Log out
          </button>
        </div>
      </header>

      <div className="panes">
        <nav className="pane pane-left">
          <div className="pane-title">Repositories</div>
          <RepoPanel />
          <div className="pane-title conv-divider">Conversations</div>
          <ConversationList />
        </nav>

        <main className="pane pane-center pane-chat">
          <ChatPane />
        </main>

        <aside className="pane pane-right">
          <EditorPane />
        </aside>
      </div>
    </div>
  );
}
