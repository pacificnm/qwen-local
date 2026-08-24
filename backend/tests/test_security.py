"""Unit tests for password hashing and the rate limiter."""

import time

from app.core.security import SlidingWindowLimiter, hash_password, new_session_token, verify_password


def test_password_roundtrip():
    hashed = hash_password("s3cret-Pass")
    assert hashed.startswith("$argon2")
    assert verify_password("s3cret-Pass", hashed) is True


def test_password_wrong_rejected():
    hashed = hash_password("correct")
    assert verify_password("wrong", hashed) is False
    assert verify_password("", hashed) is False


def test_password_malformed_hash_rejected():
    assert verify_password("anything", "not-a-valid-hash") is False


def test_session_token_shaped_and_unique():
    a, b = new_session_token(), new_session_token()
    assert len(a) == 64 and len(b) == 64
    assert a != b
    int(a, 16)  # hex-decodable


def test_rate_limiter_allows_then_blocks():
    limiter = SlidingWindowLimiter(limit=3, window_seconds=10.0)
    now = 1000.0
    assert all(limiter.allow("k", now=now) for _ in range(3))
    assert limiter.allow("k", now=now + 1) is False


def test_rate_limiter_slides_window():
    limiter = SlidingWindowLimiter(limit=3, window_seconds=10.0)
    assert all(limiter.allow("k", now=1000.0) for _ in range(3))
    # Old hits fall out of the window → allowed again.
    assert limiter.allow("k", now=1012.0) is True


def test_rate_limiter_keys_independent():
    limiter = SlidingWindowLimiter(limit=1, window_seconds=10.0)
    assert limiter.allow("a", now=1.0) is True
    assert limiter.allow("b", now=1.0) is True
    assert limiter.allow("a", now=1.0) is False


def test_limiter_default_window_is_real_time():
    limiter = SlidingWindowLimiter(limit=1)
    assert limiter.allow("ip") is True
    start = time.monotonic()
    assert limiter.allow("ip") is False
    assert time.monotonic() - start < 1.0
