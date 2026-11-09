import time

import jwt
import pytest

from shared.security import decode_session_token, hash_password, issue_session_token, verify_password


def test_hash_and_verify():
    h = hash_password("correct horse")
    assert h != "correct horse"
    assert verify_password("correct horse", h)
    assert not verify_password("wrong horse", h)


def test_overlong_password_rejected():
    with pytest.raises(ValueError):
        hash_password("x" * 73)
    assert not verify_password("x" * 73, hash_password("x" * 72))


def test_verify_with_garbage_hash_is_false():
    assert not verify_password("anything", "not-a-bcrypt-hash")


def test_token_roundtrip_and_audience():
    t = issue_session_token("s3cret-key-of-adequate-length-0123456789", account_id="a1", role="ngo", portal="ngo", ttl_seconds=60)
    claims = decode_session_token("s3cret-key-of-adequate-length-0123456789", t, portal="ngo")
    assert claims["sub"] == "a1" and claims["role"] == "ngo"
    assert decode_session_token("s3cret-key-of-adequate-length-0123456789", t, portal="restaurant") is None  # wrong portal
    assert decode_session_token("another-secret-of-adequate-length-0123456", t, portal="ngo") is None  # wrong secret


def test_expired_token_rejected():
    secret = "s3cret-key-of-adequate-length-0123456789"
    past = int(time.time()) - 100
    t = jwt.encode({"sub": "a", "role": "ngo", "aud": "frs-ngo", "iat": past, "exp": past + 1}, secret, algorithm="HS256")
    assert decode_session_token(secret, t, portal="ngo") is None
