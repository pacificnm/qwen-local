import { create } from "zustand";
import * as api from "../lib/api";
import { detectLanguage } from "../lib/monaco";

/** Sentinel `activeTabId` for the fixed, non-closable Tool Calls tab (never a real tab's id). */
export const TOOL_CALLS_TAB_ID = "__tool-calls__";

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
  /** Focused main-pane tab; null = the fixed Chat tab, TOOL_CALLS_TAB_ID = the fixed Tool Calls tab. */
  activeTabId: string | null;
  focusChat: () => void;
  focusToolCalls: () => void;
  focusTab: (id: string) => void;
  openFile: (repoId: string, path: string) => Promise<void>;
  openSnippet: (content: string, languageHint: string) => void;
  closeTab: (id: string) => void;
  setWorking: (id: string, text: string) => void;
  setView: (id: string, view: "edit" | "diff") => void;

  /** Active right-pane tab. */
  tab: "code" | "git" | "issues";
  setTab: (tab: "code" | "git" | "issues") => void;

  /** Set by the Issues tab's "Start branch" action; GitBranchBar consumes it
   * once (pre-fills the new-branch form) then clears it. */
  pendingBranchIssue: { number: number; title: string } | null;
  setPendingBranchIssue: (issue: { number: number; title: string } | null) => void;

  /** Code-tab folder tree (repo id + sorted file paths). */
  treeRepoId: string | null;
  treePaths: string[];
  treeLoading: boolean;
  treeError: string | null;
  loadTree: (repoId: string) => Promise<void>;

  /** Git-tab snapshot (branch, staged/changed files, upstream counts, recent log). */
  gitRepoId: string | null;
  git: api.GitState | null;
  gitLoading: boolean;
  gitError: string | null;
  loadGit: (repoId: string) => Promise<void>;
  /** Git-tab action in flight: "stage" | "unstage" | "commit" | "push" | "branch" | "checkout" | "pr" | "merge" | null. */
  gitBusy: string | null;
  gitNotice: { ok: boolean; text: string } | null;
  clearGitNotice: () => void;
  stage: (repoId: string, paths: string[]) => Promise<boolean>;
  unstage: (repoId: string, paths: string[]) => Promise<boolean>;
  commitStaged: (repoId: string, message: string) => Promise<boolean>;
  pushBranch: (repoId: string) => Promise<boolean>;
  createBranch: (repoId: string, name: string) => Promise<boolean>;
  switchBranch: (repoId: string, branch: string) => Promise<boolean>;
  openPr: (repoId: string, body: api.OpenPrBody) => Promise<boolean>;
  mergePr: (repoId: string) => Promise<boolean>;

  /** Full reset — project change: close all tabs, tab → Code, clear tree/git. */
  reset: () => void;
}

function errMsg(e: unknown): string {
  return e instanceof api.ApiError ? e.message : e instanceof Error ? e.message : String(e);
}

let snippetSeq = 0;

export const useEditor = create<EditorState>()((set, get) => ({
  editorTabs: [],
  activeTabId: null,

  focusChat() {
    set({ activeTabId: null });
  },

  focusToolCalls() {
    set({ activeTabId: TOOL_CALLS_TAB_ID });
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

  pendingBranchIssue: null,
  setPendingBranchIssue(issue) {
    set({ pendingBranchIssue: issue });
  },

  treeRepoId: null,
  treePaths: [],
  treeLoading: false,
  treeError: null,
  gitRepoId: null,
  git: null,
  gitLoading: false,
  gitError: null,
  gitBusy: null,
  gitNotice: null,

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
      pendingBranchIssue: null,
      treeRepoId: null,
      treePaths: [],
      treeLoading: false,
      treeError: null,
      gitRepoId: null,
      git: null,
      gitLoading: false,
      gitError: null,
      gitBusy: null,
      gitNotice: null,
    });
  },

  clearGitNotice: () => set({ gitNotice: null }),

  async stage(repoId, paths) {
    if (get().gitBusy) return false;
    set({ gitBusy: "stage", gitNotice: null });
    try {
      await api.stageFiles(repoId, paths);
      await get().loadGit(repoId);
      set({ gitNotice: { ok: true, text: `Staged ${paths.length} file${paths.length === 1 ? "" : "s"}.` }, gitBusy: null });
      return true;
    } catch (e) {
      set({ gitNotice: { ok: false, text: `Stage failed: ${errMsg(e)}` }, gitBusy: null });
      return false;
    }
  },

  async unstage(repoId, paths) {
    if (get().gitBusy) return false;
    set({ gitBusy: "unstage", gitNotice: null });
    try {
      await api.unstageFiles(repoId, paths);
      await get().loadGit(repoId);
      set({ gitNotice: { ok: true, text: `Unstaged ${paths.length} file${paths.length === 1 ? "" : "s"}.` }, gitBusy: null });
      return true;
    } catch (e) {
      set({ gitNotice: { ok: false, text: `Unstage failed: ${errMsg(e)}` }, gitBusy: null });
      return false;
    }
  },

  async commitStaged(repoId, message) {
    if (get().gitBusy) return false;
    set({ gitBusy: "commit", gitNotice: null });
    try {
      const { commit_sha } = await api.commitStaged(repoId, message);
      await get().loadGit(repoId);
      set({ gitNotice: { ok: true, text: `Committed ${commit_sha.slice(0, 8)} on ${get().git?.branch ?? "the current branch"}.` }, gitBusy: null });
      return true;
    } catch (e) {
      set({ gitNotice: { ok: false, text: `Commit failed: ${errMsg(e)}` }, gitBusy: null });
      return false;
    }
  },

  async pushBranch(repoId) {
    if (get().gitBusy) return false;
    set({ gitBusy: "push", gitNotice: null });
    try {
      const { branch } = await api.pushBranch(repoId);
      await get().loadGit(repoId);
      set({ gitNotice: { ok: true, text: `Pushed ${branch} to origin.` }, gitBusy: null });
      return true;
    } catch (e) {
      set({ gitNotice: { ok: false, text: `Push failed: ${errMsg(e)}` }, gitBusy: null });
      return false;
    }
  },

  async createBranch(repoId, name) {
    if (get().gitBusy) return false;
    set({ gitBusy: "branch", gitNotice: null });
    try {
      const { branch } = await api.createBranch(repoId, name);
      await get().loadGit(repoId);
      set({ gitNotice: { ok: true, text: `Switched to new branch ${branch}.` }, gitBusy: null });
      return true;
    } catch (e) {
      set({ gitNotice: { ok: false, text: `Branch creation failed: ${errMsg(e)}` }, gitBusy: null });
      return false;
    }
  },

  async switchBranch(repoId, branch) {
    if (get().gitBusy) return false;
    set({ gitBusy: "checkout", gitNotice: null });
    try {
      const { branch: switched } = await api.checkoutBranch(repoId, branch);
      await get().loadGit(repoId);
      set({ gitNotice: { ok: true, text: `Switched to ${switched}.` }, gitBusy: null });
      return true;
    } catch (e) {
      set({ gitNotice: { ok: false, text: `Switch failed: ${errMsg(e)}` }, gitBusy: null });
      return false;
    }
  },

  async openPr(repoId, body) {
    if (get().gitBusy) return false;
    set({ gitBusy: "pr", gitNotice: null });
    try {
      await api.openPullRequest(repoId, body);
      await get().loadGit(repoId);
      set({ gitNotice: { ok: true, text: "Pull request opened." }, gitBusy: null });
      return true;
    } catch (e) {
      set({ gitNotice: { ok: false, text: `Opening the PR failed: ${errMsg(e)}` }, gitBusy: null });
      return false;
    }
  },

  async mergePr(repoId) {
    if (get().gitBusy) return false;
    set({ gitBusy: "merge", gitNotice: null });
    try {
      await api.mergePullRequest(repoId);
      await get().loadGit(repoId);
      set({ gitNotice: { ok: true, text: "Pull request merged." }, gitBusy: null });
      return true;
    } catch (e) {
      set({ gitNotice: { ok: false, text: `Merge failed: ${errMsg(e)}` }, gitBusy: null });
      return false;
    }
  },

}));
