import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
} from "react";
import "@vscode/codicons/dist/codicon.css";
import { fileIconFor } from "../lib/fileIcons";
import {
  createRepoDir,
  createRepoFile,
  deleteRepoFile,
  getRepoFile,
  renameRepoFile,
} from "../lib/api";
import { copyText, readText } from "../lib/clipboard";
import { addFileToChat } from "../lib/fileMentionBus";
import { useEditor } from "../store/editor";
import { useProjects } from "../store/projects";
import { useRepos } from "../store/repos";
import { BadgeChip, type Badge } from "./file/BadgeChip";

type Node =
  | { kind: "dir"; name: string; path: string; children: Node[] }
  | { kind: "file"; name: string; path: string };

type MenuState = { x: number; y: number; path: string; kind: "file" | "dir" };
type Renaming = { path: string; value: string };
type MenuItem = { label: string; danger?: boolean; action: () => void };

function parentDirOf(path: string): string {
  const i = path.lastIndexOf("/");
  return i > 0 ? path.slice(0, i) : "";
}

/* --- git status badges, VSCode's explorer letters and (dark-theme) colors --- */
type GitEntry = { path: string; status: string };
const EMPTY_ENTRIES: GitEntry[] = [];
const BADGES: Record<string, Badge> = {
  M: { letter: "M", color: "#73cffe" },
  U: { letter: "U", color: "#81b88b" },
  A: { letter: "A", color: "#81b88b" },
  D: { letter: "D", color: "#c74e39" },
  C: { letter: "C", color: "#e46767" },
  R: { letter: "R", color: "#73cffe" },
};
const RANK: Record<string, number> = { C: 5, U: 4, M: 3, A: 2, D: 2, R: 1 };

function statusFromCode(code: string): Badge | null {
  if (code === "??" || code === "?") return BADGES.U;
  if (code.includes("U")) return BADGES.C;
  if (code.includes("R")) return BADGES.R;
  if (code.includes("M")) return BADGES.M;
  if (code.includes("A")) return BADGES.A;
  if (code.includes("D")) return BADGES.D;
  return null;
}

/** Badge for a row: the file's own status, or the most prominent descendant status for a folder. */
function statusFor(entries: GitEntry[], path: string, isDir: boolean): Badge | null {
  let best: Badge | null = null;
  for (const e of entries) {
    const hit = isDir ? e.path.startsWith(path + "/") : e.path === path;
    if (!hit) continue;
    const b = statusFromCode(e.status);
    if (b && (!best || RANK[b.letter] > RANK[best.letter])) best = b;
  }
  return best;
}

function buildTree(paths: string[]): Node[] {
  const root: Node[] = [];
  for (const p of paths) {
    const parts = p.split("/");
    let level = root;
    let acc = "";
    for (let i = 0; i < parts.length; i++) {
      acc = acc ? `${acc}/${parts[i]}` : parts[i];
      const isLast = i === parts.length - 1;
      let node = level.find(
        (n) => n.name === parts[i] && (isLast ? n.kind === "file" : n.kind === "dir"),
      );
      if (!node) {
        node = isLast
          ? { kind: "file", name: parts[i], path: acc }
          : { kind: "dir", name: parts[i], path: acc, children: [] };
        level.push(node);
      }
      if (!isLast) level = (node as Extract<Node, { kind: "dir" }>).children;
    }
  }
  const sort = (nodes: Node[]) => {
    nodes.sort((a, b) =>
      a.kind === b.kind ? a.name.localeCompare(b.name) : a.kind === "dir" ? -1 : 1,
    );
    for (const n of nodes) if (n.kind === "dir") sort(n.children);
  };
  sort(root);
  return root;
}

/** All directory paths containing at least one file matching `filter`. */
function ancestorsOfMatches(paths: string[], needle: string): Set<string> {
  const out = new Set<string>();
  for (const p of paths) {
    if (!p.toLowerCase().includes(needle)) continue;
    const parts = p.split("/");
    let acc = "";
    for (let i = 0; i < parts.length - 1; i++) {
      acc = acc ? `${acc}/${parts[i]}` : parts[i];
      out.add(acc);
    }
  }
  return out;
}

function RenameInput({
  value,
  onEdit,
  onCommit,
  onCancel,
}: {
  value: string;
  onEdit: (value: string) => void;
  onCommit: () => void;
  onCancel: () => void;
}) {
  return (
    <input
      className="tree-rename"
      value={value}
      autoFocus
      spellCheck={false}
      onChange={(e) => onEdit(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          onCommit();
        } else if (e.key === "Escape") {
          e.preventDefault();
          onCancel();
        }
      }}
      onBlur={onCommit}
      onClick={(e) => e.stopPropagation()}
    />
  );
}

function TreeRows({
  nodes,
  depth,
  expanded,
  selectedPath,
  dirty,
  renaming,
  onRenameEdit,
  onRenameCommit,
  onRenameCancel,
  onOpenMenu,
  onToggle,
  onPick,
}: {
  nodes: Node[];
  depth: number;
  expanded: Set<string>;
  selectedPath: string | null;
  dirty: GitEntry[];
  renaming: Renaming | null;
  onRenameEdit: (value: string) => void;
  onRenameCommit: () => void;
  onRenameCancel: () => void;
  onOpenMenu: (e: ReactMouseEvent, path: string, kind: "file" | "dir") => void;
  onToggle: (path: string) => void;
  onPick: (path: string) => void;
}) {
  return (
    <>
      {nodes.map((n) => {
        const pad = { paddingLeft: `${0.4 + depth * 0.75}rem` };
        if (n.kind === "file") {
          const badge = statusFor(dirty, n.path, false);
          const ic = fileIconFor(n.name);
          if (renaming && renaming.path === n.path) {
            return (
              <div key={n.path} className="tree-row tree-file renaming-row" style={pad}>
                <span className="ficon" style={{ color: ic.color }} aria-hidden>
                  {ic.c}
                </span>
                <RenameInput
                  value={renaming.value}
                  onEdit={onRenameEdit}
                  onCommit={onRenameCommit}
                  onCancel={onRenameCancel}
                />
                {badge && <BadgeChip b={badge} />}
              </div>
            );
          }
          return (
            <button
              key={n.path}
              type="button"
              className={n.path === selectedPath ? "tree-row tree-file active" : "tree-row tree-file"}
              style={pad}
              onClick={() => onPick(n.path)}
              onContextMenu={(e) => onOpenMenu(e, n.path, "file")}
              title={n.path}
            >
              <span className="ficon" style={{ color: ic.color }} aria-hidden>
                {ic.c}
              </span>
              <span
                className="tree-name"
                style={
                  badge && n.path !== selectedPath ? { color: badge.color } : undefined
                }
              >
                {n.name}
              </span>
              {badge && <BadgeChip b={badge} />}
            </button>
          );
        }
        const open = expanded.has(n.path);
        const badge = statusFor(dirty, n.path, true);
        if (renaming && renaming.path === n.path) {
          return (
            <div key={n.path} className="tree-row tree-dir renaming-row" style={pad}>
              <span className="tree-caret" aria-hidden>
                <i
                  className={`codicon ${open ? "codicon-chevron-down" : "codicon-chevron-right"}`}
                />
              </span>
              <span className="ficon ficon-folder" aria-hidden>
                <i className="codicon codicon-folder" />
              </span>
              <RenameInput
                value={renaming.value}
                onEdit={onRenameEdit}
                onCommit={onRenameCommit}
                onCancel={onRenameCancel}
              />
              {badge && <BadgeChip b={badge} />}
            </div>
          );
        }
        return (
          <div key={n.path}>
            <button
              type="button"
              className="tree-row tree-dir"
              style={pad}
              onClick={() => onToggle(n.path)}
              onContextMenu={(e) => onOpenMenu(e, n.path, "dir")}
              aria-expanded={open}
              title={n.path}
            >
              <span className="tree-caret" aria-hidden>
                <i
                  className={`codicon ${open ? "codicon-chevron-down" : "codicon-chevron-right"}`}
                />
              </span>
              <span className="ficon ficon-folder" aria-hidden>
                <i className={`codicon ${open ? "codicon-folder-opened" : "codicon-folder"}`} />
              </span>
              <span className="tree-name" style={badge ? { color: badge.color } : undefined}>
                {n.name}
              </span>
              {badge && <BadgeChip b={badge} />}
            </button>
            {open && (
              <TreeRows
                nodes={n.children}
                depth={depth + 1}
                expanded={expanded}
                selectedPath={selectedPath}
                dirty={dirty}
                renaming={renaming}
                onRenameEdit={onRenameEdit}
                onRenameCommit={onRenameCommit}
                onRenameCancel={onRenameCancel}
                onOpenMenu={onOpenMenu}
                onToggle={onToggle}
                onPick={onPick}
              />
            )}
          </div>
        );
      })}
    </>
  );
}

export default function FileTree({ repoId }: { repoId: string }) {
  const treePaths = useEditor((s) => s.treePaths);
  const treeLoading = useEditor((s) => s.treeLoading);
  const treeError = useEditor((s) => s.treeError);
  const selected = useEditor((s) => {
    const t = s.editorTabs.find((x) => x.id === s.activeTabId);
    return t && t.repoId === repoId ? (t.path ?? null) : null;
  });
  const activeProject = useProjects((s) => s.projects.find((p) => p.id === s.activeId) ?? null);
  const repos = useRepos((s) => s.repos);
  // Git status for badges — only when this tree's repo is the one we have status for.
  const git = useEditor((s) => (s.gitRepoId === repoId ? s.git : null));
  const dirty = git?.dirty ?? EMPTY_ENTRIES;

  const repoName =
    repos.find((r) => r.id === repoId)?.github_full_name ??
    activeProject?.repo?.github_full_name;

  const [filter, setFilter] = useState("");
  const [expanded, setExpanded] = useState<Set<string> | null>(null);
  const [menu, setMenu] = useState<MenuState | null>(null);
  const [renaming, setRenaming] = useState<Renaming | null>(null);
  // Guards against a double commit when Enter and the following blur both fire.
  const renameGuard = useRef<string | null>(null);

  const rootNode = useMemo(() => buildTree(treePaths), [treePaths]);
  // Nothing is expanded by default — users open folders themselves.
  const defaultExpanded = useMemo(() => new Set<string>(), []);

  // No filter: user-expanded set (starts empty).
  // With a filter: auto-expand every ancestor of a match.
  const needle = filter.trim().toLowerCase();
  const effExpanded = useMemo(
    () => (needle ? ancestorsOfMatches(treePaths, needle) : (expanded ?? defaultExpanded)),
    [needle, treePaths, expanded, defaultExpanded],
  );

  const visibleNodes = useMemo(() => {
    if (!needle) return rootNode;
    const walk = (nodes: Node[]): Node[] => {
      const out: Node[] = [];
      for (const n of nodes) {
        if (n.kind === "file") {
          if (n.name.toLowerCase().includes(needle)) out.push(n);
        } else {
          const kids = walk(n.children);
          if (kids.length > 0) out.push({ ...n, children: kids });
        }
      }
      return out;
    };
    return walk(rootNode);
  }, [rootNode, needle]);

  // Tree clicks open (or re-focus) a file tab in the main pane.
  const onPick = (path: string) => {
    void useEditor.getState().openFile(repoId, path);
  };

  const onToggle = (path: string) => {
    setExpanded((prev) => {
      const next = new Set(prev ?? defaultExpanded);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  /* --- context menu --- */

  const refresh = () => {
    void useEditor.getState().loadTree(repoId);
    void useEditor.getState().loadGit(repoId);
  };

  const openMenu = (e: ReactMouseEvent, path: string, kind: "file" | "dir") => {
    e.preventDefault();
    e.stopPropagation();
    setRenaming(null);
    // Keep the menu fully on-screen.
    const x = Math.max(8, Math.min(e.clientX, window.innerWidth - 218));
    const y = Math.max(8, Math.min(e.clientY, window.innerHeight - 300));
    setMenu({ x, y, path, kind });
  };

  useEffect(() => {
    if (!menu) return;
    const close = (e: Event) => {
      // A click inside the menu should let the item's own click handler run.
      if (e.target instanceof Element && e.target.closest(".ctxmenu")) return;
      setMenu(null);
    };
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") setMenu(null);
    };
    document.addEventListener("mousedown", close, true);
    document.addEventListener("keydown", onKey, true);
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close, true);
    return () => {
      document.removeEventListener("mousedown", close, true);
      document.removeEventListener("keydown", onKey, true);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close, true);
    };
  }, [menu]);

  const doCopyFile = async (path: string) => {
    try {
      const f = await getRepoFile(repoId, path);
      if (!(await copyText(f.content))) {
        alert("The clipboard is unavailable in this browser context.");
      }
    } catch (err) {
      alert(err instanceof Error ? err.message : "Copy failed");
    }
  };

  /** Create a new file from the clipboard text (or a prompt fallback). */
  const doPaste = async (dir: string) => {
    let text = await readText();
    if (text === null) {
      text = window.prompt(
        "Clipboard read is unavailable here. Paste the file contents (Ctrl+V), or leave blank to create an empty file:",
      );
      if (text === null) return; // user cancelled
    }
    const existing = new Set(treePaths);
    const base = dir ? `${dir}/` : "";
    let name = "paste.txt";
    for (let i = 2; existing.has(`${base}${name}`); i += 1) name = `paste-${i}.txt`;
    try {
      await createRepoFile(repoId, `${base}${name}`, text);
      refresh();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Paste failed");
    }
  };

  /** Ask for a name, then create a new empty file in `dir` ("" = repo root). */
  const doNewFile = (dir: string) => {
    const name = window.prompt("New file name (e.g. notes.md):")?.trim();
    if (!name) return;
    if (name === "." || name === ".." || name.includes("/")) {
      alert("That file name is not valid — no “/” characters allowed.");
      return;
    }
    const target = dir ? `${dir}/${name}` : name;
    void (async () => {
      try {
        await createRepoFile(repoId, target, "");
        refresh();
      } catch (err) {
        alert(err instanceof Error ? err.message : "Create failed");
      }
    })();
  };

  /** Ask for a name, then create a new folder in `dir` ("" = repo root).
   * Git cannot track an empty folder, so the backend plants a `.gitkeep`. */
  const doNewFolder = (dir: string) => {
    const name = window.prompt("New folder name (e.g. docs):")?.trim();
    if (!name) return;
    if (name === "." || name === ".." || name.includes("/")) {
      alert("That folder name is not valid — no “/” characters allowed.");
      return;
    }
    const target = dir ? `${dir}/${name}` : name;
    void (async () => {
      try {
        await createRepoDir(repoId, target);
        refresh();
      } catch (err) {
        alert(err instanceof Error ? err.message : "Create failed");
      }
    })();
  };

  const doDelete = async (path: string, kind: "file" | "dir") => {
    const what = kind === "dir" ? "this folder and everything inside it" : "this file";
    if (!window.confirm(`Delete ${what}?\n${path}`)) return;
    try {
      await deleteRepoFile(repoId, path);
      setRenaming(null);
      refresh();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Delete failed");
    }
  };

  const copyPath = async (path: string) => {
    const full = repoName ? `${repoName}/${path}` : path;
    if (!(await copyText(full))) alert("The clipboard is unavailable in this browser context.");
  };

  const copyRel = async (path: string) => {
    if (!(await copyText(path))) alert("The clipboard is unavailable in this browser context.");
  };

  const startRename = (path: string) => {
    renameGuard.current = null;
    setRenaming({ path, value: path.split("/").pop() ?? path });
  };

  const commitRename = () => {
    const r = renaming;
    setRenaming(null);
    if (!r) return;
    if (renameGuard.current === r.path) return; // blur right after Enter — already committed
    const name = r.value.trim();
    if (!name || name === "." || name === ".." || name.includes("/")) {
      alert("That file name is not valid.");
      return;
    }
    if (name === (r.path.split("/").pop() ?? "")) return;
    renameGuard.current = r.path;
    const dir = parentDirOf(r.path);
    void (async () => {
      try {
        await renameRepoFile(repoId, r.path, dir ? `${dir}/${name}` : name);
        refresh();
      } catch (err) {
        renameGuard.current = null; // allow retrying via a fresh rename
        alert(err instanceof Error ? err.message : "Rename failed");
      }
    })();
  };

  const buildMenuItems = (m: MenuState): MenuItem[] => {
    if (m.kind === "file") {
      const parent = parentDirOf(m.path);
      return [
        { label: "Open", action: () => onPick(m.path) },
        { label: "New File", action: () => doNewFile(parent) },
        { label: "New Folder", action: () => doNewFolder(parent) },
        { label: "Rename", action: () => startRename(m.path) },
        { label: "Delete", danger: true, action: () => doDelete(m.path, "file") },
        { label: "Copy", action: () => doCopyFile(m.path) },
        { label: "Paste", action: () => doPaste(parent) },
        { label: "Copy Path", action: () => copyPath(m.path) },
        { label: "Copy Relative Path", action: () => copyRel(m.path) },
        { label: "Add to Chat", action: () => addFileToChat(m.path) },
      ];
    }
    const open = effExpanded.has(m.path);
    return [
      { label: open ? "Close" : "Open", action: () => onToggle(m.path) },
      { label: "New File", action: () => doNewFile(m.path) },
      { label: "New Folder", action: () => doNewFolder(m.path) },
      { label: "Rename", action: () => startRename(m.path) },
      { label: "Delete", danger: true, action: () => doDelete(m.path, "dir") },
      { label: "Paste", action: () => doPaste(m.path) },
      { label: "Copy Path", action: () => copyPath(m.path) },
      { label: "Copy Relative Path", action: () => copyRel(m.path) },
    ];
  };

  return (
    <div className="filetree">
      <div className="filetree-head">
        <div className="filetree-repo" title={repoName}>
          {repoName ?? "—"}
        </div>
        <button
          type="button"
          className="filetree-refresh"
          title="Reload file list"
          disabled={treeLoading}
          onClick={() => void useEditor.getState().loadTree(repoId)}
        >
          ↻
        </button>
      </div>
      <input
        className="filetree-filter"
        placeholder="Filter files by name…"
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        spellCheck={false}
      />
      {treeError && <p className="banner banner-error">{treeError}</p>}
      <div className="filetree-body">
        {treeLoading && <p className="dim">Loading files…</p>}
        {!treeLoading && treePaths.length === 0 && (
          <p className="dim">No files — sync the repository first.</p>
        )}
        {!treeLoading && treePaths.length > 0 && visibleNodes.length === 0 && (
          <p className="dim">No files match “{filter.trim()}”.</p>
        )}
        <TreeRows
          nodes={visibleNodes}
          depth={0}
          expanded={effExpanded}
          selectedPath={selected}
          dirty={dirty}
          renaming={renaming}
          onRenameEdit={(v) => setRenaming((r) => (r ? { ...r, value: v } : r))}
          onRenameCommit={commitRename}
          onRenameCancel={() => setRenaming(null)}
          onOpenMenu={openMenu}
          onToggle={onToggle}
          onPick={onPick}
        />
      </div>
      {menu && (
        <div
          className="ctxmenu"
          style={{ left: menu.x, top: menu.y }}
          role="menu"
          aria-label="File actions"
        >
          {buildMenuItems(menu).map((it) => (
            <button
              key={it.label}
              type="button"
              role="menuitem"
              className={it.danger ? "ctxmenu-item danger" : "ctxmenu-item"}
              onClick={() => {
                setMenu(null);
                void it.action();
              }}
            >
              {it.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
