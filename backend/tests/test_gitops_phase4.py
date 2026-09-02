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
from app.repos.errors import FileNotFound, GitError, GithubApiError, InvalidBranch

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
        return 201, {"html_url": "https://github.com/o/r/pull/7", "number": 7}

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


# --- open_branch_pull_request / find_open_pr (Git-tab flow) ------------------


async def test_open_branch_pr_appends_closes_when_issue_given(monkeypatch):
    captured: dict = {}

    async def fake_post(url, headers, payload):
        captured.update(payload)
        return 201, {"html_url": "https://github.com/o/r/pull/12", "number": 12}

    monkeypatch.setattr(gitops, "_post_json", fake_post)
    result = await gitops.open_branch_pull_request(
        "o/r", "pat", "issue-42-fix-thing", "main", "Fix thing", "body text", 42
    )
    assert result == {"number": 12, "url": "https://github.com/o/r/pull/12"}
    assert captured["head"] == "issue-42-fix-thing"
    assert captured["base"] == "main"
    assert "Closes #42" in captured["body"]
    assert "body text" in captured["body"]


async def test_open_branch_pr_omits_closes_without_issue(monkeypatch):
    captured: dict = {}

    async def fake_post(url, headers, payload):
        captured.update(payload)
        return 201, {"html_url": "https://github.com/o/r/pull/13", "number": 13}

    monkeypatch.setattr(gitops, "_post_json", fake_post)
    await gitops.open_branch_pull_request("o/r", "pat", "chore/cleanup", "main", "Cleanup", "", None)
    assert "Closes" not in captured["body"]


async def test_find_open_pr_returns_none_when_empty(monkeypatch):
    async def fake_get(url, headers):
        assert "head=o:feature" in url
        assert "state=open" in url
        return 200, []

    monkeypatch.setattr(gitops, "_get_json", fake_get)
    assert await gitops.find_open_pr("o/r", "pat", "feature") is None


async def test_find_open_pr_returns_first_match(monkeypatch):
    async def fake_get(url, headers):
        return 200, [{"number": 5, "html_url": "https://github.com/o/r/pull/5", "title": "T"}]

    monkeypatch.setattr(gitops, "_get_json", fake_get)
    pr = await gitops.find_open_pr("o/r", "pat", "feature")
    assert pr == {"number": 5, "url": "https://github.com/o/r/pull/5", "title": "T"}


async def test_find_open_pr_raises_on_error_status(monkeypatch):
    async def fake_get(url, headers):
        return 404, {"message": "Not Found"}

    monkeypatch.setattr(gitops, "_get_json", fake_get)
    with pytest.raises(GithubApiError):
        await gitops.find_open_pr("o/r", "pat", "feature")


# --- create_branch (Git-tab flow: no collision suffixing) --------------------


async def test_create_branch_creates_and_checks_out(tmp_path):
    ws = tmp_path / "ws"
    upstream = tmp_path / "upstream"
    _make_upstream_with_clone(upstream, ws / "o__r")
    clone = ws / "o__r"

    branch = await gitops.create_branch(clone, "o/r", "", "issue-7-fix-login")
    assert branch == "issue-7-fix-login"
    assert _git(clone, "branch", "--show-current") == "issue-7-fix-login"


async def test_create_branch_rejects_local_collision(tmp_path):
    ws = tmp_path / "ws"
    upstream = tmp_path / "upstream"
    _make_upstream_with_clone(upstream, ws / "o__r")
    clone = ws / "o__r"
    _git(clone, "checkout", "-q", "-b", "taken")
    _git(clone, "checkout", "-q", "main")

    with pytest.raises(GitError, match="already exists locally"):
        await gitops.create_branch(clone, "o/r", "", "taken")
    # No silent suffix / switch — still on main.
    assert _git(clone, "branch", "--show-current") == "main"


async def test_create_branch_rejects_invalid_name(tmp_path):
    ws = tmp_path / "ws"
    upstream = tmp_path / "upstream"
    _make_upstream_with_clone(upstream, ws / "o__r")

    with pytest.raises(InvalidBranch):
        await gitops.create_branch(ws / "o__r", "o/r", "", "-leading-dash")


# --- merge_pull_request (Git-tab flow) ---------------------------------------


async def test_merge_pull_request_deletes_branch_and_returns_to_default(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    upstream = tmp_path / "upstream"
    _make_upstream_with_clone(upstream, ws / "o__r")
    clone = ws / "o__r"
    _git(clone, "checkout", "-q", "-b", "issue-1-fix")
    (clone / "new.txt").write_text("x\n")
    _git(clone, "add", "new.txt")
    _git(clone, "-c", "user.email=t@t.co", "-c", "user.name=T", "commit", "-q", "-m", "c2")
    await gitops.push_branch(clone, "o/r", "")

    async def fake_put(url, headers, payload):
        assert payload["merge_method"] == "squash"
        return 200, {"merged": True, "sha": "abc123"}

    monkeypatch.setattr(gitops, "_put_json", fake_put)
    result = await gitops.merge_pull_request(clone, "o/r", "", "issue-1-fix", 3, "main")

    assert result == {"merged": True, "sha": "abc123"}
    assert _git(clone, "branch", "--show-current") == "main"
    # The remote branch is gone.
    remote_branches = _git(upstream, "branch", "--list", "issue-1-fix")
    assert remote_branches == ""


async def test_merge_pull_request_raises_when_github_rejects(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    upstream = tmp_path / "upstream"
    _make_upstream_with_clone(upstream, ws / "o__r")
    clone = ws / "o__r"
    _git(clone, "checkout", "-q", "-b", "issue-2-fix")

    async def fake_put(url, headers, payload):
        return 405, {"message": "Pull Request is not mergeable"}

    monkeypatch.setattr(gitops, "_put_json", fake_put)
    with pytest.raises(GithubApiError):
        await gitops.merge_pull_request(clone, "o/r", "", "issue-2-fix", 3, "main")
    # Not merged — still on the feature branch, nothing deleted.
    assert _git(clone, "branch", "--show-current") == "issue-2-fix"


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


async def test_list_file_paths_includes_untracked(tmp_path):
    """Context-menu creates are untracked until committed — the file tree must
    still show them (previously only `git ls-tree HEAD` / tracked files)."""
    repo = tmp_path / "r"
    _init_repo(str(repo))
    (repo / "tracked.txt").write_text("t\n")
    subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "base"], check=True, capture_output=True)
    (repo / "new-from-menu.txt").write_text("n\n")

    paths = await gitops.list_file_paths(repo)

    assert "tracked.txt" in paths
    assert "new-from-menu.txt" in paths
    assert ".git" not in paths


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
        return 201, {"html_url": "https://github.com/o/r/pull/9", "number": 9}

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


# --- repo_state (Git tab snapshot) -------------------------------------------


async def test_repo_state_parses_dirty_and_recent(tmp_path, monkeypatch):
    """Snapshot parses porcelain status + NUL-separated log (short lines
    under 4 chars are skipped; log lines missing fields too)."""
    sep = "\x00"
    log = "\n".join(f"sha{i:02d}{sep}auth{i}{sep}2026-08-0{i + 1}{sep}subject {i}" for i in range(10))

    async def fake_run(args, pat, cwd=None, env_extra=None):
        if args[1] == "branch":
            return "main\n"
        if args[1] == "rev-parse" and args[2] == "HEAD":
            return "a" * 40 + "\n"
        if args[1] == "rev-parse" and args[2] == "--abbrev-ref":
            return "origin/main\n"
        if args[1] == "rev-list":
            return "0 2\n"  # behind 0, ahead 2
        if args[1] == "status":
            # MM (both sides), unstaged modify, untracked, staged rename,
            # staged delete — plus a malformed short line to skip.
            return (
                "MM both.py\n"
                " M unstaged.py\n"
                "?? brand_new.py\n"
                "R  old.py -> new.py\n"
                " D dead.py\n"
                "ab\n"
            )
        if args[1] == "log":
            assert len(args) == 5 and args[2] == "-10", f"expected -10 cap: {args}"
            # NULs must be git's %x00 escape — a raw NUL byte truncates argv.
            assert args[4] == "--pretty=format:%h%x00%an%x00%ad%x00%s", args[4]
            return log + "\n"
        raise AssertionError(f"unexpected git call: {args}")

    monkeypatch.setattr(gitops, "_run", fake_run)

    state = await gitops.repo_state(tmp_path / "o__r", "o/r")
    assert state["branch"] == "main"
    assert state["head_sha"] == "a" * 40
    assert state["upstream"] == "origin/main"
    assert state["ahead"] == 2
    assert state["behind"] == 0
    # "MM" appears on BOTH sides; the rename keeps its old path for unstaging.
    assert state["staged"] == [
        {"status": "M", "path": "both.py", "old_path": None},
        {"status": "R", "path": "new.py", "old_path": "old.py"},
    ]
    assert state["changes"] == [
        {"status": "M", "path": "both.py", "old_path": None},
        {"status": "M", "path": "unstaged.py", "old_path": None},
        {"status": "??", "path": "brand_new.py", "old_path": None},
        {"status": "D", "path": "dead.py", "old_path": None},
    ]
    assert state["dirty"] == [
        {"status": "MM", "path": "both.py"},
        {"status": "M", "path": "unstaged.py"},
        {"status": "??", "path": "brand_new.py"},
        {"status": "R", "path": "new.py"},
        {"status": "D", "path": "dead.py"},
    ]
    assert len(state["recent"]) == 10  # -10 cap honoured by git itself upstream
    assert state["recent"][0] == {
        "sha": "sha00", "author": "auth0", "date": "2026-08-01", "subject": "subject 0",
    }


async def test_repo_state_clean_tree_is_empty(tmp_path, monkeypatch):
    async def fake_run(args, pat, cwd=None, env_extra=None):
        if args[1] == "branch":
            return "main\n"
        if args[1] == "rev-parse" and args[2] == "HEAD":
            return "b" * 40 + "\n"
        if args[1] == "rev-parse" and args[2] == "--abbrev-ref":
            raise GitError("no upstream configured")
        if args[1] == "status":
            return ""  # clean tree
        if args[1] == "log":
            return ""  # empty repo (no commits yet)
        raise AssertionError(f"unexpected git call: {args}")

    monkeypatch.setattr(gitops, "_run", fake_run)
    state = await gitops.repo_state(tmp_path / "o__r", "o/r")
    assert state == {
        "branch": "main",
        "has_commits": True,
        "head_sha": "b" * 40,
        "upstream": None,
        "ahead": None,
        "behind": None,
        "staged": [],
        "changes": [],
        "dirty": [],
        "recent": [],
    }


async def test_repo_state_unborn_head_is_graceful(tmp_path):
    """Zero-commit repo (fresh GitHub repo not pushed yet): no 500 — empty
    head_sha, no log, but staged files still surface for the first commit."""
    repo = tmp_path / "o__r"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True, capture_output=True)
    (repo / "README.md").write_text("# fresh\n")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True, capture_output=True)

    state = await gitops.repo_state(repo, "o/r")
    assert state["branch"] == "main"
    assert state["has_commits"] is False
    assert state["head_sha"] == ""
    assert state["recent"] == []
    assert state["upstream"] is None
    assert state["staged"] == [{"status": "A", "path": "README.md", "old_path": None}]
    assert state["dirty"] == [{"status": "A", "path": "README.md"}]


async def test_ensure_repo_survives_empty_remote(tmp_path):
    """Refresh of an unborn clone when the remote still has zero commits
    (fetch HEAD fails): keep the clone, report the current branch."""
    ws = tmp_path / "ws"
    upstream = tmp_path / "upstream"
    subprocess.run(["git", "init", "-q", "-b", "main", str(upstream)], check=True, capture_output=True)
    clone = ws / "o__empty"
    subprocess.run(["git", "clone", "-q", str(upstream), str(clone)], check=True, capture_output=True)

    repo_dir, branch = await gitops.ensure_repo(ws, "o/empty", pat="")
    assert repo_dir == clone
    assert branch == "main"
    # Still unborn — the failed fetch must not leave the clone broken.
    assert (
        subprocess.run(["git", "-C", str(clone), "rev-parse", "HEAD"], capture_output=True)
    ).returncode != 0


async def test_commit_staged_creates_first_commit_on_unborn_head(tmp_path, monkeypatch):
    """Git-tab 'Commit to current branch' works as the repo's FIRST commit.

    Identity is pinned explicitly so the test does not depend on host git config."""
    monkeypatch.setattr(
        gitops, "_commit_identity",
        lambda: {
            "GIT_AUTHOR_NAME": "T", "GIT_COMMITTER_NAME": "T",
            "GIT_AUTHOR_EMAIL": "t@t.co", "GIT_COMMITTER_EMAIL": "t@t.co",
        },
    )
    tmp_path.joinpath("o__r").mkdir()
    repo = tmp_path / "o__r"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True, capture_output=True)
    (repo / "README.md").write_text("# hi\n")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True, capture_output=True)

    sha = await gitops.commit_staged(repo, "o/r", "Initial commit")
    assert len(sha) == 40
    log = subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "--pretty=%s"], text=True, capture_output=True, check=True
    ).stdout
    assert log.strip() == "Initial commit"


# --- Phase 6: stage / unstage / commit / push (real local repos) -------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, text=True, capture_output=True
    ).stdout.strip()


def _base_commit(repo: Path, name: str, content: str) -> None:
    (repo / name).write_text(content)
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", "base")


def _upstream_of(repo: Path, *refs: str) -> None:
    for ref in refs:
        # no -q: with --verify a *missing* ref exits 1 (check=True raises), an
        # existing ref prints "sha\tref" — assert on the non-empty output.
        assert _git(repo, "show-ref", "--verify", ref)


def test_parse_status_splits_sides_and_renames():
    staged, changes, dirty = gitops._parse_status(
        "MM both.py\n"
        "M  staged.py\n"
        " M unstaged.py\n"
        "?? new-dir/\n"
        " D gone.py\n"
        "R  old.py -> new.py\n"
        "ab\n"
    )
    assert [e["path"] for e in staged] == ["both.py", "staged.py", "new.py"]
    assert staged[2] == {"status": "R", "path": "new.py", "old_path": "old.py"}
    assert [e["path"] for e in changes] == ["both.py", "unstaged.py", "new-dir/", "gone.py"]
    assert [e["status"] for e in dirty] == ["MM", "M", "M", "??", "D", "R"]
    assert len(dirty) == 6  # the short "ab" line was skipped


async def test_stage_paths_rejects_unknown_path(tmp_path):
    repo = tmp_path / "r"
    _init_repo(str(repo))
    _base_commit(repo, "a.txt", "a\n")
    with pytest.raises(GitError):
        await gitops.stage_paths(repo, "o/r", ["nope/missing.txt"])


async def test_stage_unstage_roundtrip_keeps_worktree(tmp_path):
    repo = tmp_path / "r"
    _init_repo(str(repo))
    _base_commit(repo, "hello.txt", "v1\n")
    (repo / "fresh.txt").write_text("new file\n")
    (repo / "hello.txt").write_text("v2\n")  # tracked + modified

    await gitops.stage_paths(repo, "o/r", ["hello.txt", "fresh.txt"])
    state = await gitops.repo_state(repo, "o/r")
    assert sorted(e["path"] for e in state["staged"]) == ["fresh.txt", "hello.txt"]
    assert state["changes"] == []

    await gitops.unstage_paths(repo, "o/r", ["hello.txt", "fresh.txt"])
    state = await gitops.repo_state(repo, "o/r")
    assert state["staged"] == []
    assert sorted(e["path"] for e in state["changes"]) == ["fresh.txt", "hello.txt"]
    # Unstaging must NOT touch the worktree — file content survives.
    assert (repo / "hello.txt").read_text() == "v2\n"
    assert (repo / "fresh.txt").read_text() == "new file\n"


async def test_unstage_rename_requires_both_paths(tmp_path):
    """A staged rename is an add/remove pair in the index; resetting only the
    new path leaves ` D old.py` staged behind (callers must send both)."""
    repo = tmp_path / "r"
    _init_repo(str(repo))
    _base_commit(repo, "old.py", "x\n")
    _git(repo, "mv", "old.py", "new.py")  # stages the rename as one entry

    state = await gitops.repo_state(repo, "o/r")
    assert state["staged"] == [{"status": "R", "path": "new.py", "old_path": "old.py"}]

    # Incomplete unstage (new path only) leaves the delete staged...
    await gitops.unstage_paths(repo, "o/r", ["new.py"])
    assert (await gitops.repo_state(repo, "o/r"))["staged"]
    # ...only the BOTH-paths call clears the index completely.
    await gitops.unstage_paths(repo, "o/r", ["old.py", "new.py"])
    state = await gitops.repo_state(repo, "o/r")
    assert state["staged"] == []
    assert sorted(e["path"] for e in state["changes"]) == ["new.py", "old.py"]


async def test_commit_staged_commits_index_only(tmp_path):
    repo = tmp_path / "r"
    _init_repo(str(repo))
    _base_commit(repo, "a.txt", "a\n")
    (repo / "b.txt").write_text("b\n")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-q", "-m", "two files")

    (repo / "a.txt").write_text("a2\n")  # staged → committed
    (repo / "b.txt").write_text("b2\n")  # left behind in the worktree

    await gitops.stage_paths(repo, "o/r", ["a.txt"])
    sha = await gitops.commit_staged(repo, "o/r", "stage test")
    assert len(sha) == 40

    assert _git(repo, "show", "HEAD:a.txt") == "a2"
    assert _git(repo, "show", "HEAD:b.txt") == "b"  # the b2 edit is NOT committed
    state = await gitops.repo_state(repo, "o/r")
    assert state["staged"] == []
    assert [e["path"] for e in state["changes"]] == ["b.txt"]


async def test_commit_staged_rejects_empty_and_unstaged(tmp_path):
    repo = tmp_path / "r"
    _init_repo(str(repo))
    _base_commit(repo, "a.txt", "a\n")

    with pytest.raises(GitError, match="empty"):
        await gitops.commit_staged(repo, "o/r", "   ")
    (repo / "a.txt").write_text("a2\n")  # modified but NOT staged
    with pytest.raises(GitError, match="nothing staged"):
        await gitops.commit_staged(repo, "o/r", "nope")


async def test_push_branch_new_branch_sets_upstream(tmp_path):
    ws = tmp_path / "ws"
    upstream = tmp_path / "upstream"
    _make_upstream_with_clone(upstream, ws / "o__r")
    clone = ws / "o__r"
    _git(clone, "config", "user.email", "t@t.co")
    _git(clone, "config", "user.name", "T")
    _git(clone, "checkout", "-q", "-b", "feature")

    result = await gitops.push_branch(clone, "o/r", "")
    assert result["branch"] == "feature"
    _upstream_of(upstream, "refs/heads/feature")

    # Upstream tracking is set; local feature == origin/feature → level.
    state = await gitops.repo_state(clone, "o/r")
    assert state["upstream"] == "origin/feature"
    assert state["ahead"] == 0
    assert state["behind"] == 0

    # Second push with nothing new: still succeeds (no upstream error).
    assert (await gitops.push_branch(clone, "o/r", ""))["branch"] == "feature"


async def test_push_branch_rejects_detached_head(tmp_path):
    ws = tmp_path / "ws"
    upstream = tmp_path / "upstream"
    _make_upstream_with_clone(upstream, ws / "o__r")
    clone = ws / "o__r"
    _git(clone, "checkout", "--detach", "HEAD")
    with pytest.raises(GitError, match="detached"):
        await gitops.push_branch(clone, "o/r", "")


async def test_push_branch_materializes_upstream_from_empty_remote(tmp_path):
    """A shallow clone of a zero-commit remote (what `ensure_repo` makes) has no
    `origin/<branch>` remote-tracking ref even after `push -u`, so `@{u}` (the
    Git tab's upstream + ahead/behind) never resolves. `push_branch` must
    materialize the tracking ref after a successful push. Regression pin for the
    reactamp "no upstream yet" case."""
    upstream = tmp_path / "upstream"
    clone = tmp_path / "clone"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(upstream)], check=True, capture_output=True)
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", f"file://{upstream}", str(clone)], check=True, capture_output=True
    )
    _git(clone, "config", "user.email", "t@t.co")
    _git(clone, "config", "user.name", "T")

    def ref_present() -> bool:
        rc = subprocess.run(
            ["git", "-C", str(clone), "show-ref", "--verify", "--quiet", "refs/remotes/origin/main"],
            check=False, capture_output=True,
        ).returncode
        return rc == 0

    assert not ref_present()  # empty remote → no origin/main tracking ref yet

    (clone / "README.md").write_text("# demo\n")
    _git(clone, "add", "README.md")
    _git(clone, "commit", "-q", "-m", "Initial commit")

    assert (await gitops.push_branch(clone, "o/r", ""))["branch"] == "main"

    assert ref_present()  # push_branch materialized the tracking ref

    state = await gitops.repo_state(clone, "o/r")
    assert state["upstream"] == "origin/main"
    assert state["ahead"] == 0
    assert state["behind"] == 0
    assert state["has_commits"] is True


async def test_repo_state_ahead_behind_on_real_repo(tmp_path):
    ws = tmp_path / "ws"
    upstream = tmp_path / "upstream"
    _make_upstream_with_clone(upstream, ws / "o__r")
    clone = ws / "o__r"
    _git(clone, "config", "user.email", "t@t.co")
    _git(clone, "config", "user.name", "T")

    state = await gitops.repo_state(clone, "o/r")
    assert state["upstream"] == "origin/main"
    assert state["ahead"] == 0
    assert state["behind"] == 0

    # Local commit → ahead 1...
    (clone / "local.txt").write_text("x\n")
    _git(clone, "add", "local.txt")
    _git(clone, "commit", "-q", "-m", "local ahead")
    # ...then the upstream moves → behind 1 (diverged). The counts compare
    # against LOCAL tracking refs, so clone must fetch the new commit.
    (upstream / "up.txt").write_text("y\n")
    _git(upstream, "add", "up.txt")
    _git(upstream, "commit", "-q", "-m", "upstream moved")
    _git(clone, "fetch", "-q", "origin")

    state = await gitops.repo_state(clone, "o/r")
    assert state["ahead"] == 1  # right side of @{u}...HEAD
    assert state["behind"] == 1  # left side
