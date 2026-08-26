import { DiffEditor, Editor } from "@monaco-editor/react";
import "../lib/monaco";
import ChatPane from "./ChatPane";
import { useEditor, type EditorTab } from "../store/editor";

/** One editor tab's body: head (path + view toggle) + Monaco + commit hint. */
function EditorBody({ tab }: { tab: EditorTab }) {
  const setWorking = useEditor((s) => s.setWorking);
  const setView = useEditor((s) => s.setView);
  const showDiff = tab.view === "diff" && tab.original !== null;

  const loading = tab.original === null && !tab.loadError && tab.repoId !== null;
  const readOnly = loading;

  return (
    <div className="editor-body">
      <div className="editor-head">
        <div className="editor-path" title={tab.path ?? tab.label}>
          {tab.path ?? tab.label}
        </div>
        <div className="editor-viewtoggle" role="group" aria-label="View">
          <button
            type="button"
            className={showDiff ? "" : "active"}
            onClick={() => setView(tab.id, "edit")}
            disabled={readOnly}
          >
            Edit
          </button>
          <button
            type="button"
            className={showDiff ? "active" : ""}
            onClick={() => setView(tab.id, "diff")}
            disabled={tab.original === null}
            title={tab.original === null ? "Diff needs a repo file as the original" : "Repo original vs. current"}
          >
            Diff
          </button>
        </div>
      </div>

      {tab.loadError && (
        <p className="banner banner-error" style={{ margin: "0 1rem" }}>
          {tab.loadError}
        </p>
      )}
      {loading && !tab.loadError && <p className="dim" style={{ padding: "0 1rem" }}>Loading…</p>}

      <div className="monaco-box">
        {showDiff ? (
          <DiffEditor
            original={tab.original ?? undefined}
            modified={tab.working}
            language={tab.language}
            theme="vs-dark"
            options={{ minimap: { enabled: false }, fontSize: 13, readOnly }}
          />
        ) : (
          <Editor
            height="100%"
            language={tab.language}
            value={tab.working}
            onChange={(v) => setWorking(tab.id, v ?? "")}
            theme="vs-dark"
            options={{
              minimap: { enabled: false },
              fontSize: 13,
              wordWrap: "on",
              readOnly,
            }}
          />
        )}
      </div>

    </div>
  );
}

/** Center pane: fixed, non-closable Chat tab + one closable tab per open file. */
export default function MainPane() {
  const tabs = useEditor((s) => s.editorTabs);
  const activeId = useEditor((s) => s.activeTabId);
  const activeTab = tabs.find((t) => t.id === activeId) ?? null;
  const chatActive = activeId === null;

  return (
    <main className="pane pane-center mainpane">
      <div className="maintabs" role="tablist" aria-label="Panels">
        <button
          type="button"
          role="tab"
          aria-selected={chatActive}
          aria-controls="mainpane-tabs"
          className={chatActive ? "maintab active" : "maintab"}
          onClick={() => useEditor.getState().focusChat()}
          title="Chat (fixed)"
        >
          ✦ Chat
        </button>
        {tabs.map((t) => {
          const active = t.id === activeId;
          const dirty = t.original !== null && t.working !== t.original;
          return (
            <span
              key={t.id}
              role="tab"
              tabIndex={0}
              aria-selected={active}
              className={active ? "maintab active" : "maintab"}
              title={t.path ?? t.label}
              onClick={() => useEditor.getState().focusTab(t.id)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") useEditor.getState().focusTab(t.id);
              }}
            >
              {t.path !== null && dirty && (
                <span className="maintab-dot" aria-label="Uncommitted edits" />
              )}
              {t.label}
              <button
                type="button"
                className="maintab-x"
                aria-label={`Close ${t.label}`}
                onClick={(e) => {
                  e.stopPropagation();
                  useEditor.getState().closeTab(t.id);
                }}
              >
                ✕
              </button>
            </span>
          );
        })}
      </div>

      <div className="mainpanes" id="mainpane-tabs">
        <section
          className={chatActive ? "tabpane chatpane" : "tabpane chatpane hidden"}
          aria-hidden={!chatActive}
        >
          <ChatPane />
        </section>
        {activeTab && (
          <section className="tabpane" key={activeTab.id} aria-hidden={false}>
            <EditorBody tab={activeTab} />
          </section>
        )}
      </div>
    </main>
  );
}
