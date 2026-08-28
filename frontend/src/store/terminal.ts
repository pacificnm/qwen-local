import { create } from "zustand";

/** Live terminal connection status, surfaced to the bottom-left launcher. */
export type TermStatus = "idle" | "connecting" | "running" | "error" | "closed";

const OPEN_KEY = "qc.terminalCollapsed"; // legacy flag: "false" == dock expanded/open

function readOpen(): boolean {
  try {
    return localStorage.getItem(OPEN_KEY) === "false";
  } catch {
    return false;
  }
}

interface TermUI {
  /** Terminal panel visible (in the center pane) vs. hidden. */
  open: boolean;
  setOpen: (open: boolean) => void;
  status: TermStatus;
  setStatus: (status: TermStatus) => void;
  /** Active project's repo full name, shown next to the launcher icon. */
  repoName: string;
  setRepoName: (repoName: string) => void;
}

export const useTerminalUI = create<TermUI>()((set) => ({
  open: readOpen(),
  setOpen: (open) => {
    try {
      localStorage.setItem(OPEN_KEY, String(!open));
    } catch {
      /* storage blocked — keep in-memory state */
    }
    set({ open });
    if (!open) set({ status: "idle" });
  },
  status: "idle",
  setStatus: (status) => set({ status }),
  repoName: "",
  setRepoName: (repoName) => set({ repoName }),
}));
