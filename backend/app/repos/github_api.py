"""Small GitHub REST helpers shared by `gitops.py` (branch/PR/merge) and
`issues.py` (Issues tab). Each is module-level so tests can monkeypatch it —
same seam style as the rest of the repo's GitHub-facing code.
"""

from __future__ import annotations

import httpx


def github_headers(pat: str) -> dict:
    return {
        "Authorization": f"token {pat}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def get_json(url: str, headers: dict) -> tuple[int, object, httpx.Headers]:
    """GET sibling of `post_json`. GitHub list endpoints (e.g. `/pulls`, `/issues`)
    return a JSON array, so unlike `post_json` this does NOT coerce to a dict.
    Response headers are returned too — pagination (`Link`, `rel="next"`) lives
    there, not in the body."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        resp = await client.get(url, headers=headers)
    try:
        data = resp.json()
    except ValueError:
        data = None
    return resp.status_code, data, resp.headers


async def post_json(url: str, headers: dict, payload: dict) -> tuple[int, dict]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        resp = await client.post(url, headers=headers, json=payload)
    try:
        data = resp.json()
    except ValueError:
        data = {}
    return resp.status_code, data if isinstance(data, dict) else {"message": str(data)}


async def put_json(url: str, headers: dict, payload: dict) -> tuple[int, dict]:
    """Used for the PR merge endpoint."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        resp = await client.put(url, headers=headers, json=payload)
    try:
        data = resp.json()
    except ValueError:
        data = {}
    return resp.status_code, data if isinstance(data, dict) else {"message": str(data)}


async def patch_json(url: str, headers: dict, payload: dict) -> tuple[int, dict]:
    """Used for issue updates (title/body/state/labels/assignees)."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        resp = await client.patch(url, headers=headers, json=payload)
    try:
        data = resp.json()
    except ValueError:
        data = {}
    return resp.status_code, data if isinstance(data, dict) else {"message": str(data)}
