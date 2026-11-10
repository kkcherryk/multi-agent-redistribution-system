"""Phase 6: agent runtime, logistics agent (ranking + routes) and backup collection agent (spec Module 11)."""
from datetime import timedelta

import pytest
from sqlalchemy.orm import sessionmaker

from core_service.agents.routing import (ROAD_FACTOR, best_order, directions_url, plan_route, road_km,
                                         travel_minutes)
from core_service.models import BackupRequest, FoodListing, ListingEscalation
from shared.geo import haversine_km
from tests.conftest import svc
from tests.test_fulfilment import collect
from tests.test_listings import MYSURU, now, post, world  # noqa: F401  (world is a fixture)

MID = {"latitude": 12.9200, "longitude": 77.6400}     # ~7 km from the restaurant: outside 5 km, inside 10 km
FAR_10 = {"latitude": 13.0600, "longitude": 77.6050}  # ~9.4 km away


# ---------------------------------------------------------------- routing maths

def test_road_estimate_and_travel_time():
    a, b = (12.9757, 77.6050), (12.9740, 77.6070)
    assert road_km(a, b) == pytest.approx(haversine_km(*a, *b) * ROAD_FACTOR)
    assert travel_minutes(18) == pytest.approx(60)


def test_best_order_visits_nearest_first_and_respects_fixed_end():
    pts = [(0.0, 0.0), (0.0, 0.03), (0.0, 0.01), (0.0, 0.02)]
    assert best_order(pts) == [0, 2, 3, 1]
    pts_end = [(0.0, 0.0), (0.0, 0.02), (0.0, 0.01), (0.0, 0.05)]
    assert best_order(pts_end, fixed_end=True) == [0, 2, 1, 3]


def test_two_opt_removes_crossing():
    # A route that zig-zags across a square is improved to walk around it.
    pts = [(0.0, 0.0), (0.0, 0.01), (0.01, 0.0), (0.01, 0.01)]
    order = best_order(pts)
    plan_len = sum(road_km(pts[order[i]], pts[order[i + 1]]) for i in range(3))
    crossing = sum(road_km(pts[o], pts[p]) for o, p in [(0, 3), (3, 1), (1, 2)])
    assert plan_len < crossing


def test_plan_route_totals_and_directions():
    plan = plan_route([("A", (12.97, 77.60)), ("B", (12.98, 77.61)), ("C", (12.99, 77.62))], reorder=False)
    assert plan.order == ["A", "B", "C"] and len(plan.legs) == 2
    assert plan.total_minutes == pytest.approx(sum(l.minutes for l in plan.legs) + 5)
    assert directions_url([(12.97, 77.6), (12.98, 77.61)]) == \
        "https://www.google.com/maps/dir/12.970000,77.600000/12.980000,77.610000"


# ---------------------------------------------------------------- logistics agent

def test_new_listing_gets_ranked_recommendations(world):
    farther = world["make_ngo"]("farther@example.org", location={"latitude": 12.9600, "longitude": 77.6200})
    off = world["make_ngo"]("off@example.org", available=False)
    post(world["rest"])
    near_item = world["ngo"].get("/api/feed").json()["listings"][0]
    far_item = farther.get("/api/feed").json()["listings"][0]
    assert near_item["recommended"]["rank"] == 1 and far_item["recommended"]["rank"] == 2
    assert near_item["recommended"]["eta_minutes"] < far_item["recommended"]["eta_minutes"]
    assert "NGO base" in near_item["recommended"]["reason"]
    assert off.get("/api/feed").json()["listings"][0]["recommended"] is None  # unavailable: not recommended


def test_live_location_is_used_for_ranking(world):
    farther = world["make_ngo"]("farther@example.org", location={"latitude": 12.9600, "longitude": 77.6200})
    farther.put("/api/location", json={"latitude": 12.9758, "longitude": 77.6051})  # standing at the restaurant
    post(world["rest"])
    rec = farther.get("/api/feed").json()["listings"][0]["recommended"]
    assert rec["rank"] == 1 and "current location" in rec["reason"]


def test_fairness_penalty_spreads_work(world):
    twin = world["make_ngo"]("twin@example.org")  # same NGO base as the main agent
    ngo = world["ngo"]
    for _ in range(3):  # the main agent completes three pickups
        lid = post(world["rest"]).json()["id"]
        ngo.post(f"/api/listings/{lid}/accept")
        collect(ngo, lid)
        ngo.post(f"/api/pickups/{lid}/store", json={})
    post(world["rest"], food_type="Dal")
    rec_twin = twin.get("/api/feed").json()["listings"][0]["recommended"]
    rec_main = ngo.get("/api/feed").json()["listings"][0]["recommended"]
    assert rec_twin["rank"] == 1 and rec_main["rank"] == 2 and "3 pickup(s)" in rec_main["reason"]


def test_pickup_route_before_and_after_collection(world):
    ngo = world["ngo"]
    lid = post(world["rest"]).json()["id"]
    ngo.post(f"/api/listings/{lid}/accept")
    r = ngo.get(f"/api/pickups/{lid}/route").json()
    assert r["order"][1] == "Restaurant" and r["destination"]["kind"] == "storage" and len(r["legs"]) == 2
    assert ngo.post("/api/recipients", json={"name": "Far Home", "kind": "shelter", "address": "Far Rd",
                                             **MID}).status_code == 201
    assert ngo.post("/api/recipients", json={"name": "Near Home", "kind": "orphanage", "address": "Near Rd",
                                             "latitude": 12.9760, "longitude": 77.6060}).status_code == 201
    r = ngo.get(f"/api/pickups/{lid}/route").json()
    assert r["destination"] == {"name": "Near Home", "kind": "recipient"}
    assert r["directions_url"].startswith("https://www.google.com/maps/dir/")
    collect(ngo, lid)
    after = ngo.get(f"/api/pickups/{lid}/route").json()
    assert after["order"][0] == "Restaurant" and len(after["legs"]) == 1


def test_route_is_private_to_the_collector(world):
    lid = post(world["rest"]).json()["id"]
    world["ngo"].post(f"/api/listings/{lid}/accept")
    other = world["make_ngo"]("other@example.org")
    assert other.get(f"/api/pickups/{lid}/route").status_code == 404


# ---------------------------------------------------------------- backup agent

def run_agents(world):
    r = world["host"].post("/api/agent-system/run")
    assert r.status_code == 200, r.text
    assert all(a["last_error"] is None for a in r.json()["agents"]), r.json()


def age_listing(core_app, listing_id, minutes):
    with sessionmaker(core_app.state.engine)() as s:
        s.get(FoodListing, listing_id).created_at = now() - timedelta(minutes=minutes)
        s.commit()


def age_escalation(core_app, listing_id, minutes):
    with sessionmaker(core_app.state.engine)() as s:
        s.get(ListingEscalation, listing_id).updated_at = now() - timedelta(minutes=minutes)
        s.commit()


def volunteer(world, email, location):
    c = world["make_ngo"](email, location=location, available=False)
    assert c.put("/api/backup", json={"active": True, "max_distance_km": 10}).status_code == 200
    return c


def escalation(world, listing_id):
    return next((e for e in world["host"].get("/api/agent-system").json()["escalations"]
                 if e["listing_id"] == listing_id), None)


def test_fresh_listing_is_not_escalated(world):
    lid = post(world["rest"]).json()["id"]
    run_agents(world)
    assert escalation(world, lid) is None


def test_unaccepted_food_triggers_wider_alert(core_app, world):
    mid = world["make_ngo"]("mid@example.org", location=MID)
    lid = post(world["rest"]).json()["id"]
    assert mid.get("/api/feed").json()["listings"] == []  # outside 5 km
    age_listing(core_app, lid, 25)
    run_agents(world)
    esc = escalation(world, lid)
    assert esc["stage"] == "widened" and "not accepted after" in esc["reason"]
    topics = [m["topic"] for m in world["host"].get("/api/agent-system").json()["messages"]]
    assert "food_at_risk" in topics


def test_backup_request_flow_accept_and_store(core_app, world):
    vol = volunteer(world, "vol@example.org", MID)
    lid = post(world["rest"]).json()["id"]
    age_listing(core_app, lid, 25)
    run_agents(world)                 # -> widened
    age_escalation(core_app, lid, 11)
    run_agents(world)                 # -> backup requested
    assert escalation(world, lid)["stage"] == "backup_requested"
    reqs = vol.get("/api/backup-requests").json()
    assert len(reqs) == 1 and reqs[0]["listing"]["id"] == lid
    # Volunteer accepts even though they are not "Available" for normal pickups.
    r = vol.post(f"/api/backup-requests/{reqs[0]['id']}/accept")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "assigned"
    route = vol.get(f"/api/pickups/{lid}/route").json()
    assert route["backup_collection"] is True and route["destination"]["kind"] == "storage"
    assert escalation(world, lid)["stage"] == "resolved"
    assert world["ngo"].post(f"/api/listings/{lid}/accept").status_code == 409  # no double assignment


def test_decline_moves_to_next_volunteer_then_host(core_app, world):
    first = volunteer(world, "vol1@example.org", MID)
    second = volunteer(world, "vol2@example.org", FAR_10)
    lid = post(world["rest"]).json()["id"]
    world["host"].post(f"/api/listings/{lid}/escalate")       # host skips the waiting
    req1 = first.get("/api/backup-requests").json()
    assert len(req1) == 1 and second.get("/api/backup-requests").json() == []  # nearest volunteer first
    assert first.post(f"/api/backup-requests/{req1[0]['id']}/decline").json()["status"] == "declined"
    req2 = second.get("/api/backup-requests").json()
    assert len(req2) == 1
    second.post(f"/api/backup-requests/{req2[0]['id']}/decline")
    esc = escalation(world, lid)
    assert esc["stage"] == "needs_host" and [q["status"] for q in esc["backup_requests"]] == ["declined", "declined"]


def test_unanswered_request_times_out(core_app, world):
    first = volunteer(world, "vol1@example.org", MID)
    second = volunteer(world, "vol2@example.org", FAR_10)
    lid = post(world["rest"]).json()["id"]
    world["host"].post(f"/api/listings/{lid}/escalate")
    with sessionmaker(core_app.state.engine)() as s:
        req = s.query(BackupRequest).filter_by(listing_id=lid).one()
        req.created_at = now() - timedelta(minutes=11)
        s.commit()
    run_agents(world)
    assert first.get("/api/backup-requests").json() == []
    assert len(second.get("/api/backup-requests").json()) == 1


def test_no_agent_nearby_goes_straight_to_backup(core_app, world, portals):
    world["ngo"].put("/api/availability", json={"available": False})
    vol = volunteer(world, "vol@example.org", MID)
    lid = post(world["rest"]).json()["id"]
    age_listing(core_app, lid, 6)      # nobody could be alerted for 6 minutes
    run_agents(world)
    esc = escalation(world, lid)
    assert esc["stage"] == "backup_requested" and "no available NGO agent nearby" in esc["reason"]
    assert len(vol.get("/api/backup-requests").json()) == 1


def test_escalation_resolves_when_someone_accepts(core_app, world):
    vol = volunteer(world, "vol@example.org", MID)
    lid = post(world["rest"]).json()["id"]
    world["host"].post(f"/api/listings/{lid}/escalate")
    assert len(vol.get("/api/backup-requests").json()) == 1
    world["ngo"].post(f"/api/listings/{lid}/accept")  # a normal agent takes it after all
    run_agents(world)
    assert escalation(world, lid)["stage"] == "resolved"
    assert vol.get("/api/backup-requests").json() == []  # offer withdrawn


def test_expiring_food_is_at_risk(core_app, world):
    lid = post(world["rest"], expires_at=(now() + timedelta(minutes=50)).isoformat()).json()["id"]
    run_agents(world)
    assert "min left before expiry" in escalation(world, lid)["reason"]


def test_backup_rules_and_access(world, core):
    new = world["make_ngo"]("new@example.org", verified=False, available=False)
    assert new.put("/api/backup", json={"active": True}).status_code == 403
    assert world["ngo"].put("/api/backup", json={"active": True, "max_distance_km": 99}).status_code == 422
    vol = volunteer(world, "vol@example.org", MID)
    lid = post(world["rest"]).json()["id"]
    world["host"].post(f"/api/listings/{lid}/escalate")
    rid = vol.get("/api/backup-requests").json()[0]["id"]
    assert world["ngo"].post(f"/api/backup-requests/{rid}/accept").status_code == 404  # not their request
    assert core.get("/internal/agent-system", headers=svc("ngo")).status_code == 403
    assert core.post("/internal/agent-system/run", headers=svc("restaurant")).status_code == 403
    world["rest"].post(f"/api/listings/{lid}/cancel")
    assert world["host"].post(f"/api/listings/{lid}/escalate").status_code == 409


def test_agent_status_and_message_log(world):
    post(world["rest"])
    data = world["host"].get("/api/agent-system").json()
    assert {a["name"] for a in data["agents"]} == {"logistics", "backup"}
    ranked = [m for m in data["messages"] if m["topic"] == "collectors_ranked"]
    assert ranked and ranked[0]["sender"] == "logistics" and ranked[0]["payload"]["ranking"][0]["eta_minutes"] > 0
