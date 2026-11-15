"""Discrete-event simulator of a day (or several) of food redistribution.

Uses the same rules and numbers as the real app (imported, not copied): the 5 km alert radius, one pickup per
agent, travel estimates (road factor, city speed, time per stop), the backup agent's thresholds, and the trust and
bid formulas. People are modelled simply: an offered agent answers after 1-6 minutes, says yes with
``accept_prob``, and may later give the pickup back (unreliable NGOs more often).

Random draws for people depend only on (seed, post, agent, attempt), so every strategy faces the same behaviour
("common random numbers") and differences in results come from the strategy, not from luck.
"""
from __future__ import annotations

import heapq
import random
from dataclasses import dataclass, field

from core_service.agents import backup as backup_rules
from core_service.agents.routing import STOP_MINUTES, road_km, travel_minutes
from core_service.listings import DEFAULT_RADIUS_KM, MAX_ACTIVE_ASSIGNMENTS_PER_AGENT

from .scenario import Scenario, SimNgo, SimPost
from .strategies import Strategy

RETRY_MINUTES = 5          # when nobody can be offered the food, look again after this long
RESPONSE_MINUTES = (1, 6)  # how long an offered agent takes to answer
GIVEBACK_AFTER = (5, 20)   # minutes after accepting that a give-back happens

assert MAX_ACTIVE_ASSIGNMENTS_PER_AGENT == 1, "the simulator models one pickup per agent, like the app"


@dataclass
class AgentState:
    ngo: SimNgo
    busy_until: float = 0.0
    successes: int = 0
    failures: int = 0
    job_times: list[float] = field(default_factory=list)
    pickups: int = 0

    def jobs_in_last(self, now: float, minutes: float) -> int:
        return sum(1 for t in self.job_times if now - minutes <= t <= now)


@dataclass
class PostState:
    post: SimPost
    status: str = "open"              # open / offered / assigned / saved / wasted
    declined: set[str] = field(default_factory=set)
    attempts: int = 0
    accepted_at: float | None = None
    picked_up_at: float | None = None
    escalation: str | None = None     # None / widened / backup
    escalated_at: float | None = None
    rescued_by_escalation: bool = False
    givebacks: int = 0


class Simulation:
    def __init__(self, scenario: Scenario, strategy: Strategy) -> None:
        self.scenario = scenario
        self.strategy = strategy
        self.seed = scenario.config.seed
        self.cfg = scenario.config
        self.agents = {n.id: AgentState(n) for n in scenario.ngos}
        self.posts = {p.id: PostState(p) for p in scenario.posts}
        self.now = 0.0
        self.km = 0.0
        self._queue: list = []
        self._seq = 0
        # Phase 9: what happened after each offer, keyed by (post id, attempt) - used as learning feedback.
        self.offers: dict[tuple[str, int], dict] = {}

    # ---------------------------------------------------------------- event queue
    def schedule(self, t: float, kind: str, *data) -> None:
        self._seq += 1
        heapq.heappush(self._queue, (t, self._seq, kind, data))

    def draw(self, *key) -> random.Random:
        return random.Random(":".join(map(str, (self.seed,) + key)))

    # ---------------------------------------------------------------- rules
    def radius(self, ps: PostState) -> float:
        return backup_rules.EXTENDED_RADIUS_KM if ps.escalation else DEFAULT_RADIUS_KM

    def candidates(self, ps: PostState) -> list[AgentState]:
        out = []
        for a in self.agents.values():
            if a.ngo.id in ps.declined or a.busy_until > self.now:
                continue
            on_duty = a.ngo.available(self.now)
            via_backup = ps.escalation == "backup" and a.ngo.backup_volunteer
            if not (on_duty or via_backup):
                continue
            if road_km(a.ngo.base, ps.post.restaurant.pos) / 1.35 > self.radius(ps):
                continue
            out.append(a)
        return out

    def update_escalation(self, ps: PostState) -> None:
        """The backup agent's thresholds (Phase 6), applied only when the strategy uses escalation."""
        if not self.strategy.escalation:
            return
        age = self.now - ps.post.posted_at
        left = ps.post.expires_at - self.now
        if ps.escalation is None and (age >= backup_rules.NO_ACCEPT_MINUTES or left <= backup_rules.EXPIRY_RISK_MINUTES):
            ps.escalation, ps.escalated_at = "widened", self.now
        elif ps.escalation == "widened" and (self.now - ps.escalated_at >= backup_rules.WIDEN_WAIT_MINUTES
                                             or left <= backup_rules.EXPIRY_RISK_MINUTES - 15):
            ps.escalation, ps.escalated_at = "backup", self.now

    # ---------------------------------------------------------------- event handlers
    def on_dispatch(self, ps: PostState) -> None:
        if ps.status != "open":
            return
        if self.now >= ps.post.expires_at:
            ps.status = "wasted"
            return
        self.update_escalation(ps)
        ordered = self.strategy.order(self, ps.post, self.candidates(ps))
        if not ordered:
            self.schedule(min(self.now + RETRY_MINUTES, ps.post.expires_at), "dispatch", ps.post.id)
            return
        agent = ordered[0]
        ps.status, ps.attempts = "offered", ps.attempts + 1
        self.offers[(ps.post.id, ps.attempts)] = {"agent": agent.ngo.id, "outcome": "pending", "km": 0.0,
                                                  "quantity": ps.post.quantity}
        agent.busy_until = float("inf")  # reserved while deciding, so it is not offered two things at once
        rng = self.draw(ps.post.id, agent.ngo.id, ps.attempts)
        accepts = rng.random() < self.cfg.accept_prob
        self.schedule(self.now + rng.uniform(*RESPONSE_MINUTES), "response", ps.post.id, agent.ngo.id, accepts)

    def on_response(self, ps: PostState, agent: AgentState, accepts: bool) -> None:
        agent.busy_until = self.now
        offer = self.offers[(ps.post.id, ps.attempts)]
        if not accepts or self.now >= ps.post.expires_at:
            offer["outcome"] = "declined"
            ps.declined.add(agent.ngo.id)
            ps.status = "open"
            self.on_dispatch(ps)
            return
        ps.status, ps.accepted_at = "assigned", ps.accepted_at or self.now
        rng = self.draw(ps.post.id, agent.ngo.id, ps.attempts, "giveback")
        if rng.random() < agent.ngo.giveback_prob:
            self.schedule(self.now + rng.uniform(*GIVEBACK_AFTER), "giveback", ps.post.id, agent.ngo.id)
            agent.busy_until = float("inf")
            offer["outcome"] = "giveback"
            return
        # "Rescued" = this agent could not have taken the food under the normal rules (off duty or beyond 5 km).
        normally_eligible = (agent.ngo.available(self.now) and
                             road_km(agent.ngo.base, ps.post.restaurant.pos) / 1.35 <= DEFAULT_RADIUS_KM)
        ps.rescued_by_escalation = not normally_eligible
        to_restaurant = road_km(agent.ngo.base, ps.post.restaurant.pos)
        arrive = self.now + travel_minutes(to_restaurant)
        self.km += to_restaurant
        if arrive > ps.post.expires_at:          # too late: the food expired on the way
            offer["outcome"], offer["km"] = "late", to_restaurant
            ps.status = "wasted"
            agent.failures += 1
            agent.busy_until = arrive
            return
        back = road_km(ps.post.restaurant.pos, agent.ngo.base)
        self.km += back
        offer["outcome"], offer["km"] = "saved", to_restaurant + back
        ps.status, ps.picked_up_at = "saved", arrive
        agent.successes += 1
        agent.pickups += 1
        agent.job_times.append(self.now)
        agent.busy_until = arrive + STOP_MINUTES + travel_minutes(back) + STOP_MINUTES

    def on_giveback(self, ps: PostState, agent: AgentState) -> None:
        agent.busy_until = self.now
        agent.failures += 1
        ps.givebacks += 1
        ps.declined.add(agent.ngo.id)
        ps.status = "open"
        self.on_dispatch(ps)

    # ---------------------------------------------------------------- run
    def run(self) -> "Simulation":
        for p in self.scenario.posts:
            self.schedule(p.posted_at, "dispatch", p.id)
            self.schedule(p.expires_at, "expire", p.id)
        while self._queue:
            self.now, _, kind, data = heapq.heappop(self._queue)
            ps = self.posts[data[0]]
            if kind == "dispatch":
                self.on_dispatch(ps)
            elif kind == "response":
                self.on_response(ps, self.agents[data[1]], data[2])
            elif kind == "giveback":
                self.on_giveback(ps, self.agents[data[1]])
            elif kind == "expire" and ps.status == "open":
                ps.status = "wasted"
        return self
