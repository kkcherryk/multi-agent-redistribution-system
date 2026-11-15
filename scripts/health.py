"""Check that all five services are up:  python -m scripts.health"""
from __future__ import annotations

import sys

import httpx

from shared.config import get_settings


def main() -> int:
    s = get_settings()
    targets = {"core": s.core_url, **s.portal_urls, "landing": s.landing_url}
    bad = 0
    for name, url in targets.items():
        try:
            r = httpx.get(url.rstrip("/") + "/health", timeout=5)
            body = r.json()
            ok = r.status_code == 200 and body.get("status") == "ok"
            extra = ""
            if name == "core":
                failing = [a for a, fine in body.get("agents_ok", {}).items() if not fine]
                extra = f"  database {'ok' if body.get('database') else 'DOWN'}" + \
                        (f", agents with errors: {', '.join(failing)}" if failing else ", agents ok")
            elif "core_reachable" in body:
                extra = "  core reachable" if body["core_reachable"] else "  CANNOT REACH CORE"
                ok = ok and body["core_reachable"]
        except (httpx.HTTPError, ValueError):
            ok, extra = False, "  not running"
        bad += not ok
        print(f"  {'OK  ' if ok else 'FAIL'} {name:<11} {url}{extra}")
    print("All services healthy." if not bad else f"{bad} service(s) need attention.")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
