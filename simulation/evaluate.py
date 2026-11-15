"""Metrics and multi-day comparison of strategies (Phase 8 evaluation).

Each run is a separate city (seed, seed+1, ...) simulated for several consecutive days, so agent history and
trust carry over within a run; results are averaged over runs with a spread, and every strategy sees exactly the
same cities, food posts and human behaviour.
"""
from __future__ import annotations

import csv
import io
import statistics
from dataclasses import replace

from .engine import Simulation
from .scenario import ScenarioConfig, generate
from .strategies import BUILT_IN, Strategy

METRICS = {
    # key: (label, unit, higher_is_better)
    "food_saved_pct": ("Food saved", "% of portions", True),
    "portions_saved": ("Portions saved", "portions", True),
    "portions_wasted": ("Portions wasted", "portions", False),
    "minutes_to_accept": ("Time to accept", "min (average)", False),
    "minutes_to_pickup": ("Time to pickup", "min from posting (average)", False),
    "km_per_100_portions": ("Distance", "km per 100 portions saved", False),
    "fairness_gini": ("Work spread (Gini)", "0 = perfectly even", False),
    "givebacks": ("Pickups given back", "count", False),
    "giveback_rate_pct": ("Give-back rate", "% of accepted pickups", False),
    "rescued_by_escalation": ("Rescued by escalation", "pickups", True),
}


def gini(values: list[float]) -> float:
    """0 = every NGO did the same number of pickups, 1 = one NGO did all of them."""
    vals = sorted(values)
    n, total = len(vals), sum(vals)
    if n == 0 or total == 0:
        return 0.0
    cum = sum((i + 1) * v for i, v in enumerate(vals))
    return round((2 * cum) / (n * total) - (n + 1) / n, 3)


def metrics(sim: Simulation) -> dict:
    posts = list(sim.posts.values())
    total = sum(p.post.quantity for p in posts)
    saved = [p for p in posts if p.status == "saved"]
    saved_q = sum(p.post.quantity for p in saved)
    accepted = [p for p in posts if p.accepted_at is not None]
    return {
        "food_saved_pct": round(100 * saved_q / total, 1) if total else 0.0,
        "portions_saved": saved_q,
        "portions_wasted": total - saved_q,
        "minutes_to_accept": round(statistics.mean(p.accepted_at - p.post.posted_at for p in accepted), 1)
        if accepted else None,
        "minutes_to_pickup": round(statistics.mean(p.picked_up_at - p.post.posted_at for p in saved), 1)
        if saved else None,
        "km_per_100_portions": round(100 * sim.km / saved_q, 1) if saved_q else None,
        "fairness_gini": gini([a.pickups for a in sim.agents.values()]),
        "givebacks": sum(p.givebacks for p in posts),
        "giveback_rate_pct": round(100 * sum(p.givebacks for p in posts) / (len(accepted) + sum(p.givebacks for p in posts)), 1)
        if accepted else None,
        "rescued_by_escalation": sum(1 for p in saved if p.rescued_by_escalation),
    }


def run_once(config: ScenarioConfig, strategy: Strategy) -> dict:
    return metrics(Simulation(generate(config), strategy).run())


def compare(config: ScenarioConfig, strategies: list[Strategy] | None = None) -> dict:
    """Run every strategy in ``config.runs`` separate cities (seed, seed+1, ...), each for ``config.days``
    consecutive days, and summarise each metric as mean and spread across cities."""
    config.validate()
    strategies = strategies or list(BUILT_IN.values())
    rows = []
    for run in range(config.runs):
        run_cfg = replace(config, seed=config.seed + run)
        for s in strategies:
            rows.append({"run": run + 1, "seed": run_cfg.seed, "strategy": s.name, **run_once(run_cfg, s)})
    summary = {}
    for s in strategies:
        mine = [r for r in rows if r["strategy"] == s.name]
        summary[s.name] = {"label": s.label, "metrics": {}}
        for key in METRICS:
            vals = [r[key] for r in mine if r[key] is not None]
            summary[s.name]["metrics"][key] = {
                "mean": round(statistics.mean(vals), 2) if vals else None,
                "spread": round(statistics.stdev(vals), 2) if len(vals) > 1 else 0.0,
            }
    return {"config": config.as_dict(), "metrics": {k: {"label": v[0], "unit": v[1], "higher_is_better": v[2]}
                                                    for k, v in METRICS.items()},
            "summary": summary, "runs": rows}


def to_csv(result: dict) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=["run", "seed", "strategy", *METRICS])
    writer.writeheader()
    writer.writerows(result["runs"])
    return out.getvalue()


MAX_TOTAL_POSTS = 6000  # runs x days x posts per day; keeps one request to roughly half a minute


def build_router(*, require_admin):
    """Host-only API: POST /internal/simulation/run with any ScenarioConfig fields."""
    from fastapi import APIRouter, Depends, HTTPException

    r = APIRouter(prefix="/internal/simulation")

    @r.post("/run")
    def run(body: dict, _: str = Depends(require_admin)):
        allowed = {f for f in ScenarioConfig.__dataclass_fields__ if f != "center"}
        unknown = set(body) - allowed
        if unknown:
            raise HTTPException(status_code=422, detail=f"unknown setting(s): {', '.join(sorted(unknown))}")
        try:
            cfg = ScenarioConfig(**body)
            cfg.validate()
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        if cfg.runs * cfg.days * cfg.posts_per_day > MAX_TOTAL_POSTS:
            raise HTTPException(status_code=422, detail=f"too large: runs x days x posts per day must be at most "
                                                        f"{MAX_TOTAL_POSTS}")
        return compare(cfg)

    return r
