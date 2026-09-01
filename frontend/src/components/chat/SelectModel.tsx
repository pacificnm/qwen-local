/** Model picker in the composer bar. Reads the model store directly; takes
 * `busy` as a prop since it's derived from `phase` in ChatPane. */
import { useModels } from "../../store/models";

export function SelectModel({ busy }: { busy: boolean }) {
  const { models, selectedId, loaded, select } = useModels();

  return (
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
  );
}
