/** Reasoning-effort picker (low → xhigh). Reads/writes the `effort` value
 * on the chat store so it stays in sync with the send path. */
import { EFFORT_LEVELS, type Effort } from "../../lib/api";
import { useChat } from "../../store/chat";

export function SelectEffort() {
  const { effort, setEffort } = useChat();

  return (
    <select
      className="effort-picker"
      value={effort}
      onChange={(e) => setEffort(e.target.value as Effort)}
      aria-label="Reasoning effort"
      title="Reasoning effort — higher thinks longer (slower)"
    >
      {[...EFFORT_LEVELS].reverse().map((lvl) => (
        <option key={lvl} value={lvl}>
          {lvl}
        </option>
      ))}
    </select>
  );
}
