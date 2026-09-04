"""Auth endpoints: SSO login/callback via identity.folding-os.com, logout, me.

The session mechanism itself (the `sessions` table + HttpOnly cookie, and
`deps.get_current_user`'s lookup) is unchanged from before SSO — only *how*
a Session row gets created changes: a verified identity id_token's `sub` is
resolved to a local User (no auto-create — this is a single-account app; see
`callback()`) instead of a username/password check. Everything downstream of
`get_current_user` (every other route module, and terminals.py's own
cookie-based WS auth) needed zero changes as a result.
"""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from foldingos_api_core import OIDCSettings, OIDCVerifier, generate_pkce_pair
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.security import new_session_token
from app.core.settings import Settings, get_settings
from app.db.models import Session, User

router = APIRouter(prefix="/api/auth", tags=["auth"])

STATE_COOKIE = "oauth_state"

_verifier: OIDCVerifier | None = None


def _get_verifier(settings: Settings) -> OIDCVerifier:
    global _verifier
    if _verifier is None:
        _verifier = OIDCVerifier(
            OIDCSettings(
                issuer=settings.identity_issuer,
                client_id=settings.identity_client_id,
                client_secret=settings.identity_client_secret,
                audience=settings.identity_client_id,
            )
        )
    return _verifier


class UserOut(BaseModel):
    username: str
    name: str | None = None
    email: str | None = None
    picture: str | None = None


def _user_out(user: User) -> UserOut:
    return UserOut(username=user.username, name=user.name, email=user.email, picture=user.picture_url)


def _request_secure(request: Request) -> bool:
    """Whether this request arrived over TLS.

    The app is reachable two ways: LAN devices hit nginx over plain http,
    off-site traffic arrives via the Cloudflare Tunnel (which sets
    X-Forwarded-Proto: https, preserved by the nginx proxy). Browsers refuse
    to set or send `Secure` cookies over plain http, so the flag must match
    the transport the browser actually used — hardcoding `True` breaks local
    login, hardcoding `False` weakens the HTTPS path.
    """
    forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return request.url.scheme == "https" or forwarded == "https"


def _origin(request: Request) -> str:
    """This app's own origin as the browser sees it (scheme://host[:port]).

    Derived per-request (not fixed config) so the same deployment works
    whether reached via the public https:// domain or a plain http:// LAN
    address — as long as both are registered as redirect_uris on the
    identity server for this client.
    """
    scheme = "https" if _request_secure(request) else "http"
    return f"{scheme}://{request.headers.get('host', request.url.netloc)}"


def _cookie_kwargs(request: Request) -> dict[str, Any]:
    return {"httponly": True, "secure": _request_secure(request), "samesite": "lax", "path": "/"}


async def _exchange(settings: Settings, **data: str) -> dict[str, Any] | None:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{settings.identity_issuer}/token",
            data={
                "client_id": settings.identity_client_id,
                "client_secret": settings.identity_client_secret,
                **data,
            },
        )
    if resp.status_code != 200:
        return None
    return resp.json()


@router.get("/login")
async def login(request: Request) -> RedirectResponse:
    settings = get_settings()
    code_verifier, code_challenge = generate_pkce_pair()
    state = secrets.token_urlsafe(24)
    params = {
        "response_type": "code",
        "client_id": settings.identity_client_id,
        "redirect_uri": f"{_origin(request)}/api/auth/callback",
        "scope": "openid profile email",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    resp = RedirectResponse(f"{settings.identity_issuer}/authorize?{urlencode(params)}")
    resp.set_cookie(STATE_COOKIE, f"{state}:{code_verifier}", max_age=300, **_cookie_kwargs(request))
    return resp


@router.get("/callback")
async def callback(
    request: Request,
    code: str,
    state: str,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    settings = get_settings()

    raw_state = request.cookies.get(STATE_COOKIE)
    if not raw_state or ":" not in raw_state:
        raise HTTPException(status_code=400, detail="Missing or expired OAuth state")
    expected_state, code_verifier = raw_state.split(":", 1)
    if not secrets.compare_digest(expected_state, state):
        raise HTTPException(status_code=400, detail="OAuth state mismatch")

    tokens = await _exchange(
        settings,
        grant_type="authorization_code",
        code=code,
        # Same value /login sent identity: this request landed on the same
        # origin (browser bounced there and back), so recomputing it here
        # yields an identical string, as OAuth requires without needing to
        # stash it anywhere.
        redirect_uri=f"{_origin(request)}/api/auth/callback",
        code_verifier=code_verifier,
    )
    if tokens is None:
        raise HTTPException(status_code=401, detail="Token exchange failed")
    claims = await _get_verifier(settings).verify(tokens["id_token"])

    result = await db.execute(select(User).where(User.identity_sub == claims["sub"]))
    user = result.scalar_one_or_none()
    if user is None or not user.active:
        # No auto-create: this is a single-account app whose one User row is
        # linked to an identity account by a one-time admin action, not a
        # signup flow. An unlinked (or disabled) identity account gets a
        # clean 401, not a silently-provisioned fresh account.
        raise HTTPException(status_code=401, detail="No local account linked to this identity")

    user.name = claims.get("name")
    user.email = claims.get("email")
    user.picture_url = claims.get("picture")

    token = new_session_token()
    db.add(
        Session(
            token=token,
            user_id=user.id,
            expires_at=datetime.now(UTC) + timedelta(days=settings.session_max_age_days),
        )
    )
    await db.commit()

    resp = RedirectResponse("/")
    resp.delete_cookie(STATE_COOKIE, path="/")
    resp.set_cookie(
        settings.cookie_name,
        token,
        max_age=settings.session_max_age_days * 86400,
        **_cookie_kwargs(request),
    )
    return resp


@router.post("/logout", status_code=200)
async def logout(request: Request, db: AsyncSession = Depends(get_db)):
    settings = get_settings()
    token = request.cookies.get(settings.cookie_name)
    if token:
        await db.execute(delete(Session).where(Session.token == token))
        await db.commit()
    return {"ok": True}


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return _user_out(user)
