import { useEffect, useRef } from "react";
import { useChat } from "../../store/chat";

/**
 * Live "thinking" block shown while the model is reasoning.
 *
 * Owns its own visibility (phase + non-empty text) and auto-scrolls the inner
 * `<pre>` to the bottom on every update, so the newest reasoning is always in
 * view — no manual scrolling through the thinking.
 */
export function StreamingThinking() {
  const { phase, thinkingText } = useChat();
  const preRef = useRef<HTMLPreElement>(null);

  const show =
    (phase === "thinking" || phase === "tool" || phase === "streaming" || phase === "stopping") &&
    thinkingText !== "";

  // Keep the newest reasoning visible as it streams in.
  useEffect(() => {
    const el = preRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [thinkingText]);

  if (!show) return null;

  return (
    <details className="thinking-block" open={phase === "thinking"}>
      <summary>
        {phase === "thinking" ? "Thinking…" : "Thinking"} ({thinkingText.length} chars)
      </summary>
      <pre ref={preRef}>{thinkingText}</pre>
    </details>
  );
}
