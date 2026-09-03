import { create } from "zustand";
import * as api from "../lib/api";

export type StreamPhase =
  | "idle"
  | "waiting"
  | "thinking"
  | "streaming"
  | "tool"
  | "stopping"
  | "error"
  | "cancelled";

interface ChatState {
  conversations: api.Conversation[];
  hasMore: boolean;
  nextCursor: string | null;
  search: string;
  activeId: string | null;
  /** Project whose conversations are listed (set by loadConversations). */
  scopeId: string | null;
  messages: api.ChatMessage[];
  phase: StreamPhase;
  requestId: string | null;
  assistantText: string;
  thinkingText: string;
  toolCalls: api.ToolCallInfo[];
  error: string | null;
  /** Reasoning effort (low|medium|high|xhigh), persisted across reloads. */
  effort: api.Effort;
  /** Chat mode (ask|plan|code), persisted across reloads. */
  mode: api.ChatMode;
  /** Last turn's prompt tokens (how much of the model's context was used). */
  contextUsed: number | null;
  /** Non-fatal warning from the backend (e.g. LLM call budget exhausted). */
  warning: string | null;

  loadConversations: (projectId: string) => Promise<void>;
  loadMore: () => Promise<void>;
  setSearch: (q: string) => void;
  openConversation: (id: string) => Promise<void>;
  closeConversation: () => void;
  newConversation: () => Promise<string>;
  rename: (id: string, title: string) => Promise<void>;
  remove: (id: string) => Promise<void>;
  send: (text: string, model: string, abort: AbortController) => Promise<void>;
  setEffort: (e: api.Effort) => void;
  setMode: (m: api.ChatMode) => void;
  cancel: () => Promise<void>;
  clearError: () => void;
  clearWarning: () => void;
  /** Append a persisted note (e.g. a recorded PR link) to the active conversation. */
  appendMessage: (convId: string, message: api.ChatMessage) => void;
}

let searchTimer: ReturnType<typeof setTimeout> | null = null;

const EFFORT_KEY = "qc.effort";

function loadEffort(): api.Effort {
  try {
    const stored = localStorage.getItem(EFFORT_KEY);
    if (stored && (api.EFFORT_LEVELS as readonly string[]).includes(stored)) {
      return stored as api.Effort;
    }
  } catch {
    /* SSR / blocked storage — fall back to the default */
  }
  return "medium";
}

const MODE_KEY = "qc.mode";

function loadMode(): api.ChatMode {
  try {
    const stored = localStorage.getItem(MODE_KEY);
    if (stored && (api.MODES as readonly string[]).includes(stored)) {
      return stored as api.ChatMode;
    }
  } catch {
    /* SSR / blocked storage — fall back to the default */
  }
  return "code";
}

export const useChat = create<ChatState>()((set, get) => ({
  conversations: [],
  hasMore: false,
  nextCursor: null,
  search: "",
  activeId: null,
  scopeId: null,
  messages: [],
  phase: "idle",
  requestId: null,
  assistantText: "",
  thinkingText: "",
  toolCalls: [],
  error: null,
  effort: loadEffort(),
  mode: loadMode(),
  contextUsed: null,
  warning: null,

  async loadConversations(projectId) {
    const page = await api.listConversations({ project_id: projectId });
    set({
      scopeId: projectId,
      conversations: page.items,
      hasMore: page.has_more,
      nextCursor: page.next_cursor,
      search: "",
    });
  },

  async loadMore() {
    const s = get();
    if (!s.nextCursor || !s.scopeId) return;
    const page = await api.listConversations({ project_id: s.scopeId, cursor: s.nextCursor });
    set({
      conversations: [...s.conversations, ...page.items],
      hasMore: page.has_more,
      nextCursor: page.next_cursor,
    });
  },

  setSearch(q) {
    set({ search: q });
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = setTimeout(async () => {
      const scopeId = get().scopeId;
      if (!scopeId) return;
      try {
        const page = await api.listConversations({ project_id: scopeId, q: q || undefined });
        set({
          conversations: page.items,
          hasMore: page.has_more,
          nextCursor: page.next_cursor,
        });
      } catch {
        /* keep prior list; search is transient */
      }
    }, 250);
  },

  async openConversation(id) {
    if (get().phase !== "idle" || get().phase === "stopping") {
      const err = new Error("Finish or stop the current generation first.");
      throw err;
    }
    const detail = await api.getConversation(id);
    set({
      activeId: id,
      messages: detail.messages,
      phase: "idle",
      assistantText: "",
      thinkingText: "",
      toolCalls: [],
      error: null,
      warning: null,
      contextUsed: null,
    });
  },

  closeConversation() {
    set({
      activeId: null,
      messages: [],
      assistantText: "",
      thinkingText: "",
      toolCalls: [],
      warning: null,
      contextUsed: null,
    });
  },

  async newConversation() {
    const scopeId = get().scopeId;
    if (!scopeId) {
      throw new Error("Select a project first — every conversation belongs to one.");
    }
    const conv = await api.createConversation({ project_id: scopeId });
    set((s) => ({
      conversations: [conv, ...s.conversations],
      activeId: conv.id,
      messages: [],
      assistantText: "",
      thinkingText: "",
      toolCalls: [],
      error: null,
      warning: null,
      contextUsed: null,
    }));
    return conv.id;
  },

  async rename(id, title) {
    const updated = await api.renameConversation(id, title);
    set((s) => ({
      conversations: s.conversations.map((c) => (c.id === id ? { ...c, title: updated.title } : c)),
    }));
  },

  async remove(id) {
    await api.deleteConversation(id);
    const wasActive = get().activeId === id;
    set((s) => ({
      conversations: s.conversations.filter((c) => c.id !== id),
      ...(wasActive
        ? {
            activeId: null,
            messages: [],
            assistantText: "",
            thinkingText: "",
            toolCalls: [],
            warning: null,
            contextUsed: null,
          }
        : {}),
    }));
  },

  async send(text, model, abort) {
    const s = get();
    if (s.phase !== "idle") return;
    let convId = s.activeId;
    if (!convId) {
      convId = await get().newConversation();
    }
    const modelId = model;

    // optimistic user bubble
    const optimistic: api.ChatMessage = {
      id: `local-user-${Date.now()}`,
      role: "user",
      content: text,
      sequence: s.messages.length,
    };
    set({
      phase: "waiting",
      assistantText: "",
      thinkingText: "",
      toolCalls: [],
      error: null,
      // Keep the previous turn's context-used figure: it carries over until
      // this turn's `done` event reports a fresh value (don't blank it here).
      messages: [...s.messages, optimistic],
    });

    try {
      await api.streamChat(
        {
          conversation_id: convId,
          message: text,
          model: modelId,
          effort: get().effort,
          mode: get().mode,
        },
        (event, data) => {
          const cur = get();
          switch (event) {
            case "session":
              set({
                requestId: (data.request_id as string) ?? cur.requestId,
                phase: "thinking",
              });
              break;
            case "thinking":
              set({
                phase: "thinking",
                thinkingText: cur.thinkingText + ((data.text as string) ?? ""),
              });
              break;
            case "token":
              set({
                phase: "streaming",
                assistantText: cur.assistantText + ((data.text as string) ?? ""),
              });
              break;
            case "tool_start":
              set({
                phase: "tool",
                toolCalls: [
                  ...cur.toolCalls,
                  { name: (data.tool as string) ?? "", arguments: data.arguments ?? undefined },
                ],
              });
              break;
            case "tool_output": {
              const i = data.index as number;
              if (typeof i !== "number" || i < 0 || i >= cur.toolCalls.length) break;
              set({
                toolCalls: cur.toolCalls.map((tc, j) =>
                  j === i ? { ...tc, output: (tc.output ?? "") + ((data.text as string) ?? "") } : tc,
                ),
              });
              break;
            }
            case "tool_end": {
              const i = data.index as number;
              if (typeof i !== "number" || i < 0 || i >= cur.toolCalls.length) break;
              set({
                toolCalls: cur.toolCalls.map((tc, j) =>
                  j === i
                    ? { ...tc, ok: Boolean(data.ok), duration_ms: (data.duration_ms as number) ?? undefined }
                    : tc,
                ),
              });
              break;
            }
            case "warning":
              set({ warning: (data.message as string) ?? "warning" });
              break;
            case "error":
              set({ phase: "error", error: (data.message as string) ?? "generation failed" });
              break;
            case "cancelled":
              set({ phase: "cancelled" });
              break;
            case "done": {
              const usage = data.usage as { prompt_tokens?: number } | undefined;
              if (usage && typeof usage.prompt_tokens === "number") {
                set({ contextUsed: usage.prompt_tokens });
              }
              break;
            }
            case "code_block":
              break;
            default:
              break;
          }
        },
        abort.signal,
      );

      // Terminal event landed (done/cancelled/error handled above). Re-fetch so
      // persisted text + tool_calls match the DB, and the title may have changed.
      const detail = await api.getConversation(convId);
      const fresh = get();
      let msgs = detail.messages;
      const lastMsg = msgs[msgs.length - 1];
      if (fresh.assistantText && lastMsg && lastMsg.role === "assistant" && !(lastMsg.content ?? "").trim()) {
        // The answer only ever existed as streamed tokens (e.g. the model's
        // tool-call budget ended before any final answer was persisted):
        // keep what the user actually saw instead of rendering an empty bubble.
        msgs = [...msgs.slice(0, -1), { ...lastMsg, content: fresh.assistantText }];
      }
      set({
        messages: msgs,
        conversations: fresh.conversations.map((c) =>
          c.id === convId ? { ...c, title: detail.conversation.title, updated_at: detail.conversation.updated_at } : c,
        ),
        assistantText: "",
        thinkingText: "",
        toolCalls: [],
        phase: fresh.phase === "error" ? "error" : "idle",
      });
    } catch (e) {
      const isAbort = e instanceof DOMException && e.name === "AbortError";
      if (!isAbort) {
        set({
          phase: "error",
          error: e instanceof Error ? e.message : String(e),
        });
      }
    } finally {
      set({ requestId: null });
    }
  },

  async cancel() {
    const s = get();
    if (!s.requestId || s.phase === "stopping") return;
    set({ phase: "stopping" });
    try {
      await api.cancelChat(s.requestId);
    } catch (e) {
      set({ phase: "stopping", error: e instanceof Error ? e.message : String(e) });
    }
  },

  clearError() {
    set({ error: null, phase: get().phase === "error" ? "idle" : get().phase });
  },

  clearWarning() {
    set({ warning: null });
  },

  setEffort(e) {
    set({ effort: e });
    try {
      localStorage.setItem(EFFORT_KEY, e);
    } catch {
      /* blocked storage — the value still applies for this session */
    }
  },

  setMode(m) {
    set({ mode: m });
    try {
      localStorage.setItem(MODE_KEY, m);
    } catch {
      /* blocked storage — the value still applies for this session */
    }
  },

  appendMessage(convId, message) {
    const s = get();
    if (s.activeId !== convId) return;
    set((cur) => ({
      messages: [...cur.messages, message],
      conversations: cur.conversations.map((c) =>
        c.id === convId ? { ...c, updated_at: new Date().toISOString() } : c,
      ),
    }));
  },
}));
