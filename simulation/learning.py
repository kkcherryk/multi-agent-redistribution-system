"""Phase 9: a learning dispatcher trained by reinforcement learning (policy gradient / REINFORCE) in the simulator.

Policy:  cost(agent) = weights . features(agent, food); the cheapest agent is offered the food first.
Training: the policy samples who to offer food to (softmax over -cost / temperature), the simulator plays the day
out, and every offer is scored:
    saved          +portions/10 - KM_COST x km travelled
    given back     -GIVEBACK_PENALTY
    arrived late   -portions/10 (the food expired on the way)
    declined       ignored (in this model whether someone says yes does not depend on who is asked)
Weights move in the direction that made better-than-average offers more likely. Training starts from the hand-set
Phase 7 weights, uses practice cities only (seeds from TRAIN_SEED_BASE), and the result is tested on different,
unseen cities with the Phase 8 report.
"""
from __future__ import annotations

import json
import math
import random
import statistics
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from core_service.agents.dispatch_features import FEATURES, HAND_SET_WEIGHTS, NAMES, cost, feature_vector
from core_service.agents.routing import road_km, travel_minutes
from core_service.trust import beta_score, confidence
from shared.config import DATA_DIR

from .engine import Simulation
from .evaluate import compare
from .scenario import ScenarioConfig, generate
from .strategies import AgentsStrategy, Strategy

POLICY_FILE = DATA_DIR / "learned_policy.json"
TRAIN_SEED_BASE = 10_000      # practice cities; the evaluation report uses small seeds (1, 2, 3, ...)
KM_COST = 0.2
GIVEBACK_PENALTY = 3.0
TEMPERATURE = 3.0             # exploration: higher = more random choices while practising


def sim_features(sim: Simulation, post, a) -> list[float]:
    eta = travel_minutes(road_km(a.ngo.base, post.restaurant.pos))
    return feature_vector(eta_minutes=eta, trust=beta_score(a.successes, a.failures),
                          confidence=confidence(a.successes + a.failures), jobs_today=a.jobs_in_last(sim.now, 1440),
                          minutes_left=post.expires_at - sim.now, quantity=post.quantity,
                          backup_only=a.ngo.backup_volunteer and not a.ngo.available(sim.now))


class LearnedStrategy(Strategy):
    """Uses fixed (learned) weights; deterministic, like the app. Escalation rules stay on."""

    name = "learned"
    label = "Learned dispatch"
    escalation = True

    def __init__(self, weights: list[float]) -> None:
        self.weights = list(weights)

    def order(self, sim, post, candidates):
        return sorted(candidates, key=lambda a: cost(self.weights, sim_features(sim, post, a)))


class PracticeStrategy(LearnedStrategy):
    """Samples one agent from the softmax policy and remembers the decision for the learning update."""

    def __init__(self, weights: list[float], rng: random.Random, temperature: float = TEMPERATURE) -> None:
        super().__init__(weights)
        self.rng, self.temperature = rng, temperature
        self.decisions: dict[tuple[str, int], tuple[list[list[float]], list[float], int]] = {}

    def order(self, sim, post, candidates):
        if not candidates:
            return []
        feats = [sim_features(sim, post, a) for a in candidates]
        logits = [-cost(self.weights, x) / self.temperature for x in feats]
        top = max(logits)
        exps = [math.exp(l - top) for l in logits]
        z = sum(exps)
        probs = [e / z for e in exps]
        chosen = self.rng.choices(range(len(candidates)), weights=probs)[0]
        attempt = sim.posts[post.id].attempts + 1  # the engine increments attempts right after ordering
        self.decisions[(post.id, attempt)] = (feats, probs, chosen)
        return [candidates[chosen]] + [c for i, c in enumerate(candidates) if i != chosen]


def offer_reward(offer: dict) -> float | None:
    o = offer["outcome"]
    if o == "saved":
        return offer["quantity"] / 10.0 - KM_COST * offer["km"]
    if o == "giveback":
        return -GIVEBACK_PENALTY
    if o == "late":
        return -offer["quantity"] / 10.0
    return None  # declined / pending: no learning signal


def train(*, episodes: int = 300, config: ScenarioConfig | None = None, learning_rate: float = 0.5,
          seed: int = 0, start: list[float] | None = None, progress=None) -> dict:
    """REINFORCE with a per-episode normalised advantage. Returns weights and the training curve."""
    base = config or ScenarioConfig(days=3, runs=1)
    weights = list(start or HAND_SET_WEIGHTS)
    rng = random.Random(f"train:{seed}")
    curve = []
    for ep in range(episodes):
        cfg = replace(base, seed=TRAIN_SEED_BASE + seed * 100_000 + ep, runs=1)
        strategy = PracticeStrategy(weights, rng)
        sim = Simulation(generate(cfg), strategy).run()
        samples = []
        for key, (feats, probs, chosen) in strategy.decisions.items():
            r = offer_reward(sim.offers.get(key, {"outcome": "pending"}))
            if r is not None and len(feats) > 1:  # with one candidate there was no choice to learn from
                samples.append((r, feats, probs, chosen))
        if len(samples) >= 2:
            rewards = [s[0] for s in samples]
            mean, sd = statistics.mean(rewards), statistics.pstdev(rewards) or 1.0
            grad = [0.0] * len(weights)
            for r, feats, probs, chosen in samples:
                adv = (r - mean) / sd
                expected = [sum(p * x[j] for p, x in zip(probs, feats)) for j in range(len(weights))]
                # d/dw log softmax(-w.x / T) at the chosen agent = -(x_chosen - E[x]) / T
                for j in range(len(weights)):
                    grad[j] += adv * -(feats[chosen][j] - expected[j]) / strategy.temperature
            step = learning_rate / math.sqrt(1 + ep / 25)
            weights = [w + step * g / len(samples) for w, g in zip(weights, grad)]
        saved = sum(p.post.quantity for p in sim.posts.values() if p.status == "saved")
        total = sum(p.post.quantity for p in sim.posts.values())
        curve.append(round(100 * saved / total, 2) if total else 0.0)
        if progress:
            progress(ep + 1, episodes)
    return {"weights": [round(w, 4) for w in weights], "curve": curve}


def evaluate(weights: list[float], config: ScenarioConfig | None = None) -> dict:
    """Phase 8 report on unseen cities: hand-set agents vs learned dispatch."""
    cfg = config or ScenarioConfig()
    return compare(cfg, [AgentsStrategy(), LearnedStrategy(weights)])


def explain(weights: list[float]) -> list[dict]:
    out = []
    for (name, label), w, h in zip(FEATURES, weights, HAND_SET_WEIGHTS):
        if h:
            change = f"{w / h:.2f}x the hand-set weight"
        else:
            change = "new factor (hand-set 0)" if abs(w) >= 0.05 else "still about 0"
        out.append({"feature": name, "label": label, "learned": round(w, 3), "hand_set": h, "change": change})
    return out


def train_and_save(*, episodes: int = 300, eval_config: ScenarioConfig | None = None, path: Path = POLICY_FILE,
                   progress=None) -> dict:
    trained = train(episodes=episodes, progress=progress)
    report = evaluate(trained["weights"], eval_config)
    agents = report["summary"]["agents"]["metrics"]
    learned = report["summary"]["learned"]["metrics"]
    policy = {
        "version": 1, "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "features": NAMES, "weights": trained["weights"], "hand_set_weights": HAND_SET_WEIGHTS,
        "episodes": episodes, "training_curve": trained["curve"],
        "reward": {"saved": "portions/10 - 0.2 x km", "given_back": -GIVEBACK_PENALTY, "late": "-portions/10"},
        "explanation": explain(trained["weights"]),
        "test": {"config": report["config"], "agents": agents, "learned": learned, **verdict(agents, learned)},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(policy, indent=2), encoding="utf-8")
    return policy


def verdict(agents: dict, learned: dict) -> dict:
    """Plain-language comparison on unseen cities. Recommended only if it saves at least as much food and does
    not make give-backs worse."""
    def d(key):
        a, l = agents[key]["mean"], learned[key]["mean"]
        return None if a is None or l is None else round(l - a, 2)

    food, give, km = d("food_saved_pct"), d("giveback_rate_pct"), d("km_per_100_portions")
    lines = [f"Food saved: {learned['food_saved_pct']['mean']}% vs {agents['food_saved_pct']['mean']}% "
             f"({food:+} points)",
             f"Give-back rate: {learned['giveback_rate_pct']['mean']}% vs {agents['giveback_rate_pct']['mean']}% "
             f"({give:+} points)",
             f"Distance: {learned['km_per_100_portions']['mean']} vs {agents['km_per_100_portions']['mean']} "
             f"km per 100 portions ({km:+})"]
    recommended = food is not None and give is not None and food >= -0.2 and give <= 0
    summary = ("Recommended: on unseen cities it saved as much food (within 0.2 points) without more give-backs."
               if recommended else
               "Not recommended: on unseen cities it saved less food or caused more give-backs; keep it off.")
    return {"recommended": recommended, "summary": summary, "details": lines}


def load_policy(path: Path | None = None) -> dict | None:
    """Return the saved policy, or None if missing or unusable (the app then falls back to the hand-set rule)."""
    path = path or POLICY_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    w = data.get("weights")
    if data.get("features") != NAMES or not isinstance(w, list) or len(w) != len(NAMES) \
            or not all(isinstance(x, (int, float)) and math.isfinite(x) for x in w):
        return None
    return data
