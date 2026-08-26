import { create } from "zustand";
import * as api from "../lib/api";
import { detectLanguage } from "../lib/monaco";
import { useChat } from "./chat";

/** One tab in the main (center) pane: a repo file or a chat snippet. */
export interface EditorTab {
  id: string;
  /** null = chat snippet (no repo original, not committable). */
  repoId: string | null;
  path: string | null;
  label: string;
  /** Original content from the repo; null => diff view unavailable. */
  original: string | null;
  /** Editable working copy. */
  working: string;
  language: string;
  view: "edit" | "diff";
  loadError: string | null;
}

/** Main-pane tabs (fixed Chat tab + editor tabs) and the right-pane Code/Git tabs. */
interface EditorState {
  /** Editor tabs open in the main pane. */
  editorTabs: EditorTab[];
  /** Focused main-pane tab; null = the fixed Chat tab. */
  activeTabId: string | null;
  focusChat: () => void;
  focusTab: (id: string) => void;
  openFile: (repoId: string, path: string) => Promise<void>;
  openSnippet: (content: string, languageHint: string) => void;
  closeTab: (id: string) => void;
  setWorking: (id: string, text: string) => void;
  setView: (id: string, view: "edit" | "diff") => void;

  /** Active right-pane tab. */
  tab: "code" | "git";
  setTab: (tab: "code" | "git") => void;

  /** Code-tab folder tree (repo id + sorted file paths). */
  treeRepoId: string | null;
  treePaths: string[];
  treeLoading: boolean;
  treeError: string | null;
  loadTree: (repoId: string) => Promise<void>;

  /** Git-tab snapshot (branch, dirty entries, recent log). */
  gitRepoId: string | null;
  git: api.GitState | null;
  gitLoading: boolean;
  gitError: string | null;
  loadGit: (repoId: string) => Promise<void>;

  committing: boolean;
  commitError: string | null;
  commitResult: api.CommitResult | null;

  /** Full reset — project change: close all tabs, tab → Code, clear tree/git. */
  reset: () => void;
  /** Commit the focused tab's repo file (branch → push → optional PR). */
  commit: (args: {
    repoId: string;
    filePath: string;
    message: string;
    branch: string;
    openPr: boolean;
    prTitle: string | null;
    prBody: string | null;
  }) => Promise<boolean>;
  clearResult: () => void;
}

function errMsg(e: unknown): string {
  return e instanceof api.ApiError ? e.message : e instanceof Error ? e.message : String(e);
}

function activeTabOf(s: Pick<EditorState, "editorTabs" | "activeTabId">): EditorTab | null {
  return s.editorTabs.find((t) => t.id === s.activeTabId) ?? null;
}

let snippetSeq = 0;

export const useEditor = create<EditorState>()((set, get) => ({
  editorTabs: [],
  activeTabId: null,

  focusChat() {
    set({ activeTabId: null });
  },

  focusTab(id) {
    if (get().editorTabs.some((t) => t.id === id)) set({ activeTabId: id });
  },

  closeTab(id) {
    const { editorTabs, activeTabId } = get();
    const idx = editorTabs.findIndex((t) => t.id === id);
    if (idx === -1) return;
    const next = editorTabs.filter((t) => t.id !== id);
    // Closed the focused tab: focus the previous one, else drop to Chat.
    set({
      editorTabs: next,
      activeTabId:
        activeTabId === id ? (next[Math.max(0, idx - 1)]?.id ?? null) : activeTabId,
    });
  },

  setWorking(id, text) {
    set((s) => ({
      editorTabs: s.editorTabs.map((t) => (t.id === id ? { ...t, working: text } : t)),
      commitResult: null,
    }));
  },

  setView(id, view) {
    set((s) => ({
      editorTabs: s.editorTabs.map((t) => (t.id === id ? { ...t, view } : t)),
    }));
  },

  async openFile(repoId, path) {
    const existing = get().editorTabs.find((t) => t.repoId === repoId && t.path === path);
    if (existing) {
      set({ activeTabId: existing.id });
      return;
    }
    const id = `${repoId}::${path}`;
    set((s) => ({
      activeTabId: id,
      editorTabs: [
        ...s.editorTabs,
        {
          id,
          repoId,
          path,
          label: path.split("/").pop() ?? path,
          original: null,
          working: "",
          language: detectLanguage(path),
          view: "edit",
          loadError: null,
        },
      ],
    }));
    try {
      const res = await api.getRepoFile(repoId, path);
      set((s) => ({
        editorTabs: s.editorTabs.map((t) =>
          t.id === id
            ? { ...t, original: res.content, working: res.content, loadError: null }
            : t,
        ),
      }));
    } catch (e) {
      set((s) => ({
        editorTabs: s.editorTabs.map((t) => (t.id === id ? { ...t, loadError: errMsg(e) } : t)),
      }));
    }
  },

  openSnippet(content, languageHint) {
    snippetSeq += 1;
    const id = `snippet-${snippetSeq}`;
    set((s) => ({
      activeTabId: id,
      editorTabs: [
        ...s.editorTabs,
        {
          id,
          repoId: null,
          path: null,
          label: `Snippet ${snippetSeq}`,
          original: null,
          working: content,
          language: detectLanguage(languageHint),
          view: "edit",
          loadError: null,
        },
      ],
    }));
  },

  tab: "code",
  setTab(tab) {
    set({ tab });
  },

  treeRepoId: null,
  treePaths: [],
  treeLoading: false,
  treeError: null,
  gitRepoId: null,
  git: null,
  gitLoading: false,
  gitError: null,
  committing: false,
  commitError: null,
  commitResult: null,

  async loadTree(repoId) {
    set({ treeLoading: true, treeError: null, treeRepoId: repoId, treePaths: [] });
    try {
      const res = await api.listRepoFiles(repoId);
      set({ treeRepoId: repoId, treePaths: res.files.map((f) => f.path).sort(), treeLoading: false });
    } catch (e) {
      set({ treeError: errMsg(e), treeLoading: false, treePaths: [] });
    }
  },

  async loadGit(repoId) {
    set({ gitLoading: true, gitError: null, gitRepoId: repoId });
    try {
      const git = await api.getRepoGit(repoId);
      set({ git, gitRepoId: repoId, gitLoading: false });
    } catch (e) {
      set({ gitError: errMsg(e), git: null, gitLoading: false });
    }
  },

  reset() {
    set({
      editorTabs: [],
      activeTabId: null,
      tab: "code",
      treeRepoId: null,
      treePaths: [],
      treeLoading: false,
      treeError: null,
      gitRepoId: null,
      git: null,
      gitLoading: false,
      gitError: null,
      commitError: null,
      commitResult: null,
    });
  },

  async commit(args) {
    const s = get();
    if (s.committing) return false;
    const t = activeTabOf(s);
    if (!t || t.repoId !== args.repoId || !t.path) return false;
    set({ committing: true, commitError: null, commitResult: null });
    try {
      const result = await api.commitToFile({
        repo_id: args.repoId,
        file_path: args.filePath,
        content: t.working,
        branch: args.branch,
        commit_message: args.message,
        open_pr: args.openPr,
        pr_title: args.prTitle,
        pr_body: args.prBody,
      });
      set({ commitResult: result });
      // The branch/commits just changed — refresh the Git tab snapshot.
      void get().loadGit(args.repoId);

      // Spec §4.4: the PR link must survive reloads → record a persisted note
      // on the active conversation carrying the commit tool-call payload.
      const chat = useChat.getState();
      const convId = chat.activeId;
      if (convId) {
        const prPart = result.pr_url
          ? ` PR: ${result.pr_url}`
          : ` branch ${result.branch} pushed (no PR).`;
        const text = `Commit ${result.commit_sha.slice(0, 8)} pushed to \`${result.branch}\` — ${args.message}.` + prPart;
        try {
          const msg = await api.recordPrNote(convId, {
            text,
            tool: {
              name: "push",
              ok: true,
              arguments: {
                repo_id: args.repoId,
                file_path: args.filePath,
                branch: result.branch,
                commit_sha: result.commit_sha,
                pr_url: result.pr_url,
              },
            },
          });
          chat.appendMessage(convId, msg);
        } catch {
          /* note is best-effort; the PR already succeeded */
        }
      }
      return true;
    } catch (e) {
      set({ commitError: errMsg(e) });
      return false;
    } finally {
      set({ committing: false });
    }
  },

  clearResult() {
    set({ commitResult: null, commitError: null });
  },
}));
