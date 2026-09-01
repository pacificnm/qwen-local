/** Send / Stop button for the composer bar.
 * Shows a Stop button while a turn is in flight, otherwise a Send button
 * (disabled until there is draft text to send). */
interface Props {
  busy: boolean;
  phase: string;
  draft: string;
  onSend: () => void;
  onCancel: () => void;
}

export function ButtonComposer({ busy, phase, draft, onSend, onCancel }: Props) {
  if (busy) {
    return (
      <button
        className="composer-send"
        disabled={phase === "stopping"}
        onClick={onCancel}
        title={phase === "stopping" ? "Stopping…" : "Stop"}
        aria-label="Stop"
      >
        {phase === "stopping" ? "…" : "■"}
      </button>
    );
  }
  return (
    <button
      className="composer-send primary"
      disabled={!draft.trim()}
      onClick={onSend}
      title="Send (Enter)"
      aria-label="Send"
    >
      ↑
    </button>
  );
}
