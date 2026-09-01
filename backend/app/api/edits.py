"""Phase 4: editor file access + Commit to GitHub (branch → commit → PR).

File reads serve the Monaco pane (original content for the diff view).
`POST /commit` implements docs/API.md "Commit / PR": create branch from the
upstream default, commit the edited file, push, optionally open a PR.
"""

import asyncio
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.settings import get_settings
from app.db.models import Repository, User
from app.repos import gitops
from app.repos.errors import (
    FileExists,
    FileNotFound,
    GitError,
    GithubApiError,
    InvalidBranch,
    SyncError,
)
from app.repos.sync import workspace

router = APIRouter(prefix="/api", tags=["edits"])

MAX_FILE_READ = 1 * 1024 * 1024  # 1 MB hard cap for the editor (spec §5 edge case)
MAX_CONTENT = 1 * 1024 * 1024  # same ceiling on the content we commit


class CommitIn(BaseModel):
    repo_id: uuid.UUID
    file_path: str = Field(min_length=1, max_length=1024)
    content: str = Field(min_length=1, max_length=MAX_CONTENT)
    base_ref: str | None = None  # accepted for API.md compatibility; commits always branch from upstream HEAD
    branch: str = Field(default="qwen-assist/edits", min_length=1)
    commit_message: str = Field(min_length=1, max_length=500)
    open_pr: bool = False
    pr_title: str | None = Field(default=None, max_length=300)
    pr_body: str | None = Field(default=None, max_length=20000)


class RenameIn(BaseModel):
    from_path: str = Field(min_length=1, max_length=1024)
    to_path: str = Field(min_length=1, max_length=1024)


class DeleteIn(BaseModel):
    path: str = Field(min_length=1, max_length=1024)


class CreateIn(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    content: str = Field(default="", max_length=MAX_CONTENT)


class FolderIn(BaseModel):
    path: str = Field(min_length=1, max_length=1024)


async def _get_repo(db: AsyncSession, repo_id: uuid.UUID) -> Repository:
    repo = await db.get(Repository, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not linked")
    return repo


@router.get("/repos/{repo_id}/files")
async def list_repo_files(
    repo_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    repo = await _get_repo(db, repo_id)
    repo_dir = gitops.workspace_repo_dir(workspace(), repo.github_full_name)
    if not repo_dir.joinpath(".git").is_dir():
        raise HTTPException(status_code=502, detail="workspace clone missing — sync the repo first")
    files = await gitops.list_file_paths(repo_dir)
    return {"repo_id": str(repo.id), "files": [{"path": p} for p in sorted(files)]}


@router.get("/repos/{repo_id}/file")
async def read_repo_file(
    repo_id: uuid.UUID,
    path: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    repo = await _get_repo(db, repo_id)
    repo_dir = gitops.workspace_repo_dir(workspace(), repo.github_full_name)
    try:
        content = await asyncio.to_thread(gitops.read_file, repo_dir, path)
    except FileNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except GitError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if len(content.encode("utf-8")) > MAX_FILE_READ:
        raise HTTPException(status_code=413, detail="file exceeds the 1 MB editor limit")
    return {"path": path, "content": content}


def _worktree(repo: Repository) -> Path:
    repo_dir = gitops.workspace_repo_dir(workspace(), repo.github_full_name)
    if not repo_dir.joinpath(".git").is_dir():
        raise HTTPException(status_code=502, detail="workspace clone missing — sync the repo first")
    return repo_dir


@router.post("/repos/{repo_id}/files/rename", status_code=201)
async def rename_repo_file(
    repo_id: uuid.UUID,
    body: RenameIn,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Context-menu Rename: move a file or folder inside the local clone."""
    repo = await _get_repo(db, repo_id)
    repo_dir = _worktree(repo)
    try:
        await asyncio.to_thread(gitops.rename_entry, repo_dir, body.from_path, body.to_path)
    except FileNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileExists as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except GitError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"path": body.to_path}


@router.delete("/repos/{repo_id}/file", status_code=201)
async def delete_repo_file(
    repo_id: uuid.UUID,
    body: DeleteIn,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Context-menu Delete: remove a file or folder from the local clone."""
    repo = await _get_repo(db, repo_id)
    repo_dir = _worktree(repo)
    try:
        await asyncio.to_thread(gitops.delete_entry, repo_dir, body.path)
    except FileNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except GitError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"deleted": body.path}


@router.post("/repos/{repo_id}/files/create", status_code=201)
async def create_repo_file(
    repo_id: uuid.UUID,
    body: CreateIn,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Context-menu Paste: create a new file from the clipboard content."""
    repo = await _get_repo(db, repo_id)
    repo_dir = _worktree(repo)
    try:
        await asyncio.to_thread(gitops.create_file, repo_dir, body.path, body.content)
    except FileExists as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except GitError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"path": body.path}


@router.post("/repos/{repo_id}/folders", status_code=201)
async def create_repo_folder(
    repo_id: uuid.UUID,
    body: FolderIn,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Context-menu "New Folder": create a folder (with a `.gitkeep`) inside the clone."""
    repo = await _get_repo(db, repo_id)
    repo_dir = _worktree(repo)
    try:
        await asyncio.to_thread(gitops.create_folder, repo_dir, body.path)
    except FileExists as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except GitError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"path": body.path}


@router.get("/repos/{repo_id}/git")
async def repo_git_state(
    repo_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Git-tab snapshot: branch, upstream/ahead/behind, staged/changed files,
    and the last 10 commits."""
    repo = await _get_repo(db, repo_id)
    repo_dir = gitops.workspace_repo_dir(workspace(), repo.github_full_name)
    if not repo_dir.joinpath(".git").is_dir():
        raise HTTPException(status_code=502, detail="workspace clone missing — sync the repo first")
    try:
        state = await gitops.repo_state(repo_dir, repo.github_full_name)
    except GitError as exc:
        raise HTTPException(status_code=502, detail=f"git state unavailable: {exc}") from exc
    return {"repo_id": str(repo.id), **state}


class GitPathsIn(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=500)


class GitCommitIn(BaseModel):
    message: str = Field(min_length=1, max_length=500)


def _valid_git_paths(paths: list[str]) -> None:
    if any(not p or len(p) > 1024 for p in paths):
        raise HTTPException(status_code=422, detail="invalid file path")


@router.post("/repos/{repo_id}/git/stage")
async def git_stage(
    repo_id: uuid.UUID,
    body: GitPathsIn,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Stage files onto the index (`git add -- <paths>`) — Git tab stage action."""
    repo = await _get_repo(db, repo_id)
    repo_dir = _worktree(repo)
    _valid_git_paths(body.paths)
    try:
        n = await gitops.stage_paths(repo_dir, repo.github_full_name, body.paths)
    except GitError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"staged": n}


@router.post("/repos/{repo_id}/git/unstage")
async def git_unstage(
    repo_id: uuid.UUID,
    body: GitPathsIn,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Unstage (`git reset -- <paths>`): move staged entries back to the worktree side."""
    repo = await _get_repo(db, repo_id)
    repo_dir = _worktree(repo)
    _valid_git_paths(body.paths)
    try:
        n = await gitops.unstage_paths(repo_dir, repo.github_full_name, body.paths)
    except GitError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"unstaged": n}


@router.post("/repos/{repo_id}/git/commit")
async def git_commit_staged(
    repo_id: uuid.UUID,
    body: GitCommitIn,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Commit the staged index on the current branch (no branch switch, no PR)."""
    repo = await _get_repo(db, repo_id)
    repo_dir = _worktree(repo)
    try:
        sha = await gitops.commit_staged(repo_dir, repo.github_full_name, body.message)
    except GitError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"commit_sha": sha}


@router.post("/repos/{repo_id}/git/push")
async def git_push_branch(
    repo_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Push the current branch to origin (setting upstream on first push)."""
    repo = await _get_repo(db, repo_id)
    repo_dir = _worktree(repo)
    pat = get_settings().github_pat
    if not pat:
        raise HTTPException(status_code=503, detail="GitHub PAT is not configured")
    try:
        result = await gitops.push_branch(repo_dir, repo.github_full_name, pat)
    except GitError as exc:
        raise HTTPException(status_code=502, detail=f"push failed: {exc}") from exc
    return {"repo_id": str(repo.id), **result}


@router.post("/commit", status_code=201)
async def commit_to_github(
    body: CommitIn,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    repo = await _get_repo(db, body.repo_id)
    pat = get_settings().github_pat
    if not pat:
        raise HTTPException(status_code=503, detail="GitHub PAT is not configured")

    branch = body.branch.strip()
    if not gitops.is_valid_branch(branch):
        raise HTTPException(status_code=422, detail="branch name is not a valid git ref name")

    pr_title = (body.pr_title or "").strip() or body.commit_message.strip()
    pr_body = (body.pr_body or "").strip() or (
        f"## Summary\n\n{body.commit_message.strip()}\n\n## Files\n\n- `{body.file_path}`\n"
    )
    try:
        result = await gitops.commit_file(
            workspace=workspace(),
            full_name=repo.github_full_name,
            pat=pat,
            file_path=body.file_path,
            content=body.content,
            branch=branch,
            commit_message=body.commit_message,
            open_pr=body.open_pr,
            pr_title=pr_title,
            pr_body=pr_body,
        )
    except FileNotFound as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except InvalidBranch as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except GithubApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except SyncError as exc:
        raise HTTPException(status_code=502, detail=f"git operation failed: {exc}") from exc

    return {
        "branch": result.branch,
        "commit_sha": result.commit_sha,
        "pr_url": result.pr_url,
    }
