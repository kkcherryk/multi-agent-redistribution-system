"""Phase 8: scenario generator, simulation engine, strategies, metrics and the host simulation API."""
from dataclasses import replace

import pytest

from simulation.engine import Simulation
from simulation.evaluate import METRICS, compare, gini, metrics, to_csv
from simulation.scenario import ScenarioConfig, generate
from simulation.strategies import BUILT_IN, AgentsStrategy, NearestStrategy, RandomStrategy
from tests.conftest import HOST_EMAIL, HOST_PASSWORD, svc

SMALL = ScenarioConfig(seed=7, restaurants=10, ngos=6, posts_per_day=15, days=2, runs=2)


# ---------------------------------------------------------------- scenario generator

def test_same_seed_same_city_and_posts():
    a, b = generate(SMALL), generate(SMALL)
    assert [(p.id, p.quantity, round(p.posted_at, 6)) for p in a.posts] == \
           [(p.id, p.quantity, round(p.posted_at, 6)) for p in b.posts]
    c = generate(replace(SMALL, seed=8))
    assert [p.posted_at for p in a.posts] != [p.posted_at for p in c.posts]


def test_scenario_shape():
    s = generate(SMALL)
    assert len(s.restaurants) == 10 and len(s.ngos) == 6 and len(s.posts) == 30
    assert all(p.expires_at - p.posted_at >= 2 * 60 for p in s.posts)
    assert sum(n.backup_volunteer for n in s.ngos) == round(6 * SMALL.backup_share)


@pytest.mark.parametrize("bad", [{"ngos": 0}, {"days": 31}, {"availability": 1.5}, {"runs": 0},
                                 {"min_shelf_hours": 5, "max_shelf_hours": 2}, {"city_radius_km": 0}])
def test_config_validation(bad):
    with pytest.raises(ValueError):
        replace(SMALL, **bad).validate()


# ---------------------------------------------------------------- engine and metrics

def test_gini():
    assert gini([3, 3, 3]) == 0 and gini([]) == 0 and gini([0, 0]) == 0
    assert gini([0, 0, 0, 9]) == pytest.approx(0.75)


@pytest.mark.parametrize("strategy", list(BUILT_IN.values()), ids=list(BUILT_IN))
def test_every_post_ends_saved_or_wasted(strategy):
    sim = Simulation(generate(SMALL), strategy).run()
    assert {p.status for p in sim.posts.values()} <= {"saved", "wasted"}
    m = metrics(sim)
    assert m["portions_saved"] + m["portions_wasted"] == sum(p.quantity for p in sim.scenario.posts)
    assert sum(a.pickups for a in sim.agents.values()) == sum(1 for p in sim.posts.values() if p.status == "saved")
    for p in sim.posts.values():
        if p.status == "saved":
            assert p.post.posted_at <= p.accepted_at <= p.picked_up_at <= p.post.expires_at


def test_simulation_is_deterministic():
    one = metrics(Simulation(generate(SMALL), AgentsStrategy()).run())
    two = metrics(Simulation(generate(SMALL), AgentsStrategy()).run())
    assert one == two


def test_far_ngos_never_collect_without_escalation():
    cfg = replace(SMALL, ngos=1, availability=1.0, backup_share=0.0, city_radius_km=1.0)
    s = generate(cfg)
    s.ngos[0].base = (s.config.center[0] + 0.2, s.config.center[1])  # ~22 km away from every restaurant
    for strategy in (RandomStrategy(), NearestStrategy(), AgentsStrategy()):
        sim = Simulation(s, strategy).run()
        assert metrics(sim)["portions_saved"] == 0


def test_backup_collectors_rescue_food_only_with_escalation():
    cfg = replace(SMALL, availability=0.0, backup_share=1.0, accept_prob=1.0, flaky_share=0.0, reliable_giveback=0.0)
    nearest = metrics(Simulation(generate(cfg), NearestStrategy()).run())
    agents = metrics(Simulation(generate(cfg), AgentsStrategy()).run())
    assert nearest["portions_saved"] == 0                     # nobody on duty, no backup in the baseline
    assert agents["portions_saved"] > 0 and agents["rescued_by_escalation"] > 0


def test_trust_penalty_moves_unreliable_agent_down():
    sim = Simulation(generate(SMALL), AgentsStrategy())
    post = sim.scenario.posts[0]
    good, bad = list(sim.agents.values())[:2]
    bad.ngo.base = good.ngo.base                                # same distance
    good.successes, bad.failures = 5, 5
    strategy = AgentsStrategy()
    assert strategy.order(sim, post, [bad, good]) == [good, bad]
    assert AgentsStrategy(use_trust=False).bid(sim, post, bad) == pytest.approx(
        AgentsStrategy(use_trust=False).bid(sim, post, good))


# ---------------------------------------------------------------- comparison and report

def test_compare_summary_and_csv():
    result = compare(SMALL)
    assert set(result["summary"]) == set(BUILT_IN)
    assert len(result["runs"]) == SMALL.runs * len(BUILT_IN)
    for s in result["summary"].values():
        assert set(s["metrics"]) == set(METRICS)
        assert 0 <= s["metrics"]["food_saved_pct"]["mean"] <= 100
    lines = to_csv(result).strip().splitlines()
    assert lines[0].startswith("run,seed,strategy,food_saved_pct") and len(lines) == 1 + len(result["runs"])


# ---------------------------------------------------------------- host API

def test_simulation_api_host_only_and_validated(portals, core):
    admin = portals["admin"]
    admin.post("/api/login", json={"email": HOST_EMAIL, "password": HOST_PASSWORD})
    small = {"restaurants": 8, "ngos": 5, "posts_per_day": 10, "days": 1, "runs": 1, "seed": 3}
    r = admin.post("/api/simulation/run", json=small)
    assert r.status_code == 200, r.text
    assert set(r.json()["summary"]) == set(BUILT_IN) and r.json()["config"]["seed"] == 3
    assert admin.post("/api/simulation/run", json={**small, "teleport": 1}).status_code == 422
    assert admin.post("/api/simulation/run", json={**small, "availability": 3}).status_code == 422
    assert admin.post("/api/simulation/run", json={**small, "runs": 20, "days": 30, "posts_per_day": 100}).status_code == 422
    assert core.post("/internal/simulation/run", json=small, headers=svc("ngo")).status_code == 403
    assert portals["restaurant"].post("/api/simulation/run", json=small).status_code in (401, 404, 405)
