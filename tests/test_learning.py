"""Phase 9: learned dispatch - shared features, policy-gradient training, evaluation, app switch with fallback."""
import json

import pytest

from core_service.agents.dispatch_features import HAND_SET_WEIGHTS, NAMES, cost, feature_vector
from simulation.engine import Simulation
from simulation.evaluate import metrics
from simulation.learning import (LearnedStrategy, PracticeStrategy, load_policy, offer_reward, train,
                                 train_and_save, verdict)
from simulation.scenario import ScenarioConfig, generate
from simulation.strategies import AgentsStrategy
from tests.conftest import svc
from tests.test_listings import post, world  # noqa: F401  (world is a fixture)

SMALL = ScenarioConfig(seed=5, restaurants=10, ngos=8, posts_per_day=20, days=2, runs=1)


# ---------------------------------------------------------------- features and policy

def test_hand_set_weights_reproduce_the_phase7_bid():
    x = feature_vector(eta_minutes=6, trust=82, confidence="medium", jobs_today=1, minutes_left=200, quantity=40)
    assert cost(HAND_SET_WEIGHTS, x) == pytest.approx(6 + 1.8 + 2)
    urgent = feature_vector(eta_minutes=6, trust=82, confidence="low", jobs_today=0, minutes_left=30, quantity=40)
    assert urgent[NAMES.index("travel_if_urgent")] == pytest.approx(0.6) and urgent[NAMES.index("low_confidence")] == 1


def test_learned_strategy_with_hand_set_weights_matches_our_agents():
    a = metrics(Simulation(generate(SMALL), AgentsStrategy()).run())
    b = metrics(Simulation(generate(SMALL), LearnedStrategy(HAND_SET_WEIGHTS)).run())
    assert a == b


def test_offer_rewards():
    assert offer_reward({"outcome": "saved", "quantity": 40, "km": 5}) == pytest.approx(4 - 1)
    assert offer_reward({"outcome": "giveback", "quantity": 40, "km": 0}) == -3
    assert offer_reward({"outcome": "late", "quantity": 40, "km": 3}) == -4
    assert offer_reward({"outcome": "declined", "quantity": 40, "km": 0}) is None


def test_practice_strategy_records_decisions():
    import random
    strat = PracticeStrategy(HAND_SET_WEIGHTS, random.Random(1))
    sim = Simulation(generate(SMALL), strat).run()
    assert strat.decisions and set(strat.decisions) <= set(sim.offers)
    feats, probs, chosen = next(iter(strat.decisions.values()))
    assert sum(probs) == pytest.approx(1) and 0 <= chosen < len(feats)


# ---------------------------------------------------------------- training

def test_training_is_deterministic_and_learns_to_value_trust():
    one = train(episodes=60, config=ScenarioConfig(days=2, runs=1))
    two = train(episodes=60, config=ScenarioConfig(days=2, runs=1))
    assert one["weights"] == two["weights"] and len(one["curve"]) == 60
    trust_w = one["weights"][NAMES.index("trust_gap")]
    assert trust_w > HAND_SET_WEIGHTS[NAMES.index("trust_gap")]  # learns that unreliable agents cost food


def test_verdict_rules():
    def m(food, give, km):
        return {"food_saved_pct": {"mean": food}, "giveback_rate_pct": {"mean": give}, "km_per_100_portions": {"mean": km}}
    assert verdict(m(90, 15, 40), m(90, 14, 41))["recommended"] is True
    assert verdict(m(90, 15, 40), m(89, 10, 30))["recommended"] is False   # less food saved
    assert verdict(m(90, 15, 40), m(91, 16, 30))["recommended"] is False   # more give-backs


def test_train_and_save_and_load(tmp_path):
    path = tmp_path / "policy.json"
    pol = train_and_save(episodes=20, eval_config=ScenarioConfig(restaurants=10, ngos=8, posts_per_day=15, days=1, runs=1),
                         path=path)
    assert load_policy(path)["weights"] == pol["weights"]
    assert {"summary", "details", "recommended"} <= set(pol["test"])
    path.write_text("{not json", encoding="utf-8")
    assert load_policy(path) is None
    path.write_text(json.dumps({**pol, "weights": [1, 2]}), encoding="utf-8")
    assert load_policy(path) is None                              # wrong shape: ignored, app falls back
    assert load_policy(tmp_path / "missing.json") is None


# ---------------------------------------------------------------- the real app: host switch and fallback

def write_policy(core_app, tmp_path, weights):
    from core_service.agents.logistics import LogisticsAgent
    path = core_app.state.runtime.get("logistics").policy_path
    path.write_text(json.dumps({"features": NAMES, "weights": weights}), encoding="utf-8")
    return path


def test_switch_requires_trained_policy_and_host(world, core):
    host = world["host"]
    s = host.get("/api/learned-dispatch").json()
    assert s == {"enabled": False, "switched_on": False, "trained": False, "policy": None}
    assert host.put("/api/learned-dispatch", json={"enabled": True}).status_code == 409
    assert core.get("/internal/learned-dispatch", headers=svc("ngo")).status_code == 403
    assert core.put("/internal/learned-dispatch", json={"enabled": False}, headers=svc("restaurant")).status_code == 403


def test_learned_weights_used_only_when_switched_on(core_app, world, tmp_path):
    far = world["make_ngo"]("far@example.org", location={"latitude": 12.9600, "longitude": 77.6200})
    near = world["ngo"]
    # A deliberately odd policy (prefers the farther agent) so the switch is easy to see.
    path = write_policy(core_app, tmp_path, [-10.0, 0, 0, 0, 0, 0, 0])
    post(world["rest"])
    assert near.get("/api/feed").json()["listings"][0]["recommended"]["rank"] == 1   # off: normal rule
    on = world["host"].put("/api/learned-dispatch", json={"enabled": True}).json()
    assert on["enabled"] and on["trained"]
    post(world["rest"], food_type="Dal")
    dal = [x for x in far.get("/api/feed").json()["listings"] if x["food_type"] == "Dal"][0]["recommended"]
    assert dal["rank"] == 1 and dal["reason"].startswith("learned dispatch")
    # Fallback: if the policy file disappears, ranking silently returns to the normal rule.
    path.unlink()
    post(world["rest"], food_type="Idli")
    idli = [x for x in near.get("/api/feed").json()["listings"] if x["food_type"] == "Idli"][0]["recommended"]
    assert idli["rank"] == 1 and not idli["reason"].startswith("learned")
    assert world["host"].get("/api/learned-dispatch").json()["enabled"] is False


def test_simulation_tab_includes_learned_strategy_once_trained(core_app, world, tmp_path):
    small = {"restaurants": 8, "ngos": 5, "posts_per_day": 10, "days": 1, "runs": 1}
    assert "learned" not in world["host"].post("/api/simulation/run", json=small).json()["summary"]
    write_policy(core_app, tmp_path, HAND_SET_WEIGHTS)
    assert "learned" in world["host"].post("/api/simulation/run", json=small).json()["summary"]
