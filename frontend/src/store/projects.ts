import { create } from "zustand";
import * as api from "../lib/api";
import { useRepos } from "./repos";

const ACTIVE_KEY = "qc.activeProject";

/** Repo sync states that count as "in flight" (progress worth polling). */
const RUNNING_STATES = new Set([
  "queued",
  "cloning",
  "scanning",
  "processing",
  "running",
]);

let loading = false;

interface ProjectsState {
  projects: api.Project[];
  /** Selected folder — its repo + conversations fill the left pane. */
  activeId: string | null;
  loaded: boolean;
  /** A create/rename/attach/detach/delete request is in flight. */
  busy: boolean;
  /** Last action error, cleared by the next attempt. */
  error: string | null;

  load: () => Promise<void>;
  /** Select a project folder (persists across reloads). */
  setActive: (id: string) => void;
  create: (name: string, fullName?: string | null) => Promise<api.Project | null>;
  rename: (id: string, name: string) => Promise<void>;
  remove: (id: string) => Promise<void>;
  attachRepo: (projectId: string, fullName: string) => Promise<void>;
  detachRepo: (projectId: string) => Promise<void>;
  syncRepo: (projectId: string) => Promise<void>;
}

function message(e: unknown): string {
  return e instanceof api.ApiError ? e.message : e instanceof Error ? e.message : String(e);
}

function storedActive(): string | null {
  try {
    return localStorage.getItem(ACTIVE_KEY);
  } catch {
    return null;
  }
}

function persistActive(id: string | null) {
  try {
    if (id) localStorage.setItem(ACTIVE_KEY, id);
    else localStorage.removeItem(ACTIVE_KEY);
  } catch {
    /* blocked storage — selection still applies for this session */
  }
}

export const useProjects = create<ProjectsState>()((set, get) => {
  function refreshAll() {
    void useRepos.getState().load();
  }

  /** Replace the given project with the fresh row returned by the API. */
  function replaceProject(p: api.Project) {
    set((s) => ({ projects: s.projects.map((x) => (x.id === p.id ? p : x)) }));
  }

  return {
    projects: [],
    activeId: storedActive(),
    loaded: false,
    busy: false,
    error: null,

    async load() {
      if (loading) return;
      loading = true;
      try {
        const projects = await api.listProjects();
        let activeId = get().activeId;
        if (!activeId || !projects.some((p) => p.id === activeId)) {
          activeId = projects[0]?.id ?? null;
          persistActive(activeId);
        }
        set({ projects, activeId, loaded: true });
        refreshAll();
      } finally {
        loading = false;
      }
    },

    setActive(id) {
      if (id === get().activeId) return;
      persistActive(id);
      set({ activeId: id });
    },

    async create(name, fullName) {
      set({ busy: true, error: null });
      try {
        const p = await api.createProject({
          name: name.trim() || undefined,
          full_name: fullName?.trim() || null,
        });
        set((s) => ({ projects: [...s.projects, p] }));
        set({ activeId: p.id });
        persistActive(p.id);
        refreshAll();
        return p;
      } catch (e) {
        set({ error: message(e) });
        return null;
      } finally {
        set({ busy: false });
      }
    },

    async rename(id, name) {
      set({ busy: true, error: null });
      try {
        replaceProject(await api.updateProject(id, { name: name.trim() }));
      } catch (e) {
        set({ error: message(e) });
        throw e;
      } finally {
        set({ busy: false });
      }
    },

    async remove(id) {
      set({ busy: true, error: null });
      try {
        await api.deleteProject(id);
        const rest = get().projects.filter((p) => p.id !== id);
        const activeId = get().activeId === id ? (rest[0]?.id ?? null) : get().activeId;
        persistActive(activeId);
        set({ projects: rest, activeId });
        if (activeId) refreshAll();
      } catch (e) {
        set({ error: message(e) });
        throw e;
      } finally {
        set({ busy: false });
      }
    },

    async attachRepo(projectId, fullName) {
      set({ busy: true, error: null });
      try {
        replaceProject(await api.updateProject(projectId, { full_name: fullName.trim() }));
        refreshAll();
      } catch (e) {
        set({ error: message(e) });
        throw e;
      } finally {
        set({ busy: false });
      }
    },

    async detachRepo(projectId) {
      set({ busy: true, error: null });
      try {
        replaceProject(await api.updateProject(projectId, { detach_repo: true }));
      } catch (e) {
        set({ error: message(e) });
        throw e;
      } finally {
        set({ busy: false });
      }
    },

    async syncRepo(projectId) {
      const repo = get().projects.find((p) => p.id === projectId)?.repo;
      if (!repo) return;
      set({ busy: true, error: null });
      try {
        await api.syncRepo(repo.id);
        refreshAll();
      } catch (e) {
        set({ error: message(e) });
        throw e;
      } finally {
        set({ busy: false });
      }
    },
  };
});

export function activeProject(s: ProjectsState): api.Project | null {
  return s.projects.find((p) => p.id === s.activeId) ?? null;
}

// A sync's own background progress-polling loop (useRepos) is the only thing
// that knows when a job actually finishes; nothing re-fetches `projects`
// afterward, so the Project Settings repo card (file/chunk counts, branch,
// last commit) — which reads from THIS store, not useRepos — kept showing a
// stale pre-sync snapshot until the next full page load. Refresh once
// polling reports no repo running anymore, having previously reported one.
useRepos.subscribe((state, prev) => {
  const wasRunning = Object.keys(prev.progress).length > 0;
  const isRunning = Object.keys(state.progress).length > 0;
  if (wasRunning && !isRunning) {
    void useProjects.getState().load();
  }
});

export function isRepoRunning(repo: api.Repo | null): boolean {
  return repo !== null && RUNNING_STATES.has(repo.state);
}
