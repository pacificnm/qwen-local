import type { ToolCallInfo } from "../lib/api";

function statusLabel(t: ToolCallInfo): string {
  if (t.ok === true) return "✓ succeeded";
  if (t.ok === false) return "✗ failed";
  return "… running";
}

/** Detail view for one tool call: full arguments + full output, opened from
 *  a click on its chip in the Tool Calls tab. Reads the same `ToolCallInfo`
 *  object the chip does, so a still-running call's output keeps updating
 *  live while the modal is open. */
export function ToolCallModal({ call, onClose }: { call: ToolCallInfo; onClose: () => void }) {
  const argsText = call.arguments !== undefined ? JSON.stringify(call.arguments, null, 2) : "(no arguments)";
  const outputText = call.output && call.output.length > 0 ? call.output : "(no output)";

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div
        className="modal modal-wide"
        onMouseDown={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={`Tool call: ${call.name}`}
      >
        <div className="modal-head">
          <h2 className="modal-title">⚙ {call.name}</h2>
          <button className="modal-close" type="button" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>
        <p className="modal-sub">
          {statusLabel(call)}
          {call.duration_ms !== undefined ? ` · ${call.duration_ms} ms` : ""}
        </p>

        <section className="settings-section">
          <h3>Arguments</h3>
          <pre className="toolcall-block">
            <code>{argsText}</code>
          </pre>
        </section>

        <section className="settings-section">
          <h3>Output</h3>
          <pre className="toolcall-block">
            <code>{outputText}</code>
          </pre>
        </section>
      </div>
    </div>
  );
}
