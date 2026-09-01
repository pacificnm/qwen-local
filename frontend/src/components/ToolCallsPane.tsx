import type { ToolCallInfo } from "../lib/api";
import { useChat } from "../store/chat";

export function ToolChips({ calls }: { calls: ToolCallInfo[] }) {
  if (calls.length === 0) return null;
  return (
    <div className="tool-chips">
      {calls.map((t, i) => (
        <span
          key={i}
          className={`tool-chip${t.ok === false ? " fail" : t.ok === true ? " ok" : ""}`}
          title={t.arguments !== undefined ? JSON.stringify(t.arguments) : undefined}
        >
          ⚙ {t.name}
          {t.ok !== undefined ? (t.ok ? " ✓" : " ✗") : " …"}
          {t.duration_ms !== undefined ? ` · ${t.duration_ms} ms` : ""}
        </span>
      ))}
    </div>
  );
}

/** Live streamed output for tools that emit `tool_output` (e.g. code_interpreter).
 *  Open while the call is running, collapsed once it finishes. */
export function ToolOutput({ calls }: { calls: ToolCallInfo[] }) {
  const withOutput = calls.filter((t) => (t.output ?? "").length > 0);
  if (withOutput.length === 0) return null;
  return (
    <div className="tool-outputs">
      {withOutput.map((t, i) => (
        <details key={i} className="tool-output" open={t.ok === undefined}>
          <summary>
            ⚙ {t.name} — output ({t.ok === false ? "failed" : t.ok === true ? "complete" : "streaming…"})
          </summary>
          <pre>{t.output}</pre>
        </details>
      ))}
    </div>
  );
}

function PrLinks({ calls }: { calls: ToolCallInfo[] }) {
  const links = calls
    .map((c) => (c.arguments as Record<string, unknown> | undefined)?.pr_url)
    .filter((u): u is string => typeof u === "string");
  if (links.length === 0) return null;
  return (
    <div className="pr-links">
      {links.map((u) => (
        <a key={u} href={u} target="_blank" rel="noopener noreferrer" className="pr-link">
          {u}
        </a>
      ))}
    </div>
  );
}

/** One historical turn's tool activity (chips + streamed output + any PR links). */
function TurnTools({ calls }: { calls: ToolCallInfo[] }) {
  if (calls.length === 0) return null;
  return (
    <div className="toolcalls-turn">
      <ToolChips calls={calls} />
      <ToolOutput calls={calls} />
      <PrLinks calls={calls} />
    </div>
  );
}

/** Fixed, non-closable tab: every tool call across the conversation (history + live). */
export default function ToolCallsPane() {
  const { messages, toolCalls, phase } = useChat();
  const turns = messages.filter((m) => m.role === "assistant" && (m.tool_calls?.length ?? 0) > 0);
  const showLive = toolCalls.length > 0 && phase !== "idle";

  if (turns.length === 0 && !showLive) {
    return (
      <div className="chat-pane">
        <div className="chat-scroll">
          <p className="dim">No tool calls yet.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="chat-pane">
      <div className="chat-scroll">
        {turns.map((m) => (
          <TurnTools key={m.id} calls={m.tool_calls!} />
        ))}
        {showLive && <TurnTools calls={toolCalls} />}
      </div>
    </div>
  );
}
