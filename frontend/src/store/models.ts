import { create } from "zustand";
import * as api from "../lib/api";

interface ModelState {
  models: api.Model[];
  selectedId: string;
  loaded: boolean;
  load: () => Promise<void>;
  /** Always re-fetches (unlike `load`, which is once-per-session) — used by
   *  a manual "refresh installed models" affordance in ProjectSettings. */
  refresh: () => Promise<void>;
  select: (id: string) => void;
}

export const useModels = create<ModelState>()((set, get) => ({
  models: [],
  selectedId: "",
  loaded: false,

  async load() {
    if (get().loaded) return;
    await get().refresh();
  },

  async refresh() {
    const models = await api.models();
    const current = get().selectedId;
    const selectedId = models.some((m) => m.id === current)
      ? current
      : (models.find((m) => m.is_default)?.id ?? models[0]?.id ?? "");
    set({ models, selectedId, loaded: true });
  },

  select(id) {
    set({ selectedId: id });
  },
}));
