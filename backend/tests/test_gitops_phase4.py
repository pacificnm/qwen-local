"""Phase 4 unit tests: branch validation, safe path resolution, PR open helper.

No network: `open_pull_request` and `_remote_branch_exists` are monkeypatched,
and branch-creation logic is exercised against a real local git repo.
"""

import subprocess

import pytest

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
