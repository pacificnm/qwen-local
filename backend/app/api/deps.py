"""Shared FastAPI dependencies."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from foldingos_api_core import KEY_PREFIX, ApiKeyVerifier
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.core.settings import get_settings
from app.db.models import Session, User
from app.db.session import db_session


async def get_db() -> AsyncIterator[AsyncSession]:
    async for session in db_session():
        yield session


_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="ApiKey",
    description="Identity-issued API key (fos_...), minted at identity.folding-os.com",
)

_api_keys: ApiKeyVerifier | None = None


def _get_api_keys() -> ApiKeyVerifier:
    global _api_keys
    if _api_keys is None:
        settings = get_settings()
        _api_keys = ApiKeyVerifier(
            issuer=settings.identity_issuer,
            client_id=settings.identity_client_id,
            client_secret=settings.identity_client_secret,
        )
    return _api_keys


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
    bearer: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> User:
    """Resolve an identity API key (Bearer fos_...) or the session cookie to a User."""
    if bearer is not None and bearer.credentials.startswith(KEY_PREFIX):
        claims = await _get_api_keys().resolve(bearer.credentials)
        if claims is None:
            raise HTTPException(status_code=401, detail="Invalid or revoked API key")
        # Same linking rule as the SSO callback: no auto-create.
        result = await db.execute(select(User).where(User.identity_sub == claims["sub"]))
        user = result.scalar_one_or_none()
        if user is None or not user.active:
            raise HTTPException(status_code=401, detail="No local account linked to this identity")
        return user

    settings = get_settings()
    token = request.cookies.get(settings.cookie_name)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    stmt = (
        select(Session)
        .options(joinedload(Session.user))
        .where(Session.token == token)
    )
    result = await db.execute(stmt)
    session: Session | None = result.scalar_one_or_none()
    if session is None or session.expires_at < datetime.now(UTC):
        if session is not None:
            await db.delete(session)
            await db.commit()
        raise HTTPException(status_code=401, detail="Session expired")

    user: User | None = session.user
    if user is None or not user.active:
        raise HTTPException(status_code=401, detail="Account disabled")
    return user


def client_ip(request: Request) -> str:
    """Best-effort client address for rate limiting (tunnel preserves XFF)."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
