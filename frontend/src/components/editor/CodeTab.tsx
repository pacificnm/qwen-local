import FileTree from "../FileTree";
import { NoRepoHint } from "./NoRepoHint";
import { useTabRepoId } from "./useTabRepoId";

export function CodeTab() {
  const repoId = useTabRepoId();
  if (!repoId) return <NoRepoHint />;
  return <FileTree repoId={repoId} />;
}
