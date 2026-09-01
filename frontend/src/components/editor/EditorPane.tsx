import { useEffect } from "react";
import { useEditor } from "../../store/editor";
import { useProjects } from "../../store/projects";
import { CodeTab } from "./CodeTab";
import { GitTab } from "./GitTab";

export default function EditorPane() {
  const tab = useEditor((s) => s.tab);
  const activeId = useProjects((s) => s.activeId);
  const activeRepoId = useProjects(
    (s) => s.projects.find((p) => p.id === s.activeId)?.repo?.id ?? null,
  );

  // Selecting a project defaults the tabs to that repo on the Code tab:
  // close all open editor tabs and preload its tree + git snapshot.
  useEffect(() => {
    const ed = useEditor.getState();
    ed.reset();
    if (activeRepoId) {
      void ed.loadTree(activeRepoId);
      void ed.loadGit(activeRepoId);
    }
  }, [activeId, activeRepoId]);

  const setTab = (t: "code" | "git") => useEditor.getState().setTab(t);

  return (
    <div className="editor-pane">
      <div className="rtabbar" role="tablist" aria-label="Panel">
        <button
          role="tab"
          id="rtab-code"
          aria-selected={tab === "code"}
          aria-controls="rtab-panes"
          className={tab === "code" ? "active" : ""}
          onClick={() => setTab("code")}
        >
          Code
        </button>
        <button
          role="tab"
          id="rtab-git"
          aria-selected={tab === "git"}
          aria-controls="rtab-panes"
          className={tab === "git" ? "active" : ""}
          onClick={() => setTab("git")}
        >
          Git
        </button>
      </div>

      <div className="editor-scroll" id="rtab-panes">
        <section className={tab === "code" ? "tabpane" : "tabpane hidden"}>
          <CodeTab />
        </section>
        <section className={tab === "git" ? "tabpane" : "tabpane hidden"}>
          <GitTab />
        </section>
      </div>
    </div>
  );
}
