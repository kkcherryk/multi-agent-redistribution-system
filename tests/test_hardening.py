"""Phase 10 security review: sign-in limits, security headers, cross-site refusal, and that every route checks
who is calling."""
import re

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from portals.security import AttemptLimiter
from tests.conftest import HOST_EMAIL, HOST_PASSWORD, applicant

# Routes that are deliberately open (no sign-in / no service key).
PUBLIC_CORE = {"/health", "/public/impact"}  # impact: totals only, nothing personal
PUBLIC_PORTAL = {"/", "/health", "/api/portal", "/api/login", "/api/register", "/api/logout",
                 # other ways in: Google, an SMS code, a password-reset email (all rate limited)
                 "/api/login/google", "/api/login/code/start", "/api/login/code/verify",
                 "/api/password/forgot", "/api/password/reset"}


def fill(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "x" * 32, path)


def test_every_core_route_needs_a_service_key(core_app):
    client = TestClient(core_app)
    checked = 0
    for route in core_app.routes:
        if not isinstance(route, APIRoute) or route.path in PUBLIC_CORE:
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            r = client.request(method, fill(route.path), json={})
            assert r.status_code == 401, f"{method} {route.path} answered {r.status_code} without a service key"
            checked += 1
    assert checked > 60


@pytest.mark.parametrize("portal", ["admin", "restaurant", "ngo"])
def test_every_portal_api_needs_sign_in(portals, portal):
    app = portals[portal].app
    client = TestClient(app)
    checked = 0
    for route in app.routes:
        if not isinstance(route, APIRoute) or route.path in PUBLIC_PORTAL:
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            r = client.request(method, fill(route.path), json={})
            # 422 = the request body was refused before the sign-in check ran; nothing was read or changed.
            assert r.status_code in (401, 422), f"{portal}: {method} {route.path} answered {r.status_code}"
            checked += 1
    assert checked > 5


def test_host_cannot_be_self_registered(portals):
    assert portals["admin"].post("/api/register", json=applicant()).status_code in (404, 405)


def test_login_is_limited_after_repeated_failures(portals):
    rest = portals["restaurant"]
    rest.post("/api/register", json=applicant(email="cook@example.org"))
    for _ in range(5):
        assert rest.post("/api/login", json={"email": "cook@example.org", "password": "wrong-pass"}).status_code == 401
    r = rest.post("/api/login", json={"email": "cook@example.org", "password": "secret-pass-1"})
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0   # even the right password waits
    # Other accounts are not locked out by someone else's failures.
    rest.post("/api/register", json=applicant(email="other@example.org"))
    assert rest.post("/api/login", json={"email": "other@example.org", "password": "secret-pass-1"}).status_code == 200


def test_successful_login_resets_the_counter(portals):
    host = portals["admin"]
    for _ in range(4):
        host.post("/api/login", json={"email": HOST_EMAIL, "password": "nope-nope"})
    assert host.post("/api/login", json={"email": HOST_EMAIL, "password": HOST_PASSWORD}).status_code == 200
    for _ in range(4):
        assert host.post("/api/login", json={"email": HOST_EMAIL, "password": "nope-nope"}).status_code == 401


def test_registration_limit_per_network(portals):
    ngo = portals["ngo"]
    codes = [ngo.post("/api/register", json=applicant(email=f"n{i}@example.org")).status_code for i in range(11)]
    assert codes[:10] == [201] * 10 and codes[10] == 429


def test_attempt_limiter_window():
    t = [0.0]
    lim = AttemptLimiter(limit=2, window=60, clock=lambda: t[0])
    lim.hit("k"), lim.hit("k")
    assert lim.retry_after("k") > 0
    t[0] = 61
    assert lim.retry_after("k") == 0


def test_security_headers(portals):
    r = portals["restaurant"].get("/")
    assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert portals["restaurant"].get("/api/portal").headers["cache-control"] == "no-store"


def test_cookie_flags(portals):
    portals["restaurant"].post("/api/register", json=applicant())
    r = portals["restaurant"].post("/api/login", json={"email": "cook@example.org", "password": "secret-pass-1"})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie


def test_cross_site_post_is_refused(portals):
    r = portals["restaurant"].post("/api/login", json={"email": "a@b.co", "password": "x"},
                                   headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    same = portals["restaurant"].post("/api/login", json={"email": "a@b.co", "password": "xxxxxxxx"},
                                      headers={"Origin": "http://testserver"})
    assert same.status_code == 401


def test_uploaded_documents_cannot_run_script(portals):
    from tests.test_onboarding import complete_restaurant
    rest = portals["restaurant"]
    complete_restaurant(rest)
    doc = rest.get("/api/onboarding").json()["documents"][0]
    r = rest.get(f"/api/documents/{doc['id']}")
    assert "sandbox" in r.headers["content-security-policy"]
