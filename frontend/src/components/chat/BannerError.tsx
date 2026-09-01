import { useChat } from "../../store/chat";

/**
 * Error banners shown in the chat scroll area.
 *
 * Renders the store-level `error` (with a dismiss button) and the local
 * `streamErr` (a send/stream failure, passed in as a prop). Returns null when
 * neither is present.
 */
export function BannerError({ streamErr }: { streamErr: string | null }) {
  const { error, clearError } = useChat();

  if (!error && !streamErr) return null;

  return (
    <>
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
