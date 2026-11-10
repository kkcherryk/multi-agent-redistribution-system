# Multi-Agent Food Redistribution System — Prototype

Connects restaurants with surplus food to NGOs that collect and distribute it, supervised by a host.
The phased roadmap is in [PHASES.md](PHASES.md).

## Architecture

```
                    Landing page :8004  (choose Restaurant / NGO / Host)
                       │            │             │
      Restaurant portal :8002   NGO portal :8003   Admin/Host portal :8001
      (own frontend+backend)    (own frontend+backend)   (own frontend+backend)
                       │  X-Service-Key  │               │
                       └────────► Core data service :8000 ◄┘
                                 (authoritative records: accounts, events)
```

* **Three separate portals** (spec Module 13): each is its own FastAPI app with its own frontend, session
  cookie and signing secret. A session issued by one portal is rejected by the others.
* **Core data service**: the only owner of shared records. Portals call it with a per-portal service key, and the
  core enforces data ownership. A portal only signs in accounts of its own role. Restaurant and NGO portals can only
  read accounts of their own role. Only the admin portal can list all accounts and the event log.
* **Host accounts** are created by the project team (`scripts/create_host.py`); the admin portal has no sign-up.
* **Event log**: every registration, login and failed login is recorded. Later phases feed the map and agents from it.

## Phase 2: onboarding and verification

* Restaurant and NGO applicants complete a profile (including latitude/longitude) and upload evidence, then submit.
  Status flow: `applicant → under_review → verified | on_hold (resubmit) | declined`.
* Uploads are limited to 5 MB. The real file type is detected from its content (JPEG/PNG/WEBP/PDF), and files are
  served only to their owner and the host, with `nosniff` and a sandboxing CSP.
* The host portal has a review queue with an evidence viewer, verify/hold/decline (a note is required to hold or
  decline), review history, an NGO agent directory and badges.
* Badges (Module 5): restaurant blue *Verified* / orange *Unverified* or *On hold*; NGO green *Verified NGO*;
  agent green *Available* / red *Unavailable*.

## Phase 3: food listings and nearby alerts

* A verified restaurant posts food: type, quantity (portions/kg), veg/non-veg, condition, preparation and expiry
  time, a photo and pickup notes.
* Verified, available NGO agents within 5 km are alerted. Their "Nearby food" feed (refreshed every 15 s) shows
  distance and time left.
* The first agent to accept gets the pickup; nobody else can take it. The restaurant sees who is collecting, and
  the agent gets the restaurant's contact details.
* The host's "Food listings" tab shows every listing, its status and how many agents were alerted.

## Phase 4: pickup, food check, delivery and storage

* The agent marks "I'm on my way", then records a food check at the restaurant (quantity received and condition),
  and either collects or rejects the food.
* Collected food is delivered to one of the NGO's recipients (orphanage, shelter, old-age home, ...) with the
  receiver's name, or stored at the NGO and given out later in parts. Expired food is recorded as disposed.
* The restaurant and the host see the full history of each listing; the host has an "NGO storage" tab.

## Phase 5: host live map

* The host's "Live map" tab (OpenStreetMap) shows restaurants, open food, pickups in progress with the agent's
  movement trail, and NGO agents (live, last known, or at their NGO base). It refreshes every 10 seconds, and
  clicking an agent opens a details panel.
* NGO agents share their live location with "Start sharing my location" (only while Available or on a pickup).
* The map needs an internet connection to load the map tiles.

## Phase 6: multi-agent layer

* Two backend agents run every 30 seconds and exchange messages (all logged):
  * **Logistics agent:** ranks the best collectors for new food (shown as "Recommended for you" in the NGO
    feed) and plans each pickup route (agent → restaurant → nearest recipient or NGO storage) with a directions link.
  * **Backup agent:** spots food at risk, alerts agents up to 10 km away, then asks opted-in backup NGOs one
    at a time to collect and store it, and flags the host if nobody can.
* NGOs can opt in to backup collections and accept or decline requests. The host has an "Agents" tab, an
  "Escalate" button for open food, and "Run agents now".

## Run it

```bash
pip install -r requirements.txt
python -m scripts.seed_dev      # optional: dev host/restaurant/NGO accounts, credentials in data/dev_credentials.json
python -m scripts.run_all       # starts all 5 services; open http://127.0.0.1:8004
python -m pytest -q             # test suite
```

Create a real host account: `python -m scripts.create_host --email you@org.example --name "Your Name"`.

Ports, URLs and secrets can be overridden with `FRS_*` environment variables (see `shared/config.py`).
Generated secrets live in `data/.secrets.json`, and the database is `data/core.db`. The whole `data/` folder is
git-ignored.
