"""Phase 4: pickup, inspection, delivery and NGO storage (spec Modules 8 and 9)."""
from datetime import timedelta

import pytest
from sqlalchemy.orm import sessionmaker

from core_service.models import FoodListing, StorageItem
from tests.conftest import svc
from tests.test_listings import now, post, world  # noqa: F401  (world is a fixture)

ORPHANAGE = {"name": "Sunshine Orphanage", "kind": "orphanage", "address": "8 Lake Rd, Bengaluru",
             "latitude": 12.97, "longitude": 77.61, "contact_phone": "+91 90000 22222"}


@pytest.fixture
def accepted(world):
    listing_id = post(world["rest"]).json()["id"]  # 40 portions
    assert world["ngo"].post(f"/api/listings/{listing_id}/accept").status_code == 200
    return listing_id


def act(client, listing_id, action, **body):
    return client.post(f"/api/pickups/{listing_id}/{action}", json=body)


def collect(client, listing_id, qty=40, **extra):
    return act(client, listing_id, "inspection", decision="collect", actual_quantity=qty,
               observed_condition="good", **extra)


def recipient(client):
    return client.post("/api/recipients", json=ORPHANAGE).json()["id"]


# ---------------------------------------------------------------- pickup and inspection (Module 8)

def test_full_flow_to_delivery(world, accepted):
    ngo, rest = world["ngo"], world["rest"]
    assert act(ngo, accepted, "start").json()["assignment"]["status"] == "en_route"
    r = collect(ngo, accepted)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "collected" and r.json()["inspection"]["outcome"] == "full"
    rid = recipient(ngo)
    d = act(ngo, accepted, "deliver", recipient_id=rid, received_by="Sister Mary")
    assert d.status_code == 200, d.text
    body = d.json()
    assert body["status"] == "delivered" and body["handovers"][0]["recipient"]["name"] == "Sunshine Orphanage"
    assert body["handovers"][0]["quantity"] == 40 and body["handovers"][0]["received_by"] == "Sister Mary"
    # The restaurant sees the whole story.
    mine = rest.get("/api/listings").json()[0]
    assert mine["status"] == "delivered" and mine["inspection"]["actual_quantity"] == 40
    assert mine["handovers"][0]["recipient"]["kind"] == "orphanage"
    # Host listing shows it too, and the agent is free for the next pickup.
    assert world["host"].get("/api/listings").json()[0]["status"] == "delivered"
    nxt = post(rest, food_type="Idli").json()["id"]
    assert ngo.post(f"/api/listings/{nxt}/accept").status_code == 200


def test_food_not_collected_until_inspection(world, accepted):
    rid = recipient(world["ngo"])
    assert act(world["ngo"], accepted, "deliver", recipient_id=rid, received_by="X Y").status_code == 409
    assert act(world["ngo"], accepted, "store").status_code == 409


def test_partial_quantity_needs_note(world, accepted):
    ngo = world["ngo"]
    assert collect(ngo, accepted, qty=30).status_code == 422
    r = collect(ngo, accepted, qty=30, notes="Only 30 portions were packed")
    assert r.status_code == 200 and r.json()["inspection"]["outcome"] == "partial"
    assert r.json()["inspection"]["listed_quantity"] == 40


@pytest.mark.parametrize("body", [
    {"decision": "collect", "actual_quantity": 50, "observed_condition": "good"},     # more than listed
    {"decision": "collect", "actual_quantity": 0, "observed_condition": "good"},
    {"decision": "collect", "actual_quantity": 10, "observed_condition": "spoiled"},  # spoiled must be rejected
    {"decision": "collect", "actual_quantity": 10, "observed_condition": "smelly"},
    {"decision": "maybe", "actual_quantity": 10, "observed_condition": "good"},
    {"decision": "reject", "observed_condition": "spoiled"},                          # reject needs a reason
])
def test_inspection_validation(world, accepted, body):
    assert world["ngo"].post(f"/api/pickups/{accepted}/inspection", json=body).status_code == 422


def test_reject_closes_listing_and_frees_agent(world, accepted):
    ngo = world["ngo"]
    r = act(ngo, accepted, "inspection", decision="reject", observed_condition="spoiled", notes="Food smelled sour")
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert world["rest"].get("/api/listings").json()[0]["inspection"]["notes"] == "Food smelled sour"
    assert "pickup.rejected" in [e["type"] for e in world["host"].get("/api/events").json()]
    assert ngo.post(f"/api/listings/{post(world['rest']).json()['id']}/accept").status_code == 200


def test_release_reoffers_food(world, accepted):
    second = world["make_ngo"]("second@example.org")
    act(world["ngo"], accepted, "start")
    r = world["ngo"].post(f"/api/pickups/{accepted}/release", json={"reason": "Vehicle broke down"})
    assert r.status_code == 200 and r.json()["status"] == "active"
    assert [x["id"] for x in second.get("/api/feed").json()["listings"]] == [accepted]
    assert second.post(f"/api/listings/{accepted}/accept").status_code == 200  # reassigned
    assert world["ngo"].get("/api/pickups").json() == []


def test_cannot_release_after_collection(world, accepted):
    collect(world["ngo"], accepted)
    assert world["ngo"].post(f"/api/pickups/{accepted}/release", json={}).status_code == 409


def test_other_agent_cannot_touch_pickup(world, accepted):
    other = world["make_ngo"]("other@example.org")
    assert act(other, accepted, "start").status_code == 404
    assert collect(other, accepted).status_code == 404


def test_expired_food_cannot_be_collected_or_delivered(core_app, world, accepted):
    with sessionmaker(core_app.state.engine)() as s:
        s.get(FoodListing, accepted).expires_at = now() - timedelta(minutes=1)
        s.commit()
    assert collect(world["ngo"], accepted).status_code == 409


def test_dispose_after_expiry_in_transit(core_app, world, accepted):
    ngo = world["ngo"]
    collect(ngo, accepted)
    with sessionmaker(core_app.state.engine)() as s:
        s.get(FoodListing, accepted).expires_at = now() - timedelta(minutes=1)
        s.commit()
    rid = recipient(ngo)
    assert act(ngo, accepted, "deliver", recipient_id=rid, received_by="X Y").status_code == 409
    assert act(ngo, accepted, "dispose").status_code == 422  # reason required
    r = act(ngo, accepted, "dispose", reason="Expired in traffic")
    assert r.status_code == 200 and r.json()["status"] == "disposed"


# ---------------------------------------------------------------- delivery and storage (Module 9)

def test_delivery_needs_own_recipient_and_receiver_name(world, accepted):
    ngo = world["ngo"]
    collect(ngo, accepted)
    other = world["make_ngo"]("other@example.org")
    foreign = recipient(other)
    assert act(ngo, accepted, "deliver", recipient_id=foreign, received_by="X Y").status_code == 404
    rid = recipient(ngo)
    assert act(ngo, accepted, "deliver", recipient_id=rid, received_by=" ").status_code == 422


def test_recipient_validation(world):
    assert world["ngo"].post("/api/recipients", json={**ORPHANAGE, "kind": "zoo"}).status_code == 422
    assert world["ngo"].post("/api/recipients", json={**ORPHANAGE, "name": ""}).status_code == 422
    assert [r["name"] for r in world["ngo"].get("/api/recipients").json()] == []


def test_store_then_distribute_in_parts(world, accepted):
    ngo = world["ngo"]
    collect(ngo, accepted)
    r = act(ngo, accepted, "store", notes="Fridge 2")
    assert r.status_code == 200 and r.json()["status"] == "stored"
    stock = ngo.get("/api/storage").json()
    assert len(stock) == 1 and stock[0]["remaining_quantity"] == 40 and stock[0]["location"] == "4 Church St, Bengaluru"
    item = stock[0]["id"]
    rid = recipient(ngo)
    assert ngo.post(f"/api/storage/{item}/distribute",
                    json={"recipient_id": rid, "quantity": 50, "received_by": "Sister Mary"}).status_code == 422
    s1 = ngo.post(f"/api/storage/{item}/distribute", json={"recipient_id": rid, "quantity": 25, "received_by": "Sister Mary"})
    assert s1.json()["remaining_quantity"] == 15 and s1.json()["status"] == "in_stock"
    s2 = ngo.post(f"/api/storage/{item}/distribute", json={"recipient_id": rid, "quantity": 15, "received_by": "Sister Mary"})
    assert s2.json()["status"] == "distributed"
    assert ngo.post(f"/api/storage/{item}/dispose", json={"reason": "x"}).status_code == 409
    kinds = [h["kind"] for h in world["rest"].get("/api/listings").json()[0]["handovers"]]
    assert kinds == ["stored", "distributed", "distributed"]
    # Storing frees the agent for the next pickup.
    assert ngo.post(f"/api/listings/{post(world['rest']).json()['id']}/accept").status_code == 200


def test_expired_stock_must_be_disposed(core_app, world, accepted):
    ngo = world["ngo"]
    collect(ngo, accepted)
    act(ngo, accepted, "store")
    item = ngo.get("/api/storage").json()[0]["id"]
    with sessionmaker(core_app.state.engine)() as s:
        s.get(StorageItem, item).expires_at = now() - timedelta(minutes=1)
        s.commit()
    stock = ngo.get("/api/storage").json()[0]
    assert stock["expired"] is True
    rid = recipient(ngo)
    assert ngo.post(f"/api/storage/{item}/distribute",
                    json={"recipient_id": rid, "quantity": 5, "received_by": "A B"}).status_code == 409
    d = ngo.post(f"/api/storage/{item}/dispose", json={"reason": "Past best-before"})
    assert d.json()["status"] == "disposed" and d.json()["remaining_quantity"] == 0


def test_host_sees_all_storage(world, accepted, core):
    collect(world["ngo"], accepted)
    act(world["ngo"], accepted, "store")
    rows = world["host"].get("/api/storage").json()
    assert rows[0]["ngo_name"] == "NGO near@example.org" and rows[0]["remaining_quantity"] == 40
    assert core.get("/internal/storage", headers=svc("ngo")).status_code == 403


def test_unknown_pickup_action_and_wrong_portal(world, accepted, core):
    assert world["ngo"].post(f"/api/pickups/{accepted}/teleport", json={}).status_code == 404
    rest_id = world["rest"].get("/api/me").json()["id"]
    assert core.post(f"/internal/pickups/{accepted}/start", params={"agent_id": rest_id},
                     headers=svc("restaurant")).status_code == 403
