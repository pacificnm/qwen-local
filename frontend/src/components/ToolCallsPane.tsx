import { useState } from "react";
import type { ToolCallInfo } from "../lib/api";
import { useChat } from "../store/chat";
import { ToolCallModal } from "./ToolCallModal";

export function ToolChips({ calls, onSelect }: { calls: ToolCallInfo[]; onSelect: (index: number) => void }) {
  if (calls.length === 0) return null;
  return (
    <div className="tool-chips">
      {calls.map((t, i) => (
        <button
          key={i}
          type="button"
          className={`tool-chip clickable${t.ok === false ? " fail" : t.ok === true ? " ok" : ""}`}
          onClick={() => onSelect(i)}
          title="Click for full call + response"
        >
          ⚙ {t.name}
          {t.ok !== undefined ? (t.ok ? " ✓" : " ✗") : " …"}
          {t.duration_ms !== undefined ? ` · ${t.duration_ms} ms` : ""}
        </button>
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

/** One historical (or the live) turn's tool activity (chips + any PR links).
 *  Click a chip to see its full arguments + output in a modal. */
function TurnTools({
  turnId,
  calls,
  onSelect,
}: {
  turnId: string;
  calls: ToolCallInfo[];
  onSelect: (turnId: string, index: number) => void;
}) {
  if (calls.length === 0) return null;
  return (
    <div className="toolcalls-turn">
      <ToolChips calls={calls} onSelect={(i) => onSelect(turnId, i)} />
      <PrLinks calls={calls} />
    </div>
  );
}

/** Fixed, non-closable tab: every tool call across the conversation (history + live). */
export default function ToolCallsPane() {
  const { messages, toolCalls, phase } = useChat();
  // An index into either a historical message's tool_calls or the live
  // toolCalls list, re-resolved on every render (not the ToolCallInfo object
  // itself, which the store replaces on each streamed update) — so the modal
  // keeps showing a still-running call's output live while it's open.
  const [selection, setSelection] = useState<{ turnId: string; index: number } | null>(null);
  const turns = messages.filter((m) => m.role === "assistant" && (m.tool_calls?.length ?? 0) > 0);
  const showLive = toolCalls.length > 0 && phase !== "idle";

  const selectedCall = selection
    ? selection.turnId === "live"
      ? toolCalls[selection.index]
      : turns.find((m) => m.id === selection.turnId)?.tool_calls?.[selection.index]
    : undefined;

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
          <TurnTools key={m.id} turnId={m.id} calls={m.tool_calls!} onSelect={(t, i) => setSelection({ turnId: t, index: i })} />
        ))}
        {showLive && (
          <TurnTools turnId="live" calls={toolCalls} onSelect={(t, i) => setSelection({ turnId: t, index: i })} />
        )}
      </div>
      {selectedCall && <ToolCallModal call={selectedCall} onClose={() => setSelection(null)} />}
    </div>
  );
}
