"""Phase 3: food listings, expiry, nearby-NGO alerts and first-accept claims (spec Modules 6 and 7)."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from core_service.models import Assignment, FoodListing
from tests.conftest import HOST_EMAIL, HOST_PASSWORD, applicant, svc
from tests.test_onboarding import JPEG, NGO_PROFILE, PDF, PNG, RESTAURANT_PROFILE, complete_ngo, complete_restaurant

MYSURU = {"latitude": 12.2958, "longitude": 76.6394}  # ~130 km from the Bengaluru restaurant


def now():
    return datetime.now(timezone.utc)


def listing_data(**over):
    data = {"food_type": "Veg biryani", "description": "Packed in foil trays", "dietary": "veg", "quantity": 40,
            "unit": "portions", "condition": "fresh", "prepared_at": (now() - timedelta(hours=1)).isoformat(),
            "expires_at": (now() + timedelta(hours=4)).isoformat(), "pickup_notes": "Back door"}
    data.update(over)
    return data


def post(client, photo=PNG, **over):
    return client.post("/api/listings", data={"data": json.dumps(listing_data(**over))},
                       files={"photo": ("food.png", photo, "image/png")})


def new_client(portals, portal):
    return TestClient(portals[portal].app)


@pytest.fixture
def world(portals):
    """A verified restaurant, a verified + available nearby NGO, and a signed-in host."""
    host = portals["admin"]
    assert host.post("/api/login", json={"email": HOST_EMAIL, "password": HOST_PASSWORD}).status_code == 200

    def verify(client):
        client.post("/api/submit")
        acc_id = client.get("/api/me").json()["id"]
        assert host.post(f"/api/accounts/{acc_id}/review", json={"decision": "verify"}).status_code == 200
        return acc_id

    rest = portals["restaurant"]
    complete_restaurant(rest)
    verify(rest)

    def make_ngo(email, location=None, available=True, verified=True):
        c = new_client(portals, "ngo")
        c.post("/api/register", json=applicant(email=email, name="NGO " + email))
        c.put("/api/profile", json={**NGO_PROFILE, "ngo_name": "NGO " + email, **(location or {})})
        c.post("/api/documents", data={"kind": "representative_photo"}, files={"file": ("p.jpg", JPEG, "image/jpeg")})
        c.post("/api/documents", data={"kind": "ngo_registration"}, files={"file": ("r.pdf", PDF, "application/pdf")})
        c.post("/api/documents", data={"kind": "representative_id"}, files={"file": ("i.png", PNG, "image/png")})
        if verified:
            verify(c)
        if available:
            assert c.put("/api/availability", json={"available": True}).status_code == 200
        return c

    return {"host": host, "rest": rest, "ngo": make_ngo("near@example.org"), "make_ngo": make_ngo, "verify": verify}


# ---------------------------------------------------------------- posting (Module 6)

def test_unverified_restaurant_cannot_post(portals):
    rest = portals["restaurant"]
    complete_restaurant(rest)
    r = post(rest)
    assert r.status_code == 403 and "verified" in r.json()["detail"]


def test_verified_restaurant_posts_listing(world):
    r = post(world["rest"])
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "active" and body["quantity"] == 40 and body["unit"] == "portions"
    assert body["latitude"] == RESTAURANT_PROFILE["latitude"]  # pickup point comes from the verified profile
    assert body["photo_id"] and body["assignment"] is None
    assert [x["id"] for x in world["rest"].get("/api/listings").json()] == [body["id"]]


@pytest.mark.parametrize("over,status", [
    ({"expires_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()}, 422),     # already expired
    ({"expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()}, 422),     # too soon to collect
    ({"expires_at": (datetime.now(timezone.utc) + timedelta(hours=80)).isoformat()}, 422),      # beyond 72 h
    ({"prepared_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}, 422),      # prepared in future
    ({"prepared_at": (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(),
      "expires_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()}, 422),      # expiry before prep
    ({"quantity": 0}, 422), ({"unit": "litres"}, 422), ({"dietary": "vegan"}, 422),
    ({"condition": "spoiled"}, 422), ({"food_type": " "}, 422),
])
def test_listing_validation(world, over, status):
    assert post(world["rest"], **over).status_code == status


def test_listing_photo_required_and_must_be_image(world):
    assert post(world["rest"], photo=b"").status_code == 422
    assert post(world["rest"], photo=PDF).status_code == 415
    assert post(world["rest"], photo=b"<html>").status_code == 415


# ---------------------------------------------------------------- nearby alerts (Module 7)

def test_only_nearby_verified_available_agents_are_alerted(world, portals):
    far = world["make_ngo"]("far@example.org", location=MYSURU)
    busy = world["make_ngo"]("off@example.org", available=False)
    unverified = world["make_ngo"]("new@example.org", verified=False)
    listing = post(world["rest"]).json()

    feed = world["ngo"].get("/api/feed").json()
    assert feed["radius_km"] == 5.0
    item = feed["listings"][0]
    assert item["id"] == listing["id"] and item["alerted"] and item["new"] and item["distance_km"] < 1
    assert world["ngo"].get("/api/feed").json()["listings"][0]["new"] is False  # alert marked as seen
    assert far.get("/api/feed").json()["listings"] == []
    assert busy.get("/api/feed").json()["listings"][0]["alerted"] is False  # visible in feed, but was not alerted
    assert unverified.get("/api/feed").status_code == 403
    assert world["host"].get("/api/listings").json()[0]["notified_agents"] == 1


def test_feed_hides_restaurant_badge_and_contact_until_accepted(world):
    post(world["rest"])
    item = world["ngo"].get("/api/feed").json()["listings"][0]
    assert "badges" not in json.dumps(item) and "phone" not in item["restaurant"]
    assert item["restaurant"]["name"] == "Green Bowl Kitchen" and item["restaurant"]["address"]


def test_agent_becoming_available_is_alerted_about_waiting_food(world):
    late = world["make_ngo"]("late@example.org", available=False)
    listing_id = post(world["rest"]).json()["id"]
    assert late.get("/api/feed").json()["listings"][0]["alerted"] is False
    late.put("/api/availability", json={"available": True})
    item = late.get("/api/feed").json()["listings"][0]
    assert item["id"] == listing_id and item["alerted"] and item["new"]


# ---------------------------------------------------------------- accepting (first come, no double assignment)

def test_first_accept_wins_and_restaurant_sees_collector(world):
    second = world["make_ngo"]("second@example.org")
    listing_id = post(world["rest"]).json()["id"]
    r = world["ngo"].post(f"/api/listings/{listing_id}/accept")
    assert r.status_code == 200, r.text
    pickup = r.json()
    assert pickup["status"] == "assigned" and pickup["restaurant"]["phone"] == "+91 98765 43210"
    again = second.post(f"/api/listings/{listing_id}/accept")
    assert again.status_code == 409 and "already been taken" in again.json()["detail"]
    mine = world["rest"].get("/api/listings").json()[0]
    assert mine["status"] == "assigned" and mine["assignment"]["representative_name"] == "Ravi Kumar"
    assert world["ngo"].get("/api/feed").json()["listings"] == []  # no longer offered
    assert [p["id"] for p in world["ngo"].get("/api/pickups").json()] == [listing_id]


def test_one_active_pickup_per_agent(world):
    first = post(world["rest"]).json()["id"]
    second = post(world["rest"], food_type="Chapati").json()["id"]
    assert world["ngo"].post(f"/api/listings/{first}/accept").status_code == 200
    r = world["ngo"].post(f"/api/listings/{second}/accept")
    assert r.status_code == 409 and "current pickup" in r.json()["detail"]


def test_unavailable_or_far_agent_cannot_accept(world):
    listing_id = post(world["rest"]).json()["id"]
    far = world["make_ngo"]("far@example.org", location=MYSURU)
    assert far.post(f"/api/listings/{listing_id}/accept").status_code == 403
    world["ngo"].put("/api/availability", json={"available": False})
    assert world["ngo"].post(f"/api/listings/{listing_id}/accept").status_code == 409


def test_database_rejects_second_assignment(core_app, world):
    listing_id = post(world["rest"]).json()["id"]
    agent_id = world["ngo"].get("/api/me").json()["id"]
    world["ngo"].post(f"/api/listings/{listing_id}/accept")
    with sessionmaker(core_app.state.engine)() as s:
        s.add(Assignment(listing_id=listing_id, agent_id=agent_id, distance_km=0.1))
        with pytest.raises(IntegrityError):
            s.flush()


# ---------------------------------------------------------------- cancel and expiry

def test_cancel_rules(world, portals):
    listing_id = post(world["rest"]).json()["id"]
    other = new_client(portals, "restaurant")
    other.post("/api/register", json=applicant(email="other@example.org"))
    assert other.post(f"/api/listings/{listing_id}/cancel").status_code == 404
    assert world["rest"].post(f"/api/listings/{listing_id}/cancel").json()["status"] == "cancelled"
    assert world["ngo"].post(f"/api/listings/{listing_id}/accept").status_code == 409
    assigned = post(world["rest"]).json()["id"]
    world["ngo"].post(f"/api/listings/{assigned}/accept")
    assert world["rest"].post(f"/api/listings/{assigned}/cancel").status_code == 409


def test_expired_listing_drops_out_and_cannot_be_accepted(core_app, world):
    listing_id = post(world["rest"]).json()["id"]
    with sessionmaker(core_app.state.engine)() as s:  # simulate time passing
        s.get(FoodListing, listing_id).expires_at = now() - timedelta(minutes=1)
        s.commit()
    assert world["ngo"].get("/api/feed").json()["listings"] == []
    assert world["ngo"].post(f"/api/listings/{listing_id}/accept").status_code == 409
    assert world["rest"].get("/api/listings").json()[0]["status"] == "expired"
    assert "listing.expired" in [e["type"] for e in world["host"].get("/api/events").json()]


# ---------------------------------------------------------------- access control

def test_listing_photo_access(world, portals, core):
    listing_id = post(world["rest"]).json()["id"]
    assert world["rest"].get(f"/api/listings/{listing_id}/photo").content == PNG
    assert world["ngo"].get(f"/api/listings/{listing_id}/photo").content == PNG      # nearby agent
    assert world["host"].get(f"/api/listings/{listing_id}/photo").content == PNG
    far = world["make_ngo"]("far@example.org", location=MYSURU)
    assert far.get(f"/api/listings/{listing_id}/photo").status_code == 404
    other = new_client(portals, "restaurant")
    other.post("/api/register", json=applicant(email="other@example.org"))
    assert other.get(f"/api/listings/{listing_id}/photo").status_code == 404


def test_portals_cannot_act_for_other_roles(world, core):
    rest_id = world["rest"].get("/api/me").json()["id"]
    agent_id = world["ngo"].get("/api/me").json()["id"]
    assert core.get(f"/internal/agents/{rest_id}/feed", headers=svc("ngo")).status_code == 403
    assert core.get(f"/internal/agents/{agent_id}/feed", headers=svc("restaurant")).status_code == 403
    assert core.get(f"/internal/restaurants/{rest_id}/listings", headers=svc("ngo")).status_code == 403
    assert core.get("/internal/listings", headers=svc("restaurant")).status_code == 403
    assert world["ngo"].post("/api/listings", data={"data": "{}"}).status_code in (404, 405)
