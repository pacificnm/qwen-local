/** The chat input textarea. Owns no state — receives draft value, busy flag,
 * and callbacks from the parent so the file-mention effect can still focus it. */
import type { RefObject } from "react";

interface Props {
  draft: string;
  setDraft: (v: string) => void;
  busy: boolean;
  onSend: () => void;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
}

export function TextareaComposer({ draft, setDraft, busy, onSend, textareaRef }: Props) {
  return (
    <textarea
      ref={textareaRef}
      rows={3}
      placeholder="Ask about your codebase… (Enter to send, Shift+Enter for newline)"
      value={draft}
      disabled={busy}
      onChange={(e) => setDraft(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          void onSend();
        }
      }}
      aria-label="Message"
    />
  );
}
