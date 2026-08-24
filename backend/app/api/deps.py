"""Shared FastAPI dependencies."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.core.settings import get_settings
from app.db.models import Session, User
from app.db.session import db_session


async def get_db() -> AsyncIterator[AsyncSession]:
    async for session in db_session():
        yield session


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """Resolve the session cookie to a User; 401 if missing/expired/revoked."""
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
