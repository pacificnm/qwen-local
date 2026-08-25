"""Git operations for repo ingestion + Phase 4 branch → commit → PR flow.

Clones live under the app workspace (docker-compose volume `./workspace`) so
Phase 4 reuses the same working copy for branch → commit → PR work.
Clone URLs are never logged; any PAT is stripped from error messages.
"""

import asyncio
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.core.settings import get_settings

from .errors import FileNotFound, GitError, GithubApiError, InvalidBranch

FULL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9._-]+$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,119}$")

# One commit at a time per repo: the working copy is shared state.
_repo_locks: dict[str, asyncio.Lock] = {}


def _lock_for(full_name: str) -> asyncio.Lock:
    lock = _repo_locks.get(full_name)
    if lock is None:
        lock = asyncio.Lock()
        _repo_locks[full_name] = lock
    return lock


def is_valid_full_name(full_name: str) -> bool:
    return FULL_NAME_RE.fullmatch(full_name) is not None


def workspace_repo_dir(workspace: Path, full_name: str) -> Path:
    """`owner/name` → `workspace/owner__name` (validated: no traversal)."""
    return workspace / full_name.replace("/", "__")


def _clone_url(full_name: str, pat: str) -> str:
    auth = f"x-access-token:{pat}@" if pat else ""
    return f"https://{auth}github.com/{full_name}.git"


def _scrub(message: str, pat: str) -> str:
    return message.replace(pat, "[redacted]") if pat else message


async def _run(
    args: list[str],
    pat: str,
    cwd: str | None = None,
    env_extra: dict[str, str] | None = None,
) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    if env_extra:
        env.update(env_extra)
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        detail = (err or out).decode("utf-8", "replace").strip() or f"git {args[0]} failed"
        raise GitError(_scrub(detail, pat))
    return out.decode("utf-8", "replace")


@dataclass(frozen=True)
class RepoFile:
    path: str
    blob_sha: str


async def _upstream_default_branch(repo_dir: Path) -> str | None:
    """Upstream default branch name via the `origin/HEAD` symref (set by
    clone), or None when it cannot be resolved."""
    try:
        out = await _run(
            ["git", "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD"],
            pat="",
            cwd=str(repo_dir),
        )
    except GitError:
        return None
    name = out.strip()
    if name.startswith("origin/"):
        name = name[len("origin/") :]
    return name or None


async def _current_branch(repo_dir: Path) -> str:
    branch = (await _run(["git", "symbolic-ref", "--short", "HEAD"], pat="", cwd=str(repo_dir))).strip()
    return branch or "HEAD"


async def ensure_repo(workspace: Path, full_name: str, pat: str) -> tuple[Path, str]:
    """Shallow-clone if missing, otherwise refresh; returns (repo_dir, branch).

    The returned `branch` is the upstream DEFAULT branch and the worktree is
    left checked out on it: Phase 4 branch creation and PR bases must not
    depend on which branch a previous run left the shared worktree on.
    """
    repo_dir = workspace_repo_dir(workspace, full_name)
    if (repo_dir / ".git").is_dir():
        # Refresh in place. `reset --hard` + `clean -fd` is intended: a sync
        # re-points the working copy at upstream HEAD (Phase 4 commits happen
        # on branches, not on the sync checkout).
        await _run(["git", "fetch", "--depth", "1", "origin", "HEAD"], pat=pat, cwd=str(repo_dir))
        branch = await _upstream_default_branch(repo_dir) or await _current_branch(repo_dir)
        # A previous Phase 4 run may have parked the worktree on a feature
        # branch — switch back to the true default before aligning upstream.
        await _run(["git", "checkout", "-f", branch], pat=pat, cwd=str(repo_dir))
        await _run(["git", "reset", "--hard", "FETCH_HEAD"], pat=pat, cwd=str(repo_dir))
        await _run(["git", "clean", "-fd"], pat=pat, cwd=str(repo_dir))
    else:
        workspace.mkdir(parents=True, exist_ok=True)
        await _run(["git", "clone", "--depth", "1", _clone_url(full_name, pat), str(repo_dir)], pat=pat)
        branch = await _upstream_default_branch(repo_dir) or await _current_branch(repo_dir)
    return repo_dir, branch


async def list_tree(repo_dir: Path) -> list[RepoFile]:
    """All tracked files at HEAD with blob shas (drives the incremental diff)."""
    out = await _run(["git", "ls-tree", "-r", "-z", "HEAD"], pat="", cwd=str(repo_dir))
    files: list[RepoFile] = []
    for entry in out.split("\0"):
        if not entry:
            continue
        meta, _, path = entry.partition("\t")
        parts = meta.split()
        if len(parts) >= 3:
            files.append(RepoFile(path=path, blob_sha=parts[2]))
    return files


async def head_sha(repo_dir: Path) -> str:
    return (await _run(["git", "rev-parse", "HEAD"], pat="", cwd=str(repo_dir))).strip()


def remove_repo(workspace: Path, full_name: str) -> None:
    shutil.rmtree(workspace_repo_dir(workspace, full_name), ignore_errors=True)


# ---------------------------------------------------------------------------
# Phase 4: editor reads + branch → commit → PR
# ---------------------------------------------------------------------------


def is_valid_branch(branch: str) -> bool:
    if not BRANCH_RE.fullmatch(branch) or "//" in branch or branch.endswith("/"):
        return False
    return True


def resolve_safe(repo_dir: Path, rel_path: str) -> Path:
    """Resolve `rel_path` inside `repo_dir`; raise if it escapes the worktree."""
    if not rel_path or rel_path.startswith("/") or "\x00" in rel_path:
        raise GitError("invalid file path")
    if ".." in rel_path.split("/"):
        raise GitError("invalid file path")
    base = repo_dir.resolve()
    target = (base / rel_path).resolve()
    if target != base and base not in target.parents:
        raise GitError("invalid file path")
    return target


async def list_file_paths(repo_dir: Path) -> list[str]:
    return [f.path for f in (await list_tree(repo_dir)) if (repo_dir / f.path).is_file()]


def read_file(repo_dir: Path, rel_path: str) -> str:
    target = resolve_safe(repo_dir, rel_path)
    if not target.is_file():
        raise FileNotFound(f"file not found in repository: {rel_path}")
    return target.read_text(encoding="utf-8", errors="replace")


@dataclass(frozen=True)
class CommitResult:
    branch: str
    commit_sha: str
    pr_url: str | None


def _commit_identity() -> dict[str, str]:
    """Env giving `git commit` an explicit author/committer identity.

    The backend runs as a container user with no ambient git config, so without
    this `git commit` fails with "Author identity unknown". Identity comes from
    Settings (GIT_AUTHOR_NAME / GIT_AUTHOR_EMAIL).
    """
    s = get_settings()
    env: dict[str, str] = {}
    if s.git_author_name:
        env["GIT_AUTHOR_NAME"] = s.git_author_name
        env["GIT_COMMITTER_NAME"] = s.git_author_name
    if s.git_author_email:
        env["GIT_AUTHOR_EMAIL"] = s.git_author_email
        env["GIT_COMMITTER_EMAIL"] = s.git_author_email
    return env


async def _local_ref_exists(repo_dir: Path, ref: str) -> bool:
    try:
        await _run(["git", "rev-parse", "--verify", "--quiet", ref], pat="", cwd=str(repo_dir))
        return True
    except GitError:
        return False


async def _remote_branch_exists(repo_dir: Path, pat: str, branch: str) -> bool:
    out = await _run(["git", "ls-remote", "--heads", "origin", branch], pat=pat, cwd=str(repo_dir))
    return out.strip() != ""


async def _create_branch(repo_dir: Path, pat: str, branch: str) -> str:
    """Create `branch` from the current HEAD (upstream default).

    Collision policy (API.md): if the name is already on the remote, append a
    timestamp suffix and retry once. A stale local ref is re-pointed with -B.
    """
    name = branch
    if await _remote_branch_exists(repo_dir, pat, branch):
        name = f"{branch}-{int(time.time())}"
    if await _local_ref_exists(repo_dir, f"refs/heads/{name}"):
        flag = "-B"  # re-point a stale local ref
    else:
        flag = "-b"
    await _run(["git", "checkout", flag, name], pat=pat, cwd=str(repo_dir))
    return name


async def _post_json(url: str, headers: dict, payload: dict) -> tuple[int, dict]:
    """Small GitHub REST helper (module-level so tests can monkeypatch it)."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        resp = await client.post(url, headers=headers, json=payload)
    try:
        data = resp.json()
    except ValueError:
        data = {}
    return resp.status_code, data if isinstance(data, dict) else {"message": str(data)}


async def open_pull_request(
    full_name: str,
    pat: str,
    title: str,
    body: str,
    head: str,
    base: str,
) -> str:
    status, data = await _post_json(
        f"https://api.github.com/repos/{full_name}/pulls",
        {
            "Authorization": f"token {pat}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        {"title": title, "body": body, "head": head, "base": base},
    )
    if status != 201:
        raise GithubApiError(f"GitHub PR creation failed ({status}): {data.get('message', 'unknown error')}")
    url = data.get("html_url")
    if not url:
        raise GithubApiError("GitHub did not return a PR URL")
    return str(url)


async def commit_file(
    workspace: Path,
    full_name: str,
    pat: str,
    file_path: str,
    content: str,
    branch: str,
    commit_message: str,
    open_pr: bool,
    pr_title: str,
    pr_body: str,
) -> CommitResult:
    """Branch from upstream HEAD → write file → commit → push → optional PR.

    Raises FileNotFound / InvalidBranch / GithubApiError / GitError. The
    working copy is restored to the default branch before returning; the
    commit stays safe on the remote branch.
    """
    if not is_valid_branch(branch):
        raise InvalidBranch("branch name is not a valid git ref name")
    async with _lock_for(full_name):
        repo_dir, default_branch = await ensure_repo(workspace, full_name, pat)
        target = resolve_safe(repo_dir, file_path)
        if not target.is_file():
            raise FileNotFound(f"file not found on the base branch: {file_path}")

        name = await _create_branch(repo_dir, pat, branch)
        await asyncio.to_thread(target.write_text, content, encoding="utf-8")
        await _run(["git", "add", "--", file_path], pat=pat, cwd=str(repo_dir))
        await _run(
            ["git", "commit", "-m", commit_message],
            pat=pat,
            cwd=str(repo_dir),
            env_extra=_commit_identity(),
        )
        sha = (await _run(["git", "rev-parse", "HEAD"], pat=pat, cwd=str(repo_dir))).strip()
        await _run(["git", "push", "origin", name], pat=pat, cwd=str(repo_dir))

        pr_url = None
        if open_pr:
            pr_url = await open_pull_request(
                full_name, pat, pr_title, pr_body, name, default_branch
            )

        # Leave the shared worktree back on the default branch — a branch
        # *switch* (`checkout -- <name>` would parse the name as a pathspec
        # and fail for branch names). The commit stays on the remote branch.
        if default_branch != "HEAD":
            await _run(["git", "checkout", "-f", default_branch], pat=pat, cwd=str(repo_dir))

    return CommitResult(branch=name, commit_sha=sha, pr_url=pr_url)


async def commit_workspace(
    workspace: Path,
    full_name: str,
    pat: str,
    *,
    message: str,
    branch: str | None = None,
    open_pr: bool = False,
    pr_title: str = "",
    pr_body: str = "",
) -> CommitResult:
    """Commit the agent's working-tree edits: branch → `add -A` → commit → push → optional PR.

    Mirrors `commit_file` but commits whatever the agent changed in the
    shared worktree (creates, edits, deletes) instead of one file. Crucially
    it does NOT run the `ensure_repo` refresh first: that path does
    `reset --hard` + `clean -fd`, which would wipe the very edits being
    committed. The repo must already be cloned (the chat turn only happens
    against a synced, bound repo).

    Raises GitError when there is nothing to commit or the clone is missing,
    InvalidBranch / GithubApiError as usual.
    """
    if not message.strip():
        raise GitError("commit message must not be empty")
    name = branch or f"qwen-assist/change-{int(time.time())}"
    if not is_valid_branch(name):
        raise InvalidBranch("branch name is not a valid git ref name")
    async with _lock_for(full_name):
        repo_dir = workspace_repo_dir(workspace, full_name)
        if not (repo_dir / ".git").is_dir():
            raise GitError("repository is not cloned yet — run a sync first")
        status = await _run(["git", "status", "--porcelain"], pat=pat, cwd=str(repo_dir))
        if not status.strip():
            raise GitError("nothing to commit: the repository has no pending changes")
        default_branch = await _upstream_default_branch(repo_dir) or await _current_branch(repo_dir)

        await _create_branch(repo_dir, pat, name)
        await _run(["git", "add", "-A"], pat=pat, cwd=str(repo_dir))
        await _run(
            ["git", "commit", "-m", message],
            pat=pat,
            cwd=str(repo_dir),
            env_extra=_commit_identity(),
        )
        sha = (await _run(["git", "rev-parse", "HEAD"], pat=pat, cwd=str(repo_dir))).strip()
        await _run(["git", "push", "origin", name], pat=pat, cwd=str(repo_dir))

        pr_url = None
        if open_pr:
            pr_url = await open_pull_request(
                full_name, pat, pr_title or message, pr_body, name, default_branch
            )

        if default_branch != "HEAD":
            await _run(["git", "checkout", "-f", default_branch], pat=pat, cwd=str(repo_dir))

    return CommitResult(branch=name, commit_sha=sha, pr_url=pr_url)
