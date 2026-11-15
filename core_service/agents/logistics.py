"""Logistics / route-optimisation agent (spec Module 11).

* Ranks eligible collectors for every open listing by estimated travel time (plus a small fairness penalty so the
  same agent is not always chosen) and marks the top ones as "recommended" in their feed. Advisory only: the first
  agent to accept still gets the pickup.
* Plans the trip for an accepted pickup: agent -> restaurant -> nearest recipient (or the NGO's storage).
* Ranks backup volunteers when the backup agent asks for them.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .. import listings, trust
from ..models import (Account, AccountStatus, AgentLocation, AgentMessage, AgentRecommendation, AppSetting, Assignment,
                      BackupRequest, BackupVolunteer, FoodListing, Handover, ListingStatus, NgoProfile, Recipient,
                      utcnow)
from .routing import directions_url, plan_route, road_km, travel_minutes
from .dispatch_features import cost as dispatch_cost, feature_vector
from .ngo_agents import address as ngo_address
from .runtime import Agent

TOP_N = 3
LEARNED_DISPATCH_KEY = "learned_dispatch"
FRESH_LOCATION = timedelta(minutes=10)   # older live positions are ignored in favour of the NGO base
FAIRNESS_MIN_PER_RECENT_JOB = 2.0        # minutes added per pickup the agent completed in the last 24 h
REFRESH_AFTER = timedelta(minutes=5)


def agent_position(db: Session, agent_id: str, profile: NgoProfile) -> tuple[tuple[float, float], str]:
    loc = db.get(AgentLocation, agent_id)
    if loc is not None and utcnow() - loc.updated_at <= FRESH_LOCATION:
        return (loc.latitude, loc.longitude), "current location"
    return (profile.latitude, profile.longitude), "NGO base"


def recent_jobs(db: Session, agent_id: str) -> int:
    since = utcnow() - timedelta(hours=24)
    return db.scalar(select(func.count()).select_from(Handover)
                     .where(Handover.agent_id == agent_id, Handover.kind.in_(("delivered", "stored")),
                            Handover.created_at >= since)) or 0


class LogisticsAgent(Agent):
    name = "logistics"
    description = "Ranks the best collectors for new food and plans pickup routes."

    def __init__(self, radius_km: float, policy_path=None) -> None:
        super().__init__()
        self.radius_km = radius_km
        self.policy_path = policy_path  # None = the default data/learned_policy.json

    # ---------------------------------------------------------------- collector ranking
    def nearby_ngos(self, db: Session, listing: FoodListing) -> list[str]:
        """Verified NGOs whose agent (live position or NGO base) is within the radius: they get the call."""
        out = []
        for acc, profile in db.execute(select(Account, NgoProfile).join(NgoProfile, NgoProfile.account_id == Account.id)
                                       .where(Account.role == "ngo", Account.status == AccountStatus.VERIFIED)):
            start, _ = agent_position(db, acc.id, profile)
            if road_km(start, (listing.latitude, listing.longitude)) / 1.35 <= self.radius_km:
                out.append(acc.id)
        return out

    def rank_collectors(self, db: Session, listing: FoodListing, *, announce: bool = False) -> list[dict]:
        """Contract-Net round (Phase 7): call for proposals -> proposals/refusals -> score -> ranking.

        Bid total (lower is better) = travel minutes + trust penalty ((100 - trust) / 10, max 10)
        + fairness penalty (2 min per pickup in the last 24 h). With ``announce`` every message is logged.
        """
        ngo_agents = self.bus.agents["ngo-agents"]
        learned = self.learned_weights(db)  # Phase 9: host-switched learned dispatch, else None
        minutes_left = (listing.expires_at - utcnow()).total_seconds() / 60
        proposals, refusals = [], []
        for agent_id in self.nearby_ngos(db, listing):
            if announce:
                reply = self.send(db, ngo_address(agent_id), "call_for_proposals", listing_id=listing.id)
            else:
                reply = ngo_agents.bid(db, agent_id, listing)
            if reply["decision"] != "propose":
                refusals.append({"agent_id": agent_id, "reason": reply["reason"]})
                continue
            trust_pen = trust.bid_trust_penalty(reply["trust"])
            fair_pen = FAIRNESS_MIN_PER_RECENT_JOB * reply["recent_jobs"]
            total = reply["eta_minutes"] + trust_pen + fair_pen
            if learned is not None:
                total = dispatch_cost(learned, feature_vector(
                    eta_minutes=reply["eta_minutes"], trust=reply["trust"], confidence=reply["trust_confidence"],
                    jobs_today=reply["recent_jobs"], minutes_left=minutes_left, quantity=listing.quantity))
            reason = (("learned dispatch; " if learned is not None else "")
                      + f"~{reply['eta_minutes']:.0f} min from {reply['origin']} ({reply['km']:.1f} km by road); "
                      f"trust {reply['trust']:.0f} ({reply['trust_confidence']} confidence)"
                      + (f"; {reply['recent_jobs']} pickup(s) in the last 24 h" if reply["recent_jobs"] else ""))
            proposals.append({"agent_id": agent_id, "name": reply["name"], "km": reply["km"],
                              "eta": reply["eta_minutes"], "trust": reply["trust"], "trust_penalty": round(trust_pen, 1),
                              "fairness_penalty": fair_pen, "score": round(total, 1), "reason": reason})
        proposals.sort(key=lambda r: (r["score"], r["km"]))
        self._last_refusals = refusals
        return proposals

    def learned_weights(self, db: Session) -> list[float] | None:
        """Learned weights if the host switched learned dispatch on AND a valid trained policy exists;
        otherwise None, so ranking falls back to the hand-set Phase 7 bid."""
        setting = db.get(AppSetting, LEARNED_DISPATCH_KEY)
        if setting is None or setting.value != "on":
            return None
        from simulation.learning import load_policy  # local: the simulator package imports this module
        policy = load_policy(self.policy_path)
        return policy["weights"] if policy else None

    def recommend(self, db: Session, listing: FoodListing, *, announce: bool = False) -> list[AgentRecommendation]:
        bids = self.rank_collectors(db, listing, announce=announce)
        ranked = bids[:TOP_N]
        old = [r.agent_id for r in db.scalars(select(AgentRecommendation)
                                              .where(AgentRecommendation.listing_id == listing.id)
                                              .order_by(AgentRecommendation.rank))]
        db.execute(delete(AgentRecommendation).where(AgentRecommendation.listing_id == listing.id))
        recs = []
        for i, r in enumerate(ranked, start=1):
            rec = AgentRecommendation(listing_id=listing.id, agent_id=r["agent_id"], rank=i,
                                      distance_km=round(r["km"], 2), eta_minutes=round(r["eta"], 1), reason=r["reason"])
            db.add(rec)
            recs.append(rec)
        db.flush()
        new = [r.agent_id for r in recs]
        if new != old or announce:  # only talk when the outcome changed (or on the first round)
            for rec in recs:
                if rec.agent_id not in old or announce:
                    self.send(db, ngo_address(rec.agent_id), "award", listing_id=listing.id, rank=rec.rank,
                              eta_minutes=rec.eta_minutes)
            self.send(db, "host", "negotiation_round", listing_id=listing.id,
                      bids=[{k: b[k] for k in ("agent_id", "name", "eta", "trust", "trust_penalty",
                                               "fairness_penalty", "score")} for b in bids],
                      refusals=self._last_refusals, recommended=new)
        return recs

    def tick(self, db: Session) -> None:
        listings.expire_due(db)
        cutoff = utcnow() - REFRESH_AFTER
        for listing in db.scalars(select(FoodListing).where(FoodListing.status == ListingStatus.ACTIVE)):
            newest = db.scalar(select(func.max(AgentRecommendation.created_at))
                               .where(AgentRecommendation.listing_id == listing.id))
            if newest is None or newest < cutoff:
                self.recommend(db, listing)

    # ---------------------------------------------------------------- routes
    def pickup_route(self, db: Session, listing: FoodListing, assignment: Assignment) -> dict:
        profile = db.get(NgoProfile, assignment.agent_id)
        start, origin = agent_position(db, assignment.agent_id, profile)
        restaurant = (listing.latitude, listing.longitude)
        backup = db.scalar(select(BackupRequest).where(BackupRequest.listing_id == listing.id,
                                                       BackupRequest.volunteer_id == assignment.agent_id,
                                                       BackupRequest.status == "accepted")) is not None
        destination, dest_label, dest_kind = (profile.latitude, profile.longitude), f"{profile.ngo_name} storage", "storage"
        if not backup:  # backup collections go to NGO storage; normal ones to the nearest recipient if any
            recips = [r for r in db.scalars(select(Recipient).where(Recipient.ngo_id == assignment.agent_id))
                      if r.latitude is not None and r.longitude is not None]
            if recips:
                best = min(recips, key=lambda r: road_km(restaurant, (r.latitude, r.longitude)))
                destination, dest_label, dest_kind = (best.latitude, best.longitude), best.name, "recipient"
        # Before collection the trip starts where the agent is; afterwards it starts at the restaurant.
        stops = [("Restaurant", restaurant), (dest_label, destination)]
        if listing.status == ListingStatus.ASSIGNED:
            stops.insert(0, (f"You ({origin})", start))
        plan = plan_route(stops, reorder=False)
        points = [p for _, p in stops]
        return {**plan.as_dict(), "destination": {"name": dest_label, "kind": dest_kind},
                "backup_collection": backup, "directions_url": directions_url(points),
                "assumptions": "road distance = straight line x 1.35; average city speed 18 km/h; total includes 5 min at each stop"}

    # ---------------------------------------------------------------- backup volunteer ranking
    def rank_backup(self, db: Session, listing: FoodListing, exclude: set[str]) -> list[dict]:
        out = []
        for vol, agent, profile in db.execute(
                select(BackupVolunteer, Account, NgoProfile)
                .join(Account, Account.id == BackupVolunteer.agent_id)
                .join(NgoProfile, NgoProfile.account_id == BackupVolunteer.agent_id)
                .where(BackupVolunteer.active.is_(True), Account.status == AccountStatus.VERIFIED)):
            if agent.id in exclude or listings.active_assignment_count(db, agent.id) >= listings.MAX_ACTIVE_ASSIGNMENTS_PER_AGENT:
                continue
            start, _ = agent_position(db, agent.id, profile)
            km = road_km(start, (listing.latitude, listing.longitude))
            if km / 1.35 > vol.max_distance_km:  # compare straight-line distance with the volunteer's limit
                continue
            out.append({"agent_id": agent.id, "km": round(km, 2), "eta": round(travel_minutes(km), 1)})
        return sorted(out, key=lambda r: r["eta"])

    # ---------------------------------------------------------------- messages from other agents
    def handle(self, db: Session, msg: AgentMessage) -> dict | None:
        listing = db.get(FoodListing, msg.listing_id) if msg.listing_id else None
        if msg.topic in ("recommend", "food_posted") and listing is not None:
            return {"recommended": [r.agent_id for r in self.recommend(db, listing, announce=msg.topic == "food_posted")]}
        if msg.topic == "rank_backup" and listing is not None:
            return {"candidates": self.rank_backup(db, listing, set(msg.payload.get("exclude", [])))}
        return None
