import { create } from "zustand";
import * as api from "../lib/api";
import type { AuthUser } from "../lib/api";

type Status = "loading" | "loggedOut" | "loggedIn";

interface AuthState {
  status: Status;
  user: AuthUser | null;
  /** Restore the session (cookie) on app start. */
  init: () => Promise<void>;
  logout: () => Promise<void>;
}

export const useAuth = create<AuthState>()((set) => ({
  status: "loading",
  user: null,

  async init() {
    try {
      const user = await api.me();
      set({ status: "loggedIn", user });
    } catch {
      set({ status: "loggedOut", user: null });
    }
  },

  async logout() {
    try {
      await api.logout();
    } finally {
      set({ status: "loggedOut", user: null });
    }
  },
}));
