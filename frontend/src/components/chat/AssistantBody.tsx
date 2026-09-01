import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChatMessage } from "../../lib/api";
import { CodeBlock } from "./CodeBlock";

export const markdownComponents = { pre: CodeBlock };

export function AssistantBody({ msg }: { msg: ChatMessage }) {
  const body = (msg.content ?? "").trim();
  const hasTools = msg.tool_calls && msg.tool_calls.length > 0;
  return (
    <div className="md-body">
      {body ? (
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
          {msg.content}
        </ReactMarkdown>
      ) : (
        <p className="dim">
          {hasTools
            ? "Run finished with tool calls only — no final answer was generated. Ask a follow-up to continue."
            : "No response"}
        </p>
      )}
    </div>
  );
}
