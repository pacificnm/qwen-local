import { useEffect, useState } from "react";
import { useIssues } from "../../store/issues";
import { useRepos } from "../../store/repos";
import { IssueCreateForm } from "./IssueCreateForm";
import { IssueDetail } from "./IssueDetail";
import { IssueRow } from "./IssueRow";
import { NoRepoHint } from "./NoRepoHint";
import { useTabRepoId } from "./useTabRepoId";

export function IssuesTab() {
  const repoId = useTabRepoId();
  const repos = useRepos((s) => s.repos);

  const items = useIssues((s) => s.items);
  const hasMore = useIssues((s) => s.hasMore);
  const loading = useIssues((s) => s.loading);
  const error = useIssues((s) => s.error);
  const stateFilter = useIssues((s) => s.stateFilter);
  const search = useIssues((s) => s.search);
  const selectedNumber = useIssues((s) => s.selectedNumber);
  const notice = useIssues((s) => s.notice);
  const loadIssues = useIssues((s) => s.loadIssues);
  const loadMore = useIssues((s) => s.loadMore);
  const setStateFilter = useIssues((s) => s.setStateFilter);
  const setSearch = useIssues((s) => s.setSearch);
  const selectIssue = useIssues((s) => s.selectIssue);
  const clearNotice = useIssues((s) => s.clearNotice);

  const [creating, setCreating] = useState(false);

  useEffect(() => {
    if (repoId) {
      void useIssues.getState().loadIssues(repoId);
      void useIssues.getState().loadMeta(repoId);
    }
  }, [repoId]);

  if (!repoId) return <NoRepoHint />;

  const repoName = repos.find((r) => r.id === repoId)?.github_full_name;
  const filtered = search.trim()
    ? items.filter((i) => i.title.toLowerCase().includes(search.trim().toLowerCase()))
    : items;

  if (creating) {
    return (
      <div className="issuestab">
        <IssueCreateForm repoId={repoId} onDone={() => setCreating(false)} />
      </div>
    );
  }

  if (selectedNumber !== null) {
    return (
      <div className="issuestab">
        <IssueDetail repoId={repoId} />
      </div>
    );
  }

  return (
    <div className="issuestab">
      <div className="git-head">
        <div className="git-head-main">
          <div className="git-repo">{repoName ?? "—"}</div>
        </div>
        <button
          type="button"
          className="filetree-refresh"
          title="Refresh issues"
          disabled={loading}
          onClick={() => void loadIssues(repoId)}
        >
          ↻
        </button>
      </div>

      {error && <p className="banner banner-error">{error}</p>}
      {notice && (
        <div className={`banner ${notice.ok ? "banner-ok" : "banner-error"}`} role={notice.ok ? "status" : "alert"}>
          <span>{notice.text}</span>
          <button onClick={clearNotice}>dismiss</button>
        </div>
      )}

      <div className="issues-toolbar">
        <div className="issues-statefilter" role="tablist">
          {(["open", "closed", "all"] as const).map((f) => (
            <button
              key={f}
              type="button"
              className={stateFilter === f ? "active" : ""}
              onClick={() => setStateFilter(repoId, f)}
            >
              {f[0].toUpperCase() + f.slice(1)}
            </button>
          ))}
        </div>
        <input
          placeholder="Filter by title…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          aria-label="Filter issues by title"
        />
        <button type="button" className="primary" onClick={() => setCreating(true)}>
          + New issue
        </button>
      </div>

      {loading && items.length === 0 ? (
        <p className="dim">Loading issues…</p>
      ) : filtered.length === 0 ? (
        <p className="dim">{search ? "No matches." : `No ${stateFilter === "all" ? "" : stateFilter} issues.`}</p>
      ) : (
        <ul className="issues-list">
          {filtered.map((issue) => (
            <IssueRow
              key={issue.number}
              issue={issue}
              onClick={() => void selectIssue(repoId, issue.number)}
            />
          ))}
        </ul>
      )}

      {hasMore && !search && (
        <button type="button" onClick={() => void loadMore()} disabled={loading}>
          {loading ? "Loading…" : "Load more"}
        </button>
      )}
    </div>
  );
}
