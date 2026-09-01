import { useEditor } from "../../store/editor";
import { useProjects } from "../../store/projects";
import { useRepos } from "../../store/repos";

/** Repo the tabs operate on: what the Code tab loaded, else the active
 * project's repo, else the focused tab's repo, else the first linked repo. */
export function useTabRepoId(): string | null {
  const treeRepoId = useEditor((s) => s.treeRepoId);
  const activeRepo = useProjects((s) => s.projects.find((p) => p.id === s.activeId)?.repo ?? null);
  const focused = useEditor(
    (s) => s.editorTabs.find((t) => t.id === s.activeTabId)?.repoId ?? null,
  );
  const repos = useRepos((s) => s.repos);
  return treeRepoId ?? activeRepo?.id ?? focused ?? repos[0]?.id ?? null;
}
