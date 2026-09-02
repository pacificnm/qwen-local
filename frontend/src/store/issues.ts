import { create } from "zustand";
import * as api from "../lib/api";

type StateFilter = "open" | "closed" | "all";

interface IssuesState {
  repoId: string | null;
  items: api.GitHubIssue[];
  hasMore: boolean;
  page: number;
  stateFilter: StateFilter;
  search: string;
  loading: boolean;
  error: string | null;

  selectedNumber: number | null;
  selected: { issue: api.GitHubIssue; comments: api.GitHubComment[] } | null;
  selectedLoading: boolean;
  selectedError: string | null;

  meta: api.IssuesMeta | null;

  /** Action in flight: "create" | "update" | "comment" | null. */
  busy: string | null;
  notice: { ok: boolean; text: string } | null;
  clearNotice: () => void;

  loadIssues: (repoId: string) => Promise<void>;
  loadMore: () => Promise<void>;
  setStateFilter: (repoId: string, filter: StateFilter) => void;
  setSearch: (q: string) => void;
  selectIssue: (repoId: string, number: number | null) => Promise<void>;
  loadMeta: (repoId: string) => Promise<void>;
  createIssue: (repoId: string, body: api.IssueCreateBody) => Promise<api.GitHubIssue | null>;
  updateIssue: (repoId: string, number: number, body: api.IssueUpdateBody) => Promise<boolean>;
  addComment: (repoId: string, number: number, body: string) => Promise<boolean>;
  reset: () => void;
}

function errMsg(e: unknown): string {
  return e instanceof api.ApiError ? e.message : e instanceof Error ? e.message : String(e);
}

const initial = {
  repoId: null,
  items: [],
  hasMore: false,
  page: 1,
  stateFilter: "open" as StateFilter,
  search: "",
  loading: false,
  error: null,
  selectedNumber: null,
  selected: null,
  selectedLoading: false,
  selectedError: null,
  meta: null,
  busy: null,
  notice: null,
};

export const useIssues = create<IssuesState>()((set, get) => ({
  ...initial,

  async loadIssues(repoId) {
    set({ repoId, loading: true, error: null, page: 1, selectedNumber: null, selected: null });
    try {
      const page = await api.listIssues(repoId, { state: get().stateFilter, page: 1 });
      set({ items: page.items, hasMore: page.has_more, page: 1, loading: false });
    } catch (e) {
      set({ error: errMsg(e), loading: false, items: [] });
    }
  },

  async loadMore() {
    const s = get();
    if (!s.repoId || !s.hasMore || s.loading) return;
    const nextPage = s.page + 1;
    set({ loading: true });
    try {
      const page = await api.listIssues(s.repoId, { state: s.stateFilter, page: nextPage });
      set({ items: [...s.items, ...page.items], hasMore: page.has_more, page: nextPage, loading: false });
    } catch (e) {
      set({ error: errMsg(e), loading: false });
    }
  },

  setStateFilter(repoId, filter) {
    set({ stateFilter: filter });
    void get().loadIssues(repoId);
  },

  setSearch(q) {
    set({ search: q });
  },

  async selectIssue(repoId, number) {
    if (number === null) {
      set({ selectedNumber: null, selected: null, selectedError: null });
      return;
    }
    set({ selectedNumber: number, selected: null, selectedLoading: true, selectedError: null });
    try {
      const detail = await api.getIssue(repoId, number);
      set({ selected: detail, selectedLoading: false });
    } catch (e) {
      set({ selectedError: errMsg(e), selectedLoading: false });
    }
  },

  async loadMeta(repoId) {
    try {
      const meta = await api.getIssuesMeta(repoId);
      set({ meta });
    } catch (e) {
      set({ notice: { ok: false, text: `Loading labels/assignees failed: ${errMsg(e)}` } });
    }
  },

  async createIssue(repoId, body) {
    set({ busy: "create", notice: null });
    try {
      const issue = await api.createIssue(repoId, body);
      set((s) => ({ items: [issue, ...s.items], busy: null, notice: { ok: true, text: `Issue #${issue.number} created.` } }));
      return issue;
    } catch (e) {
      set({ busy: null, notice: { ok: false, text: `Creating the issue failed: ${errMsg(e)}` } });
      return null;
    }
  },

  async updateIssue(repoId, number, body) {
    set({ busy: "update", notice: null });
    try {
      const issue = await api.updateIssue(repoId, number, body);
      set((s) => ({
        items: s.items.map((i) => (i.number === number ? issue : i)),
        selected: s.selected && s.selectedNumber === number ? { ...s.selected, issue } : s.selected,
        busy: null,
      }));
      return true;
    } catch (e) {
      set({ busy: null, notice: { ok: false, text: `Update failed: ${errMsg(e)}` } });
      return false;
    }
  },

  async addComment(repoId, number, body) {
    set({ busy: "comment", notice: null });
    try {
      const comment = await api.addIssueComment(repoId, number, body);
      set((s) => ({
        selected:
          s.selected && s.selectedNumber === number
            ? { ...s.selected, comments: [...s.selected.comments, comment] }
            : s.selected,
        items: s.items.map((i) => (i.number === number ? { ...i, comments: i.comments + 1 } : i)),
        busy: null,
      }));
      return true;
    } catch (e) {
      set({ busy: null, notice: { ok: false, text: `Comment failed: ${errMsg(e)}` } });
      return false;
    }
  },

  clearNotice() {
    set({ notice: null });
  },

  reset() {
    set({ ...initial });
  },
}));
