# Prototype Development Roadmap

Source: *Multi-Agent Food Redistribution System – Project modules and workflow specification* (13 modules).
Each phase must be reviewed and explicitly approved before the next one starts.

> **Note on scope.** The specification describes the trust/negotiation agent, the logistics agent and the
> backup/storage agent, but it does **not** define a scoring formula, a negotiation protocol, a reinforcement
> learning component or evaluation methods (it lists them as open decisions). Phases 7–9 therefore contain
> *proposed* designs, marked as such, for you to approve or change.

| Phase | Name | Spec modules | Key deliverables |
|---|---|---|---|
| 1 ✅ | Foundation: portals, core data service, role selection & authentication | M1, M4, M13 | Core data service (authoritative records), 3 separate portal apps (Admin/Host, Restaurant, NGO) with own frontend + backend, role-selection landing page, per-portal login, service-to-service keys, host accounts created by the team, audit event log |
| 2 ✅ | Onboarding, verification & availability | M2, M3, M5 | Restaurant onboarding (lat/long, photos, licence/evidence), NGO + representative onboarding with photo verification, host review queue (approve / hold / decline / request info), badges (restaurant blue/orange, NGO green, agent green/red), agent directory |
| **3** | Food listings & nearby-NGO notification | M6, M7 (basic) | Listing with quantity, condition, prep/expiry time, photo; expiry enforcement; only verified restaurants; distance-based nearby-agent alerts; first-accept claim with no double assignment |
| 4 | Pickup, inspection, delivery & storage | M8, M9 | Assignment state machine, inspection record (actual qty, condition, discrepancy), delivery confirmation to recipient, NGO storage inventory with expiry tracking |
| 5 | Host dashboard & live map | M10 | Map of restaurants, agents, open tickets and movement; agent detail panel; last-known vs live location; operational overview |
| 6 | Multi-agent layer: logistics & backup collection | M11 | Agent runtime + message bus, route-optimisation agent (nearest-collector + route heuristics), backup/storage agent with trigger rules (no acceptance / all agents busy) |
| 7 | Trust assessment & negotiation protocol *(proposed design)* | M12, M7 (advanced) | Explainable trust/reputation scores with confidence and reasons (decision support only), Contract-Net-style negotiation between listing and NGO agents, host review flags |
| 8 | Simulation & evaluation harness *(proposed design)* | cross-cutting | Synthetic city/scenario generator, metrics (food rescued %, waste, pickup latency, distance, fairness, trust calibration), baseline comparisons, reports |
| 9 | Reinforcement-learning dispatch policy *(proposed design)* | M7, M11 | RL / bandit policy trained in the simulator, compared against heuristic baselines, deployed behind a host-controlled switch |
| 10 | Hardening & integration | all | Notifications, privacy/retention rules, security review, deployment scripts, end-to-end demo |

Phases can be re-ordered, split or extended as requirements change.

## Decisions recorded

| Phase | Decision |
|---|---|
| 2 | NGO representative photo is captured at first registration only (not every login). |
| 2 | Restaurant evidence: ≥1 restaurant photo, food licence, identity/business proof. NGO evidence: representative photo, NGO registration certificate, representative ID. |
| 2 | Photo verification is a manual host comparison; no automated face matching. |
| 2 | One representative (collecting agent) per NGO account for now; multiple agents per NGO can be added later. |
| 2 | "Request more information" is the *On hold* state with a mandatory note; the applicant edits and resubmits. Declining also needs a note. The host can change any decision later. |
| 2 | Availability is stored and shown separately from verification and can be toggled once the NGO profile exists. |
| 3 | Nearby radius is 5 km (straight-line distance) from the restaurant's verified location. |
| 3 | Quantity units: portions or kg. Types: veg / non-veg / mixed. Condition: fresh / good / fair. A food photo is required. |
| 3 | Expiry must be after preparation, at least 15 minutes from now, and at most 72 hours ahead. Listings expire automatically. |
| 3 | Only verified + available NGO agents within the radius are alerted. Agents who become available later are alerted about food already waiting. |
| 3 | First agent to accept gets the pickup (no double assignment, enforced in the database). One active pickup per agent. If nobody accepts, the listing stays open until it expires. |
| 3 | Restaurant phone and contact name are shared with the NGO only after it accepts; restaurant badges are never shown to NGOs. |
