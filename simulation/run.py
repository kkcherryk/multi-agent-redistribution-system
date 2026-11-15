"""Command line: compare strategies in the simulator and save a CSV report.

    python -m simulation.run                      # default busy city: 3 cities x 5 days
    python -m simulation.run --ngos 30 --runs 5   # any ScenarioConfig field can be set
"""
from __future__ import annotations

import argparse
from dataclasses import fields
from datetime import datetime

from shared.config import DATA_DIR

from .evaluate import compare, to_csv
from .scenario import ScenarioConfig


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for f in fields(ScenarioConfig):
        if f.name != "center":
            p.add_argument("--" + f.name.replace("_", "-"), type=type(f.default), default=f.default)
    args = p.parse_args(argv)
    cfg = ScenarioConfig(**{k: v for k, v in vars(args).items()})
    result = compare(cfg)
    cols = list(result["metrics"])
    print(f"{'Strategy':<24}" + "".join(f"{result['metrics'][c]['label'][:18]:>20}" for c in cols))
    for s in result["summary"].values():
        print(f"{s['label']:<24}" + "".join(
            f"{('-' if s['metrics'][c]['mean'] is None else format(s['metrics'][c]['mean'], 'g')):>20}" for c in cols))
    out = DATA_DIR / "sim_reports" / f"simulation_{datetime.now():%Y%m%d_%H%M%S}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(to_csv(result), encoding="utf-8")
    print(f"\nPer-run results saved to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
