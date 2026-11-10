"""Phase 5: live location sharing and the host's operational map (spec Module 10)."""
from datetime import timedelta

from sqlalchemy.orm import sessionmaker

from core_service.models import AgentLocation
from tests.conftest import svc
from tests.test_fulfilment import collect
from tests.test_listings import now, post, world  # noqa: F401  (world is a fixture)

HERE = {"latitude": 12.9750, "longitude": 77.6060, "accuracy_m": 12}


def test_available_agent_can_share_location(world):
    r = world["ngo"].put("/api/location", json=HERE)
    assert r.status_code == 200, r.text
    assert r.json()["live"] is True and r.json()["latitude"] == HERE["latitude"]
    assert world["ngo"].get("/api/location").json()["location"]["accuracy_m"] == 12


def test_location_only_while_on_duty(world):
    off = world["make_ngo"]("off@example.org", available=False)
    r = off.put("/api/location", json=HERE)
    assert r.status_code == 409 and "only while" in r.json()["detail"]


def test_unverified_agent_cannot_share(world):
    new = world["make_ngo"]("new@example.org", verified=False, available=False)
    assert new.put("/api/location", json=HERE).status_code == 403


def test_location_validation(world):
    assert world["ngo"].put("/api/location", json={"latitude": 95, "longitude": 77}).status_code == 422
    assert world["ngo"].put("/api/location", json={"latitude": 12, "longitude": 190}).status_code == 422


def test_stop_sharing_and_going_off_duty_remove_location(world):
    ngo = world["ngo"]
    ngo.put("/api/location", json=HERE)
    assert ngo.delete("/api/location").status_code == 204
    assert ngo.get("/api/location").json()["location"] is None
    ngo.put("/api/location", json=HERE)
    ngo.put("/api/availability", json={"available": False})  # no pickup -> location is deleted
    assert ngo.get("/api/location").json()["location"] is None


def test_location_kept_while_on_pickup_even_if_unavailable(world):
    ngo = world["ngo"]
    listing_id = post(world["rest"]).json()["id"]
    ngo.post(f"/api/listings/{listing_id}/accept")
    ngo.put("/api/location", json=HERE)
    ngo.put("/api/availability", json={"available": False})
    assert ngo.get("/api/location").json()["location"] is not None
    assert ngo.put("/api/location", json={**HERE, "latitude": 12.9755}).status_code == 200  # still on a pickup


def test_old_location_is_last_known_not_live(core_app, world):
    ngo = world["ngo"]
    ngo.put("/api/location", json=HERE)
    agent_id = ngo.get("/api/me").json()["id"]
    with sessionmaker(core_app.state.engine)() as s:
        s.get(AgentLocation, agent_id).updated_at = now() - timedelta(minutes=5)
        s.commit()
    loc = ngo.get("/api/location").json()["location"]
    assert loc["live"] is False and loc["age_seconds"] >= 300
    agent = world["host"].get("/api/map").json()["agents"][0]
    assert agent["location"]["live"] is False


def test_host_map_contents(world):
    host, ngo, rest = world["host"], world["ngo"], world["rest"]
    open_id = post(rest, food_type="Idli").json()["id"]
    taken_id = post(rest).json()["id"]
    ngo.post(f"/api/listings/{taken_id}/accept")
    ngo.post(f"/api/pickups/{taken_id}/start", json={})
    for lat in (12.9741, 12.9745, 12.9750):  # movement while on the way
        ngo.put("/api/location", json={**HERE, "latitude": lat})

    m = host.get("/api/map").json()
    assert [r["name"] for r in m["restaurants"]] == ["Green Bowl Kitchen"]
    assert m["restaurants"][0]["open_listings"] == 2
    statuses = {l["id"]: l["status"] for l in m["listings"]}
    assert statuses == {open_id: "active", taken_id: "assigned"}
    taken = next(l for l in m["listings"] if l["id"] == taken_id)
    assert taken["step"] == "en_route" and [p[0] for p in taken["trail"]] == [12.9741, 12.9745, 12.9750]
    agent = m["agents"][0]
    assert agent["location"]["live"] and agent["current_pickup"]["listing_id"] == taken_id
    assert agent["current_pickup"]["step"] == "en_route" and agent["base"]["address"]
    assert m["summary"]["open_food"] == 1 and m["summary"]["in_progress"] == 1
    assert m["summary"]["agents_live"] == 1 and m["summary"]["agents_available"] == 1


def test_map_hides_finished_listings_and_counts_deliveries(world):
    host, ngo, rest = world["host"], world["ngo"], world["rest"]
    listing_id = post(rest).json()["id"]
    ngo.post(f"/api/listings/{listing_id}/accept")
    collect(ngo, listing_id)
    rid = ngo.post("/api/recipients", json={"name": "Hope Home", "kind": "shelter", "address": "1 A St"}).json()["id"]
    ngo.post(f"/api/pickups/{listing_id}/deliver", json={"recipient_id": rid, "received_by": "Anil"})
    m = host.get("/api/map").json()
    assert m["listings"] == [] and m["summary"]["delivered"] == 1
    assert m["agents"][0]["stats"]["deliveries"] == 1 and m["agents"][0]["current_pickup"] is None


def test_agent_without_location_is_shown_at_base(world):
    agent = world["host"].get("/api/map").json()["agents"][0]
    assert agent["location"] is None and agent["base"]["latitude"] == 12.9740


def test_map_and_location_access_rules(world, portals, core):
    assert core.get("/internal/map", headers=svc("ngo")).status_code == 403
    assert core.get("/internal/map", headers=svc("restaurant")).status_code == 403
    rest_id = world["rest"].get("/api/me").json()["id"]
    assert core.put(f"/internal/agents/{rest_id}/location", json=HERE, headers=svc("restaurant")).status_code == 403
    assert world["rest"].put("/api/location", json=HERE).status_code in (404, 405)
    signed_out = portals["admin"].__class__(portals["admin"].app)
    assert signed_out.get("/api/map").status_code == 401
