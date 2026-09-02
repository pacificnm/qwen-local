"""GitHub Issues: a thin, read-mostly wrapper around the REST API for the
Issues tab. Unlike `gitops.py` there is no local clone involved — every read
and write goes straight to GitHub; nothing is cached or stored locally
(same philosophy as `gitops.find_open_pr`).
"""

from __future__ import annotations

import re

from .errors import GithubApiError
from .github_api import get_json, github_headers, patch_json, post_json

API_ROOT = "https://api.github.com"

_LINK_NEXT_RE = re.compile(r'<[^>]*[?&]page=(\d+)[^>]*>;\s*rel="next"')


def _has_next_page(link_header: str | None) -> bool:
    return bool(link_header) and _LINK_NEXT_RE.search(link_header) is not None


def _simplify_issue(raw: dict) -> dict:
    user = raw.get("user") or {}
    return {
        "number": raw.get("number"),
        "title": raw.get("title"),
        "body": raw.get("body") or "",
        "state": raw.get("state"),
        "user": user.get("login"),
        "labels": [
            {"name": lb.get("name"), "color": lb.get("color")}
            for lb in (raw.get("labels") or [])
            if isinstance(lb, dict)
        ],
        "assignees": [
            {"login": a.get("login"), "avatar_url": a.get("avatar_url")}
            for a in (raw.get("assignees") or [])
        ],
        "comments": raw.get("comments", 0),
        "created_at": raw.get("created_at"),
        "updated_at": raw.get("updated_at"),
        "html_url": raw.get("html_url"),
    }


def _simplify_comment(raw: dict) -> dict:
    user = raw.get("user") or {}
    return {
        "id": raw.get("id"),
        "body": raw.get("body") or "",
        "user": user.get("login"),
        "avatar_url": user.get("avatar_url"),
        "created_at": raw.get("created_at"),
    }


async def list_issues(
    full_name: str,
    pat: str,
    *,
    state: str = "open",
    labels: str | None = None,
    page: int = 1,
    per_page: int = 30,
) -> dict:
    """Open/closed/all issues, newest first. GitHub's `/issues` endpoint also
    returns pull requests (a PR is an issue under the hood) — anything
    carrying a `pull_request` key is dropped. `has_more` comes from the
    response's `Link` header, not a length guess (a full page after
    PR-filtering can still be the last one)."""
    params = f"state={state}&page={page}&per_page={per_page}"
    if labels:
        params += f"&labels={labels}"
    status, data, headers = await get_json(
        f"{API_ROOT}/repos/{full_name}/issues?{params}", github_headers(pat)
    )
    if status != 200:
        msg = data.get("message", "unknown error") if isinstance(data, dict) else "unknown error"
        raise GithubApiError(f"GitHub issue list failed ({status}): {msg}")
    items = [_simplify_issue(raw) for raw in (data or []) if "pull_request" not in raw]
    return {"items": items, "has_more": _has_next_page(headers.get("link"))}


async def get_issue(full_name: str, pat: str, number: int) -> dict:
    status, data, _headers = await get_json(
        f"{API_ROOT}/repos/{full_name}/issues/{number}", github_headers(pat)
    )
    if status != 200:
        msg = data.get("message", "unknown error") if isinstance(data, dict) else "unknown error"
        raise GithubApiError(f"GitHub issue lookup failed ({status}): {msg}")
    return _simplify_issue(data)


async def create_issue(
    full_name: str,
    pat: str,
    title: str,
    body: str,
    labels: list[str] | None = None,
    assignees: list[str] | None = None,
) -> dict:
    payload: dict = {"title": title, "body": body}
    if labels is not None:
        payload["labels"] = labels
    if assignees is not None:
        payload["assignees"] = assignees
    status, data = await post_json(
        f"{API_ROOT}/repos/{full_name}/issues", github_headers(pat), payload
    )
    if status != 201:
        msg = data.get("message", "unknown error")
        raise GithubApiError(f"GitHub issue creation failed ({status}): {msg}")
    return _simplify_issue(data)


async def update_issue(
    full_name: str,
    pat: str,
    number: int,
    *,
    title: str | None = None,
    body: str | None = None,
    state: str | None = None,
    labels: list[str] | None = None,
    assignees: list[str] | None = None,
) -> dict:
    """PATCH an issue. Only the given (non-None) fields are sent. `labels` and
    `assignees` are a FULL REPLACE (GitHub's own semantics) — callers pass the
    complete desired set, not a delta."""
    payload: dict = {}
    if title is not None:
        payload["title"] = title
    if body is not None:
        payload["body"] = body
    if state is not None:
        payload["state"] = state
    if labels is not None:
        payload["labels"] = labels
    if assignees is not None:
        payload["assignees"] = assignees
    status, data = await patch_json(
        f"{API_ROOT}/repos/{full_name}/issues/{number}", github_headers(pat), payload
    )
    if status != 200:
        raise GithubApiError(f"GitHub issue update failed ({status}): {data.get('message', 'unknown error')}")
    return _simplify_issue(data)


async def list_comments(full_name: str, pat: str, number: int) -> list[dict]:
    status, data, _headers = await get_json(
        f"{API_ROOT}/repos/{full_name}/issues/{number}/comments", github_headers(pat)
    )
    if status != 200:
        msg = data.get("message", "unknown error") if isinstance(data, dict) else "unknown error"
        raise GithubApiError(f"GitHub comment list failed ({status}): {msg}")
    return [_simplify_comment(raw) for raw in (data or [])]


async def add_comment(full_name: str, pat: str, number: int, body: str) -> dict:
    status, data = await post_json(
        f"{API_ROOT}/repos/{full_name}/issues/{number}/comments", github_headers(pat), {"body": body}
    )
    if status != 201:
        raise GithubApiError(f"GitHub comment failed ({status}): {data.get('message', 'unknown error')}")
    return _simplify_comment(data)


async def list_labels(full_name: str, pat: str) -> list[dict]:
    status, data, _headers = await get_json(f"{API_ROOT}/repos/{full_name}/labels", github_headers(pat))
    if status != 200:
        msg = data.get("message", "unknown error") if isinstance(data, dict) else "unknown error"
        raise GithubApiError(f"GitHub label list failed ({status}): {msg}")
    return [{"name": lb.get("name"), "color": lb.get("color")} for lb in (data or [])]


async def list_assignable_users(full_name: str, pat: str) -> list[dict]:
    status, data, _headers = await get_json(f"{API_ROOT}/repos/{full_name}/assignees", github_headers(pat))
    if status != 200:
        msg = data.get("message", "unknown error") if isinstance(data, dict) else "unknown error"
        raise GithubApiError(f"GitHub assignee list failed ({status}): {msg}")
    return [{"login": u.get("login"), "avatar_url": u.get("avatar_url")} for u in (data or [])]
