import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { useAuth } from "../store/auth";
import { useModels } from "../store/models";
import { useChat } from "../store/chat";
import { useProjects } from "../store/projects";
import ConversationList from "./ConversationList";
import MainPane from "./MainPane";
import EditorPane from "./EditorPane";
import ProjectNav from "./ProjectNav";
import ProjectSettings from "./ProjectSettings";

const PANE_KEY = "qc.paneWidths";
const DEFAULTS = { left: 240, right: 360 };
const MIN_WIDTH = 200;
const MAX_WIDTH = 640;
const CENTER_MIN = 280;
const HANDLE = 6;

type Side = "left" | "right";
type Widths = { left: number; right: number };

function readWidths(): Widths {
  try {
    const raw = localStorage.getItem(PANE_KEY);
    if (raw) {
      const p = JSON.parse(raw) as Partial<Widths>;
      const num = (v: unknown, d: number) => (typeof v === "number" && Number.isFinite(v) ? v : d);
      return { left: num(p.left, DEFAULTS.left), right: num(p.right, DEFAULTS.right) };
    }
  } catch {
    // corrupted store — fall back to defaults
  }
  return { ...DEFAULTS };
}

export default function Shell() {
  const user = useAuth((s) => s.user);
  const logout = useAuth((s) => s.logout);
  const loadModels = useModels((s) => s.load);
  const loadConversations = useChat((s) => s.loadConversations);
  const newConversation = useChat((s) => s.newConversation);
  const projects = useProjects((s) => s.projects);
  const activeId = useProjects((s) => s.activeId);
  const loadProjects = useProjects((s) => s.load);

  const activeProject = projects.find((p) => p.id === activeId) ?? null;

  const [widths, setWidths] = useState<Widths>(readWidths);
  const widthsRef = useRef(widths);
  widthsRef.current = widths;

  useEffect(() => {
    void loadModels().catch(() => undefined);
    void loadProjects().catch(() => undefined);
  }, [loadModels, loadProjects]);

  // Conversations are project-scoped: (re)load whenever the folder changes.
  useEffect(() => {
    if (!activeProject) return;
    void loadConversations(activeProject.id).catch(() => undefined);
  }, [activeProject, loadConversations]);

  // Persist widths; re-clamp when the window shrinks so the center pane stays usable.
  const clampWidth = (side: Side, w: number) => {
    const s = widthsRef.current;
    const avail = window.innerWidth - HANDLE * 2;
    const other = side === "left" ? s.right : s.left;
    const max = Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, avail - CENTER_MIN - other));
    return Math.round(Math.min(max, Math.max(MIN_WIDTH, w)));
  };

  useEffect(() => {
    localStorage.setItem(PANE_KEY, JSON.stringify(widths));
    const { left, right } = widthsRef.current;
    const avail = window.innerWidth - HANDLE * 2;
    const next = {
      left: Math.min(left, Math.max(MIN_WIDTH, avail - CENTER_MIN - right)),
      right: Math.min(right, Math.max(MIN_WIDTH, avail - CENTER_MIN - left)),
    };
    if (next.left !== widths.left || next.right !== widths.right) setWidths(next);
  }, [widths]);

  useEffect(() => {
    const onResize = () => {
      const { left, right } = widthsRef.current;
      const avail = window.innerWidth - HANDLE * 2;
      const next = {
        left: Math.min(left, Math.max(MIN_WIDTH, avail - CENTER_MIN - right)),
        right: Math.min(right, Math.max(MIN_WIDTH, avail - CENTER_MIN - left)),
      };
      if (next.left !== left || next.right !== right) setWidths(next);
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  function setSideWidth(side: Side, w: number) {
    setWidths({ ...widthsRef.current, [side]: clampWidth(side, w) });
  }

  function startDrag(side: Side, e: PointerEvent<HTMLDivElement>) {
    e.preventDefault();
    document.body.classList.add("pane-dragging");
    const start = e.clientX;
    const startW = side === "left" ? widthsRef.current.left : widthsRef.current.right;
    const onMove = (ev: globalThis.PointerEvent) => {
      const delta = ev.clientX - start;
      setSideWidth(side, side === "left" ? startW + delta : startW - delta);
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      document.body.classList.remove("pane-dragging");
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  }

  function onHandleKey(side: Side, e: KeyboardEvent<HTMLDivElement>) {
    const step = e.shiftKey ? 48 : 16;
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      e.preventDefault();
      const dir = e.key === "ArrowLeft" ? -1 : 1;
      const w = side === "left" ? widthsRef.current.left : widthsRef.current.right;
      setSideWidth(side, side === "left" ? w + step * dir : w - step * dir);
    } else if (e.key === "Home") {
      setSideWidth(side, DEFAULTS[side]);
    }
  }

  // Plain JSX factory (not a nested component): keeps a stable element
  // identity so the handle node isn't remounted mid-drag.
  function resizer(side: Side) {
    return (
      <div
        className="pane-resizer"
        role="separator"
        aria-orientation="vertical"
        aria-label={side === "left" ? "Resize left panel" : "Resize right panel"}
        title="Drag to resize · double-click to reset"
        tabIndex={0}
        onPointerDown={(e) => startDrag(side, e)}
        onDoubleClick={() => setSideWidth(side, DEFAULTS[side])}
        onKeyDown={(e) => onHandleKey(side, e)}
      />
    );
  }

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

      <div
        className="panes"
        style={{
          gridTemplateColumns: `${widths.left}px ${HANDLE}px minmax(${CENTER_MIN}px, 1fr) ${HANDLE}px ${widths.right}px`,
        }}
      >
        <nav className="pane pane-left">
          <div className="pane-left-scroll">
            <ProjectNav />
            {activeProject && (
              <>
                <div className="proj-head conv-divider">
                  <span>{activeProject.name}</span>
                  <button
                    className="proj-toggle"
                    title="New conversation"
                    aria-label="New conversation"
                    onClick={() => void newConversation().catch(() => undefined)}
                  >
                    +
                  </button>
                </div>
                <ConversationList />
              </>
            )}
          </div>
          {activeProject && (
            <ProjectSettings projectId={activeProject.id} projectName={activeProject.name} />
          )}
        </nav>

        {resizer("left")}

        <MainPane />

        {resizer("right")}

        <aside className="pane pane-right">
          <EditorPane />
        </aside>
      </div>
    </div>
  );
}
