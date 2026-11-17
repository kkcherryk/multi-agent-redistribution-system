"""Public impact totals: real numbers for the landing page, with nothing personal in them."""
from fastapi.testclient import TestClient

from landing.app import create_landing_app
from scripts.demo import run_story
from tests.conftest import HOST_EMAIL, HOST_PASSWORD

KEYS = {"meals_delivered", "kg_delivered", "donations_completed", "restaurants", "ngos", "now_waiting", "now_on_the_way"}
URLS = {"admin": "http://h:8001", "restaurant": "http://h:8002", "ngo": "http://h:8003"}


def test_impact_starts_at_zero_and_needs_no_service_key(core):
    r = core.get("/public/impact")
    assert r.status_code == 200
    assert r.json() == {k: 0 for k in KEYS}


def test_impact_counts_a_completed_donation(core, portals):
    clients = [TestClient(portals[k].app) for k in ("admin", "restaurant", "ngo")]
    run_story(*clients, host_email=HOST_EMAIL, host_password=HOST_PASSWORD, tag="imp", say=lambda _: None)
    d = core.get("/public/impact").json()
    assert set(d) == KEYS  # totals only: no names, emails, phones, addresses or locations
    assert d["donations_completed"] >= 1 and d["meals_delivered"] > 0
    assert d["restaurants"] >= 1 and d["ngos"] >= 1


def test_landing_serves_and_caches_impact():
    calls = []

    def fetch():
        calls.append(1)
        return {k: 3 for k in KEYS}

    client = TestClient(create_landing_app(URLS, fetch_impact=fetch, cache_seconds=60))
    assert client.get("/api/impact").json()["meals_delivered"] == 3
    client.get("/api/impact")
    assert len(calls) == 1  # second page view comes from the cache


def test_landing_impact_when_core_is_down():
    def fetch():
        raise ConnectionError("core down")

    assert TestClient(create_landing_app(URLS, fetch_impact=fetch)).get("/api/impact").status_code == 503
    assert TestClient(create_landing_app(URLS)).get("/api/impact").status_code == 503
