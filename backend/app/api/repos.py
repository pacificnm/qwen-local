"""Repository link / unlink / sync endpoints (Phase 2: GitHub ingestion)."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.db.models import CodeChunk, CodeFile, Repository, User
from app.repos import gitops
from app.repos.errors import SyncError
from app.repos.sync import cancel, get_job, submit, workspace

router = APIRouter(prefix="/api/repos", tags=["repos"])


class LinkRepoIn(BaseModel):
    full_name: str = Field(min_length=3, max_length=255, description="GitHub owner/name")


class RepoOut(BaseModel):
    id: uuid.UUID
    github_full_name: str
    default_branch: str
    last_synced_at: datetime | None
    last_commit_sha: str | None
    file_count: int
    chunk_count: int
    state: str  # running-stage | done | error | idle


async def _repo_out(db: AsyncSession, repo: Repository) -> RepoOut:
    file_count = (
        await db.execute(select(func.count(CodeFile.id)).where(CodeFile.repo_id == repo.id))
    ).scalar_one()
    chunk_count = (
        await db.execute(select(func.count(CodeChunk.id)).where(CodeChunk.repo_id == repo.id))
    ).scalar_one()
    job = get_job(repo.id)
    if job is not None:
        state = job.public()["state"] if job.is_running() else job.stage
        if job.error:
            state = f"error: {job.error[:160]}"
    else:
        state = "idle"
    return RepoOut(
        id=repo.id,
        github_full_name=repo.github_full_name,
        default_branch=repo.default_branch,
        last_synced_at=repo.last_synced_at,
        last_commit_sha=repo.last_commit_sha,
        file_count=file_count,
        chunk_count=chunk_count,
        state=state,
    )


async def _get_repo(db: AsyncSession, repo_id: uuid.UUID) -> Repository:
    repo = await db.get(Repository, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not linked")
    return repo


@router.post("", status_code=202)
async def link_repo(
    body: LinkRepoIn,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    full_name = body.full_name.strip()
    if not gitops.is_valid_full_name(full_name):
        raise HTTPException(status_code=422, detail="full_name must look like 'owner/name'")
    exists = await db.scalar(select(Repository.id).where(Repository.github_full_name == full_name))
    if exists is not None:
        raise HTTPException(status_code=409, detail="Repository already linked")
    repo = Repository(github_full_name=full_name, default_branch="HEAD")
    db.add(repo)
    await db.commit()
    try:
        job = submit(repo.id)
    except SyncError as exc:
        await db.delete(repo)
        await db.commit()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": str(repo.id), "state": job.public()["state"]}


@router.get("", response_model=list[RepoOut])
async def list_repos(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    repos = (
        await db.execute(select(Repository).order_by(Repository.created_at))
    ).scalars().all()
    return [await _repo_out(db, repo) for repo in repos]


@router.post("/{repo_id}/sync", status_code=202)
async def trigger_sync(
    repo_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    await _get_repo(db, repo_id)
    try:
        job = submit(repo_id)
    except SyncError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"repo_id": str(repo_id), "state": job.public()["state"]}


@router.get("/{repo_id}/sync-status")
async def sync_status(
    repo_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    repo = await _get_repo(db, repo_id)
    job = get_job(repo_id)
    base = {
        "repo_id": str(repo.id),
        "last_synced_at": repo.last_synced_at,
        "last_commit_sha": repo.last_commit_sha,
    }
    if job is None:
        return {**base, "state": "idle", "stage": None, "files_total": 0, "files_done": 0,
                "files_added": 0, "files_removed": 0, "chunks_written": 0, "error": None}
    return {**base, **job.public()}


@router.delete("/{repo_id}", status_code=204)
async def unlink_repo(
    repo_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    repo = await _get_repo(db, repo_id)
    cancel(repo_id)
    full_name = repo.github_full_name
    await db.delete(repo)  # cascades code_files + code_chunks
    await db.commit()
    gitops.remove_repo(workspace(), full_name)
    return Response(status_code=204)
