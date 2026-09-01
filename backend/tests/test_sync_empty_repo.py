"""Syncing a zero-commit repo (fresh GitHub repo) finishes cleanly instead of
erroring — so a just-linked repo stays usable for its first commit+push."""

import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace

from app.repos import gitops, sync


class _FakeDB:
    """Async-context-manager stand-in for the session `get`/`commit` surface."""

    def __init__(self, repo):
        self._repo = repo
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, _model, _id):
        return self._repo

    async def commit(self):
        self.commits += 1


async def test_zero_commit_sync_is_clean_noop(tmp_path, monkeypatch):
    repo = SimpleNamespace(
        github_full_name="o/empty",
        default_branch="HEAD",  # link-time placeholder
        last_commit_sha=None,
        last_synced_at=None,
    )
    db = _FakeDB(repo)
    monkeypatch.setattr(sync, "get_session_factory", lambda: (lambda: db))

    # Unborn worktree (what a clone of an empty remote looks like).
    worktree = tmp_path / "o__empty"
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(worktree)], check=True, capture_output=True
    )

    async def fake_ensure_repo(workspace: Path, full_name: str, pat: str):
        return worktree, "main"

    monkeypatch.setattr(gitops, "ensure_repo", fake_ensure_repo)

    repo_id = uuid.uuid4()
    job = sync.submit(repo_id)
    await sync._tasks[repo_id]

    assert job.stage == "done"
    assert job.error is None
    assert repo.last_synced_at is not None
    assert repo.default_branch == "main"  # placeholder replaced with the real branch
    assert db.commits >= 1
