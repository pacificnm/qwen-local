import { create } from "zustand";
import * as api from "../lib/api";
import { detectLanguage } from "../lib/monaco";
import { useChat } from "./chat";

/** Right-pane editor state (Phase 4): repo file or chat snippet, diff view, commit. */
interface EditorState {
  /** True once something has been opened (repo file or snippet). */
  open: boolean;
  /** Repo the content was loaded from; null = chat snippet (no original). */
  fileRepoId: string | null;
  /** Path inside the repo; null = snippet pasted from chat. */
  filePath: string | null;
  /** Original content from the repo; null => diff view unavailable. */
  original: string | null;
  /** Editable working copy. */
  working: string;
  /** Monaco language id. */
  language: string;
  view: "edit" | "diff";
  /** File picker data (repo id + sorted paths). */
  pickerRepoId: string | null;
  pickerPaths: string[];
  pickerLoading: boolean;
  pickerError: string | null;

  committing: boolean;
  commitError: string | null;
  commitResult: api.CommitResult | null;

  setOpen: (open: boolean) => void;
  reset: () => void;
  loadPickerFiles: (repoId: string) => Promise<void>;
  openRepoFile: (repoId: string, path: string) => Promise<void>;
  openSnippet: (content: string, languageHint: string) => void;
  setWorking: (text: string) => void;
  setView: (view: "edit" | "diff") => void;
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

const CLOSED = {
  open: false,
  fileRepoId: null,
  filePath: null,
  original: null,
  working: "",
  view: "edit" as const,
  commitError: null,
  commitResult: null,
};

export const useEditor = create<EditorState>()((set, get) => ({
  ...CLOSED,
  language: "plaintext",
  pickerRepoId: null,
  pickerPaths: [],
  pickerLoading: false,
  pickerError: null,
  committing: false,

  setOpen(isOpen) {
    set({ open: isOpen });
  },

  reset() {
    set({ ...CLOSED, language: "plaintext" });
  },

  async loadPickerFiles(repoId) {
    set({ pickerLoading: true, pickerError: null, pickerRepoId: repoId, pickerPaths: [] });
    try {
      const res = await api.listRepoFiles(repoId);
      set({ pickerRepoId: repoId, pickerPaths: res.files.map((f) => f.path).sort(), pickerLoading: false });
    } catch (e) {
      set({ pickerError: errMsg(e), pickerLoading: false, pickerPaths: [] });
    }
  },

  async openRepoFile(repoId, path) {
    const language = detectLanguage(path);
    set({ open: true, committing: false, commitError: null, commitResult: null });
    try {
      const res = await api.getRepoFile(repoId, path);
      set({
        open: true,
        fileRepoId: repoId,
        filePath: res.path,
        original: res.content,
        working: res.content,
        language,
        view: "edit",
        commitError: null,
        commitResult: null,
      });
    } catch (e) {
      set({ open: true, commitError: errMsg(e) });
    }
  },

  openSnippet(content, languageHint) {
    set({
      open: true,
      fileRepoId: null,
      filePath: null,
      original: null,
      working: content,
      language: detectLanguage(languageHint),
      view: "edit",
      commitError: null,
      commitResult: null,
    });
  },

  setWorking(text) {
    set({ working: text, commitResult: null });
  },

  setView(view) {
    set({ view });
  },

  async commit(args) {
    const s = get();
    if (s.committing) return false;
    set({ committing: true, commitError: null, commitResult: null });
    try {
      const result = await api.commitToFile({
        repo_id: args.repoId,
        file_path: args.filePath,
        content: s.working,
        branch: args.branch,
        commit_message: args.message,
        open_pr: args.openPr,
        pr_title: args.prTitle,
        pr_body: args.prBody,
      });
      set({ commitResult: result });

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
