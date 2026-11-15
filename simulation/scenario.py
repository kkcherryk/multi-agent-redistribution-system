"""Scenario generator: a reproducible pretend city of restaurants, NGO agents and food posts.

The same seed always produces the same city, so strategies can be compared on identical days.
Times are minutes since midnight of day 0.
"""
from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass, field

KM_PER_DEG_LAT = 111.0


@dataclass
class ScenarioConfig:
    seed: int = 1
    restaurants: int = 40
    ngos: int = 15
    posts_per_day: int = 80
    days: int = 5                    # consecutive days in one city (history and trust carry over)
    runs: int = 3                    # independent cities (seed, seed+1, ...) to average over
    center: tuple[float, float] = (12.9716, 77.5946)   # Bengaluru
    city_radius_km: float = 8.0
    availability: float = 0.45       # chance an agent is on duty in any 2-hour block (8:00-24:00)
    accept_prob: float = 0.75        # chance an agent says yes when offered food
    flaky_share: float = 0.25        # share of NGOs that often give pickups back
    flaky_giveback: float = 0.4      # give-back chance for a flaky NGO
    reliable_giveback: float = 0.05  # give-back chance for everyone else
    backup_share: float = 0.2        # share of NGOs opted in as backup collectors
    min_shelf_hours: float = 2.0
    max_shelf_hours: float = 8.0

    LIMITS = {"restaurants": (1, 200), "ngos": (1, 200), "posts_per_day": (1, 500), "days": (1, 30), "runs": (1, 20)}

    def validate(self) -> None:
        for name, (lo, hi) in self.LIMITS.items():
            v = getattr(self, name)
            if not lo <= v <= hi:
                raise ValueError(f"{name} must be between {lo} and {hi}")
        for name in ("availability", "accept_prob", "flaky_share", "flaky_giveback", "reliable_giveback",
                     "backup_share"):
            if not 0 <= getattr(self, name) <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        if not 0.5 <= self.min_shelf_hours <= self.max_shelf_hours <= 72:
            raise ValueError("shelf life must satisfy 0.5 <= min <= max <= 72 hours")
        if not 1 <= self.city_radius_km <= 50:
            raise ValueError("city_radius_km must be between 1 and 50")

    def as_dict(self) -> dict:
        d = asdict(self)
        d["center"] = list(self.center)
        return d


@dataclass
class SimRestaurant:
    id: str
    pos: tuple[float, float]


@dataclass
class SimNgo:
    id: str
    base: tuple[float, float]
    giveback_prob: float
    backup_volunteer: bool
    on_duty_blocks: set[int] = field(default_factory=set)  # absolute 2-hour block numbers

    def available(self, t: float) -> bool:
        return int(t // 120) in self.on_duty_blocks


@dataclass
class SimPost:
    id: str
    restaurant: SimRestaurant
    quantity: int
    posted_at: float
    expires_at: float


@dataclass
class Scenario:
    config: ScenarioConfig
    restaurants: list[SimRestaurant]
    ngos: list[SimNgo]
    posts: list[SimPost]


def _random_point(rng: random.Random, center: tuple[float, float], radius_km: float) -> tuple[float, float]:
    r = radius_km * math.sqrt(rng.random())
    a = rng.random() * 2 * math.pi
    dlat = r * math.cos(a) / KM_PER_DEG_LAT
    dlon = r * math.sin(a) / (KM_PER_DEG_LAT * math.cos(math.radians(center[0])))
    return (center[0] + dlat, center[1] + dlon)


def _post_minute(rng: random.Random) -> float:
    """Lunch and dinner rushes, plus a trickle through the day."""
    u = rng.random()
    if u < 0.45:
        return rng.uniform(12 * 60, 15 * 60)
    if u < 0.85:
        return rng.uniform(20 * 60, 23 * 60)
    return rng.uniform(8 * 60, 23 * 60)


def generate(config: ScenarioConfig) -> Scenario:
    config.validate()
    rng = random.Random(f"scenario:{config.seed}")
    restaurants = [SimRestaurant(f"R{i}", _random_point(rng, config.center, config.city_radius_km))
                   for i in range(config.restaurants)]
    ngos = []
    flaky_count = round(config.ngos * config.flaky_share)
    backup_count = round(config.ngos * config.backup_share)
    for i in range(config.ngos):
        blocks = set()
        for day in range(config.days):
            for block in range(4, 12):  # 8:00 to 24:00
                if rng.random() < config.availability:
                    blocks.add(day * 12 + block)
        ngos.append(SimNgo(f"N{i}", _random_point(rng, config.center, config.city_radius_km),
                           config.flaky_giveback if i < flaky_count else config.reliable_giveback,
                           backup_volunteer=i >= config.ngos - backup_count, on_duty_blocks=blocks))
    rng.shuffle(ngos)  # flaky and backup NGOs are spread across the city, not grouped by index
    posts = []
    for day in range(config.days):
        for k in range(config.posts_per_day):
            t = day * 1440 + _post_minute(rng)
            shelf = rng.uniform(config.min_shelf_hours, config.max_shelf_hours) * 60
            posts.append(SimPost(f"P{day}-{k}", rng.choice(restaurants), rng.randint(10, 60), t, t + shelf))
    posts.sort(key=lambda p: p.posted_at)
    return Scenario(config, restaurants, ngos, posts)
