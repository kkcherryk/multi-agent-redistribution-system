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

from .. import listings
from ..models import (Account, AccountStatus, AgentLocation, AgentMessage, AgentRecommendation, Assignment,
                      BackupRequest, BackupVolunteer, FoodListing, Handover, ListingStatus, NgoProfile, Recipient,
                      utcnow)
from .routing import directions_url, plan_route, road_km, travel_minutes
from .runtime import Agent

TOP_N = 3
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

    def __init__(self, radius_km: float) -> None:
        super().__init__()
        self.radius_km = radius_km

    # ---------------------------------------------------------------- collector ranking
    def rank_collectors(self, db: Session, listing: FoodListing) -> list[dict]:
        ranked = []
        for agent, profile, _straight in listings.eligible_agents(db, listing, self.radius_km):
            start, origin = agent_position(db, agent.id, profile)
            km = road_km(start, (listing.latitude, listing.longitude))
            eta = travel_minutes(km)
            jobs = recent_jobs(db, agent.id)
            score = eta + FAIRNESS_MIN_PER_RECENT_JOB * jobs
            ranked.append({"agent_id": agent.id, "name": profile.representative_name, "km": km, "eta": eta,
                           "score": score, "reason": f"~{eta:.0f} min from {origin} ({km:.1f} km by road)"
                           + (f"; {jobs} pickup(s) in the last 24 h" if jobs else "")})
        ranked.sort(key=lambda r: (r["score"], r["km"]))
        return ranked

    def recommend(self, db: Session, listing: FoodListing) -> list[AgentRecommendation]:
        ranked = self.rank_collectors(db, listing)[:TOP_N]
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
        if new != old:  # only talk when the suggestion actually changed
            for rec in recs:
                if rec.agent_id not in old:
                    self.send(db, rec.agent_id, "recommended_pickup", listing_id=listing.id, rank=rec.rank,
                              eta_minutes=rec.eta_minutes)
            self.send(db, "host", "collectors_ranked", listing_id=listing.id,
                      ranking=[{"agent_id": r["agent_id"], "name": r["name"], "eta_minutes": round(r["eta"], 1),
                                "reason": r["reason"]} for r in ranked])
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
        if msg.topic == "recommend" and listing is not None:
            return {"recommended": [r.agent_id for r in self.recommend(db, listing)]}
        if msg.topic == "rank_backup" and listing is not None:
            return {"candidates": self.rank_backup(db, listing, set(msg.payload.get("exclude", [])))}
        return None
