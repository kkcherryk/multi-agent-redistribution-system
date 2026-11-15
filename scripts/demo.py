"""End-to-end demo: the whole food-rescue story through the three real portals, checked step by step.

    python -m scripts.demo --host-email admin@gmail.com

Start the app first (python -m scripts.run_all). The host password is asked for (or set FRS_HOST_PASSWORD).
It creates a new demo restaurant and NGO (unique emails), so it can be run any number of times.
The same story is the automated end-to-end test (tests/test_end_to_end.py).
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PDF = b"%PDF-1.4\n%demo pdf\n"
BENGALURU = {"latitude": 12.9757, "longitude": 77.6050}


class DemoFailed(AssertionError):
    pass


def check(ok: bool, what: str) -> None:
    if not ok:
        raise DemoFailed(what)


def ok_json(resp, what: str, status: int = 200):
    if resp.status_code != status:
        raise DemoFailed(f"{what}: HTTP {resp.status_code} {resp.text[:300]}")
    return resp.json() if resp.content else None


def unread_titles(client) -> list[str]:
    data = ok_json(client.get("/api/notifications"), "read notifications")
    return [n["title"] for n in data["items"] if not n["read"]]


def run_story(host, rest, ngo, *, host_email: str, host_password: str, tag: str | None = None, say=print) -> list[str]:
    """``host``/``rest``/``ngo``: HTTP clients (httpx.Client or FastAPI TestClient) for the three portals, each
    keeping its own cookies. Returns the list of completed steps; raises DemoFailed on the first broken step."""
    tag = tag or str(int(time.time()))
    done: list[str] = []

    def step(text: str) -> None:
        done.append(text)
        say(f"  ✔ {len(done):>2}. {text}")

    ok_json(host.post("/api/login", json={"email": host_email, "password": host_password}), "host sign-in")
    step("Host signed in")

    # --- restaurant onboarding (Modules 1, 2, 5)
    r_email = f"demo-restaurant-{tag}@example.org"
    ok_json(rest.post("/api/register", json={"email": r_email, "password": "demo-pass-123",
                                             "display_name": "Demo Kitchen", "phone": "+91 90000 11111"}),
            "restaurant registration", 201)
    ok_json(rest.put("/api/profile", json={"applicant_name": "Asha Rao", "restaurant_name": f"Demo Kitchen {tag}",
                                           "address": "12 MG Road, Bengaluru", **BENGALURU,
                                           "licence_number": "FSSAI-1234567890"}), "restaurant profile")
    for kind, name, data, ctype in (("restaurant_photo", "shop.png", PNG, "image/png"),
                                    ("food_licence", "licence.pdf", PDF, "application/pdf"),
                                    ("identity_proof", "id.jpg", JPEG, "image/jpeg")):
        ok_json(rest.post("/api/documents", data={"kind": kind}, files={"file": (name, data, ctype)}),
                f"restaurant {kind}", 201)
    ok_json(rest.post("/api/submit"), "restaurant submit")
    rest_id = ok_json(rest.get("/api/me"), "restaurant me")["id"]
    step("Restaurant registered, filled its profile, uploaded 3 documents and submitted")
    check("New application to review" in unread_titles(host), "host was not notified about the application")
    step("Host got a notification: new application to review")

    ok_json(host.post(f"/api/accounts/{rest_id}/review", json={"decision": "verify"}), "verify restaurant")
    check("Your account is approved" in unread_titles(rest), "restaurant was not told it is approved")
    step("Host verified the restaurant; the restaurant was notified")

    # --- NGO onboarding (Modules 3, 5)
    n_email = f"demo-ngo-{tag}@example.org"
    ok_json(ngo.post("/api/register", json={"email": n_email, "password": "demo-pass-123",
                                            "display_name": "Demo Helpers", "phone": "+91 90000 33333"}),
            "NGO registration", 201)
    ok_json(ngo.put("/api/profile", json={"ngo_name": f"Demo Helpers {tag}", "ngo_registration_id": "NGO-KA-2024-77",
                                          "representative_name": "Ravi Kumar", "address": "4 Church St, Bengaluru",
                                          "latitude": 12.9740, "longitude": 77.6070}), "NGO profile")
    for kind, name, data, ctype in (("representative_photo", "me.jpg", JPEG, "image/jpeg"),
                                    ("ngo_registration", "reg.pdf", PDF, "application/pdf"),
                                    ("representative_id", "id.png", PNG, "image/png")):
        ok_json(ngo.post("/api/documents", data={"kind": kind}, files={"file": (name, data, ctype)}),
                f"NGO {kind}", 201)
    ok_json(ngo.post("/api/submit"), "NGO submit")
    ngo_id = ok_json(ngo.get("/api/me"), "NGO me")["id"]
    ok_json(host.post(f"/api/accounts/{ngo_id}/review", json={"decision": "verify"}), "verify NGO")
    ok_json(ngo.put("/api/availability", json={"available": True}), "NGO availability")
    step("NGO registered with agent photo, was verified by the host and went Available")

    # --- food posting, matching and agent recommendation (Modules 6, 7, 11, 12)
    now = datetime.now(timezone.utc)
    food = {"food_type": f"Veg biryani {tag}", "description": "Packed in foil trays", "dietary": "veg",
            "quantity": 40, "unit": "portions", "condition": "fresh",
            "prepared_at": (now - timedelta(hours=1)).isoformat(), "expires_at": (now + timedelta(hours=4)).isoformat(),
            "pickup_notes": "Back door"}
    listing = ok_json(rest.post("/api/listings", data={"data": json.dumps(food)},
                                files={"photo": ("food.png", PNG, "image/png")}), "post food", 201)
    lid = listing["id"]
    step("Restaurant posted 40 portions of food with a photo")
    check(any(t.startswith("New food nearby") for t in unread_titles(ngo)), "NGO was not alerted about nearby food")
    feed = ok_json(ngo.get("/api/feed"), "NGO feed")["listings"]
    mine = [x for x in feed if x["id"] == lid]
    check(bool(mine), "food is missing from the NGO feed")
    check(mine[0]["restaurant"].get("phone") is None, "restaurant phone leaked before acceptance")
    rec = mine[0].get("recommended")
    step("NGO got a 'new food nearby' notification and sees it in the feed"
         + (f" (agents recommend this NGO: rank {rec['rank']})" if rec else ""))

    # --- pickup, inspection, delivery (Modules 8, 9, 10)
    ok_json(ngo.post(f"/api/listings/{lid}/accept"), "accept")
    check(any("will collect your food" in t for t in unread_titles(rest)), "restaurant was not told about the pickup")
    rest_view = [x for x in ok_json(rest.get("/api/listings"), "restaurant listings") if x["id"] == lid][0]
    check(rest_view["assignment"]["phone"] == "+91 90000 33333", "restaurant cannot see the collector's phone")
    step("NGO accepted; the restaurant was notified and can see the collector's phone")
    ok_json(ngo.post(f"/api/pickups/{lid}/start", json={}), "start pickup")
    ok_json(ngo.post(f"/api/pickups/{lid}/inspection", json={"decision": "collect", "actual_quantity": 40,
                                                             "observed_condition": "good"}), "inspection")
    step("Agent started the trip, checked the food (40 of 40, good) and collected it")
    rcp = ok_json(ngo.post("/api/recipients", json={"name": "Sunshine Orphanage", "kind": "orphanage",
                                                    "address": "8 Lake Rd, Bengaluru", "latitude": 12.97,
                                                    "longitude": 77.61}), "add recipient", 201)
    done_view = ok_json(ngo.post(f"/api/pickups/{lid}/deliver",
                                 json={"recipient_id": rcp["id"], "received_by": "Sister Mary"}), "deliver")
    check(done_view["status"] == "delivered", "food is not marked delivered")
    check("Your food reached people" in unread_titles(rest), "restaurant was not told the food was delivered")
    step("Food delivered to Sunshine Orphanage; the restaurant was notified")

    # --- privacy and trust (Modules 12, Phase 10)
    rest_view = [x for x in ok_json(rest.get("/api/listings"), "restaurant listings") if x["id"] == lid][0]
    check(rest_view["assignment"]["phone"] is None, "collector phone still visible after the pickup finished")
    step("Privacy: once the pickup finished, phone numbers are hidden again")
    trust = ok_json(host.get("/api/trust"), "trust overview")
    row = [x for x in trust["ngos"] if x["account_id"] == ngo_id]
    check(bool(row) and row[0]["score"] > 50, "NGO trust did not go up after a successful delivery")
    step(f"Trust: the NGO's score rose to {row[0]['score']} after one successful delivery")
    ok_json(host.get(f"/api/privacy/accounts/{ngo_id}/export"), "data export")
    step("Host exported everything stored about the NGO (data request)")
    return done


def main(argv=None) -> int:
    import httpx

    from shared.config import get_settings

    p = argparse.ArgumentParser(description="Run the end-to-end demo against the running app.")
    p.add_argument("--host-email", required=True)
    args = p.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # the tick marks below on Windows consoles
        sys.stderr.reconfigure(encoding="utf-8")
    password = os.environ.get("FRS_HOST_PASSWORD") or getpass.getpass("Host password: ")
    urls = get_settings().portal_urls
    clients = [httpx.Client(base_url=urls[k], timeout=30) for k in ("admin", "restaurant", "ngo")]
    print("End-to-end demo: restaurant -> agents -> NGO -> recipient")
    try:
        steps = run_story(*clients, host_email=args.host_email, host_password=password)
    except DemoFailed as exc:
        print(f"  ✘ FAILED: {exc}", file=sys.stderr)
        return 1
    except httpx.ConnectError:
        print("  ✘ The app is not running. Start it with:  python -m scripts.run_all", file=sys.stderr)
        return 1
    print(f"All {len(steps)} steps passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
