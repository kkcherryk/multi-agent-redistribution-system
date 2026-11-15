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

## Phase 7: trust scores and agent negotiation

* **Trust (host only, decision support):** every NGO and restaurant gets a 0–100 score with a confidence level,
  plain-language reasons and review flags; the host can add notes. A new account starts at 50.
* **Negotiation (Contract-Net):** when food is posted, the restaurant's agent announces it, the logistics agent
  asks nearby NGO agents for offers, each answers with an offer (travel time, trust) or a refusal, and the best
  offers become "Recommended". People still press Accept. Every round is shown in the host's Agents tab.

## Phase 8: simulator and evaluation

* A pretend city (never your real data) replays thousands of food posts in seconds and compares four ways of
  choosing a collector: Random, Nearest agent, Our agents, and Our agents without trust.
* Host portal → "Simulation" tab: change the settings, run, see charts and a table, download a CSV.
* Command line: `python -m simulation.run` (any setting can be passed, e.g. `--ngos 30 --runs 5`); CSV files
  are saved in `data/sim_reports/`.

## Phase 9: learned dispatch (reinforcement learning)

* A learning dispatcher practises in the simulator (policy gradient) and learns how much travel time, trust,
  fairness and urgency should count when choosing who is offered food first.
* Host portal → Agents tab → "Learned dispatch": train it (about 20 seconds), read its test result on unseen
  cities, and switch it on or off. It is off by default and falls back to the normal rule if not trained.
* Command line: `python -m simulation.train` (saves `data/learned_policy.json`).

## Phase 10: hardening and integration

* **Notifications:** a bell in every portal's header shows a red unread count. People are told about:
  * new nearby food (NGO);
  * pickup accepted, on the way, collected or rejected, given back, delivered, stored or expired (restaurant);
  * account approved, held or declined (applicant);
  * new applications and food needing attention (host);
  * backup requests (backup collector).
* **Privacy:** phone numbers are shared only while a pickup is in progress. Old data is deleted automatically:
  * movement trails after 7 days;
  * stale positions after 1 day;
  * notifications after 30 days if read, 90 if unread;
  * agent messages and sign-in records after 90 days.

  Host portal → **Privacy** tab: read the rules, run the clean-up, and export or erase one account's data.
* **Security:**
  * limits on repeated sign-ins and registrations;
  * security headers;
  * cross-site requests refused;
  * no caching of personal data.

  See [SECURITY.md](SECURITY.md) for the full review.
* **Deployment:**
  * `scripts/setup.py` handles one-time setup;
  * `.env.example` lists all settings;
  * `python -m scripts.health` checks all five services.
* **End-to-end demo:** `python -m scripts.demo --host-email <host email>` runs the whole story (13 checked steps)
  against the running app. The same story is the automated test `tests/test_end_to_end.py`.

## Run it (VS Code terminal)

```bash
python -m scripts.setup --host-email you@org.example --host-name "Your Name"
python -m scripts.run_all
```

Open http://127.0.0.1:8004 and choose a portal. Press Ctrl+C in the terminal to stop.

Other commands, each run in a second terminal while the app is running:

```bash
python -m scripts.health
python -m scripts.demo --host-email you@org.example
python -m pytest -q
```

* `python -m scripts.seed_dev` creates dev host, restaurant and NGO accounts; their credentials go in `data/dev_credentials.json`.
* Settings can be put in a `.env` file: copy `.env.example`.
* Generated secrets live in `data/.secrets.json` and the database is `data/core.db`. The whole `data/` folder and
  `.env` are git-ignored.
