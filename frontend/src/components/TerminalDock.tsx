import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { Terminal as XTerm } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import "@xterm/xterm/css/xterm.css";
import { useProjects } from "../store/projects";
import { connectTerminal, type TerminalConnection } from "../lib/terminal";

const HEIGHT_KEY = "qc.terminalHeight";
const COLLAPSED_KEY = "qc.terminalCollapsed";
const MIN_H = 110;
const DEFAULT_H = 180;
const BAR_H = 35;

function loadHeight(): number {
  try {
    const v = Number(localStorage.getItem(HEIGHT_KEY));
    if (Number.isFinite(v) && v >= MIN_H && v <= (window.innerHeight || 900)) return v;
  } catch {
    /* blocked storage */
  }
  return DEFAULT_H;
}

function loadCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSED_KEY) !== "false";
  } catch {
    return true;
  }
}

function persist(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* blocked storage — the value still applies for this session */
  }
}

type TermState = "idle" | "connecting" | "running" | "error" | "closed";

export default function TerminalDock() {
  const repoId = useProjects((s) => s.projects.find((p) => p.id === s.activeId)?.repo?.id ?? null);
  const repoName = useProjects(
    (s) => s.projects.find((p) => p.id === s.activeId)?.repo?.github_full_name ?? null,
  );

  const [collapsed, setCollapsed] = useState<boolean>(() => loadCollapsed());
  const [height, setHeight] = useState<number>(() => loadHeight());
  const [state, setState] = useState<TermState>("idle");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  const hostRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<XTerm | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const connRef = useRef<TerminalConnection | null>(null);
  const dragRef = useRef<{ startY: number; startH: number } | null>(null);

  // Terminal + WebSocket lifecycle: (re)create when expanded/repo changes/nonce.
  useEffect(() => {
    if (collapsed || !repoId) return;
    const host = hostRef.current;
    if (!host) return;

    const term = new XTerm({
      cursorBlink: true,
      convertEol: false,
      fontSize: 13,
      fontFamily:
        '"JetBrains Mono", "Fira Code", ui-monospace, "SF Mono", Menlo, Consolas, monospace',
      theme: {
        background: "#0d1117",
        foreground: "#e6edf3",
        cursor: "#58a6ff",
        selectionBackground: "rgba(56,139,253,0.35)",
      },
      scrollback: 4000,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.loadAddon(new WebLinksAddon());
    term.open(host);
    termRef.current = term;
    fitRef.current = fit;

    const doFit = () => {
      if (host.clientWidth <= 0 || host.clientHeight <= 0) return;
      try {
        fit.fit();
      } catch {
        /* geometry not ready yet */
      }
      connRef.current?.resize(term.rows, term.cols);
    };

    const conn = connectTerminal(repoId);
    connRef.current = conn;
    setState("connecting");
    setErrorMsg(null);

    conn.onReady((info) => {
      setState("running");
      const tag = info.tag ? ` · ${info.tag}` : "";
      term.write(`\x1b[90m▸ sandbox ${info.cwd || ""}${tag}\x1b[0m\n`);
      doFit();
      term.focus();
    });
    conn.onOutput((bytes) => term.write(bytes));
    conn.onError((msg) => {
      setErrorMsg(msg);
      setState("error");
      term.write(`\x1b[31m[terminal error]\x1b[0m ${msg}\n`);
    });
    conn.onClose(() => {
      setState("closed");
      term.write(`\x1b[90m[connection closed — reopen to start a new shell]\x1b[0m\n`);
    });

    const dataSub = term.onData((d) => conn.send(d));
    const ro = new ResizeObserver(() => doFit());
    ro.observe(host);

    doFit();
    term.focus();

    return () => {
      ro.disconnect();
      dataSub.dispose();
      try {
        term.dispose();
      } catch {
        /* already disposed */
      }
      termRef.current = null;
      fitRef.current = null;
      conn.close();
      connRef.current = null;
    };
    // termRef/fitRef/connRef/dragRef are refs — intentionally excluded from deps.
  }, [collapsed, repoId, nonce]);

  function toggle() {
    setCollapsed((c) => {
      const next = !c;
      persist(COLLAPSED_KEY, String(next));
      return next;
    });
  }

  function onHandleDown(e: ReactPointerEvent<HTMLDivElement>) {
    e.preventDefault();
    dragRef.current = { startY: e.clientY, startH: height };
    const move = (ev: PointerEvent) => {
      const d = dragRef.current;
      if (!d) return;
      const next = Math.max(MIN_H, Math.min(window.innerHeight, d.startH + (d.startY - ev.clientY)));
      setHeight(next);
    };
    const up = () => {
      dragRef.current = null;
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      persist(HEIGHT_KEY, String(height));
      // Re-fit once dragging settles so the prompt reflows cleanly.
      const host = hostRef.current;
      if (host && host.clientWidth > 0) fitRef.current?.fit();
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  function onBarKey(e: React.KeyboardEvent) {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      toggle();
    }
  }

  const stateBadge =
    state === "running" ? "live" : state === "connecting" ? "connecting…" : state === "closed" ? "closed" : state === "error" ? "error" : "idle";

  return (
    <section
      className={`tdock ${collapsed ? "tdock-collapsed" : "tdock-open"}`}
      style={collapsed ? undefined : { height: BAR_H + height }}
      aria-label="Sandbox terminal"
    >
      {!collapsed && (
        <div
          className="tdock-handle"
          onPointerDown={onHandleDown}
          title="Drag to resize terminal height"
          role="separator"
          aria-orientation="horizontal"
        />
      )}

      <div
        className="tdock-bar"
        role="button"
        tabIndex={0}
        aria-expanded={!collapsed}
        onClick={toggle}
        onKeyDown={onBarKey}
      >
        <span className="tdock-title">
          <span className="tdock-ico" aria-hidden>
            ▣
          </span>
          Terminal
          {repoName && (
            <span className="tdock-repo" title={repoName}>
              {repoName}
            </span>
          )}
        </span>
        <span className={`tdock-status tdock-status--${state}`}>{stateBadge}</span>
        <span className="tdock-chevron" aria-hidden>
          {collapsed ? "▴" : "▾"}
        </span>
      </div>

      {collapsed && !repoId && (
        <div className="tdock-panel tdock-panel--hint">
          <p className="tdock-empty">Expand to open a sandbox shell — link a repository first.</p>
        </div>
      )}

      {!collapsed && (
        <div className="tdock-panel">
          {!repoId ? (
            <p className="tdock-empty">
              No repository is linked to this project. Link one (top-left, “Link repo”) to get a live
              sandbox terminal with a writable <code>/workspace</code> and the repo at <code>/repo</code>.
            </p>
          ) : (
            <>
              {errorMsg && state === "error" && (
                <div className="tdock-errorbanner">
                  <span title={errorMsg}>{errorMsg}</span>
                  <button type="button" className="tdock-reopen" onClick={() => setNonce((n) => n + 1)}>
                    Reopen
                  </button>
                </div>
              )}
              <div className="tdock-termhost" ref={hostRef} />
            </>
          )}
        </div>
      )}
    </section>
  );
}
