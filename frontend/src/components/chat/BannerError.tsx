import { useChat } from "../../store/chat";

/**
 * Error + warning banners shown in the chat scroll area.
 *
 * Renders the store-level `warning` (non-fatal, e.g. LLM call budget
 * exhausted) and `error` (both with dismiss buttons), plus the local
 * `streamErr` (a send/stream failure, passed in as a prop). Returns null
 * when none are present.
 */
export function BannerError({ streamErr }: { streamErr: string | null }) {
  const { error, clearError, warning, clearWarning } = useChat();

  if (!error && !streamErr && !warning) return null;

  return (
    <>
      {warning && (
        <div className="banner banner-warning" role="status">
          <span>{warning}</span>
          <button onClick={clearWarning}>dismiss</button>
        </div>
      )}
      {error && (
        <div className="banner banner-error" role="alert">
          <span>{error}</span>
          <button onClick={clearError}>dismiss</button>
        </div>
      )}
      {streamErr && <p className="banner banner-error">{streamErr}</p>}
    </>
  );
}
