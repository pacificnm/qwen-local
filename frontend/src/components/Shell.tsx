import { useEffect } from "react";
import { useAuth } from "../store/auth";
import { useModels } from "../store/models";
import { useChat } from "../store/chat";
import { useProjects } from "../store/projects";
import ChatPane from "./ChatPane";
import ConversationList from "./ConversationList";
import EditorPane from "./EditorPane";
import ProjectNav from "./ProjectNav";
import ProjectRepoCard from "./ProjectRepoCard";

export default function Shell() {
  const user = useAuth((s) => s.user);
  const logout = useAuth((s) => s.logout);
  const loadModels = useModels((s) => s.load);
  const loadConversations = useChat((s) => s.loadConversations);
  const projects = useProjects((s) => s.projects);
  const activeId = useProjects((s) => s.activeId);
  const loadProjects = useProjects((s) => s.load);

  const activeProject = projects.find((p) => p.id === activeId) ?? null;

  useEffect(() => {
    void loadModels().catch(() => undefined);
    void loadProjects().catch(() => undefined);
  }, [loadModels, loadProjects]);

  // Conversations are project-scoped: (re)load whenever the folder changes.
  useEffect(() => {
    if (!activeProject) return;
    void loadConversations(activeProject.id).catch(() => undefined);
  }, [activeProject, loadConversations]);

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
          <ProjectNav />
          {activeProject && (
            <>
              <div className="pane-title conv-divider">{activeProject.name}</div>
              <ProjectRepoCard projectId={activeProject.id} />
              <ConversationList />
            </>
          )}
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
