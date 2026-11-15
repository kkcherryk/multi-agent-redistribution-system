"""Phase 7: trust assessment (Module 12) and Contract-Net negotiation between agents."""
from datetime import timedelta

import pytest
from sqlalchemy.orm import sessionmaker

from core_service.models import Handover
from core_service.trust import beta_score, bid_trust_penalty, confidence
from tests.conftest import svc
from tests.test_fulfilment import collect
from tests.test_listings import now, post, world  # noqa: F401  (world is a fixture)


# ---------------------------------------------------------------- the formula

def test_beta_score_and_confidence():
    assert beta_score(0, 0) == 50.0                       # newcomer: neutral
    assert beta_score(8, 1) == pytest.approx(81.8)        # the worked example
    assert beta_score(0, 1) == pytest.approx(33.3)        # one bad event is not zero
    assert [confidence(n) for n in (0, 4, 5, 19, 20)] == ["low", "low", "medium", "medium", "high"]


def test_bid_trust_penalty_is_capped():
    assert bid_trust_penalty(100) == 0 and bid_trust_penalty(50) == 5 and bid_trust_penalty(0) == 10
    assert bid_trust_penalty(82) == pytest.approx(1.8)


# ---------------------------------------------------------------- helpers

def finish(ngo, rest, how="store", qty=40, **post_over):
    lid = post(rest, **post_over).json()["id"]
    assert ngo.post(f"/api/listings/{lid}/accept").status_code == 200
    if how == "release":
        ngo.post(f"/api/pickups/{lid}/release", json={"reason": "bike broke"})
        return lid
    if how == "reject":
        ngo.post(f"/api/pickups/{lid}/inspection", json={"decision": "reject", "observed_condition": "spoiled",
                                                         "notes": "smelled sour"})
        return lid
    notes = None if qty == 40 else "short"
    collect(ngo, lid, qty=qty, **({"notes": notes} if notes else {}))
    if how == "dispose":
        ngo.post(f"/api/pickups/{lid}/dispose", json={"reason": "fell over"})
    else:
        ngo.post(f"/api/pickups/{lid}/store", json={})
    return lid


def trust_row(world, name):
    data = world["host"].get("/api/trust").json()
    return next(r for r in data["ngos"] + data["restaurants"] if r["name"] == name)


# ---------------------------------------------------------------- NGO and restaurant scores

def test_new_accounts_start_neutral(world):
    ngo = trust_row(world, "NGO near@example.org")
    assert ngo["score"] == 50 and ngo["confidence"] == "low" and ngo["reasons"] == ["No finished pickups yet"]
    rest = trust_row(world, "Green Bowl Kitchen")
    assert rest["score"] == 50 and rest["reasons"] == ["No food checks yet"]


def test_ngo_score_reasons_and_flags(world):
    ngo, rest = world["ngo"], world["rest"]
    finish(ngo, rest)
    finish(ngo, rest)
    finish(ngo, rest, how="release")
    finish(ngo, rest, how="release")
    finish(ngo, rest, how="dispose")
    row = trust_row(world, "NGO near@example.org")
    assert row["score"] == pytest.approx(beta_score(2, 3)) and row["confidence"] == "medium"
    assert "2 of 5 finished pickups delivered or stored" in row["reasons"]
    assert "2 pickups given back before collection" in row["reasons"]
    assert "1 collected pickup had to be disposed" in row["reasons"]
    assert any("given back in the last 7 days" in f for f in row["flags"])
    assert not any("Low score" in f for f in row["flags"])  # 42.9 is above the 40 threshold


def test_restaurant_score_from_food_checks(world):
    ngo, rest = world["ngo"], world["rest"]
    finish(ngo, rest)                       # full
    finish(ngo, rest, qty=30)               # partial -> half a failure
    for _ in range(3):
        finish(ngo, rest, how="reject")     # rejected
    rest_id = rest.get("/api/me").json()["id"]
    lid = post(rest).json()["id"]
    rest.post(f"/api/listings/{lid}/cancel")
    row = trust_row(world, "Green Bowl Kitchen")
    assert row["score"] == pytest.approx(beta_score(1, 3.5)) and row["events"] == 4.5
    assert "1 of 5 food checks matched the listing" in row["reasons"]
    assert any("cancelled by the restaurant (not counted" in r for r in row["reasons"])
    assert any("3 rejections in the last 7 days" in f for f in row["flags"])
    assert rest_id


def test_host_notes_and_access(world, core):
    ngo_id = world["ngo"].get("/api/me").json()["id"]
    r = world["host"].post(f"/api/trust/{ngo_id}/notes", json={"note": "Called them, bike repaired"})
    assert r.status_code == 201
    assert trust_row(world, "NGO near@example.org")["notes"][0]["note"] == "Called them, bike repaired"
    host_id = world["host"].get("/api/me").json()["id"]
    assert world["host"].post(f"/api/trust/{host_id}/notes", json={"note": "x y"}).status_code == 404
    assert core.get("/internal/trust", headers=svc("ngo")).status_code == 403
    assert core.get("/internal/trust", headers=svc("restaurant")).status_code == 403
    assert world["ngo"].get("/api/trust").status_code in (404, 405)


def test_map_panel_shows_trust(world):
    agent = world["host"].get("/api/map").json()["agents"][0]
    assert agent["trust"] == {"score": 50.0, "confidence": "low"}


# ---------------------------------------------------------------- Contract-Net negotiation

def test_negotiation_round_messages(world):
    off = world["make_ngo"]("off@example.org", available=False)
    lid = post(world["rest"]).json()["id"]
    msgs = [m for m in world["host"].get("/api/agent-system").json()["messages"] if m["listing_id"] == lid]
    topics = [m["topic"] for m in reversed(msgs)]
    assert topics[0] == "food_posted" and msgs[-1]["sender"].startswith("restaurant-agent:")
    assert topics.count("call_for_proposals") == 2          # both nearby NGOs were asked
    assert "propose" in topics and "refuse" in topics and "award" in topics and "negotiation_round" in topics
    refusal = next(m for m in msgs if m["topic"] == "refuse")
    assert refusal["payload"]["reason"] == "unavailable"
    rnd = next(m for m in msgs if m["topic"] == "negotiation_round")["payload"]
    assert len(rnd["bids"]) == 1 and len(rnd["refusals"]) == 1
    bid = rnd["bids"][0]
    assert bid["score"] == pytest.approx(bid["eta"] + bid["trust_penalty"] + bid["fairness_penalty"], abs=0.1)
    assert off.get("/api/feed").json()["listings"][0]["recommended"] is None


def test_trust_breaks_a_tie_in_travel_time(core_app, world):
    twin = world["make_ngo"]("twin@example.org")          # same base, no history
    ngo, rest = world["ngo"], world["rest"]
    for _ in range(3):
        finish(ngo, rest)                                   # main agent builds a good record
    with sessionmaker(core_app.state.engine)() as s:      # ...more than 24 h ago (no fairness penalty)
        for h in s.query(Handover).all():
            h.created_at = now() - timedelta(days=2)
        s.commit()
    post(rest, food_type="Dal")
    main_rec = ngo.get("/api/feed").json()["listings"][0]["recommended"]
    twin_rec = twin.get("/api/feed").json()["listings"][0]["recommended"]
    assert main_rec["rank"] == 1 and twin_rec["rank"] == 2
    assert "trust 80" in main_rec["reason"] and "trust 50" in twin_rec["reason"]


def test_low_trust_never_removes_an_agent(world):
    ngo, rest = world["ngo"], world["rest"]
    for _ in range(5):
        finish(ngo, rest, how="release")                   # poor record: 0 of 5
    post(rest, food_type="Upma")
    rec = ngo.get("/api/feed").json()["listings"][0]["recommended"]
    assert rec is not None and rec["rank"] == 1           # still recommended: trust is a nudge, not a veto
    row = trust_row(world, "NGO near@example.org")
    assert row["score"] == pytest.approx(14.3) and any("Low score" in f for f in row["flags"])


def test_expired_pickup_counts_against_the_agent(core_app, world):
    from core_service.models import FoodListing
    lid = post(world["rest"]).json()["id"]
    world["ngo"].post(f"/api/listings/{lid}/accept")
    with sessionmaker(core_app.state.engine)() as s:
        s.get(FoodListing, lid).expires_at = now() - timedelta(minutes=1)
        s.commit()
    row = trust_row(world, "NGO near@example.org")
    assert "1 accepted pickup expired before collection" in row["reasons"]
    assert row["score"] == pytest.approx(beta_score(0, 1))
