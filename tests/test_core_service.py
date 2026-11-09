from tests.conftest import HOST_EMAIL, HOST_PASSWORD, applicant, svc


def test_health_is_public(core):
    assert core.get("/health").json()["status"] == "ok"


def test_service_key_required(core):
    assert core.post("/internal/accounts", json=applicant()).status_code == 401
    assert core.post("/internal/accounts", json=applicant(), headers={"X-Service-Key": "nope"}).status_code == 401


def test_restaurant_portal_registers_restaurant_as_applicant(core):
    r = core.post("/internal/accounts", json=applicant(email="Cook@Example.org "), headers=svc("restaurant"))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["role"] == "restaurant" and body["status"] == "applicant"
    assert body["email"] == "cook@example.org"  # normalised
    assert "password" not in body and "password_hash" not in body


def test_ngo_portal_registers_ngo(core):
    r = core.post("/internal/accounts", json=applicant(email="ngo@example.org"), headers=svc("ngo"))
    assert r.status_code == 201 and r.json()["role"] == "ngo"


def test_admin_portal_cannot_self_register_hosts(core):
    assert core.post("/internal/accounts", json=applicant(), headers=svc("admin")).status_code == 403


def test_duplicate_email_conflict_across_roles(core):
    assert core.post("/internal/accounts", json=applicant(), headers=svc("restaurant")).status_code == 201
    assert core.post("/internal/accounts", json=applicant(), headers=svc("ngo")).status_code == 409


def test_registration_validation(core):
    bad = [applicant(email="not-an-email"), applicant(password="short"), applicant(name=" "),
           applicant(phone="call me"), applicant(password="é" * 40)]  # 80 bytes > bcrypt limit
    for body in bad:
        assert core.post("/internal/accounts", json=body, headers=svc("restaurant")).status_code == 422, body


def test_verify_credentials_role_bound(core):
    core.post("/internal/accounts", json=applicant(), headers=svc("restaurant"))
    ok = core.post("/internal/auth/verify", json={"email": "cook@example.org", "password": "secret-pass-1"},
                   headers=svc("restaurant"))
    assert ok.status_code == 200 and ok.json()["role"] == "restaurant"
    # Right password, wrong portal -> same generic 401 as a wrong password.
    for portal in ("ngo", "admin"):
        r = core.post("/internal/auth/verify", json={"email": "cook@example.org", "password": "secret-pass-1"},
                      headers=svc(portal))
        assert r.status_code == 401 and r.json()["detail"] == "invalid email or password"
    assert core.post("/internal/auth/verify", json={"email": "cook@example.org", "password": "wrong-pass"},
                     headers=svc("restaurant")).status_code == 401
    assert core.post("/internal/auth/verify", json={"email": "ghost@example.org", "password": "whatever1"},
                     headers=svc("restaurant")).status_code == 401


def test_host_credentials_only_on_admin_portal(core):
    creds = {"email": HOST_EMAIL, "password": HOST_PASSWORD}
    assert core.post("/internal/auth/verify", json=creds, headers=svc("admin")).json()["role"] == "host"
    assert core.post("/internal/auth/verify", json=creds, headers=svc("restaurant")).status_code == 401


def test_cross_role_reads_are_hidden(core):
    rid = core.post("/internal/accounts", json=applicant(), headers=svc("restaurant")).json()["id"]
    assert core.get(f"/internal/accounts/{rid}", headers=svc("restaurant")).status_code == 200
    assert core.get(f"/internal/accounts/{rid}", headers=svc("ngo")).status_code == 404
    assert core.get(f"/internal/accounts/{rid}", headers=svc("admin")).status_code == 200


def test_admin_only_listing_and_filters(core):
    core.post("/internal/accounts", json=applicant(), headers=svc("restaurant"))
    core.post("/internal/accounts", json=applicant(email="n@example.org"), headers=svc("ngo"))
    assert core.get("/internal/accounts", headers=svc("restaurant")).status_code == 403
    everyone = core.get("/internal/accounts", headers=svc("admin")).json()
    assert {a["role"] for a in everyone} == {"host", "restaurant", "ngo"}
    ngos = core.get("/internal/accounts", params={"role": "ngo"}, headers=svc("admin")).json()
    assert [a["role"] for a in ngos] == ["ngo"]
    pending = core.get("/internal/accounts", params={"status": "applicant"}, headers=svc("admin")).json()
    assert len(pending) == 2
    assert core.get("/internal/accounts", params={"role": "pirate"}, headers=svc("admin")).status_code == 422


def test_events_recorded_and_admin_only(core):
    core.post("/internal/accounts", json=applicant(), headers=svc("restaurant"))
    core.post("/internal/auth/verify", json={"email": "cook@example.org", "password": "secret-pass-1"},
              headers=svc("restaurant"))
    core.post("/internal/auth/verify", json={"email": "cook@example.org", "password": "bad-pass-00"},
              headers=svc("restaurant"))
    assert core.get("/internal/events", headers=svc("ngo")).status_code == 403
    types = [e["type"] for e in core.get("/internal/events", headers=svc("admin")).json()]
    assert {"account.registered", "auth.login", "auth.failed"} <= set(types)
    # Failed-login events must not store the full email or password.
    failed = [e for e in core.get("/internal/events", headers=svc("admin")).json() if e["type"] == "auth.failed"][0]
    assert "cook@" not in str(failed["payload"]) and "bad-pass" not in str(failed["payload"])


def test_timestamps_are_utc_aware_after_reload(core):
    from datetime import datetime

    rid = core.post("/internal/accounts", json=applicant(), headers=svc("restaurant")).json()["id"]
    stored = core.get(f"/internal/accounts/{rid}", headers=svc("restaurant")).json()["created_at"]
    assert datetime.fromisoformat(stored).utcoffset() is not None, stored  # browsers must not treat it as local time
    for ev in core.get("/internal/events", headers=svc("admin")).json():
        assert datetime.fromisoformat(ev["created_at"]).utcoffset() is not None
