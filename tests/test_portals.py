from fastapi.testclient import TestClient

from landing.app import create_landing_app
from portals.restaurant.app import create_restaurant_app
from shared.core_client import CoreClient
from tests.conftest import HOST_EMAIL, HOST_PASSWORD, JWT, applicant


def test_register_login_me_logout_restaurant(portals):
    rest = portals["restaurant"]
    r = rest.post("/api/register", json=applicant())
    assert r.status_code == 201 and r.json()["status"] == "under_review"
    assert "frs_restaurant_session" in r.cookies
    assert rest.get("/api/me").json()["email"] == "cook@example.org"  # auto-signed-in after registering
    assert rest.post("/api/logout").status_code == 204
    assert rest.get("/api/me").status_code == 401
    r = rest.post("/api/login", json={"email": "cook@example.org", "password": "secret-pass-1"})
    assert r.status_code == 200
    assert rest.get("/api/me").json()["role"] == "restaurant"


def test_ngo_register_and_login(portals):
    ngo = portals["ngo"]
    assert ngo.post("/api/register", json=applicant(email="ngo@example.org", name="Food Bank")).status_code == 201
    ngo.cookies.clear()
    assert ngo.post("/api/login", json={"email": "ngo@example.org", "password": "secret-pass-1"}).status_code == 200
    assert ngo.get("/api/me").json()["role"] == "ngo"


def test_wrong_portal_login_rejected(portals):
    portals["ngo"].post("/api/register", json=applicant(email="ngo@example.org"))
    creds = {"email": "ngo@example.org", "password": "secret-pass-1"}
    assert portals["restaurant"].post("/api/login", json=creds).status_code == 401
    assert portals["admin"].post("/api/login", json=creds).status_code == 401
    assert portals["restaurant"].post("/api/login", json={"email": HOST_EMAIL, "password": HOST_PASSWORD}).status_code == 401


def test_session_cookie_not_valid_on_other_portal(portals):
    rest, ngo = portals["restaurant"], portals["ngo"]
    token = rest.post("/api/register", json=applicant()).cookies["frs_restaurant_session"]
    ngo.cookies.set("frs_ngo_session", token)  # replay the restaurant token against the NGO portal
    assert ngo.get("/api/me").status_code == 401


def test_tampered_cookie_rejected(portals):
    rest = portals["restaurant"]
    rest.cookies.set("frs_restaurant_session", "garbage.token.value")
    assert rest.get("/api/me").status_code == 401


def test_admin_has_no_registration_and_host_can_login(portals):
    admin = portals["admin"]
    assert admin.post("/api/register", json=applicant()).status_code in (404, 405)
    info = admin.get("/api/portal").json()
    assert (info["portal"], info["role"], info["allow_registration"]) == ("admin", "host", False)
    assert admin.get("/api/accounts").status_code == 401  # signed out
    r = admin.post("/api/login", json={"email": HOST_EMAIL, "password": HOST_PASSWORD})
    assert r.status_code == 200 and r.json()["status"] == "verified"


def test_admin_sees_cross_portal_records(portals):
    portals["restaurant"].post("/api/register", json=applicant())
    portals["ngo"].post("/api/register", json=applicant(email="ngo@example.org"))
    admin = portals["admin"]
    admin.post("/api/login", json={"email": HOST_EMAIL, "password": HOST_PASSWORD})
    roles = sorted(a["role"] for a in admin.get("/api/accounts").json())
    assert roles == ["host", "ngo", "restaurant"]
    assert admin.get("/api/accounts", params={"status": "under_review"}).json().__len__() == 2
    assert any(e["type"] == "account.registered" for e in admin.get("/api/events").json())


def test_registration_errors_pass_through(portals):
    rest = portals["restaurant"]
    assert rest.post("/api/register", json=applicant()).status_code == 201
    assert rest.post("/api/register", json=applicant()).status_code == 409
    assert rest.post("/api/register", json=applicant(email="x@y.org", password="short")).status_code == 422


def test_core_unavailable_returns_503():
    dead = CoreClient("http://127.0.0.1:9", "k", timeout=0.5)
    app = TestClient(create_restaurant_app(dead, JWT["restaurant"]))
    assert app.post("/api/login", json={"email": "a@b.co", "password": "whatever1"}).status_code == 503
    assert app.get("/health").json()["core_reachable"] is False


def test_bad_service_key_is_not_reported_as_user_auth_failure(core_app):
    bad = CoreClient("http://core", "wrong-key", http=TestClient(core_app, base_url="http://core"))
    app = TestClient(create_restaurant_app(bad, JWT["restaurant"]))
    assert app.post("/api/login", json={"email": "a@b.co", "password": "whatever1"}).status_code == 502


def test_frontends_served(portals):
    for name, client in portals.items():
        page = client.get("/")
        assert page.status_code == 200 and "portal.js" in page.text, name
        assert client.get("/static/common/portal.js").status_code == 200
        assert client.get("/health").json()["core_reachable"] is True
    assert "register-form" not in portals["admin"].get("/").text
    assert "register-form" in portals["restaurant"].get("/").text


def test_landing_offers_three_portals():
    urls = {"admin": "http://h:8001", "restaurant": "http://h:8002", "ngo": "http://h:8003"}
    page = TestClient(create_landing_app(urls)).get("/").text
    for portal, url in urls.items():
        assert f'href="{url}"' in page and f'data-portal="{portal}"' in page
