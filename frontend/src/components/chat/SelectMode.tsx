/** Chat-mode picker (Ask / Plan / Code). Reads/writes the `mode` value on
 * the chat store so it stays in sync with the send path. */
import { MODES, type ChatMode } from "../../lib/api";
import { useChat } from "../../store/chat";

const LABELS: Record<ChatMode, string> = { ask: "Ask", plan: "Plan", code: "Code" };

export function SelectMode({ busy }: { busy: boolean }) {
  const { mode, setMode } = useChat();

  return (
    <select
      className="mode-picker"
      value={mode}
      disabled={busy}
      onChange={(e) => setMode(e.target.value as ChatMode)}
      aria-label="Chat mode"
      title="Chat mode — Ask/Plan are read-only (investigate only); Code can edit, run, and commit"
    >
      {MODES.map((m) => (
        <option key={m} value={m}>
          {LABELS[m]}
        </option>
      ))}
    </select>
  );
}
