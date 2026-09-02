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

from app.core.settings import get_settings

from .errors import FileExists, FileNotFound, GitError, GithubApiError, InvalidBranch
from .github_api import get_json, github_headers, post_json, put_json

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
        try:
            await _run(["git", "fetch", "--depth", "1", "origin", "HEAD"], pat=pat, cwd=str(repo_dir))
        except GitError as exc:
            if "couldn't find remote ref" not in str(exc):
                raise
            # Remote still has zero commits (fresh repo): keep the unborn
            # clone as-is; the sync reports zero files and the first commit
            # is pushed from the Git tab.
            return repo_dir, await _current_branch(repo_dir)
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


def _parse_status(status_out: str) -> tuple[list[dict], list[dict], list[dict]]:
    """Split `git status --porcelain` (v1) into (staged, changes, dirty).

    `staged` = index-vs-HEAD entries; `changes` = worktree-vs-index entries
    plus untracked. A path with staged edits AND further unstaged edits
    appears in both lists (git's "MM"). Renames ("R  old -> new") record the
    new path as `path` and the old as `old_path` — unstaging must reset both.
    `dirty` (legacy field) stays the in-order union with the old status codes.
    """
    staged: list[dict] = []
    changes: list[dict] = []
    dirty: list[dict] = []
    for line in status_out.splitlines():
        if len(line) < 4:
            continue
        x, y = line[0], line[1]
        path = line[3:]
        old_path: str | None = None
        if " -> " in path:  # rename/copy: "R  old.py -> new.py"
            old_raw, _, path = path.partition(" -> ")
            old_path = old_raw or None
        code = (x + y).strip() or "??"
        dirty.append({"status": code, "path": path})
        if x == "?" or y == "?":  # untracked ("??", incl. collapsed dirs)
            changes.append({"status": code, "path": path, "old_path": None})
            continue
        if x != " ":
            staged.append({"status": x, "path": path, "old_path": old_path})
        if y != " ":
            changes.append({"status": y, "path": path, "old_path": None})
    return staged, changes, dirty


async def _upstream_and_counts(repo_dir: Path) -> tuple[str | None, int | None, int | None]:
    """Upstream ref + (ahead, behind) counts; (None, None, None) without upstream."""
    try:
        upstream = (
            await _run(["git", "rev-parse", "--abbrev-ref", "@{u}"], pat="", cwd=str(repo_dir))
        ).strip()
    except GitError:
        return None, None, None
    try:
        counts = (
            await _run(
                ["git", "rev-list", "--left-right", "--count", "@{u}...HEAD"], pat="", cwd=str(repo_dir)
            )
        ).split()
        return upstream, int(counts[1]), int(counts[0])
    except (GitError, IndexError, ValueError):
        return upstream, None, None


async def repo_state(repo_dir: Path, full_name: str) -> dict:
    """Read-only git snapshot for the Git tab.

    Branch, upstream/ahead/behind, staged vs. changed files (for the
    stage/commit UI), and the recent log. Takes the same per-repo lock the
    write paths use so a commit cannot land mid-snapshot. No PAT needed —
    everything is local.
    """
    async with _lock_for(full_name):
        branch = (await _run(["git", "branch", "--show-current"], pat="", cwd=str(repo_dir))).strip()
        sha = ""
        try:
            sha = await head_sha(repo_dir)
        except GitError:
            pass  # unborn HEAD: zero commits — the first one is pending
        status = await _run(["git", "status", "--porcelain"], pat="", cwd=str(repo_dir))
        staged, changes, dirty = _parse_status(status)
        upstream, ahead, behind = await _upstream_and_counts(repo_dir)
        recent: list[dict] = []
        if sha:
            sep = "\x00"
            # %x00 is git's own NUL escape — a raw NUL in argv would corrupt
            # the format string, and git would not emit the field separators.
            log = await _run(
                ["git", "log", "-10", "--date=short", "--pretty=format:%h%x00%an%x00%ad%x00%s"],
                pat="",
                cwd=str(repo_dir),
            )
            for line in log.splitlines():
                parts = line.split(sep)
                if len(parts) == 4:
                    recent.append({"sha": parts[0], "author": parts[1], "date": parts[2], "subject": parts[3]})
        return {
            "branch": branch,
            "has_commits": bool(sha),
            "head_sha": sha,
            "upstream": upstream,
            "ahead": ahead,
            "behind": behind,
            "staged": staged,
            "changes": changes,
            "dirty": dirty,
            "recent": recent,
        }


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
    """Files in the worktree: index (tracked/staged) + untracked, .gitignore-respecting.

    Context-menu creates and pastes are untracked until committed, so a
    `git ls-tree HEAD`-only listing would hide new files/folders until commit.
    """
    cached = await _run(["git", "ls-files", "-z", "--cached"], pat="", cwd=str(repo_dir))
    others = await _run(
        ["git", "ls-files", "-z", "-o", "--exclude-standard"], pat="", cwd=str(repo_dir)
    )
    paths = {p for p in cached.split("\0") + others.split("\0") if p}
    return sorted(p for p in paths if (repo_dir / p).is_file())


def read_file(repo_dir: Path, rel_path: str) -> str:
    target = resolve_safe(repo_dir, rel_path)
    if not target.is_file():
        raise FileNotFound(f"file not found in repository: {rel_path}")
    return target.read_text(encoding="utf-8", errors="replace")


def rename_entry(repo_dir: Path, from_rel: str, to_rel: str) -> None:
    """Move a file or folder inside the worktree (context-menu Rename)."""
    src = resolve_safe(repo_dir, from_rel)
    dst = resolve_safe(repo_dir, to_rel)
    if not src.exists():
        raise FileNotFound(f"not found in repository: {from_rel}")
    if dst.exists():
        raise FileExists(f"destination already exists: {to_rel}")
    if not dst.parent.is_dir():
        raise GitError(f"destination folder does not exist: {to_rel.rsplit('/', 1)[0]}")
    src.rename(dst)


def delete_entry(repo_dir: Path, rel: str) -> None:
    """Delete a file or folder inside the worktree (context-menu Delete)."""
    target = resolve_safe(repo_dir, rel)
    if target == repo_dir.resolve():
        raise GitError("cannot delete the repository root")
    if not target.exists():
        raise FileNotFound(f"not found in repository: {rel}")
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()


def create_file(repo_dir: Path, rel: str, content: str) -> None:
    """Create a new file in the worktree (context-menu Paste)."""
    target = resolve_safe(repo_dir, rel)
    if target.exists():
        raise FileExists(f"file already exists: {rel}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def create_folder(repo_dir: Path, rel: str) -> None:
    """Create a new folder in the worktree (context-menu "New Folder").

    Git cannot represent an empty directory, so a `.gitkeep` placeholder is
    planted — that keeps the folder alive across syncs and `git clean -fd`.
    """
    target = resolve_safe(repo_dir, rel)
    if target == repo_dir.resolve():
        raise GitError("cannot create a folder at the repository root")
    if target.exists():
        raise FileExists(f"folder already exists: {rel}")
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:  # e.g. an intermediate path is a file
        raise GitError(f"cannot create folder {rel}: {exc}") from exc
    (target / ".gitkeep").write_text("", encoding="utf-8")


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


async def create_branch(repo_dir: Path, full_name: str, pat: str, name: str) -> str:
    """Create and check out `name` from the current HEAD.

    Unlike `_create_branch` (used by the automated `commit_file`/
    `commit_workspace` flows, which silently suffixes a colliding name), this
    is for a user typing an exact branch name in the Git tab — a collision
    should be a clear error, not a silently different branch.
    """
    if not is_valid_branch(name):
        raise InvalidBranch("branch name is not a valid git ref name")
    async with _lock_for(full_name):
        if await _local_ref_exists(repo_dir, f"refs/heads/{name}"):
            raise GitError(f"branch '{name}' already exists locally")
        if await _remote_branch_exists(repo_dir, pat, name):
            raise GitError(f"branch '{name}' already exists on the remote")
        await _run(["git", "checkout", "-b", name], pat=pat, cwd=str(repo_dir))
    return name
async def _open_pr(full_name: str, pat: str, title: str, body: str, head: str, base: str) -> dict:
    """POST a new PR; returns GitHub's raw parsed response body."""
    status, data = await post_json(
        f"https://api.github.com/repos/{full_name}/pulls",
        github_headers(pat),
        {"title": title, "body": body, "head": head, "base": base},
    )
    if status != 201:
        raise GithubApiError(f"GitHub PR creation failed ({status}): {data.get('message', 'unknown error')}")
    if not data.get("html_url") or not isinstance(data.get("number"), int):
        raise GithubApiError("GitHub did not return a PR URL/number")
    return data


async def open_pull_request(
    full_name: str,
    pat: str,
    title: str,
    body: str,
    head: str,
    base: str,
) -> str:
    data = await _open_pr(full_name, pat, title, body, head, base)
    return str(data["html_url"])


async def open_branch_pull_request(
    full_name: str,
    pat: str,
    branch: str,
    base: str,
    title: str,
    body: str,
    issue_number: int | None,
) -> dict:
    """Like `open_pull_request`, but for the Git tab's working-tree flow: takes
    a branch (not a single-file commit result) and appends `Closes #N` to the
    body when `issue_number` is given, returning both the PR number and URL
    (merge needs the number)."""
    final_body = body or ""
    if issue_number is not None and f"#{issue_number}" not in final_body:
        closes = f"Closes #{issue_number}"
        final_body = f"{final_body}\n\n{closes}" if final_body.strip() else closes
    data = await _open_pr(full_name, pat, title, final_body, branch, base)
    return {"number": data["number"], "url": str(data["html_url"])}


async def find_open_pr(full_name: str, pat: str, branch: str) -> dict | None:
    """The open PR (if any) whose head is `branch`. `None` when there is none."""
    owner = full_name.split("/", 1)[0]
    status, data, _headers = await get_json(
        f"https://api.github.com/repos/{full_name}/pulls?head={owner}:{branch}&state=open",
        github_headers(pat),
    )
    if status != 200:
        msg = data.get("message", "unknown error") if isinstance(data, dict) else "unknown error"
        raise GithubApiError(f"GitHub PR lookup failed ({status}): {msg}")
    if not isinstance(data, list) or not data:
        return None
    pr = data[0]
    return {"number": pr.get("number"), "url": pr.get("html_url"), "title": pr.get("title")}


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


# ---------------------------------------------------------------------------
# Phase 6: Git tab — stage / unstage / commit / push on the current branch
# ---------------------------------------------------------------------------


def _validated_paths(repo_dir: Path, paths: list[str]) -> None:
    if not paths:
        raise GitError("no file paths given")
    for p in paths:
        resolve_safe(repo_dir, p)


async def stage_paths(repo_dir: Path, full_name: str, paths: list[str]) -> int:
    """`git add -- <paths>`: stage entries onto the index. Returns the count."""
    async with _lock_for(full_name):
        _validated_paths(repo_dir, paths)
        await _run(["git", "add", "--", *paths], pat="", cwd=str(repo_dir))
    return len(paths)


async def unstage_paths(repo_dir: Path, full_name: str, paths: list[str]) -> int:
    """`git reset -- <paths>` (mixed): move staged entries back to the worktree side.

    Rename entries carry an add/remove pair in the index, so callers must pass
    BOTH the old and new path to fully unstage a rename (see GitTab: staged
    rename rows send `old_path` + `path`).
    """
    async with _lock_for(full_name):
        _validated_paths(repo_dir, paths)
        await _run(["git", "reset", "-q", "--", *paths], pat="", cwd=str(repo_dir))
    return len(paths)


async def commit_staged(repo_dir: Path, full_name: str, message: str) -> str:
    """Commit the current index (staged entries only) on the checked-out branch.

    Returns the new HEAD sha. Raises GitError for an empty message or a clean
    index. The worktree is never touched (`git commit` with no pathspec).
    """
    if not message.strip():
        raise GitError("commit message must not be empty")
    async with _lock_for(full_name):
        status = await _run(["git", "status", "--porcelain"], pat="", cwd=str(repo_dir))
        staged, _changes, _dirty = _parse_status(status)
        if not staged:
            raise GitError("nothing staged to commit — stage files first")
        await _run(
            ["git", "commit", "-m", message],
            pat="",
            cwd=str(repo_dir),
            env_extra=_commit_identity(),
        )
        return (await _run(["git", "rev-parse", "HEAD"], pat="", cwd=str(repo_dir))).strip()


async def _ensure_tracking_ref(repo_dir: Path, pat: str, branch: str, head: str) -> None:
    """Make `@{u}` resolve for `branch` after a push.

    A shallow clone of a *zero-commit* remote (what `ensure_repo` makes) is
    missing the plumbing git normally writes at clone time: it has neither a
    `remote.origin.fetch` refspec nor an `origin/<branch>` remote-tracking ref.
    `push -u` records `branch.<b>.remote`/`merge` but fixes neither — and `@{u}`
    resolves `branch.<b>.merge` *through* the `remote.<r>.fetch` refspec, so the
    Git tab's upstream + ahead/behind stay `null` until both are in place.
    """
    # 1) Install the standard fetch refspec if the clone never got one.
    try:
        await _run(["git", "config", "--get", "remote.origin.fetch"], pat=pat, cwd=str(repo_dir))
    except GitError:
        await _run(
            ["git", "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*"],
            pat=pat,
            cwd=str(repo_dir),
        )
    # 2) Materialize the remote-tracking ref at the commit we just pushed.
    await _run(["git", "update-ref", f"refs/remotes/origin/{branch}", head], pat=pat, cwd=str(repo_dir))


async def push_branch(repo_dir: Path, full_name: str, pat: str) -> dict:
    """Push the checked-out branch to `origin/<branch>`, setting upstream if unset.

    An explicit refspec is ALWAYS used: a bare `git push` under
    push.default=simple can target a stale upstream ref (a branch created
    from `main` may keep `origin/main` as upstream and push onto main).
    The origin URL already embeds the PAT from clone time; `pat` only scrubs
    tokens out of error messages.
    """
    async with _lock_for(full_name):
        try:
            branch = (
                await _run(["git", "symbolic-ref", "--short", "HEAD"], pat=pat, cwd=str(repo_dir))
            ).strip()
        except GitError:
            raise GitError("HEAD is detached — check out a branch before pushing")
        if not branch:
            raise GitError("no branch is checked out — check out a branch before pushing")
        out = await _run(["git", "push", "-u", "origin", branch], pat=pat, cwd=str(repo_dir))
        # Repair the tracking plumbing for empty-remote clones (see the helper).
        # Best-effort: a failure here must not sink a push that already landed.
        try:
            head = (await _run(["git", "rev-parse", "HEAD"], pat="", cwd=str(repo_dir))).strip()
            if head:
                await _ensure_tracking_ref(repo_dir, pat, branch, head)
        except GitError:
            pass
    return {"branch": branch, "output": out.strip()}


async def merge_pull_request(
    repo_dir: Path,
    full_name: str,
    pat: str,
    branch: str,
    pr_number: int,
    default_branch: str,
) -> dict:
    """Squash-merge `pr_number`, delete the now-merged remote branch, and
    check the local clone back onto `default_branch` (fast-forwarded so the
    Git tab reads "up to date" right away). Raises GithubApiError on failure
    (e.g. merge conflicts) — no conflict resolution is attempted.

    Branch deletion + the local checkout-back are best-effort: the merge
    itself already landed on GitHub by the time either could fail, so a
    failure there must not be reported as the merge having failed.
    """
    async with _lock_for(full_name):
        status, data = await put_json(
            f"https://api.github.com/repos/{full_name}/pulls/{pr_number}/merge",
            github_headers(pat),
            {"merge_method": "squash"},
        )
        if status != 200 or not data.get("merged"):
            raise GithubApiError(f"GitHub merge failed ({status}): {data.get('message', 'unknown error')}")
        sha = str(data.get("sha") or "")

        try:
            await _run(["git", "push", "origin", "--delete", branch], pat=pat, cwd=str(repo_dir))
        except GitError:
            pass

        if default_branch and default_branch != "HEAD" and default_branch != branch:
            try:
                await _run(["git", "checkout", "-f", default_branch], pat=pat, cwd=str(repo_dir))
                await _run(["git", "fetch", "origin", default_branch], pat=pat, cwd=str(repo_dir))
                await _run(
                    ["git", "merge", "--ff-only", f"origin/{default_branch}"],
                    pat=pat,
                    cwd=str(repo_dir),
                )
            except GitError:
                pass

    return {"merged": True, "sha": sha}
