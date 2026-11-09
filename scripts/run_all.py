"""Start the core data service, the three portals and the landing page as separate processes.

Usage:  python -m scripts.run_all        (Ctrl+C stops everything)
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

from shared.config import DEFAULT_PORTS, ROOT, get_settings

SERVICES = [
    ("core", "core_service.app:build_default_app"),
    ("admin", "portals.admin.app:build_default_app"),
    ("restaurant", "portals.restaurant.app:build_default_app"),
    ("ngo", "portals.ngo.app:build_default_app"),
    ("landing", "landing.app:build_default_app"),
]


def main() -> int:
    get_settings()  # create data/.secrets.json once, before the processes start in parallel
    host = os.environ.get("FRS_HOST", "127.0.0.1")
    procs = []
    for name, target in SERVICES:
        port = int(os.environ.get(f"FRS_{name.upper()}_PORT", DEFAULT_PORTS[name]))
        cmd = [sys.executable, "-m", "uvicorn", target, "--factory", "--host", host, "--port", str(port)]
        procs.append((name, subprocess.Popen(cmd, cwd=ROOT)))
        print(f"  {name:<11} http://{host}:{port}")
    print("Open the landing page to choose a portal. Ctrl+C to stop.")
    try:
        while all(p.poll() is None for _, p in procs):
            time.sleep(1)
        for name, p in procs:
            if p.poll() is not None:
                print(f"{name} exited with code {p.returncode}; stopping the others.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        for _, p in procs:
            if p.poll() is None:
                p.terminate()
        for _, p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    raise SystemExit(main())
