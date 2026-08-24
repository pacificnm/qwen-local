import { useEffect, useRef, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChatMessage, ToolCallInfo } from "../lib/api";
import { useChat } from "../store/chat";
import { useEditor } from "../store/editor";
import { useModels } from "../store/models";
import { useRepos } from "../store/repos";

function nodeText(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(nodeText).join("");
  if (node && typeof node === "object" && "props" in node) {
    return nodeText((node as { props: { children?: ReactNode } }).props.children);
  }
  return "";
}

function codeClassOf(children: ReactNode): string {
  if (Array.isArray(children)) {
    for (const c of children) {
      const hit = codeClassOf(c);
      if (hit) return hit;
    }
    return "";
  }
  if (children && typeof children === "object" && "props" in children) {
    const p = (children as { type?: unknown; props?: { className?: string } }).props;
    if (p?.className) return p.className;
  }
  return "";
}

/** Fenced code block wrapped with a language tag + "Open in editor" action. */
function CodeBlock({ children }: { children?: ReactNode }) {
  const openSnippet = useEditor((s) => s.openSnippet);
  const langMatch = /language-([\w+-]+)/.exec(codeClassOf(children));
  const lang = langMatch?.[1] ?? "";
  const text = nodeText(children).replace(/\n+$/, "");
  return (
    <div className="codeblock">
      <div className="codeblock-bar">
        <span className="codeblock-lang">{lang || "code"}</span>
        <button
          type="button"
          className="codeblock-open"
          onClick={() => openSnippet(text, lang)}
          title="Open this code in the editor pane"
        >
          ⧉ Open in editor
        </button>
      </div>
      <div className="codeblock-body">{children}</div>
    </div>
  );
}

const markdownComponents = { pre: CodeBlock };

function ToolChips({ calls }: { calls: ToolCallInfo[] }) {
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
function ToolOutput({ calls }: { calls: ToolCallInfo[] }) {
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

function AssistantBody({ msg }: { msg: ChatMessage }) {
  return (
    <div className="md-body">
      {msg.tool_calls && msg.tool_calls.length > 0 && (
        <>
          <ToolChips calls={msg.tool_calls} />
          <ToolOutput calls={msg.tool_calls} />
          <PrLinks calls={msg.tool_calls} />
        </>
      )}
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
        {msg.content || ""}
      </ReactMarkdown>
    </div>
  );
}

export default function ChatPane() {
  const {
    messages,
    phase,
    assistantText,
    thinkingText,
    toolCalls,
    error,
    send,
    cancel,
    clearError,
  } = useChat();
  const { models, selectedId, loaded, select } = useModels();
  const { repos } = useRepos();
  const conv = useChat((s) => s.conversations.find((c) => c.id === s.activeId)) ?? null;

  const [draft, setDraft] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const [streamErr, setStreamErr] = useState<string | null>(null);

  const busy =
    phase === "waiting" ||
    phase === "thinking" ||
    phase === "streaming" ||
    phase === "tool" ||
    phase === "stopping";

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, assistantText, thinkingText, toolCalls]);

  useEffect(
    () => () => {
      abortRef.current?.abort();
    },
    [],
  );

  async function doSend() {
    const text = draft.trim();
    if (!text || busy) return;
    setDraft("");
    setStreamErr(null);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      await send(text, selectedId, ctrl);
    } catch (e) {
      setStreamErr(e instanceof Error ? e.message : String(e));
    }
  }

  function doCancel() {
    void cancel();
  }

  const showStreamingAssistant =
    (phase === "streaming" || phase === "tool" || phase === "stopping") && assistantText !== "";
  const showStreamingThinking =
    (phase === "thinking" || phase === "tool" || phase === "streaming" || phase === "stopping") &&
    thinkingText !== "";
  const repoName = conv?.repo_name ?? repos.find((r) => r.id === conv?.repo_id)?.github_full_name;

  return (
    <div className="chat-pane">
      <header className="chat-head">
        <div className="chat-head-title">
          <span className="chat-title">{conv ? conv.title : "New chat"}</span>
          {repoName && <span className="repo-chip">{repoName}</span>}
        </div>
        <div className="model-picker chat-model">
          <select
            value={selectedId}
            disabled={!loaded || models.length === 0 || busy}
            onChange={(e) => select(e.target.value)}
            aria-label="Model"
          >
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label}
              </option>
            ))}
          </select>
        </div>
      </header>

      <div className="chat-scroll" ref={scrollRef}>
        {messages.length === 0 && !assistantText && phase !== "thinking" && (
          <div className="chat-welcome">
            <p>Ask anything about your codebase.</p>
            {repoName ? (
              <p className="dim">
                This conversation reads <strong>{repoName}</strong> (RAG top-8).
              </p>
            ) : (
              <p className="dim">No repo linked yet — pick one in the repo selector above "New conversation" to enable RAG.</p>
            )}
          </div>
        )}

        {messages.map((m) =>
          m.role === "user" ? (
            <div key={m.id} className="msg msg-user">
              {m.content}
            </div>
          ) : (
            <div key={m.id} className="msg msg-assistant">
              <AssistantBody msg={m} />
            </div>
          ),
        )}

        {showStreamingThinking && (
          <details className="thinking-block" open={phase === "thinking"}>
            <summary>
              {phase === "thinking" ? "Thinking…" : "Thinking"} ({thinkingText.length} chars)
            </summary>
            <pre>{thinkingText}</pre>
          </details>
        )}

        {toolCalls.length > 0 && phase !== "idle" && (
          <>
            <ToolChips calls={toolCalls} />
            <ToolOutput calls={toolCalls} />
          </>
        )}

        {showStreamingAssistant && (
          <div className="msg msg-assistant streaming">
            <div className="md-body">
              <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
                {assistantText}
              </ReactMarkdown>
            </div>
          </div>
        )}

        {phase === "stopping" && <p className="chat-status">Stopping…</p>}
        {phase === "cancelled" && <p className="chat-status cancelled">■ Stopped — partial answer saved.</p>}

        {error && (
          <div className="banner banner-error" role="alert">
            <span>{error}</span>
            <button onClick={clearError}>dismiss</button>
          </div>
        )}
        {streamErr && <p className="banner banner-error">{streamErr}</p>}
      </div>

      <div className="composer">
        <textarea
          rows={3}
          placeholder="Ask about your codebase… (Enter to send, Shift+Enter for newline)"
          value={draft}
          disabled={busy}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void doSend();
            }
          }}
          aria-label="Message"
        />
        <div className="composer-actions">
          {busy ? (
            <button disabled={phase === "stopping"} onClick={doCancel}>
              {phase === "stopping" ? "Stopping…" : "■ Stop"}
            </button>
          ) : (
            <button className="primary" disabled={!draft.trim()} onClick={() => void doSend()}>
              Send
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
