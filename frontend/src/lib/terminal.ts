/**
 * Terminal WebSocket client (Phase 6).
 *
 * Same-origin socket to `/api/terminals/ws/{repo_id}`. The HttpOnly `session`
 * cookie is sent automatically (same-origin), so no explicit auth is needed.
 *
 * Wire protocol (mirrors backend/app/api/terminals.py):
 *   client -> server:
 *     binary  keystroke bytes (UTF-8 string from xterm `onData`)
 *     text    {"type":"resize","rows":N,"cols":M}
 *   server -> client:
 *     binary  raw pty output
 *     text    {"type":"ready","cwd":..,"tag":..}   after spawn
 *             {"type":"error","error":..}           on spawn failure
 */

export interface TerminalReady {
  cwd: string;
  tag: string;
}

export interface TerminalConnection {
  /** Send keystroke bytes to the shell (no-op until the socket is open). */
  send(data: string): void;
  resize(rows: number, cols: number): void;
  onOutput(cb: (bytes: Uint8Array) => void): void;
  onReady(cb: (info: TerminalReady) => void): void;
  onError(cb: (message: string) => void): void;
  onClose(cb: (code: number, reason: string) => void): void;
  /** Idempotent. Closes the socket; the server drops the session. */
  close(): void;
}

export function terminalWsUrl(repoId: string): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/api/terminals/ws/${encodeURIComponent(repoId)}`;
}

export function connectTerminal(repoId: string): TerminalConnection {
  const ws = new WebSocket(terminalWsUrl(repoId));
  ws.binaryType = "arraybuffer";

  let outputCb: ((bytes: Uint8Array) => void) | null = null;
  let readyCb: ((info: TerminalReady) => void) | null = null;
  let errorCb: ((message: string) => void) | null = null;
  let closeCb: ((code: number, reason: string) => void) | null = null;
  let opened = false;

  ws.onopen = () => {
    opened = true;
  };

  ws.onmessage = (ev: MessageEvent) => {
    if (typeof ev.data === "string") {
      let msg: Record<string, unknown>;
      try {
        msg = JSON.parse(ev.data) as Record<string, unknown>;
      } catch {
        return; // ignore malformed control frames
      }
      if (msg.type === "ready") {
        readyCb?.({
          cwd: typeof msg.cwd === "string" ? msg.cwd : "",
          tag: typeof msg.tag === "string" ? msg.tag : "",
        });
      } else if (msg.type === "error") {
        errorCb?.(typeof msg.error === "string" ? msg.error : "terminal failed to start");
      }
    } else if (ev.data instanceof ArrayBuffer) {
      outputCb?.(new Uint8Array(ev.data));
    }
  };

  ws.onerror = () => {
    // onclose will always follow; surface nothing extra here.
  };

  ws.onclose = (ev: CloseEvent) => {
    opened = false;
    closeCb?.(ev.code, ev.reason ?? "");
  };

  const encoder = new TextEncoder();
  const isSendable = () => ws.readyState === WebSocket.OPEN && opened;

  return {
    send(data: string) {
      if (!isSendable()) return;
      ws.send(encoder.encode(data));
    },
    resize(rows: number, cols: number) {
      if (!isSendable()) return;
      ws.send(JSON.stringify({ type: "resize", rows, cols }));
    },
    onOutput(cb) {
      outputCb = cb;
    },
    onReady(cb) {
      readyCb = cb;
    },
    onError(cb) {
      errorCb = cb;
    },
    onClose(cb) {
      closeCb = cb;
    },
    close() {
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
        try {
          ws.close();
        } catch {
          /* already closing */
        }
      }
    },
  };
}
