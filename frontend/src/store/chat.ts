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
  /** Repo a new conversation should bind to (null = general chat, no RAG). */
  newChatRepoId: string | null;
  messages: api.ChatMessage[];
  phase: StreamPhase;
  requestId: string | null;
  assistantText: string;
  thinkingText: string;
  toolCalls: api.ToolCallInfo[];
  error: string | null;

  loadConversations: () => Promise<void>;
  loadMore: () => Promise<void>;
  setSearch: (q: string) => void;
  setNewChatRepo: (id: string | null) => void;
  openConversation: (id: string) => Promise<void>;
  closeConversation: () => void;
  newConversation: (repoId?: string | null) => Promise<string>;
  rename: (id: string, title: string) => Promise<void>;
  remove: (id: string) => Promise<void>;
  send: (text: string, model: string, abort: AbortController) => Promise<void>;
  cancel: () => Promise<void>;
  clearError: () => void;
  /** Append a persisted note (e.g. a recorded PR link) to the active conversation. */
  appendMessage: (convId: string, message: api.ChatMessage) => void;
}

let searchTimer: ReturnType<typeof setTimeout> | null = null;

export const useChat = create<ChatState>()((set, get) => ({
  conversations: [],
  hasMore: false,
  nextCursor: null,
  search: "",
  activeId: null,
  newChatRepoId: null,
  messages: [],
  phase: "idle",
  requestId: null,
  assistantText: "",
  thinkingText: "",
  toolCalls: [],
  error: null,

  async loadConversations() {
    const page = await api.listConversations();
    set({
      conversations: page.items,
      hasMore: page.has_more,
      nextCursor: page.next_cursor,
    });
  },

  async loadMore() {
    const s = get();
    if (!s.nextCursor) return;
    const page = await api.listConversations({ cursor: s.nextCursor });
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
      try {
        const page = await api.listConversations(q ? { q } : undefined);
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

  setNewChatRepo(id) {
    set({ newChatRepoId: id });
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
    });
  },

  closeConversation() {
    set({
      activeId: null,
      messages: [],
      assistantText: "",
      thinkingText: "",
      toolCalls: [],
    });
  },

  async newConversation(repoId) {
    const conv = await api.createConversation({
      repo_id: repoId ?? undefined,
      model_default: undefined,
    });
    set((s) => ({
      conversations: [conv, ...s.conversations],
      activeId: conv.id,
      messages: [],
      assistantText: "",
      thinkingText: "",
      toolCalls: [],
      error: null,
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
        ? { activeId: null, messages: [], assistantText: "", thinkingText: "", toolCalls: [] }
        : {}),
    }));
  },

  async send(text, model, abort) {
    const s = get();
    if (s.phase !== "idle") return;
    let convId = s.activeId;
    if (!convId) {
      convId = await get().newConversation(get().newChatRepoId);
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
      messages: [...s.messages, optimistic],
    });

    try {
      await api.streamChat(
        { conversation_id: convId, message: text, model: modelId },
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
            case "error":
              set({ phase: "error", error: (data.message as string) ?? "generation failed" });
              break;
            case "cancelled":
              set({ phase: "cancelled" });
              break;
            case "done":
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
      set({
        messages: detail.messages,
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
