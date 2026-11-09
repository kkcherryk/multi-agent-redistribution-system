"""Phase 2: onboarding, document evidence, host review, badges and NGO availability (spec Modules 2, 3, 5)."""
import pytest

from tests.conftest import HOST_EMAIL, HOST_PASSWORD, applicant, svc

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PDF = b"%PDF-1.4\n%fake test pdf\n"

RESTAURANT_PROFILE = {"applicant_name": "Asha Rao", "restaurant_name": "Green Bowl Kitchen",
                      "address": "12 MG Road, Bengaluru", "latitude": 12.9757, "longitude": 77.6050,
                      "licence_number": "FSSAI-1234567890"}
NGO_PROFILE = {"ngo_name": "Helping Hands Trust", "ngo_registration_id": "NGO-KA-2024-77",
               "representative_name": "Ravi Kumar", "address": "4 Church St, Bengaluru",
               "latitude": 12.9740, "longitude": 77.6070}


def upload(client, kind, data=PNG, name="file.png", ctype="image/png"):
    return client.post("/api/documents", data={"kind": kind}, files={"file": (name, data, ctype)})


def complete_restaurant(client, email="cook@example.org"):
    assert client.post("/api/register", json=applicant(email=email)).status_code == 201
    assert client.put("/api/profile", json=RESTAURANT_PROFILE).status_code == 200
    assert upload(client, "restaurant_photo").status_code == 201
    assert upload(client, "food_licence", PDF, "licence.pdf", "application/pdf").status_code == 201
    assert upload(client, "identity_proof", JPEG, "id.jpg", "image/jpeg").status_code == 201


def complete_ngo(client, email="ngo@example.org"):
    assert client.post("/api/register", json=applicant(email=email, name="Helping Hands")).status_code == 201
    assert client.put("/api/profile", json=NGO_PROFILE).status_code == 200
    assert upload(client, "representative_photo", JPEG, "me.jpg", "image/jpeg").status_code == 201
    assert upload(client, "ngo_registration", PDF, "reg.pdf", "application/pdf").status_code == 201
    assert upload(client, "representative_id", PNG).status_code == 201


@pytest.fixture
def host(portals):
    admin = portals["admin"]
    assert admin.post("/api/login", json={"email": HOST_EMAIL, "password": HOST_PASSWORD}).status_code == 200
    return admin


# ---------------------------------------------------------------- restaurant onboarding (Module 2)

def test_new_applicant_sees_checklist(portals):
    rest = portals["restaurant"]
    rest.post("/api/register", json=applicant())
    ob = rest.get("/api/onboarding").json()
    assert ob["status"] == "applicant" and ob["editable"] is True
    assert ob["missing"] == ["Profile details", "Restaurant photo", "Food licence", "Identity / business proof"]
    assert ob["badges"]["verification"] == {"color": "orange", "label": "Unverified"}


def test_full_restaurant_flow_to_blue_badge(portals, host):
    rest = portals["restaurant"]
    complete_restaurant(rest)
    assert rest.get("/api/onboarding").json()["missing"] == []
    assert rest.get("/api/me").json()["display_name"] == "Green Bowl Kitchen"  # profile name becomes display name
    sub = rest.post("/api/submit")
    assert sub.status_code == 200 and sub.json()["status"] == "under_review"

    queue = host.get("/api/review-queue").json()
    assert [q["profile"]["restaurant_name"] for q in queue] == ["Green Bowl Kitchen"]
    assert len(queue[0]["documents"]) == 3
    r = host.post(f"/api/accounts/{queue[0]['account_id']}/review", json={"decision": "verify"})
    assert r.status_code == 200, r.text
    assert r.json()["account"]["badges"]["verification"] == {"color": "blue", "label": "Verified"}
    me = rest.get("/api/me").json()
    assert me["status"] == "verified" and me["badges"]["verification"]["color"] == "blue"
    assert host.get("/api/review-queue").json() == []


def test_submit_incomplete_is_rejected_with_missing_items(portals):
    rest = portals["restaurant"]
    rest.post("/api/register", json=applicant())
    rest.put("/api/profile", json=RESTAURANT_PROFILE)
    r = rest.post("/api/submit")
    assert r.status_code == 422 and "Restaurant photo" in r.json()["detail"]


def test_profile_validation(portals):
    rest = portals["restaurant"]
    rest.post("/api/register", json=applicant())
    for bad in ({**RESTAURANT_PROFILE, "latitude": 95}, {**RESTAURANT_PROFILE, "longitude": -181},
                {**RESTAURANT_PROFILE, "restaurant_name": " "}, {k: v for k, v in RESTAURANT_PROFILE.items()
                                                                  if k != "licence_number"}):
        assert rest.put("/api/profile", json=bad).status_code == 422, bad
    # An NGO-shaped body is not accepted as a restaurant profile.
    assert rest.put("/api/profile", json=NGO_PROFILE).status_code == 422


def test_application_locked_while_under_review(portals):
    rest = portals["restaurant"]
    complete_restaurant(rest)
    rest.post("/api/submit")
    assert rest.put("/api/profile", json=RESTAURANT_PROFILE).status_code == 409
    assert upload(rest, "restaurant_photo").status_code == 409
    doc_id = rest.get("/api/onboarding").json()["documents"][0]["id"]
    assert rest.delete(f"/api/documents/{doc_id}").status_code == 409
    assert rest.post("/api/submit").status_code == 409


# ---------------------------------------------------------------- uploads

def test_upload_type_checks_use_file_content_not_client_claims(portals):
    rest = portals["restaurant"]
    rest.post("/api/register", json=applicant())
    assert upload(rest, "restaurant_photo", b"<script>alert(1)</script>", "x.png", "image/png").status_code == 415
    assert upload(rest, "restaurant_photo", PDF, "x.pdf", "application/pdf").status_code == 415  # photo must be image
    assert upload(rest, "food_licence", PDF, "l.pdf", "image/png").status_code == 201  # sniffed as PDF, accepted
    assert upload(rest, "food_licence", PDF, "l.pdf").json()["content_type"] == "application/pdf"
    assert upload(rest, "selfie", PNG).status_code == 422  # unknown kind
    assert upload(rest, "restaurant_photo", b"").status_code == 422
    assert upload(rest, "restaurant_photo", PNG + b"\x00" * (5 * 1024 * 1024)).status_code == 413


def test_upload_count_limits(portals):
    rest = portals["restaurant"]
    rest.post("/api/register", json=applicant())
    for _ in range(6):
        assert upload(rest, "restaurant_photo").status_code == 201
    assert upload(rest, "restaurant_photo").status_code == 409


def test_single_slot_photo_is_replaced(portals):
    ngo = portals["ngo"]
    ngo.post("/api/register", json=applicant(email="ngo@example.org"))
    first = upload(ngo, "representative_photo").json()["id"]
    second = upload(ngo, "representative_photo", JPEG, "new.jpg", "image/jpeg").json()["id"]
    docs = ngo.get("/api/onboarding").json()["documents"]
    assert [d["id"] for d in docs] == [second] and first != second


def test_filename_is_sanitised(portals):
    rest = portals["restaurant"]
    rest.post("/api/register", json=applicant())
    doc = upload(rest, "restaurant_photo", PNG, "..\\..\\evil/../photo.png").json()
    assert doc["filename"] == "photo.png"


def test_document_access_owner_and_host_only(portals, host, core):
    rest = portals["restaurant"]
    complete_restaurant(rest)
    doc_id = rest.get("/api/onboarding").json()["documents"][0]["id"]
    own = rest.get(f"/api/documents/{doc_id}")
    assert own.status_code == 200 and own.content == PNG
    assert own.headers["x-content-type-options"] == "nosniff" and "sandbox" in own.headers["content-security-policy"]
    assert host.get(f"/api/documents/{doc_id}").content == PNG
    # Another restaurant cannot read it, nor can the NGO portal, even by calling the core directly.
    rest.post("/api/logout")
    rest.post("/api/register", json=applicant(email="other@example.org"))
    assert rest.get(f"/api/documents/{doc_id}").status_code == 404
    assert core.get(f"/internal/documents/{doc_id}/content", headers=svc("ngo")).status_code == 404
    assert core.get(f"/internal/documents/{doc_id}/content", headers=svc("restaurant")).status_code == 404


def test_delete_document_while_editable(portals):
    rest = portals["restaurant"]
    rest.post("/api/register", json=applicant())
    doc_id = upload(rest, "restaurant_photo").json()["id"]
    assert rest.delete(f"/api/documents/{doc_id}").status_code == 204
    assert rest.get("/api/onboarding").json()["documents"] == []
    assert rest.delete(f"/api/documents/{doc_id}").status_code == 404


# ---------------------------------------------------------------- host review (hold / decline / history)

def test_hold_requires_note_then_resubmit(portals, host):
    rest = portals["restaurant"]
    complete_restaurant(rest)
    rest.post("/api/submit")
    acc_id = rest.get("/api/me").json()["id"]
    assert host.post(f"/api/accounts/{acc_id}/review", json={"decision": "hold"}).status_code == 422
    r = host.post(f"/api/accounts/{acc_id}/review", json={"decision": "hold", "note": "Licence photo is blurry"})
    assert r.status_code == 200
    ob = rest.get("/api/onboarding").json()
    assert ob["status"] == "on_hold" and ob["editable"] is True
    assert ob["badges"]["verification"] == {"color": "orange", "label": "On hold"}
    assert ob["reviews"][0]["note"] == "Licence photo is blurry"
    # Applicant fixes the evidence and resubmits.
    assert upload(rest, "food_licence", PDF, "clear.pdf", "application/pdf").status_code == 201
    assert rest.post("/api/submit").json()["status"] == "under_review"
    assert host.post(f"/api/accounts/{acc_id}/review", json={"decision": "verify"}).status_code == 200
    reviews = host.get(f"/api/accounts/{acc_id}/onboarding").json()["reviews"]
    assert [r["decision"] for r in reviews] == ["verify", "hold"]
    assert reviews[0]["reviewer_id"] == host.get("/api/me").json()["id"]


def test_decline_requires_note_and_locks_application(portals, host):
    rest = portals["restaurant"]
    complete_restaurant(rest)
    rest.post("/api/submit")
    acc_id = rest.get("/api/me").json()["id"]
    assert host.post(f"/api/accounts/{acc_id}/review", json={"decision": "decline", "note": " "}).status_code == 422
    assert host.post(f"/api/accounts/{acc_id}/review",
                     json={"decision": "decline", "note": "Licence not valid"}).status_code == 200
    assert rest.get("/api/onboarding").json()["editable"] is False
    assert rest.put("/api/profile", json=RESTAURANT_PROFILE).status_code == 409


def test_review_rules(portals, host, core):
    rest = portals["restaurant"]
    rest.post("/api/register", json=applicant())
    acc_id = rest.get("/api/me").json()["id"]
    # Not submitted yet.
    assert host.post(f"/api/accounts/{acc_id}/review", json={"decision": "verify"}).status_code == 409
    assert host.post(f"/api/accounts/{acc_id}/review", json={"decision": "approve!"}).status_code == 422
    # Hosts cannot be reviewed; only the admin portal can review.
    host_id = host.get("/api/me").json()["id"]
    assert host.post(f"/api/accounts/{host_id}/review", json={"decision": "verify"}).status_code == 422
    assert core.post(f"/internal/accounts/{acc_id}/review", json={"decision": "verify"},
                     headers=svc("restaurant")).status_code == 403
    # The host never edits an application on the applicant's behalf.
    assert core.put(f"/internal/accounts/{acc_id}/profile", json=RESTAURANT_PROFILE,
                    headers=svc("admin")).status_code == 403


def test_review_events_recorded(portals, host):
    rest = portals["restaurant"]
    complete_restaurant(rest)
    rest.post("/api/submit")
    acc_id = rest.get("/api/me").json()["id"]
    host.post(f"/api/accounts/{acc_id}/review", json={"decision": "verify"})
    assert host.post(f"/api/accounts/{acc_id}/review", json={"decision": "verify"}).status_code == 409
    types = [e["type"] for e in host.get("/api/events").json()]
    for t in ("profile.updated", "document.uploaded", "application.submitted", "review.verify"):
        assert t in types


# ---------------------------------------------------------------- NGO onboarding, availability, directory (3, 5)

def test_ngo_flow_green_tick_and_availability(portals, host):
    ngo = portals["ngo"]
    complete_ngo(ngo)
    me = ngo.get("/api/me").json()
    assert me["badges"] == {"verification": {"color": "grey", "label": "Not verified"},
                            "availability": {"color": "red", "label": "Unavailable"}}
    # Availability is independent of verification: an unverified agent may be available, still not verified.
    assert ngo.put("/api/availability", json={"available": True}).json()["badges"]["availability"]["color"] == "green"
    assert ngo.get("/api/me").json()["badges"]["verification"]["label"] == "Not verified"
    ngo.post("/api/submit")
    host.post(f"/api/accounts/{me['id']}/review", json={"decision": "verify"})
    b = ngo.get("/api/me").json()["badges"]
    assert b["verification"] == {"color": "green", "label": "Verified NGO"} and b["availability"]["color"] == "green"
    assert ngo.put("/api/availability", json={"available": False}).json()["badges"]["availability"]["color"] == "red"


def test_availability_requires_profile_and_ngo_portal(portals, core):
    ngo = portals["ngo"]
    ngo.post("/api/register", json=applicant(email="ngo@example.org"))
    assert ngo.put("/api/availability", json={"available": True}).status_code == 409
    acc_id = ngo.get("/api/me").json()["id"]
    assert portals["restaurant"].put("/api/availability", json={"available": True}).status_code in (404, 405)
    assert core.put(f"/internal/accounts/{acc_id}/availability", json={"available": True},
                    headers=svc("admin")).status_code == 403


def test_ngo_directory_for_host_only(portals, host, core):
    complete_ngo(portals["ngo"])
    portals["ngo"].put("/api/availability", json={"available": True})
    rows = host.get("/api/ngo-directory").json()
    assert len(rows) == 1
    assert rows[0]["profile"]["representative_name"] == "Ravi Kumar"
    assert rows[0]["account"]["badges"]["availability"]["label"] == "Available"
    assert core.get("/internal/ngo-directory", headers=svc("ngo")).status_code == 403
    assert portals["ngo"].get("/api/ngo-directory").status_code in (404, 405)


def test_host_account_list_includes_badges(portals, host):
    complete_restaurant(portals["restaurant"])
    accounts = {a["role"]: a for a in host.get("/api/accounts").json()}
    assert accounts["restaurant"]["badges"]["verification"]["color"] == "orange"
    assert accounts["host"]["badges"] == {}
