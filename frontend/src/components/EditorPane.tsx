import { useEffect, useState } from "react";
import type { GitFileEntry } from "../lib/api";
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

function statusClass(code: string): string {
  if (code.includes("?")) return "git-st-new";
  if (code.includes("A")) return "git-st-add";
  if (code.includes("D")) return "git-st-del";
  if (code.includes("R")) return "git-st-ren";
  return "git-st-mod";
}

function GitFileRow({
  entry,
  action,
  onAction,
  disabled,
}: {
  entry: GitFileEntry;
  action: string;
  onAction: () => void;
  disabled: boolean;
}) {
  return (
    <li className="git-file">
      <span className={`git-status ${statusClass(entry.status)}`}>{entry.status}</span>
      <span
        className="git-dpath"
        title={entry.old_path ? `${entry.old_path} → ${entry.path}` : entry.path}
      >
        {entry.old_path && <span className="git-oldpath">{entry.old_path} → </span>}
        {entry.path}
      </span>
      <button type="button" className="git-row-btn" disabled={disabled} onClick={onAction}>
        {action}
      </button>
    </li>
  );
}

function GitCommitBox({ repoId, stagedCount }: { repoId: string; stagedCount: number }) {
  const gitBusy = useEditor((s) => s.gitBusy);
  const commitStaged = useEditor((s) => s.commitStaged);
  const [message, setMessage] = useState("");

  const busy = gitBusy === "commit";
  const disabled = !!gitBusy || stagedCount === 0 || !message.trim();

  return (
    <div className="git-section git-commit-sec">
      <h4 className="git-sec-title">
        Commit{stagedCount > 0 ? ` · ${stagedCount} staged` : " · nothing staged yet"}
      </h4>
      <textarea
        rows={3}
        maxLength={500}
        placeholder="commit message — e.g. feat(chunking): tree-sitter aware splits"
        value={message}
        disabled={!!gitBusy}
        onChange={(e) => setMessage(e.target.value)}
      />
      <div className="commit-actions">
        <button
          type="button"
          className="primary"
          disabled={disabled}
          onClick={() =>
            void commitStaged(repoId, message.trim()).then((ok) => {
              if (ok) setMessage("");
            })
          }
        >
          {busy ? "Committing…" : "Commit to current branch"}
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
  const gitBusy = useEditor((s) => s.gitBusy);
  const gitNotice = useEditor((s) => s.gitNotice);
  const stage = useEditor((s) => s.stage);
  const unstage = useEditor((s) => s.unstage);
  const pushBranch = useEditor((s) => s.pushBranch);
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
              {git.head_sha ? (
                <code className="git-sha">{git.head_sha.slice(0, 7)}</code>
              ) : (
                <span className="git-no-commits" title="The remote has no commits yet — stage files and commit below, then push.">
                  no commits yet
                </span>
              )}
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
      {gitNotice && (
        <div
          className={`banner ${gitNotice.ok ? "banner-ok" : "banner-error"}`}
          role={gitNotice.ok ? "status" : "alert"}
        >
          <span>{gitNotice.text}</span>
          <button onClick={() => useEditor.getState().clearGitNotice()}>dismiss</button>
        </div>
      )}
      {gitLoading && !git && <p className="dim">Loading git state…</p>}

      {git && (
        <>
          <div className="git-section">
            <div className="git-sec-head">
              <h4 className="git-sec-title">Staged</h4>
              {git.staged.length > 0 && (
                <button
                  type="button"
                  className="git-sec-action"
                  disabled={!!gitBusy}
                  onClick={() =>
                    void unstage(
                      repoId,
                      git.staged.flatMap((f) => (f.old_path ? [f.old_path, f.path] : [f.path])),
                    )
                  }
                >
                  Unstage all
                </button>
              )}
            </div>
            {git.staged.length === 0 ? (
              <p className="dim">Nothing staged — stage files below.</p>
            ) : (
              <ul className="git-files">
                {git.staged.map((f) => (
                  <GitFileRow
                    key={`${f.status}:${f.path}`}
                    entry={f}
                    action="− Unstage"
                    disabled={!!gitBusy}
                    onAction={() =>
                      void unstage(repoId, f.old_path ? [f.old_path, f.path] : [f.path])
                    }
                  />
                ))}
              </ul>
            )}
          </div>

          <div className="git-section">
            <div className="git-sec-head">
              <h4 className="git-sec-title">Changes</h4>
              {git.changes.length > 0 && (
                <button
                  type="button"
                  className="git-sec-action"
                  disabled={!!gitBusy}
                  onClick={() => void stage(repoId, git.changes.map((f) => f.path))}
                >
                  Stage all
                </button>
              )}
            </div>
            {git.changes.length === 0 ? (
              <p className="dim">No untracked or modified files.</p>
            ) : (
              <ul className="git-files">
                {git.changes.map((f) => (
                  <GitFileRow
                    key={`${f.status}:${f.path}`}
                    entry={f}
                    action="+ Stage"
                    disabled={!!gitBusy}
                    onAction={() => void stage(repoId, [f.path])}
                  />
                ))}
              </ul>
            )}
          </div>

          <GitCommitBox repoId={repoId} stagedCount={git.staged.length} />

          <div className="git-section">
            <h4 className="git-sec-title">Push</h4>
            <div className="git-push-row">
              <div className="git-push-meta">
                {git.upstream ? (
                  <>
                    <code className="git-upstream">{git.upstream}</code>
                    {git.ahead != null && git.behind != null && (
                      <span className="git-ab" title="Ahead / behind upstream (refresh to re-fetch)">
                        {git.ahead > 0 && <span className="git-ab-ahead">↑ {git.ahead}</span>}
                        {git.behind > 0 && <span className="git-ab-behind">↓ {git.behind}</span>}
                        {git.ahead === 0 && git.behind === 0 && (
                          <span className="git-ab-level">up to date</span>
                        )}
                      </span>
                    )}
                  </>
                ) : (
                  <span className="dim">no upstream yet — the first push sets it</span>
                )}
              </div>
              <button
                type="button"
                className="primary git-push-btn"
                disabled={!!gitBusy}
                onClick={() => void pushBranch(repoId)}
              >
                {gitBusy === "push" ? "Pushing…" : `Push ${git.branch || "branch"}`}
              </button>
            </div>
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
