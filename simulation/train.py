"""Command line: train the learned dispatcher in the simulator, test it on unseen cities, save it.

    python -m simulation.train                 # 300 practice runs, about 15-30 seconds
    python -m simulation.train --episodes 600

The policy is saved to data/learned_policy.json. The host still has to switch it on in the Agents tab.
"""
from __future__ import annotations

import argparse

from .learning import POLICY_FILE, train_and_save


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--episodes", type=int, default=300)
    args = p.parse_args(argv)

    def progress(done, total):
        if done % 50 == 0 or done == total:
            print(f"  practised {done}/{total} pretend days")

    print("Training the learned dispatcher in the simulator...")
    policy = train_and_save(episodes=args.episodes, progress=progress)
    print("\nWhat it learned:")
    for e in policy["explanation"]:
        print(f"  {e['label']:<42} {e['hand_set']:>6} -> {e['learned']:>8}   ({e['change']})")
    print("\nTest on unseen cities:")
    for line in policy["test"]["details"]:
        print("  " + line)
    print("\n" + policy["test"]["summary"])
    print(f"Saved to {POLICY_FILE}. Switch it on in the host portal (Agents tab) if you want to use it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
