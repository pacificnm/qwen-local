import { create } from "zustand";
import * as api from "../lib/api";

interface ModelState {
  models: api.Model[];
  selectedId: string;
  loaded: boolean;
  load: () => Promise<void>;
  select: (id: string) => void;
}

export const useModels = create<ModelState>()((set, get) => ({
  models: [],
  selectedId: "",
  loaded: false,

  async load() {
    if (get().loaded) return;
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
