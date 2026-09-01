import { useEffect, useRef, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { EFFORT_LEVELS, type ChatMessage, type Effort } from "../lib/api";
import { onFileMention } from "../lib/fileMentionBus";
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

function AssistantBody({ msg }: { msg: ChatMessage }) {
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

/** 128000 → "128.0k" for the context-status figure. */
function fmtK(n: number): string {
  return `${(n / 1000).toFixed(1)}k`;
}

export default function ChatPane() {
  const {
    messages,
    phase,
    assistantText,
    thinkingText,
    error,
    send,
    cancel,
    clearError,
    effort,
    setEffort,
    contextUsed,
  } = useChat();
  const { models, selectedId, loaded, select } = useModels();
  const contextWindow = models.find((m) => m.id === selectedId)?.context_window ?? null;
  const { repos } = useRepos();
  const conv = useChat((s) => s.conversations.find((c) => c.id === s.activeId)) ?? null;

  const [draft, setDraft] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
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
  }, [messages, assistantText, thinkingText]);

  useEffect(
    () => () => {
      abortRef.current?.abort();
    },
    [],
  );

  // "Add to Chat" from the file tree drops a path into the draft as its own line.
  useEffect(
    () =>
      onFileMention((path) => {
        setDraft((d) => (d.includes(path) ? d : `${d ? d.trimEnd() + "\n" : ""}${path}`));
        const el = composerRef.current;
        if (!el) return;
        el.focus();
        requestAnimationFrame(() => {
          const len = el.value.length;
          el.setSelectionRange(len, len);
        });
      }),
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
      <div className="chat-scroll" ref={scrollRef}>
        {messages.length === 0 && !assistantText && phase !== "thinking" && (
          <div className="chat-welcome">
            <p>Ask anything about your codebase.</p>
            {repoName ? (
              <p className="dim">
                This conversation reads <strong>{repoName}</strong> (RAG top-8).
              </p>
            ) : (
              <p className="dim">
                This project has no repository yet — attach one in the left pane to enable RAG.
              </p>
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
        <div className="composer-box">
          <textarea
            ref={composerRef}
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
          <div className="composer-bar">
            <div className="composer-bar-left">
              <select
                className="model-select"
                value={selectedId}
                disabled={!loaded || models.length === 0 || busy}
                onChange={(e) => select(e.target.value)}
                aria-label="Model"
                title="Model"
              >
                {models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.label}
                  </option>
                ))}
              </select>
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
              {contextWindow != null && (
                <span
                  className="ctx-status"
                  title="Model context window and usage of the last turn"
                >
                  {`· ${fmtK(contextWindow)} Context`}
                  {contextUsed != null &&
                    ` ${Math.min(100, (contextUsed / contextWindow) * 100).toFixed(1)}% used`}
                </span>
              )}
            </div>
            {busy ? (
              <button
                className="composer-send"
                disabled={phase === "stopping"}
                onClick={doCancel}
                title={phase === "stopping" ? "Stopping…" : "Stop"}
                aria-label="Stop"
              >
                {phase === "stopping" ? "…" : "■"}
              </button>
            ) : (
              <button
                className="composer-send primary"
                disabled={!draft.trim()}
                onClick={() => void doSend()}
                title="Send (Enter)"
                aria-label="Send"
              >
                ↑
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
