import { create } from "zustand";
import * as api from "../lib/api";

const POLL_MS = 2500;
const RUNNING_STATES = new Set(["queued", "cloning", "scanning", "processing", "running"]);

let timer: ReturnType<typeof setTimeout> | null = null;
let loading = false;

interface RepoState {
  repos: api.Repo[];
  /** Live progress for repos whose sync is running (id → status). */
  progress: Record<string, api.RepoSyncStatus>;
  loaded: boolean;
  /** A link/sync/unlink request is in flight. */
  busy: boolean;
  /** Last action error, cleared by the next attempt. */
  error: string | null;
  load: () => Promise<void>;
  link: (fullName: string) => Promise<void>;
  sync: (id: string) => Promise<void>;
  unlink: (id: string) => Promise<void>;
}

function message(e: unknown): string {
  return e instanceof api.ApiError ? e.message : e instanceof Error ? e.message : String(e);
}

export const useRepos = create<RepoState>()((set, get) => {
  function schedule(ms: number) {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => {
      void get().load();
    }, ms);
  }

  const isRunning = (state: string) => RUNNING_STATES.has(state);

  return {
    repos: [],
    progress: {},
    loaded: false,
    busy: false,
    error: null,

    async load() {
      if (loading) return;
      loading = true;
      try {
        const repos = await api.listRepos();
        const progress: Record<string, api.RepoSyncStatus> = {};
        let running = false;
        for (const r of repos) {
          if (!isRunning(r.state)) continue;
          running = true;
          try {
            progress[r.id] = await api.repoSyncStatus(r.id);
          } catch {
            progress[r.id] = get().progress[r.id]; // keep last known value
          }
        }
        set({ repos, progress: running ? progress : {}, loaded: true });
        if (running) schedule(POLL_MS);
      } finally {
        loading = false;
      }
    },

    async link(fullName) {
      set({ busy: true, error: null });
      try {
        await api.linkRepo(fullName.trim());
        await get().load();
      } catch (e) {
        set({ error: message(e) });
      } finally {
        set({ busy: false });
      }
    },

    async sync(id) {
      set({ busy: true, error: null });
      try {
        await api.syncRepo(id);
        await get().load();
      } catch (e) {
        set({ error: message(e) });
      } finally {
        set({ busy: false });
      }
    },

    async unlink(id) {
      set({ busy: true, error: null });
      try {
        await api.unlinkRepo(id);
        set({ repos: get().repos.filter((r) => r.id !== id) });
      } catch (e) {
        set({ error: message(e) });
      } finally {
        set({ busy: false });
      }
    },
  };
});
