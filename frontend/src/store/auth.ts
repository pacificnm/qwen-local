import { create } from "zustand";
import * as api from "../lib/api";

type Status = "loading" | "loggedOut" | "loggedIn";

interface AuthState {
  status: Status;
  user: string | null;
  /** Restore the session (cookie) on app start. */
  init: () => Promise<void>;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

export const useAuth = create<AuthState>()((set) => ({
  status: "loading",
  user: null,

  async init() {
    try {
      const me = await api.me();
      set({ status: "loggedIn", user: me.username });
    } catch {
      set({ status: "loggedOut", user: null });
    }
  },

  async login(username, password) {
    const me = await api.login(username, password);
    set({ status: "loggedIn", user: me.username });
  },

  async logout() {
    try {
      await api.logout();
    } finally {
      set({ status: "loggedOut", user: null });
    }
  },
}));
