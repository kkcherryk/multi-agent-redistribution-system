"""Collector-choice strategies compared by the simulator. Phase 9's learning agent plugs in through ``Strategy``.

A strategy orders the candidate agents for a listing (who is offered the food first) and says whether the app's
escalation rules (wider alerts, backup collectors) are switched on.
"""
from __future__ import annotations

import random

from core_service.agents.logistics import FAIRNESS_MIN_PER_RECENT_JOB
from core_service.agents.routing import road_km, travel_minutes
from core_service.trust import beta_score, bid_trust_penalty


class Strategy:
    name = "strategy"
    label = "Strategy"
    escalation = False   # wider alerts + backup collectors (the Phase 6 backup agent)

    def order(self, sim, post, candidates: list) -> list:
        raise NotImplementedError


class RandomStrategy(Strategy):
    name = "random"
    label = "Random"

    def order(self, sim, post, candidates):
        rng = random.Random(f"{sim.seed}:random-order:{post.id}:{sim.now:.0f}")
        out = list(candidates)
        rng.shuffle(out)
        return out


class NearestStrategy(Strategy):
    name = "nearest"
    label = "Nearest agent"

    def order(self, sim, post, candidates):
        return sorted(candidates, key=lambda a: road_km(a.ngo.base, post.restaurant.pos))


class AgentsStrategy(Strategy):
    """The app's Phase 6-7 behaviour: Contract-Net bids (travel + trust penalty + fairness) and escalation."""

    name = "agents"
    label = "Our agents"
    escalation = True

    def __init__(self, use_trust: bool = True) -> None:
        self.use_trust = use_trust
        if not use_trust:
            self.name, self.label = "agents_no_trust", "Our agents (no trust)"

    def bid(self, sim, post, a) -> float:
        eta = travel_minutes(road_km(a.ngo.base, post.restaurant.pos))
        trust_pen = bid_trust_penalty(beta_score(a.successes, a.failures)) if self.use_trust else 0.0
        fairness = FAIRNESS_MIN_PER_RECENT_JOB * a.jobs_in_last(sim.now, 1440)
        return eta + trust_pen + fairness

    def order(self, sim, post, candidates):
        return sorted(candidates, key=lambda a: self.bid(sim, post, a))


BUILT_IN = {s.name: s for s in (RandomStrategy(), NearestStrategy(), AgentsStrategy(), AgentsStrategy(use_trust=False))}
