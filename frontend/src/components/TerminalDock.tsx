import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { Terminal as XTerm } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import "@xterm/xterm/css/xterm.css";
import { useProjects } from "../store/projects";
import { useTerminalUI } from "../store/terminal";
import { connectTerminal, type TerminalConnection } from "../lib/terminal";

const HEIGHT_KEY = "qc.terminalHeight";
const MIN_H = 110;
const DEFAULT_H = 180;

function loadHeight(): number {
  try {
    const v = Number(localStorage.getItem(HEIGHT_KEY));
    if (Number.isFinite(v) && v >= MIN_H && v <= (window.innerHeight || 900)) return v;
  } catch {
    /* blocked storage */
  }
  return DEFAULT_H;
}

function persist(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* blocked storage — the value still applies for this session */
  }
}

/** Bottom of the center MainPane: the live xterm shell. The launch toggle,
 *  repo name, and status badge live in the bottom-left footer (see
 *  `useTerminalUI`); there is no header bar. Renders nothing while closed. */
export default function TerminalDock() {
  const repoId = useProjects((s) => s.projects.find((p) => p.id === s.activeId)?.repo?.id ?? null);
  const repoName = useProjects(
    (s) => s.projects.find((p) => p.id === s.activeId)?.repo?.github_full_name ?? null,
  );
  const { open, setStatus, setRepoName } = useTerminalUI();

  const [height, setHeight] = useState<number>(() => loadHeight());
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  const hostRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<XTerm | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const connRef = useRef<TerminalConnection | null>(null);
  const dragRef = useRef<{ startY: number; startH: number } | null>(null);

  // Publish the repo name so the bottom-left launcher icon can show it.
  useEffect(() => {
    setRepoName(repoName ?? "");
  }, [repoName, setRepoName]);

  // Terminal + WebSocket lifecycle: (re)create when opened / repo changes / retry nonce.
  useEffect(() => {
    if (!open) {
      setStatus("idle");
      return;
    }
    if (!repoId) {
      setErrorMsg(null);
      setStatus("idle");
      return;
    }
    const host = hostRef.current;
    if (!host) return;

    setErrorMsg(null);
    setStatus("connecting");

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

    conn.onReady((info) => {
      setStatus("running");
      const tag = info.tag ? ` · ${info.tag}` : "";
      term.write(`\x1b[90m▸ sandbox ${info.cwd || ""}${tag}\x1b[0m\n`);
      if (info.host_port) {
        term.write(
          `\x1b[90m  ↳ dev servers reachable at http://localhost:${info.host_port}\x1b[0m\n`,
        );
      }
      doFit();
      term.focus();
    });
    conn.onOutput((bytes) => term.write(bytes));
    conn.onError((msg) => {
      setErrorMsg(msg);
      setStatus("error");
      term.write(`\x1b[31m[terminal error]\x1b[0m ${msg}\n`);
    });
    conn.onClose(() => {
      setStatus("closed");
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
  }, [open, repoId, nonce, setStatus]);

  function onReopen() {
    setErrorMsg(null);
    setNonce((n) => n + 1);
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

  if (!open) return null;

  return (
    <section className="tdock tdock-open" style={{ height }} aria-label="Sandbox terminal">
      <div
        className="tdock-handle"
        onPointerDown={onHandleDown}
        title="Drag to resize terminal height"
        role="separator"
        aria-orientation="horizontal"
      />
      <div className="tdock-panel">
        {repoId ? (
          <>
            {errorMsg && (
              <div className="tdock-errorbanner">
                <span title={errorMsg}>{errorMsg}</span>
                <button type="button" className="tdock-reopen" onClick={() => onReopen()}>
                  Reopen
                </button>
              </div>
            )}
            <div className="tdock-termhost" ref={hostRef} />
          </>
        ) : (
          <p className="tdock-empty">
            No repository is linked to this project. Open <code>Settings</code> (bottom-left) and
            attach one to get a live sandbox terminal with a writable <code>/workspace</code> and the
            repo at <code>/repo</code>.
          </p>
        )}
      </div>
    </section>
  );
}
