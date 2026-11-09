"""Password hashing and portal session tokens."""
from __future__ import annotations

import time

import bcrypt
import jwt

BCRYPT_MAX_BYTES = 72  # bcrypt ignores anything beyond 72 bytes, so we reject longer passwords instead.


def hash_password(password: str) -> str:
    raw = password.encode("utf-8")
    if len(raw) > BCRYPT_MAX_BYTES:
        raise ValueError("password too long")
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    raw = password.encode("utf-8")
    if len(raw) > BCRYPT_MAX_BYTES:
        return False
    try:
        return bcrypt.checkpw(raw, password_hash.encode("ascii"))
    except ValueError:
        return False


def issue_session_token(secret: str, *, account_id: str, role: str, portal: str, ttl_seconds: int) -> str:
    now = int(time.time())
    payload = {"sub": account_id, "role": role, "aud": f"frs-{portal}", "iat": now, "exp": now + ttl_seconds}
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_session_token(secret: str, token: str, *, portal: str) -> dict | None:
    """Return the token payload, or None if the token is invalid, expired or issued for another portal."""
    try:
        return jwt.decode(token, secret, algorithms=["HS256"], audience=f"frs-{portal}")
    except jwt.PyJWTError:
        return None
