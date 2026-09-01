/** Context-window status chip: shows the model's window size and % used last turn. */
import { useChat } from "../../store/chat";
import { useModels } from "../../store/models";

/** 128000 → "128.0k" for the context-status figure. */
function fmtK(n: number): string {
  return `${(n / 1000).toFixed(1)}k`;
}

export function ContextWindow() {
  const contextUsed = useChat((s) => s.contextUsed);
  const { models, selectedId } = useModels();
  const contextWindow = models.find((m) => m.id === selectedId)?.context_window ?? null;

  if (contextWindow == null) return null;

  return (
    <span className="ctx-status" title="Model context window and usage of the last turn">
      {`· ${fmtK(contextWindow)} Context`}
      {contextUsed != null &&
        ` ${Math.min(100, (contextUsed / contextWindow) * 100).toFixed(1)}% used`}
    </span>
  );
}
