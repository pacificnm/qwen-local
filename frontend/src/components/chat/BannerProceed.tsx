import { useChat } from "../../store/chat";

/**
 * Approval-gate banner shown after an Ask/Plan turn: lets the user switch to
 * Code mode and immediately continue from the plan/answer above, without
 * retyping anything (mirrors Claude Code's own plan-mode approval gate).
 *
 * Self-hides once: the user has already switched to Code mode, a new
 * (Code-mode) reply has become the latest message, or a turn is running.
 */
export function BannerProceed({ onProceed }: { onProceed: () => void }) {
  const { messages, phase, mode } = useChat();

  if (phase !== "idle" || mode === "code") return null;
  const last = messages[messages.length - 1];
  if (!last || last.role !== "assistant") return null;
  if (last.mode !== "ask" && last.mode !== "plan") return null;

  const label = last.mode === "plan" ? "Plan ready." : "Answered above.";

  return (
    <div className="banner banner-proceed" role="status">
      <span>{label}</span>
      <button onClick={onProceed}>Proceed →</button>
    </div>
  );
}
