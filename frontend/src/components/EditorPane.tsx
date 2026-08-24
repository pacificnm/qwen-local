import { useEffect, useState } from "react";
import { DiffEditor, Editor } from "@monaco-editor/react";
import "../lib/monaco";
import { useChat } from "../store/chat";
import { useEditor } from "../store/editor";
import { useRepos } from "../store/repos";

function slugify(input: string): string {
  const base = input.split("/").pop() ?? input;
  const slug = base
    .toLowerCase()
    .replace(/\.[a-z0-9]+$/i, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug || "edits";
}

function CommitForm() {
  const { repos, load } = useRepos();
  const conv = useChat((s) => s.conversations.find((c) => c.id === s.activeId)) ?? null;
  const editor = useEditor();
  const defaultRepoId = editor.fileRepoId ?? conv?.repo_id ?? repos[0]?.id ?? "";
  const [repoId, setRepoId] = useState(defaultRepoId);
  const [filePath, setFilePath] = useState(editor.filePath ?? "");
  const [branch, setBranch] = useState(
    editor.filePath ? `qwen-assist/${slugify(editor.filePath)}` : "qwen-assist/edits",
  );
  const [message, setMessage] = useState("");
  const [openPr, setOpenPr] = useState(true);
  const [prTitle, setPrTitle] = useState("");
  const [prBody, setPrBody] = useState("");

  useEffect(() => {
    void load().catch(() => undefined);
  }, [load]);

  useEffect(() => {
    setRepoId(editor.fileRepoId ?? conv?.repo_id ?? repos[0]?.id ?? "");
    setFilePath(editor.filePath ?? "");
    setBranch(editor.filePath ? `qwen-assist/${slugify(editor.filePath)}` : "qwen-assist/edits");
    // reset only the derived fields; user edits to message/pr survive re-selects of the file
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editor.open, editor.filePath, editor.fileRepoId]);

  const disabled = editor.committing || !repoId || !filePath.trim() || !message.trim() || editor.working === "";

  return (
    <div className="commit-form">
      <div className="commit-grid">
        <label>
          Repo
          <select value={repoId} onChange={(e) => setRepoId(e.target.value)} disabled={!!editor.fileRepoId}>
            <option value="">— select repo —</option>
            {repos.map((r) => (
              <option key={r.id} value={r.id}>
                {r.github_full_name}
              </option>
            ))}
          </select>
        </label>
        <label>
          File path
          <input
            value={filePath}
            placeholder="src/auth.py"
            onChange={(e) => setFilePath(e.target.value)}
            spellCheck={false}
          />
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
          className="primary"
          disabled={disabled}
          onClick={() =>
            void editor
              .commit({
                repoId,
                filePath: filePath.trim(),
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

function EditorBody() {
  const editor = useEditor();
  const showDiff = editor.view === "diff" && editor.original !== null;
  return (
    <div className="editor-body">
      <div className="editor-head">
        <div className="editor-path" title={editor.filePath ?? "chat snippet"}>
          {editor.filePath ?? "chat snippet"}
        </div>
        <div className="editor-viewtoggle" role="group" aria-label="View">
          <button
            className={!showDiff ? "active" : ""}
            onClick={() => editor.setView("edit")}
            disabled={!editor.filePath}
          >
            Edit
          </button>
          <button
            className={showDiff ? "active" : ""}
            onClick={() => editor.setView("diff")}
            disabled={editor.original === null}
            title={editor.original === null ? "Diff needs a repo file as the original" : "Repo original vs. current"}
          >
            Diff
          </button>
        </div>
        <button className="editor-close" onClick={editor.reset}>
          ✕
        </button>
      </div>

      <div className="monaco-box">
        {showDiff ? (
          <DiffEditor
            original={editor.original ?? undefined}
            modified={editor.working}
            language={editor.language}
            theme="vs-dark"
            options={{ minimap: { enabled: false }, fontSize: 13, readOnly: false }}
          />
        ) : (
          <Editor
            height="100%"
            language={editor.language}
            value={editor.working}
            onChange={(v) => editor.setWorking(v ?? "")}
            theme="vs-dark"
            options={{ minimap: { enabled: false }, fontSize: 13, wordWrap: "on" }}
          />
        )}
      </div>

      {editor.commitResult && (
        <div className="banner banner-ok" role="status">
          <div>
            <strong>{editor.commitResult.branch}</strong> · commit{" "}
            <code>{editor.commitResult.commit_sha.slice(0, 8)}</code> pushed
            {editor.commitResult.pr_url ? (
              <>
                {" "}
                →{" "}
                <a
                  href={editor.commitResult.pr_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="pr-link"
                >
                  open PR
                </a>
              </>
            ) : (
              " (branch pushed, no PR)"
            )}
            <span className="banner-dim"> — recorded in the active conversation</span>
          </div>
          <button onClick={editor.clearResult}>dismiss</button>
        </div>
      )}
      {editor.commitError && (
        <div className="banner banner-error" role="alert">
          <span>{editor.commitError}</span>
          <button onClick={editor.clearResult}>dismiss</button>
        </div>
      )}

      {!editor.commitResult && !editor.commitError && <CommitForm />}
    </div>
  );
}

function EmptyState() {
  const editor = useEditor();
  const { repos, load, loaded } = useRepos();
  const [repoId, setRepoId] = useState("");
  const [path, setPath] = useState("");

  useEffect(() => {
    void load().catch(() => undefined);
  }, [load]);

  useEffect(() => {
    if (!repoId) return;
    void editor.loadPickerFiles(repoId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repoId]);

  return (
    <div className="editor-empty">
      <p className="dim">
        Open a repo file to edit and commit it to GitHub, or click{" "}
        <span className="codeblock-btn-sample">⧉ Open in editor</span> on any code block in the chat.
      </p>
      <label className="editor-empty-label">
        Repository
        <select
          value={repoId}
          onChange={(e) => {
            setRepoId(e.target.value);
            setPath("");
          }}
          disabled={!loaded || repos.length === 0}
        >
          <option value="">— select repo —</option>
          {repos.map((r) => (
            <option key={r.id} value={r.id}>
              {r.github_full_name}
            </option>
          ))}
        </select>
      </label>
      {editor.pickerError && <p className="banner banner-error">{editor.pickerError}</p>}
      <label className="editor-empty-label">
        File
        <select
          value={path}
          onChange={(e) => {
            setPath(e.target.value);
            if (e.target.value && repoId) void editor.openRepoFile(repoId, e.target.value);
          }}
          disabled={!repoId || editor.pickerLoading || editor.pickerPaths.length === 0}
        >
          <option value="">
            {editor.pickerLoading
              ? "Loading files…"
              : editor.pickerPaths.length === 0
                ? "No files yet"
                : "— select file —"}
          </option>
          {editor.pickerPaths.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
      </label>
      {repos.length === 0 && <p className="dim">No repositories linked yet — add one on the left.</p>}
    </div>
  );
}

export default function EditorPane() {
  const open = useEditor((s) => s.open);
  return (
    <div className="editor-pane">
      <div className="pane-title">Code</div>
      <div className="editor-scroll">
        {open ? <EditorBody /> : <EmptyState />}
      </div>
    </div>
  );
}
