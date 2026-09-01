import { useTerminalUI, type TermStatus } from "../store/terminal";

const STATE_LABEL: Record<TermStatus, string> = {
  idle: "idle",
  connecting: "connecting…",
  running: "live",
  error: "error",
  closed: "closed",
};

/** Bottom-left terminal launcher: icon + live status. Click to
 *  open/close the shell (rendered in the center pane by `TerminalDock`). */
export default function TerminalLauncher() {
  const open = useTerminalUI((s) => s.open);
  const setOpen = useTerminalUI((s) => s.setOpen);
  const status = useTerminalUI((s) => s.status);

  return (
    <button
      className="term-launch"
      type="button"
      aria-pressed={open}
      aria-label={open ? "Close terminal" : "Open terminal"}
      title={open ? "Close terminal" : "Open terminal"}
      onClick={() => setOpen(!open)}
    >
      <span className="term-launch-ico" aria-hidden>
        ▣
      </span>
      <span className={`term-launch-status tdock-status tdock-status--${status}`}>
        {STATE_LABEL[status]}
      </span>
    </button>
  );
}
