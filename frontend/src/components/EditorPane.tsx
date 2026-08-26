import { useEffect, useState } from "react";
import { useEditor } from "../store/editor";
import { useProjects } from "../store/projects";
import { useRepos } from "../store/repos";
import FileTree from "./FileTree";

function slugify(input: string): string {
  const base = input.split("/").pop() ?? input;
  const slug = base
    .toLowerCase()
    .replace(/\.[a-z0-9]+$/i, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug || "edits";
}

/** Repo the tabs operate on: what the Code tab loaded, else the active
 * project's repo, else the focused tab's repo, else the first linked repo. */
function useTabRepoId(): string | null {
  const treeRepoId = useEditor((s) => s.treeRepoId);
  const activeRepo = useProjects((s) => s.projects.find((p) => p.id === s.activeId)?.repo ?? null);
  const focused = useEditor(
    (s) => s.editorTabs.find((t) => t.id === s.activeTabId)?.repoId ?? null,
  );
  const repos = useRepos((s) => s.repos);
  return treeRepoId ?? activeRepo?.id ?? focused ?? repos[0]?.id ?? null;
}

function NoRepoHint() {
  return (
    <div className="editor-empty">
      <p className="dim">
        No repository yet — select a project that has a repo, or link one in
        the project card on the left.
      </p>
    </div>
  );
}

function CodeTab() {
  const repoId = useTabRepoId();
  if (!repoId) return <NoRepoHint />;
  return <FileTree repoId={repoId} />;
}

/** Commits the focused main-pane tab (the file open in the center editor). */
function CommitForm({ repoId }: { repoId: string }) {
  const editor = useEditor();
  const focused = useEditor((s) => s.editorTabs.find((t) => t.id === s.activeTabId) ?? null);
  const fileOpen = !!focused && focused.repoId === repoId && !!focused.path;

  const [branch, setBranch] = useState(() =>
    focused?.path ? `qwen-assist/${slugify(focused.path)}` : "qwen-assist/edits",
  );
  const [message, setMessage] = useState("");
  const [openPr, setOpenPr] = useState(true);
  const [prTitle, setPrTitle] = useState("");
  const [prBody, setPrBody] = useState("");

  useEffect(() => {
    setBranch(focused?.path ? `qwen-assist/${slugify(focused.path)}` : "qwen-assist/edits");
  }, [focused?.path]);

  const disabled =
    editor.committing || !fileOpen || !message.trim() || (focused?.working ?? "") === "";

  return (
    <div className="commit-form">
      <div className="commit-grid">
        <label>
          File
          {fileOpen ? (
            <code className="commit-file">{focused?.path}</code>
          ) : (
            <span className="commit-file-muted">open a file tab in the center pane first</span>
          )}
        </label>
        <label>
          Branch
          <input value={branch} onChange={(e) => setBranch(e.target.value)} spellCheck={false} />
        </label>
        <label>
          Commit message
          <input
            value={message}
            placeholder="fix(auth): correct PAT scope check"
            onChange={(e) => setMessage(e.target.value)}
            maxLength={500}
          />
        </label>
      </div>
      <label className="commit-pr-check">
        <input type="checkbox" checked={openPr} onChange={(e) => setOpenPr(e.target.checked)} />
        Open a pull request
      </label>
      {openPr && (
        <div className="commit-pr-extra">
          <input
            value={prTitle}
            placeholder="PR title (defaults to commit message)"
            onChange={(e) => setPrTitle(e.target.value)}
            maxLength={300}
          />
          <textarea
            rows={3}
            value={prBody}
            placeholder="PR body (optional — a summary + file list is generated)"
            onChange={(e) => setPrBody(e.target.value)}
          />
        </div>
      )}
      <div className="commit-actions">
        <button
          type="button"
          className="primary"
          disabled={disabled}
          onClick={() =>
            void editor
              .commit({
                repoId,
                filePath: focused?.path ?? "",
                message: message.trim(),
                branch: branch.trim() || "qwen-assist/edits",
                openPr,
                prTitle: prTitle.trim() || null,
                prBody: prBody.trim() || null,
              })
              .then((ok) => {
                if (ok) {
                  setMessage("");
                  setPrTitle("");
                  setPrBody("");
                }
              })
          }
        >
          {editor.committing ? "Committing…" : "↑ Commit to GitHub"}
        </button>
      </div>
    </div>
  );
}

function GitTab() {
  const repoId = useTabRepoId();
  const git = useEditor((s) => s.git);
  const gitLoading = useEditor((s) => s.gitLoading);
  const gitError = useEditor((s) => s.gitError);
  const commitResult = useEditor((s) => s.commitResult);
  const commitError = useEditor((s) => s.commitError);
  const repos = useRepos((s) => s.repos);

  useEffect(() => {
    if (repoId) void useEditor.getState().loadGit(repoId);
  }, [repoId]);

  if (!repoId) return <NoRepoHint />;

  const repoName = repos.find((r) => r.id === repoId)?.github_full_name;
  const load = () => {
    if (repoId) void useEditor.getState().loadGit(repoId);
  };

  return (
    <div className="gittab">
      <div className="git-head">
        <div className="git-head-main">
          <div className="git-repo">{repoName ?? "—"}</div>
          {git && (
            <div className="git-branchline">
              <span className="git-branch">● {git.branch || "detached HEAD"}</span>
              <code className="git-sha">{git.head_sha.slice(0, 7)}</code>
              {git.dirty.length > 0 ? (
                <span className="git-dirtycount">{git.dirty.length} dirty</span>
              ) : (
                <span className="git-clean">clean</span>
              )}
            </div>
          )}
        </div>
        <button
          type="button"
          className="filetree-refresh"
          title="Refresh git state"
          disabled={gitLoading}
          onClick={load}
        >
          ↻
        </button>
      </div>

      {gitError && <p className="banner banner-error">{gitError}</p>}
      {gitLoading && !git && <p className="dim">Loading git state…</p>}

      {git && (
        <>
          <div className="git-section">
            <h4 className="git-sec-title">Working tree</h4>
            {git.dirty.length === 0 ? (
              <p className="dim">Clean — no uncommitted changes.</p>
            ) : (
              <ul className="git-dirty">
                {git.dirty.map((d) => (
                  <li key={`${d.status}:${d.path}`}>
                    <span className="git-status">{d.status}</span>
                    <span className="git-dpath">{d.path}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div className="git-section">
            <h4 className="git-sec-title">Recent commits</h4>
            {git.recent.length === 0 ? (
              <p className="dim">No commits yet.</p>
            ) : (
              <ul className="git-log">
                {git.recent.map((c) => (
                  <li key={c.sha}>
                    <code className="git-csha">{c.sha}</code>
                    <span className="git-subject" title={c.subject}>
                      {c.subject}
                    </span>
                    <span className="git-cmeta">
                      {c.author} · {c.date}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}

      {git && <CommitForm repoId={repoId} />}
      {!git && !gitError && (
        <div className="commit-card">
          <p className="dim">Git state is required to commit (the sync clone must exist).</p>
        </div>
      )}

      {commitResult && (
        <div className="banner banner-ok" role="status">
          <div>
            <strong>{commitResult.branch}</strong> · commit{" "}
            <code>{commitResult.commit_sha.slice(0, 8)}</code> pushed
            {commitResult.pr_url ? (
              <>
                {" "}
                →{" "}
                <a href={commitResult.pr_url} target="_blank" rel="noopener noreferrer" className="pr-link">
                  open PR
                </a>
              </>
            ) : (
              " (branch pushed, no PR)"
            )}
            <span className="banner-dim"> — recorded in the active conversation</span>
          </div>
          <button onClick={() => useEditor.getState().clearResult()}>dismiss</button>
        </div>
      )}
      {commitError && (
        <div className="banner banner-error" role="alert">
          <span>{commitError}</span>
          <button onClick={() => useEditor.getState().clearResult()}>dismiss</button>
        </div>
      )}
    </div>
  );
}

export default function EditorPane() {
  const tab = useEditor((s) => s.tab);
  const activeId = useProjects((s) => s.activeId);
  const activeRepoId = useProjects(
    (s) => s.projects.find((p) => p.id === s.activeId)?.repo?.id ?? null,
  );

  // Selecting a project defaults the tabs to that repo on the Code tab:
  // close all open editor tabs and preload its tree + git snapshot.
  useEffect(() => {
    const ed = useEditor.getState();
    ed.reset();
    if (activeRepoId) {
      void ed.loadTree(activeRepoId);
      void ed.loadGit(activeRepoId);
    }
  }, [activeId, activeRepoId]);

  const setTab = (t: "code" | "git") => useEditor.getState().setTab(t);

  return (
    <div className="editor-pane">
      <div className="rtabbar" role="tablist" aria-label="Panel">
        <button
          role="tab"
          id="rtab-code"
          aria-selected={tab === "code"}
          aria-controls="rtab-panes"
          className={tab === "code" ? "active" : ""}
          onClick={() => setTab("code")}
        >
          Code
        </button>
        <button
          role="tab"
          id="rtab-git"
          aria-selected={tab === "git"}
          aria-controls="rtab-panes"
          className={tab === "git" ? "active" : ""}
          onClick={() => setTab("git")}
        >
          Git
        </button>
      </div>

      <div className="editor-scroll" id="rtab-panes">
        <section className={tab === "code" ? "tabpane" : "tabpane hidden"}>
          <CodeTab />
        </section>
        <section className={tab === "git" ? "tabpane" : "tabpane hidden"}>
          <GitTab />
        </section>
      </div>
    </div>
  );
}
