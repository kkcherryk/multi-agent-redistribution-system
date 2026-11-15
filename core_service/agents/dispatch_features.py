"""Features describing "offer this food to this agent first?" - shared by the real app and the simulator (Phase 9).

A dispatch policy gives each candidate a cost = weights . features; the lowest cost is offered the food first.
Facts about the food alone (time left, size) are the same for every candidate, so they only matter combined with
an agent feature - hence the two interaction features.

The hand-set weights reproduce the Phase 7 bid exactly:
    travel minutes + (100 - trust) / 10 + 2 x pickups in the last 24 h
"""
from __future__ import annotations

FEATURES = [
    ("travel", "Travel time (per 10 min)"),
    ("trust_gap", "Trust gap ((100 - trust) / 10)"),
    ("low_confidence", "Trust based on little history"),
    ("jobs_today", "Pickups in the last 24 h"),
    ("travel_if_urgent", "Travel time when < 90 min are left"),
    ("trust_gap_x_size", "Trust gap x food size (per 50 portions)"),
    ("backup_only", "Off duty, helping as backup collector"),
]
NAMES = [n for n, _ in FEATURES]
HAND_SET_WEIGHTS = [10.0, 1.0, 0.0, 2.0, 0.0, 0.0, 0.0]
URGENT_MINUTES = 90


def feature_vector(*, eta_minutes: float, trust: float, confidence: str, jobs_today: int, minutes_left: float,
                   quantity: float, backup_only: bool = False) -> list[float]:
    travel = eta_minutes / 10.0
    trust_gap = min(10.0, max(0.0, (100.0 - trust) / 10.0))
    return [
        travel,
        trust_gap,
        1.0 if confidence == "low" else 0.0,
        float(jobs_today),
        travel if minutes_left < URGENT_MINUTES else 0.0,
        trust_gap * (quantity / 50.0),
        1.0 if backup_only else 0.0,
    ]


def cost(weights: list[float], features: list[float]) -> float:
    return sum(w * x for w, x in zip(weights, features))
