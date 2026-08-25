"""Phase 4 unit tests: branch validation, safe path resolution, PR open helper.

No network: `open_pull_request` and `_remote_branch_exists` are monkeypatched,
and branch-creation logic is exercised against a real local git repo.
"""

import os
import subprocess
from pathlib import Path

import pytest

from app.core.settings import Settings
from app.repos import gitops
from app.repos.errors import FileNotFound, GitError, GithubApiError

# --- is_valid_branch ---------------------------------------------------------


def test_branch_valid_names():
    assert gitops.is_valid_branch("main")
    assert gitops.is_valid_branch("fix/auth")
    assert gitops.is_valid_branch("qwen-assist/my-branch_1")
    assert gitops.is_valid_branch("a/b/c")


def test_branch_rejects_bad_names():
    assert not gitops.is_valid_branch("")  # empty
    assert not gitops.is_valid_branch("/leading-slash")
    assert not gitops.is_valid_branch("a//b")  # empty segment
    assert not gitops.is_valid_branch("trailing/")
    assert not gitops.is_valid_branch("has space")
    assert not gitops.is_valid_branch("bad*char")
    assert not gitops.is_valid_branch("x" * 121)  # too long


# --- resolve_safe ------------------------------------------------------------


def test_resolve_safe_accepts_inner_paths(tmp_path):
    target = gitops.resolve_safe(tmp_path, "src/module/file.py")
    assert str(target).startswith(str(tmp_path))
    assert target.name == "file.py"


def test_resolve_safe_rejects_parent_escape(tmp_path):
    with pytest.raises(GitError):
        gitops.resolve_safe(tmp_path, "../outside.py")


def test_resolve_safe_rejects_deep_escape(tmp_path):
    with pytest.raises(GitError):
        gitops.resolve_safe(tmp_path, "a/b/../../../etc/passwd")


def test_resolve_safe_rejects_absolute_and_empty(tmp_path):
    with pytest.raises(GitError):
        gitops.resolve_safe(tmp_path, "/etc/passwd")
    with pytest.raises(GitError):
        gitops.resolve_safe(tmp_path, "")


# --- open_pull_request (monkeypatched transport) -----------------------------


async def test_open_pr_returns_url_on_201(monkeypatch):
    async def fake_post(url, headers, payload):
        return 201, {"html_url": "https://github.com/o/r/pull/7"}

    monkeypatch.setattr(gitops, "_post_json", fake_post)
    url = await gitops.open_pull_request("o/r", "pat", "T", "B", "head", "main")
    assert url == "https://github.com/o/r/pull/7"


async def test_open_pr_raises_on_non_201(monkeypatch):
    async def fake_post(url, headers, payload):
        return 409, {"message": "A pull request already exists"}

    monkeypatch.setattr(gitops, "_post_json", fake_post)
    with pytest.raises(GithubApiError):
        await gitops.open_pull_request("o/r", "pat", "T", "B", "head", "main")


async def test_open_pr_raises_when_url_missing(monkeypatch):
    async def fake_post(url, headers, payload):
        return 201, {}  # no html_url

    monkeypatch.setattr(gitops, "_post_json", fake_post)
    with pytest.raises(GithubApiError):
        await gitops.open_pull_request("o/r", "pat", "T", "B", "head", "main")


# --- _create_branch collision suffixing (real local repo) --------------------


def _init_repo(path: str) -> None:
    subprocess.run(["git", "init", "-q", path], check=True, capture_output=True)
    subprocess.run(["git", "-C", path, "config", "user.email", "t@t.co"], check=True, capture_output=True)
    subprocess.run(["git", "-C", path, "config", "user.name", "T"], check=True, capture_output=True)


async def test_create_branch_no_collision(monkeypatch, tmp_path):
    _init_repo(str(tmp_path / "r"))

    async def no_remote(*a, **k):
        return False

    monkeypatch.setattr(gitops, "_remote_branch_exists", no_remote)

    name = await gitops._create_branch(tmp_path / "r", "", "fix/auth")
    assert name == "fix/auth"


async def test_create_branch_collision_gets_suffix(monkeypatch, tmp_path):
    _init_repo(str(tmp_path / "r"))

    async def remote_yes(*a, **k):
        return True

    async def local_no(*a, **k):
        return False

    # Simulate the exact name already existing on the remote, none locally.
    monkeypatch.setattr(gitops, "_remote_branch_exists", remote_yes)
    monkeypatch.setattr(gitops, "_local_ref_exists", local_no)

    name = await gitops._create_branch(tmp_path / "r", "", "fix/auth")
    # Suffix policy: `<branch>-<timestamp>`
    assert name.startswith("fix/auth-")


# --- read_file / list_file_paths --------------------------------------------


async def test_read_file_missing_raises(tmp_path):
    with pytest.raises(FileNotFound):
        gitops.read_file(tmp_path, "does/not/exist.py")


def test_scrub_redacts_pat():
    assert "secret123" not in gitops._scrub("url=https://x:secret123@h", "secret123")
    assert "[redacted]" in gitops._scrub("url=https://x:secret123@h", "secret123")


# --- _commit_identity (Phase 4 commit authorship) ---------------------------
# Regression: the backend container user has no ambient git config, so
# `git commit` must be given an explicit identity or it fails with
# "Author identity unknown".


def test_commit_identity_maps_settings(monkeypatch):
    fake = Settings(git_author_name="Test Bot", git_author_email="bot@example.com")
    monkeypatch.setattr(gitops, "get_settings", lambda: fake)
    env = gitops._commit_identity()
    assert env == {
        "GIT_AUTHOR_NAME": "Test Bot",
        "GIT_COMMITTER_NAME": "Test Bot",
        "GIT_AUTHOR_EMAIL": "bot@example.com",
        "GIT_COMMITTER_EMAIL": "bot@example.com",
    }


def test_commit_identity_omits_unset_values(monkeypatch):
    fake = Settings(git_author_name="", git_author_email="")
    monkeypatch.setattr(gitops, "get_settings", lambda: fake)
    assert gitops._commit_identity() == {}


def test_git_commit_without_ambient_identity(tmp_path, monkeypatch):
    """Prove `git commit` succeeds (and is correctly authored) when the
    process has NO global/system git identity — the container-user condition
    that broke Phase 4."""
    repo = tmp_path / "r"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "f.txt").write_text("hello\n")
    subprocess.run(["git", "-C", str(repo), "add", "f.txt"], check=True)

    # Neutralize any ambient (global/system) git identity: point both at a
    # config that sets EMPTY user.name/email. Empty values are required (rather
    # than an empty file) because git otherwise falls back to an implicit
    # `user@hostname` ident on hosts where that is resolvable — exactly the
    # case the container does NOT have (undotted hostname → "Author identity
    # unknown").
    no_id_cfg = tmp_path / "no-ident.cfg"
    no_id_cfg.write_text("[user]\n\tname =\n\temail =\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(no_id_cfg))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(no_id_cfg))

    # Sanity: with neutralized config and no identity env, commit must FAIL.
    no_id = subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "no-id"],
        capture_output=True, text=True,
    )
    assert no_id.returncode != 0

    # ...but with the explicit identity env commit_file injects, it succeeds
    # and the commit carries the configured author.
    ident = gitops._commit_identity()
    r = subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "msg"],
        env={**os.environ, **ident}, capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    author = subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "--format=%an <%ae>"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert author == f"{ident['GIT_AUTHOR_NAME']} <{ident['GIT_AUTHOR_EMAIL']}>"


# --- ensure_repo default-branch + commit_file worktree restore --------------
# Regression: commit_file previously restored the worktree with
# `git checkout -- <default>`, which git parses as a *pathspec* and rejects
# for a branch name ("pathspec 'main' did not match any file(s)"). The restore
# must be a branch *switch* (`checkout -f <default>`).


def _make_upstream_with_clone(upstream, clone: Path) -> None:
    """Local upstream repo on `main` + a plain clone of it (origin → upstream)."""
    subprocess.run(["git", "init", "-q", "-b", "main", str(upstream)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(upstream), "config", "user.email", "t@t.co"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(upstream), "config", "user.name", "T"], check=True, capture_output=True
    )
    (upstream / "hello.txt").write_text("hi\n")
    subprocess.run(["git", "-C", str(upstream), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(upstream), "commit", "-q", "-m", "c1"], check=True, capture_output=True)
    subprocess.run(["git", "clone", "-q", str(upstream), str(clone)], check=True, capture_output=True)


async def test_ensure_repo_reports_upstream_default(tmp_path):
    ws = tmp_path / "ws"
    upstream = tmp_path / "upstream"
    _make_upstream_with_clone(upstream, ws / "o__r")

    repo_dir, branch = await gitops.ensure_repo(ws, "o/r", pat="")
    assert repo_dir == ws / "o__r"
    assert branch == "main"  # resolved from origin/HEAD, not the parked branch


async def test_commit_file_restores_worktree_to_default_branch(tmp_path):
    # Full offline commit_file: branch → commit → push → restore. The final
    # restore is the regression being pinned.
    ws = tmp_path / "ws"
    upstream = tmp_path / "upstream"
    _make_upstream_with_clone(upstream, ws / "o__r")

    result = await gitops.commit_file(
        workspace=ws,
        full_name="o/r",
        pat="",
        file_path="hello.txt",
        content="edited\n",
        branch="qwen-assist/fix",
        commit_message="update hello",
        open_pr=False,
        pr_title="",
        pr_body="",
    )
    assert result.branch == "qwen-assist/fix"
    assert result.commit_sha
    assert result.pr_url is None

    clone = ws / "o__r"
    # Worktree is BACK on the upstream default branch after the call.
    head = subprocess.run(
        ["git", "-C", str(clone), "symbolic-ref", "--short", "HEAD"],
        check=True, text=True, capture_output=True,
    ).stdout.strip()
    assert head == "main"

    # The commit landed on the (remote) feature branch.
    ref = subprocess.run(
        ["git", "-C", str(upstream), "show-ref", "--verify", "refs/heads/qwen-assist/fix"],
        check=True, text=True, capture_output=True,
    ).stdout
    assert result.commit_sha in ref


# --- commit_workspace (agent edits → branch → push → optional PR) -----------
# The Qwen-Agent repo tools stage uncommitted edits in the shared worktree;
# commit_workspace branches from HEAD, `add -A`s them, commits, pushes, and
# (optionally) opens a PR — WITHOUT the ensure_repo reset/clean that would
# wipe the very edits being committed.


async def test_commit_workspace_commits_agent_edits(tmp_path):
    ws = tmp_path / "ws"
    upstream = tmp_path / "upstream"
    _make_upstream_with_clone(upstream, ws / "o__r")
    clone = ws / "o__r"

    # The agent creates a new file and edits an existing one (uncommitted).
    (clone / "notes" / "agent.md").parent.mkdir(parents=True, exist_ok=True)
    (clone / "notes" / "agent.md").write_text("written by the agent\n")
    (clone / "hello.txt").write_text("hi\nedited by the agent\n")

    result = await gitops.commit_workspace(
        ws, "o/r", pat="", message="agent: add note + edit hello",
        branch="qwen-assist/agent-change",
    )
    assert result.branch == "qwen-assist/agent-change"
    assert result.commit_sha
    assert result.pr_url is None

    # Worktree restored to the upstream default branch.
    head = subprocess.run(
        ["git", "-C", str(clone), "symbolic-ref", "--short", "HEAD"],
        check=True, text=True, capture_output=True,
    ).stdout.strip()
    assert head == "main"

    # The committed content landed on the feature branch, not main.
    ref = subprocess.run(
        ["git", "-C", str(upstream), "show-ref", "--verify", "refs/heads/qwen-assist/agent-change"],
        check=True, text=True, capture_output=True,
    ).stdout
    assert result.commit_sha in ref
    shown = subprocess.run(
        ["git", "-C", str(upstream), "show", "qwen-assist/agent-change:notes/agent.md"],
        check=True, text=True, capture_output=True,
    ).stdout
    assert "written by the agent" in shown


async def test_commit_workspace_opens_pr_when_asked(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    upstream = tmp_path / "upstream"
    _make_upstream_with_clone(upstream, ws / "o__r")
    (ws / "o__r" / "new.txt").write_text("x\n")

    captured: dict = {}

    async def fake_post(url, headers, payload):
        captured.update(url=url, payload=payload)
        return 201, {"html_url": "https://github.com/o/r/pull/9"}

    monkeypatch.setattr(gitops, "_post_json", fake_post)
    result = await gitops.commit_workspace(
        ws, "o/r", pat="", message="agent change", branch="agent/pr",
        open_pr=True, pr_title="Agent change", pr_body="from the agent",
    )
    assert result.pr_url == "https://github.com/o/r/pull/9"
    assert "/pulls" in captured["url"]
    assert captured["payload"]["head"] == "agent/pr"
    assert captured["payload"]["base"] == "main"


async def test_commit_workspace_rejects_clean_tree(tmp_path):
    ws = tmp_path / "ws"
    upstream = tmp_path / "upstream"
    _make_upstream_with_clone(upstream, ws / "o__r")  # clean clone: no edits
    with pytest.raises(GitError):
        await gitops.commit_workspace(ws, "o/r", pat="", message="nothing here")


async def test_commit_workspace_requires_clone(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)  # no clone for this full_name
    with pytest.raises(GitError):
        await gitops.commit_workspace(ws, "missing/repo", pat="", message="no clone")


async def test_commit_workspace_rejects_empty_message(tmp_path):
    ws = tmp_path / "ws"
    upstream = tmp_path / "upstream"
    _make_upstream_with_clone(upstream, ws / "o__r")
    (ws / "o__r" / "d.txt").write_text("d\n")
    with pytest.raises(GitError):
        await gitops.commit_workspace(ws, "o/r", pat="", message="   ")
