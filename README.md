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
