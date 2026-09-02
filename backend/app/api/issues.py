"""GitHub Issues tab: list/view/create/comment/close-reopen/label+assignee edit.

Every route is a thin pass-through to `app.repos.issues` (a pure GitHub REST
wrapper — no local clone involved). Same auth/PAT/error-mapping pattern as
the Git tab's routes in `edits.py`.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.settings import get_settings
from app.db.models import Repository, User
from app.repos import issues
from app.repos.errors import GithubApiError

router = APIRouter(prefix="/api", tags=["issues"])

TITLE_MAX = 300
BODY_MAX = 20000


async def _get_repo(db: AsyncSession, repo_id: uuid.UUID) -> Repository:
    repo = await db.get(Repository, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not linked")
    return repo


def _pat() -> str:
    pat = get_settings().github_pat
    if not pat:
        raise HTTPException(status_code=503, detail="GitHub PAT is not configured")
    return pat


@router.get("/repos/{repo_id}/issues")
async def list_issues(
    repo_id: uuid.UUID,
    state: str = "open",
    labels: str | None = None,
    page: int = 1,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    if state not in ("open", "closed", "all"):
        raise HTTPException(status_code=422, detail="state must be open, closed, or all")
    repo = await _get_repo(db, repo_id)
    try:
        return await issues.list_issues(repo.github_full_name, _pat(), state=state, labels=labels, page=page)
    except GithubApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/repos/{repo_id}/issues/meta")
async def issues_meta(
    repo_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Picker options for the labels/assignees editors — one combined call."""
    repo = await _get_repo(db, repo_id)
    pat = _pat()
    try:
        labels = await issues.list_labels(repo.github_full_name, pat)
        assignees = await issues.list_assignable_users(repo.github_full_name, pat)
    except GithubApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"labels": labels, "assignees": assignees}


@router.get("/repos/{repo_id}/issues/{number}")
async def get_issue(
    repo_id: uuid.UUID,
    number: int,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """The issue plus its full comment thread, in one response."""
    repo = await _get_repo(db, repo_id)
    pat = _pat()
    try:
        issue = await issues.get_issue(repo.github_full_name, pat, number)
        comments = await issues.list_comments(repo.github_full_name, pat, number)
    except GithubApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"issue": issue, "comments": comments}


class IssueCreateIn(BaseModel):
    title: str = Field(min_length=1, max_length=TITLE_MAX)
    body: str = Field(default="", max_length=BODY_MAX)
    labels: list[str] | None = None
    assignees: list[str] | None = None


@router.post("/repos/{repo_id}/issues", status_code=201)
async def create_issue(
    repo_id: uuid.UUID,
    body: IssueCreateIn,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    repo = await _get_repo(db, repo_id)
    try:
        issue = await issues.create_issue(
            repo.github_full_name, _pat(), body.title.strip(), body.body, body.labels, body.assignees
        )
    except GithubApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return issue


class IssueUpdateIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=TITLE_MAX)
    body: str | None = Field(default=None, max_length=BODY_MAX)
    state: str | None = None
    labels: list[str] | None = None
    assignees: list[str] | None = None


@router.patch("/repos/{repo_id}/issues/{number}")
async def update_issue(
    repo_id: uuid.UUID,
    number: int,
    body: IssueUpdateIn,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    if body.state is not None and body.state not in ("open", "closed"):
        raise HTTPException(status_code=422, detail="state must be open or closed")
    repo = await _get_repo(db, repo_id)
    try:
        issue = await issues.update_issue(
            repo.github_full_name,
            _pat(),
            number,
            title=body.title.strip() if body.title is not None else None,
            body=body.body,
            state=body.state,
            labels=body.labels,
            assignees=body.assignees,
        )
    except GithubApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return issue


class IssueCommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=BODY_MAX)


@router.post("/repos/{repo_id}/issues/{number}/comments", status_code=201)
async def add_issue_comment(
    repo_id: uuid.UUID,
    number: int,
    body: IssueCommentIn,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    repo = await _get_repo(db, repo_id)
    try:
        comment = await issues.add_comment(repo.github_full_name, _pat(), number, body.body)
    except GithubApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return comment
