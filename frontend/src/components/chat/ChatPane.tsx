import { useEffect, useRef, useState } from "react";
import { onFileMention } from "../../lib/fileMentionBus";
import { useChat } from "../../store/chat";
import { useModels } from "../../store/models";
import { BannerError } from "./BannerError";
import { BannerWelcome } from "./BannerWelcome";
import { ChatMessages } from "./ChatMessages";
import { ButtonComposer } from "./ButtonComposer";
import { ContextWindow } from "./ContextWindow";
import { SelectEffort } from "./SelectEffort";
import { SelectModel } from "./SelectModel";
import { StreamingAssistant } from "./StreamingAssistant";
import { StreamingThinking } from "./StreamingThinking";
import { TextareaComposer } from "./TextareaComposer";

export default function ChatPane() {
  const { messages, phase, assistantText, thinkingText, send, cancel } = useChat();
  const { selectedId } = useModels();

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

  return (
    <div className="chat-pane">
      <div className="chat-scroll" ref={scrollRef}>
        <BannerWelcome />

        <ChatMessages />

        <StreamingThinking />
        <StreamingAssistant />

        {phase === "stopping" && <p className="chat-status">Stopping…</p>}
        {phase === "cancelled" && <p className="chat-status cancelled">■ Stopped — partial answer saved.</p>}

        <BannerError streamErr={streamErr} />
      </div>

      <div className="composer">
        <div className="composer-box">
          <TextareaComposer
            draft={draft}
            setDraft={setDraft}
            busy={busy}
            onSend={doSend}
            textareaRef={composerRef}
          />
          <div className="composer-bar">
            <div className="composer-bar-left">
              <SelectModel busy={busy} />
              <SelectEffort />
              <ContextWindow />
            </div>
            <ButtonComposer
              busy={busy}
              phase={phase}
              draft={draft}
              onSend={() => void doSend()}
              onCancel={doCancel}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
