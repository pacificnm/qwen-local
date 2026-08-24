"""Password hashing (argon2id) and session token generation."""

import secrets
import string
import time

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_hasher = PasswordHasher()

_HEX_ALPHABET = string.ascii_lowercase + string.digits


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def new_session_token() -> str:
    """64 hex characters (256 bits) — random and high-entropy for cookie use."""
    return secrets.token_hex(32)


class SlidingWindowLimiter:
    """In-memory per-key sliding-window rate limiter.

    Single-host app, so an in-memory limiter is sufficient; it is deliberately
    small and testable. State is not shared across workers — acceptable here.
    """

    def __init__(self, limit: int, window_seconds: float = 60.0):
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str, now: float | None = None) -> bool:
        ts = time.monotonic() if now is None else now
        window_start = ts - self.window
        hits = [t for t in self._hits.get(key, []) if t > window_start]
        if len(hits) >= self.limit:
            self._hits[key] = hits
            return False
        hits.append(ts)
        self._hits[key] = hits
        return True
