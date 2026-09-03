/** Model picker in the composer bar. Lists the project's three named model
 * roles first (Coding/Chat/Compaction — resolved from project settings,
 * falling back to the global default for whichever role isn't overridden),
 * then a separator, then every model actually installed on the Ollama
 * server. Reads the model store directly; takes `busy` as a prop since it's
 * derived from `phase` in ChatPane. */
import { useActiveProjectSettings } from "../../store/activeProjectSettings";
import { useModels } from "../../store/models";

const ROLES: {
  label: string;
  settingsKey: "coding_model" | "fast_chat_model" | "compaction_model";
  defaultKey: "is_default" | "is_default_fast_chat" | "is_default_compaction";
}[] = [
  { label: "Coding", settingsKey: "coding_model", defaultKey: "is_default" },
  { label: "Chat", settingsKey: "fast_chat_model", defaultKey: "is_default_fast_chat" },
  { label: "Compaction", settingsKey: "compaction_model", defaultKey: "is_default_compaction" },
];

export function SelectModel({ busy }: { busy: boolean }) {
  const { models, selectedId, loaded, select } = useModels();
  const projectSettings = useActiveProjectSettings((s) => s.settings);

  const roleOptions = ROLES.map(({ label, settingsKey, defaultKey }) => {
    const id = projectSettings?.[settingsKey] || models.find((m) => m[defaultKey])?.id;
    return id ? { label, id } : null;
  }).filter((o): o is { label: string; id: string } => o !== null);

  return (
    <select
      className="model-select"
      value={selectedId}
      disabled={!loaded || models.length === 0 || busy}
      onChange={(e) => select(e.target.value)}
      aria-label="Model"
      title="Model"
    >
      {roleOptions.map((o) => (
        <option key={`role-${o.label}`} value={o.id}>
          {o.label}
        </option>
      ))}
      {roleOptions.length > 0 && <option disabled>──────────</option>}
      {models.map((m) => (
        <option key={m.id} value={m.id}>
          {m.label}
        </option>
      ))}
    </select>
  );
}
