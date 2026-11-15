"""Phase 10: notifications, privacy rules, data retention, export and erasure."""
from datetime import timedelta

from sqlalchemy.orm import sessionmaker

from core_service.models import (Account, AgentLocation, AgentMessage, Event, LocationPing, Notification,
                                 utcnow)
from core_service.privacy import HousekeepingAgent, purge
from tests.conftest import svc
from tests.test_fulfilment import act, collect, recipient
from tests.test_listings import post, world  # noqa: F401  (world is a fixture)


def inbox(client):
    return client.get("/api/notifications").json()


# ---------------------------------------------------------------- notifications

def test_notifications_follow_the_food(world):
    rest, ngo = world["rest"], world["ngo"]
    assert "Your account is approved" in [n["title"] for n in inbox(rest)["items"]]
    lid = post(rest).json()["id"]
    alert = inbox(ngo)["items"][0]
    assert alert["title"] == "New food nearby: Veg biryani" and alert["listing_id"] == lid and not alert["read"]
    ngo.post(f"/api/listings/{lid}/accept")
    act(ngo, lid, "start")
    collect(ngo, lid)
    act(ngo, lid, "deliver", recipient_id=recipient(ngo), received_by="Sister Mary")
    titles = [n["title"] for n in inbox(rest)["items"]]
    assert titles[:4] == ["Your food reached people", "Your food was collected", "NGO near@example.org is on the way",
                          "NGO near@example.org will collect your food"]


def test_mark_read_and_unread_count(world):
    rest = world["rest"]
    post(rest)
    before = inbox(world["ngo"])
    assert before["unread"] >= 1
    one = before["items"][0]["id"]
    after = world["ngo"].post("/api/notifications/read", json={"ids": [one]}).json()
    assert after["unread"] == before["unread"] - 1
    assert world["ngo"].post("/api/notifications/read", json={"ids": None}).json()["unread"] == 0


def test_host_notified_of_applications(world):
    titles = [n["title"] for n in inbox(world["host"])["items"]]
    assert titles.count("New application to review") == 2  # the restaurant and the NGO in the world fixture


def test_inbox_is_private(world, core):
    ngo_id = world["ngo"].get("/api/me").json()["id"]
    assert core.get(f"/internal/accounts/{ngo_id}/notifications", headers=svc("restaurant")).status_code == 404
    assert core.get(f"/internal/accounts/{ngo_id}/notifications", headers=svc("ngo")).status_code == 200
    assert world["host"].get("/api/notifications").status_code == 200  # host has its own inbox
    from fastapi.testclient import TestClient
    anon = TestClient(world["rest"].app)
    assert anon.get("/api/notifications").status_code == 401


# ---------------------------------------------------------------- privacy rules

def test_phone_numbers_only_while_pickup_in_progress(world):
    rest, ngo = world["rest"], world["ngo"]
    lid = post(rest).json()["id"]
    feed = ngo.get("/api/feed").json()["listings"][0]
    assert "phone" not in feed["restaurant"]                                  # before accepting: no phone
    ngo.post(f"/api/listings/{lid}/accept")
    assert ngo.get("/api/pickups").json()[0]["restaurant"]["phone"]           # on the pickup: yes
    assert rest.get("/api/listings").json()[0]["assignment"]["phone"]
    act(ngo, lid, "start")
    collect(ngo, lid)
    act(ngo, lid, "deliver", recipient_id=recipient(ngo), received_by="Sister Mary")
    assert ngo.get("/api/pickups").json()[0]["restaurant"]["phone"] is None   # finished: hidden again
    assert rest.get("/api/listings").json()[0]["assignment"]["phone"] is None
    assert world["host"].get("/api/listings").json()[0]["assignment"]["phone"]  # the host still sees it


def test_failed_sign_in_keeps_only_the_email_domain(world):
    world["rest"].post("/api/login", json={"email": "someone@secret.org", "password": "wrong-password"})
    ev = [e for e in world["host"].get("/api/events").json() if e["type"] == "auth.failed"][0]
    assert ev["payload"] == {"email_domain": "secret.org"}


# ---------------------------------------------------------------- retention

def test_purge_deletes_only_old_records(core_app, world):
    ngo_id = world["ngo"].get("/api/me").json()["id"]
    lid = post(world["rest"]).json()["id"]
    old = utcnow() - timedelta(days=200)
    with sessionmaker(core_app.state.engine)() as db:
        db.add_all([LocationPing(agent_id=ngo_id, listing_id=lid, latitude=1, longitude=1, created_at=old),
                    LocationPing(agent_id=ngo_id, listing_id=lid, latitude=1, longitude=1),
                    AgentLocation(agent_id=ngo_id, latitude=1, longitude=1, updated_at=old),
                    Notification(account_id=ngo_id, kind="x", title="old read", read_at=old, created_at=old),
                    Notification(account_id=ngo_id, kind="x", title="old unread", created_at=old),
                    AgentMessage(sender="a", recipient="b", topic="t", created_at=old),
                    Event(type="auth.login", payload={}, created_at=old),
                    Event(type="listing.created", payload={}, created_at=old)])
        db.commit()
        result = purge(db)
        db.commit()
        assert result == {"location_pings": 1, "stale_locations": 1, "read_notifications": 1,
                          "unread_notifications": 1, "agent_messages": 1, "sign_in_events": 1}
        assert db.query(LocationPing).count() == 1                       # the recent one stays
        assert db.query(Event).filter(Event.type == "listing.created", Event.created_at < utcnow() - timedelta(days=100)).count() == 1  # audit kept


def test_housekeeping_agent_runs_hourly_and_host_can_purge(core_app, world):
    agent = core_app.state.runtime.get("housekeeping")
    assert isinstance(agent, HousekeepingAgent)
    with sessionmaker(core_app.state.engine)() as db:
        agent.tick(db)
        first = agent.last_purge
        agent.tick(db)
        assert agent.last_purge == first  # not again within the hour
    r = world["host"].post("/api/privacy/purge").json()
    assert set(r["deleted"]) == {"location_pings", "stale_locations", "read_notifications", "unread_notifications",
                                 "agent_messages", "sign_in_events"}
    policy = world["host"].get("/api/privacy").json()
    assert policy["retention_days"]["location_pings"] == 7 and policy["rules"]


# ---------------------------------------------------------------- export & erasure

def test_export_contains_the_account_but_no_password(world):
    ngo_id = world["ngo"].get("/api/me").json()["id"]
    r = world["host"].get(f"/api/privacy/accounts/{ngo_id}/export")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    data = r.json()
    assert data["account"]["email"] == "near@example.org" and len(data["documents"]) == 3
    assert "password_hash" not in r.text and "storage_key" not in r.text
    assert world["ngo"].get(f"/api/privacy/accounts/{ngo_id}/export").status_code == 404  # host portal only


def test_erase_account(core_app, world):
    host, ngo = world["host"], world["ngo"]
    ngo_id = ngo.get("/api/me").json()["id"]
    lid = post(world["rest"]).json()["id"]
    ngo.post(f"/api/listings/{lid}/accept")
    assert host.post(f"/api/privacy/accounts/{ngo_id}/erase", json={"confirm": "ERASE"}).status_code == 409  # busy
    act(ngo, lid, "release", reason="cannot come")
    assert host.post(f"/api/privacy/accounts/{ngo_id}/erase", json={"confirm": "yes"}).status_code == 422
    assert host.post(f"/api/privacy/accounts/{ngo_id}/erase", json={"confirm": "ERASE"}).json() == {"erased": True}
    with sessionmaker(core_app.state.engine)() as db:
        acc = db.get(Account, ngo_id)
        assert acc.email.endswith("@erased.invalid") and acc.phone is None and acc.display_name == "Erased account"
        assert db.query(Notification).filter_by(account_id=ngo_id).count() == 0
    assert ngo.get("/api/me").status_code == 401  # signed out everywhere
    assert ngo.post("/api/login", json={"email": "near@example.org", "password": "secret-pass-1"}).status_code == 401
    exported = host.get(f"/api/privacy/accounts/{ngo_id}/export").json()
    assert exported["documents"] == [] and exported["profile"]["representative_name"] == "Erased"
    assert host.post(f"/api/privacy/accounts/{ngo_id}/erase", json={"confirm": "ERASE"}).status_code == 409
    host_id = host.get("/api/me").json()["id"]
    assert host.post(f"/api/privacy/accounts/{host_id}/erase", json={"confirm": "ERASE"}).status_code == 422
