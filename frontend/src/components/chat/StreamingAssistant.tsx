import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useChat } from "../../store/chat";
import { markdownComponents } from "./markdownComponents";

/**
 * Live assistant answer shown while the model is streaming.
 *
 * Owns its own visibility (phase + non-empty text). The outer `.chat-scroll`
 * already auto-scrolls as `assistantText` grows, so no inner scroll handling
 * is needed here (unlike the thinking block, which has its own `<pre>`).
 */
export function StreamingAssistant() {
  const { phase, assistantText } = useChat();

  const show =
    (phase === "streaming" || phase === "tool" || phase === "stopping") && assistantText !== "";

  if (!show) return null;

  return (
    <div className="msg msg-assistant streaming">
      <div className="md-body">
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
          {assistantText}
        </ReactMarkdown>
      </div>
    </div>
  );
}
