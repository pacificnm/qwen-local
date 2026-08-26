"""Project endpoints — a project owns at most one repository (RAG + tools)
and all of its conversations. Repo-less projects are general chat (e.g.
the auto-created 'General')."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.repos import RepoOut, _repo_out
from app.db.models import Conversation, Project, Repository, User
from app.repos import gitops
from app.repos.errors import SyncError
from app.repos.sync import submit

router = APIRouter(prefix="/api/projects", tags=["projects"])


class CreateIn(BaseModel):
    name: str = Field(default="New project", min_length=1, max_length=255)
    full_name: str | None = Field(default=None, min_length=3, max_length=255,
                                 description="GitHub owner/name to attach and index (optional)")


class UpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    full_name: str | None = Field(default=None, min_length=3, max_length=255,
                                 description="attach/replace this GitHub repo (exclusive)")
    detach_repo: bool = False


class ProjectOut(BaseModel):
    id: uuid.UUID
    name: str
    created_at: datetime
    updated_at: datetime
    repo: RepoOut | None
    conversation_count: int


async def _get_project(db: AsyncSession, project_id: uuid.UUID, user: User) -> Project:
    project = await db.get(Project, project_id)
    if project is None or project.user_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


async def _project_repo(db: AsyncSession, project: Project) -> Repository | None:
    return (
        await db.execute(select(Repository).where(Repository.project_id == project.id))
    ).scalar_one_or_none()


async def _project_out(db: AsyncSession, project: Project) -> ProjectOut:
    repo = await _project_repo(db, project)
    count = (
        await db.execute(
            select(func.count(Conversation.id)).where(Conversation.project_id == project.id)
        )
    ).scalar_one()
    return ProjectOut(
        id=project.id,
        name=project.name,
        created_at=project.created_at,
        updated_at=project.updated_at,
        repo=await _repo_out(db, repo) if repo is not None else None,
        conversation_count=count,
    )


async def _attach_repo(
    db: AsyncSession, user: User, project: Project, full_name: str
) -> Repository | None:
    """Attach `full_name` to `project` (exclusive). Creates + indexes a new
    repo row when the name is unknown. Returns the attached repo, or raises."""
    full_name = full_name.strip()
    if not gitops.is_valid_full_name(full_name):
        raise HTTPException(status_code=422, detail="full_name must look like 'owner/name'")

    repo = (
        await db.execute(select(Repository).where(Repository.github_full_name == full_name))
    ).scalar_one_or_none()
    created = False
    if repo is None:
        repo = Repository(github_full_name=full_name, default_branch="HEAD")
        db.add(repo)
        created = True

    if repo.project_id not in (None, project.id):
        if created:
            await db.delete(repo)
            await db.commit()
        owner = (
            await db.execute(select(Project).where(Project.id == repo.project_id))
        ).scalar_one_or_none()
        detail = (
            f"Repository is already attached to project '{owner.name}'"
            if owner
            else "Repository is already attached to another project"
        )
        raise HTTPException(status_code=409, detail=detail)

    # A different repo may currently belong to this project — release it first
    # (the repo row is kept, unassigned).
    current = await _project_repo(db, project)
    if current is not None and current.id != repo.id:
        current.project_id = None
    repo.project_id = project.id
    await db.commit()

    if created:
        try:
            submit(repo.id)
        except SyncError as exc:
            repo.project_id = None
            await db.delete(repo)
            await db.commit()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return repo


@router.post("", status_code=201)
async def create_project(
    body: CreateIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    name = body.name.strip() or "New project"
    project = Project(user_id=user.id, name=name)
    db.add(project)
    await db.commit()

    if body.full_name is not None:
        await _attach_repo(db, user, project, body.full_name)

    await db.refresh(project)
    return await _project_out(db, project)


@router.get("", response_model=list[ProjectOut])
async def list_projects(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    projects = (
        (
            await db.execute(
                select(Project)
                .where(Project.user_id == user.id)
                .order_by(Project.name.asc(), Project.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    return [await _project_out(db, p) for p in projects]


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: uuid.UUID,
    body: UpdateIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = await _get_project(db, project_id, user)
    if body.name is not None:
        new_name = body.name.strip()
        if new_name and new_name != project.name:
            clash = (
                await db.execute(
                    select(Project.id).where(
                        Project.user_id == user.id, Project.name == new_name, Project.id != project.id
                    )
                )
            ).scalar_one_or_none()
            if clash is not None:
                raise HTTPException(status_code=409, detail=f"Project name '{new_name}' is taken")
            project.name = new_name
    if body.detach_repo:
        current = await _project_repo(db, project)
        if current is not None:
            current.project_id = None
    elif body.full_name is not None:
        await _attach_repo(db, user, project, body.full_name)
    await db.commit()
    await db.refresh(project)
    return await _project_out(db, project)


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = await _get_project(db, project_id, user)
    # Conversations (and their messages) cascade via the ORM relationship;
    # the attached repo row survives as an unassigned repo (FK SET NULL).
    await db.delete(project)
    await db.commit()
    return Response(status_code=204)
