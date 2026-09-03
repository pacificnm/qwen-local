import { create } from "zustand";
import * as api from "../lib/api";

/** Read-only cache of the active project's settings, keyed by project id —
 *  lets components other than the settings modal itself (e.g. SelectModel's
 *  named coding/chat/compaction options) read the project's model-role
 *  overrides without re-fetching. The settings modal owns its own local
 *  editing state separately and calls `refresh()` after a successful save
 *  so this cache doesn't go stale. */
interface ActiveProjectSettingsState {
  projectId: string | null;
  settings: api.ProjectSettings | null;
  loaded: boolean;
  load: (projectId: string) => Promise<void>;
  refresh: () => Promise<void>;
}

export const useActiveProjectSettings = create<ActiveProjectSettingsState>()((set, get) => ({
  projectId: null,
  settings: null,
  loaded: false,

  async load(projectId) {
    if (get().projectId === projectId && get().loaded) return;
    set({ projectId, settings: null, loaded: false });
    await get().refresh();
  },

  async refresh() {
    const { projectId } = get();
    if (!projectId) return;
    const settings = await api.getProjectSettings(projectId);
    // A project switch may have raced this fetch — only apply if still current.
    if (get().projectId !== projectId) return;
    set({ settings, loaded: true });
  },
}));
