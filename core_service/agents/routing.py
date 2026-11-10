"""Route estimation and stop ordering used by the logistics agent (and, later, the simulator).

No road-network service is used in the prototype: road distance is estimated as straight-line distance times a
detour factor, and travel time from an average city speed. Stop order uses nearest-neighbour + 2-opt.
"""
from __future__ import annotations

from dataclasses import dataclass

from shared.geo import haversine_km

ROAD_FACTOR = 1.35   # roads are ~35% longer than the straight line in a dense city
CITY_SPEED_KMH = 18.0
STOP_MINUTES = 5.0   # time spent at each stop (handover, checks)

Point = tuple[float, float]


def road_km(a: Point, b: Point) -> float:
    return haversine_km(a[0], a[1], b[0], b[1]) * ROAD_FACTOR


def travel_minutes(km: float) -> float:
    return km / CITY_SPEED_KMH * 60.0


@dataclass
class Leg:
    start: str
    end: str
    km: float
    minutes: float


@dataclass
class RoutePlan:
    order: list[str]           # stop names in visiting order (start first)
    legs: list[Leg]
    total_km: float
    total_minutes: float       # travel + stop time

    def as_dict(self) -> dict:
        return {"order": self.order, "total_km": round(self.total_km, 2), "total_minutes": round(self.total_minutes, 1),
                "legs": [{"from": l.start, "to": l.end, "km": round(l.km, 2), "minutes": round(l.minutes, 1)}
                         for l in self.legs]}


def _tour_length(points: list[Point], order: list[int]) -> float:
    return sum(road_km(points[order[i]], points[order[i + 1]]) for i in range(len(order) - 1))


def best_order(points: list[Point], *, fixed_end: bool = False) -> list[int]:
    """Visit order for an open path starting at points[0] (and ending at points[-1] if ``fixed_end``).

    Nearest-neighbour construction followed by 2-opt improvement; exact enough for the handful of stops an agent
    makes on one trip.
    """
    n = len(points)
    if n <= 2:
        return list(range(n))
    middle = list(range(1, n - 1 if fixed_end else n))
    order, current = [0], 0
    while middle:
        nxt = min(middle, key=lambda j: road_km(points[current], points[j]))
        order.append(nxt)
        middle.remove(nxt)
        current = nxt
    if fixed_end:
        order.append(n - 1)
    improved = True
    last_movable = len(order) - (1 if fixed_end else 0)
    while improved:
        improved = False
        for i in range(1, last_movable - 1):
            for k in range(i + 1, last_movable):
                candidate = order[:i] + order[i:k + 1][::-1] + order[k + 1:]
                if _tour_length(points, candidate) + 1e-9 < _tour_length(points, order):
                    order, improved = candidate, True
    return order


def plan_route(stops: list[tuple[str, Point]], *, reorder: bool = True, fixed_end: bool = False) -> RoutePlan:
    """Plan a trip through named stops; the first stop is the start position."""
    points = [p for _, p in stops]
    order = best_order(points, fixed_end=fixed_end) if reorder else list(range(len(stops)))
    legs = []
    for a, b in zip(order, order[1:]):
        km = road_km(points[a], points[b])
        legs.append(Leg(stops[a][0], stops[b][0], km, travel_minutes(km)))
    total_km = sum(l.km for l in legs)
    total_min = sum(l.minutes for l in legs) + STOP_MINUTES * max(0, len(legs) - 1)
    return RoutePlan([stops[i][0] for i in order], legs, total_km, total_min)


def directions_url(points: list[Point]) -> str:
    """Turn-by-turn directions link through all points (Google Maps 'dir' links need no API key)."""
    return "https://www.google.com/maps/dir/" + "/".join(f"{lat:.6f},{lon:.6f}" for lat, lon in points)
