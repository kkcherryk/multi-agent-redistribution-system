"""Sign in with a phone number, an SMS code or Google, and reset a forgotten password by email."""
import re

from tests.conftest import applicant


def _restaurant(portals, **kw):
    client = portals["restaurant"]
    assert client.post("/api/register", json=applicant(**kw)).status_code == 201
    client.post("/api/logout")
    return client


def test_sign_in_with_phone_number_and_password(portals):
    client = _restaurant(portals, phone="+91 98765 43210")
    for typed in ("9876543210", "+91 98765 43210", "098765 43210"):
        r = client.post("/api/login", json={"email": typed, "password": "secret-pass-1"})
        assert r.status_code == 200, typed
    assert client.post("/api/login", json={"email": "9876543210", "password": "wrong-pass-9"}).status_code == 401
    # the same number on an NGO account doesn't sign in to the restaurant portal
    assert portals["ngo"].post("/api/login", json={"email": "9876543210", "password": "secret-pass-1"}).status_code == 401


def test_a_number_shared_by_two_accounts_signs_in_neither(portals):
    client = _restaurant(portals, email="a@example.org", phone="98765 43210")
    _restaurant(portals, email="b@example.org", phone="+91 98765 43210")
    assert client.post("/api/login", json={"email": "9876543210", "password": "secret-pass-1"}).status_code == 401
    assert client.post("/api/login", json={"email": "a@example.org", "password": "secret-pass-1"}).status_code == 200


def test_forgot_password_link_works_once(portals):
    client = _restaurant(portals)
    mails = []
    client.app.state.signin.mailer = lambda to, subject, text: mails.append((to, text))
    # unknown address: same answer, no email
    r = client.post("/api/password/forgot", json={"email": "nobody@example.org"})
    assert r.status_code == 200 and mails == []
    r = client.post("/api/password/forgot", json={"email": "cook@example.org"})
    assert r.status_code == 200 and len(mails) == 1 and mails[0][0] == "cook@example.org"
    token = re.search(r"#reset=([\w.-]+)", mails[0][1]).group(1)
    r = client.post("/api/password/reset", json={"token": token, "password": "brand-new-pass"})
    assert r.status_code == 200 and client.get("/api/me").status_code == 200
    client.post("/api/logout")
    assert client.post("/api/login", json={"email": "cook@example.org", "password": "secret-pass-1"}).status_code == 401
    assert client.post("/api/login", json={"email": "cook@example.org", "password": "brand-new-pass"}).status_code == 200
    # the link can't be used again
    assert client.post("/api/password/reset", json={"token": token, "password": "another-pass-1"}).status_code == 409
    assert client.post("/api/password/reset", json={"token": token[:-4] + "abcd", "password": "x" * 9}).status_code == 401


def test_one_time_code_by_email_is_free_and_works_once(portals):
    client = _restaurant(portals, phone="+91 98765 43210")
    mails, texts = [], []
    client.app.state.signin.mailer = lambda to, subject, text: mails.append((to, text))
    assert client.get("/api/portal").json()["code_sign_in"] is True
    # unknown email: same answer, nothing sent
    assert client.post("/api/login/code/start", json={"identifier": "nobody@example.org"}).status_code == 200
    assert mails == []
    # typing the phone number with no SMS service set up sends the code to the account's email (free)
    r = client.post("/api/login/code/start", json={"identifier": "98765 43210"})
    assert r.status_code == 200 and r.json()["channel"] == "email" and mails[0][0] == "cook@example.org"
    code = re.search(r"(\d{6}) is your", mails[0][1]).group(1)
    wrong = "000000" if code != "000000" else "111111"
    r = client.post("/api/login/code/verify", json={"identifier": "9876543210", "code": wrong})
    assert r.status_code == 401 and "tries left" in r.json()["detail"]
    r = client.post("/api/login/code/verify", json={"identifier": "9876543210", "code": code})
    assert r.status_code == 200 and client.get("/api/me").json()["email"] == "cook@example.org"
    assert client.post("/api/login/code/verify", json={"identifier": "9876543210", "code": code}).status_code == 401
    # by email address too
    client.post("/api/logout")
    client.post("/api/login/code/start", json={"identifier": "Cook@Example.org"})
    code = re.search(r"(\d{6}) is your", mails[-1][1]).group(1)
    assert client.post("/api/login/code/verify", json={"identifier": "cook@example.org", "code": code}).status_code == 200
    # an SMS service, if one is ever set up, is used for phone numbers
    client.app.state.signin.sms = lambda phone, text: texts.append(phone)
    r = client.post("/api/login/code/start", json={"identifier": "+91 98765 43210"})
    assert r.json()["channel"] == "sms" and texts == ["+91 98765 43210"]


def test_continue_with_google(portals):
    client = portals["restaurant"]
    assert client.post("/api/login/google", json={"credential": "x" * 40}).status_code == 404  # not set up
    client.app.state.signin.google_verify = lambda cred: {"email": "leaf@example.org", "name": "Green Leaf"}
    first = client.post("/api/login/google", json={"credential": "x" * 40})
    assert first.status_code == 200 and first.json()["status"] == "applicant"  # a new application was started
    client.post("/api/logout")
    again = client.post("/api/login/google", json={"credential": "x" * 40})
    assert again.status_code == 200 and again.json()["id"] == first.json()["id"]

    def refuse(_cred):
        raise ValueError("this Google sign-in was not made for this site")
    client.app.state.signin.google_verify = refuse
    assert client.post("/api/login/google", json={"credential": "x" * 40}).status_code == 401


def test_google_never_creates_a_host(portals):
    host = portals["admin"]
    host.app.state.signin.google_verify = lambda cred: {"email": "stranger@example.org", "name": "Stranger"}
    assert host.post("/api/login/google", json={"credential": "x" * 40}).status_code == 401


def test_host_portal_only_lets_in_the_allowed_email(core_app):
    from fastapi.testclient import TestClient
    from portals.admin.app import create_admin_app
    from shared.messaging import SignInServices
    from tests.conftest import HOST_EMAIL, HOST_PASSWORD, JWT, _core_client_for

    def host_portal(allowed):
        return TestClient(create_admin_app(_core_client_for(core_app, "admin"), JWT["admin"],
                                           signin_services=SignInServices(), allowed_emails=allowed))
    # the seeded host is on the list: in
    ok = host_portal({HOST_EMAIL})
    assert ok.post("/api/login", json={"email": HOST_EMAIL, "password": HOST_PASSWORD}).status_code == 200
    # a different address is the only one allowed: the same correct password is refused
    other = host_portal({"new-host@gmail.com"})
    r = other.post("/api/login", json={"email": HOST_EMAIL, "password": HOST_PASSWORD})
    assert r.status_code == 403 and "host account" in r.json()["detail"]
    # and a session made before the list changed stops working
    other.cookies.update(ok.cookies)  # the host's existing session cookie, shown to a portal with a new list
    assert other.get("/api/me").status_code == 401


def test_pages_opened_by_ip_move_to_the_public_address(core_app):
    from fastapi.testclient import TestClient
    from portals.restaurant.app import create_restaurant_app
    from shared.messaging import SignInServices
    from tests.conftest import JWT, _core_client_for

    app = create_restaurant_app(_core_client_for(core_app, "restaurant"), JWT["restaurant"],
                                signin_services=SignInServices(), public_url="http://localhost:8002")
    client = TestClient(app, base_url="http://127.0.0.1:8002")
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "http://localhost:8002/"
    assert client.get("/health").status_code == 200  # health checks and the API are not redirected
