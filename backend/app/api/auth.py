"""Auth endpoints: login / logout / me (server-side killable sessions)."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import client_ip, get_current_user, get_db
from app.core.security import SlidingWindowLimiter, new_session_token, verify_password
from app.core.settings import get_settings
from app.db.models import Session, User

router = APIRouter(prefix="/api/auth", tags=["auth"])

# One limiter per process — fine for a single-host, single-worker deployment.
_login_limiter = SlidingWindowLimiter(limit=10)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class UserOut(BaseModel):
    username: str


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


@router.post("/login", response_model=UserOut, status_code=200)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    settings = get_settings()

    if not _login_limiter.allow(client_ip(request)):
        raise HTTPException(status_code=429, detail="Too many login attempts; retry in a minute")

    result = await db.execute(select(User).where(User.username == payload.username.lower().strip()))
    user = result.scalar_one_or_none()
    if user is None or not user.active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    token = new_session_token()
    db.add(
        Session(
            token=token,
            user_id=user.id,
            expires_at=datetime.now(UTC) + timedelta(days=settings.session_max_age_days),
        )
    )
    await db.commit()

    response.set_cookie(
        settings.cookie_name,
        token,
        max_age=settings.session_max_age_days * 86400,
        httponly=True,
        secure=_request_secure(request),
        samesite="lax",
        path="/",
    )
    return UserOut(username=user.username)


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
    return UserOut(username=user.username)
