"""NGO agents: one software agent per NGO account that answers calls for proposals automatically (Phase 7).

In the Contract-Net round the logistics agent (manager) sends a call for proposals to every NGO agent near the
food. Each NGO agent replies with a proposal (its travel estimate and trust) or a refusal with the reason. The
humans behind the NGO agents still press Accept; the proposal only decides the order of recommendations.
Messages are addressed to "ngo-agent:<account id>"; this single runtime agent answers for all of them.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from .. import listings, trust
from ..models import Account, AccountStatus, AgentMessage, FoodListing, NgoProfile
from .routing import road_km, travel_minutes
from .runtime import Agent

PREFIX = "ngo-agent:"


def address(account_id: str) -> str:
    return PREFIX + account_id


class NgoAgents(Agent):
    name = "ngo-agents"
    description = "Software agents of the NGOs: answer calls for proposals with a bid or a refusal."

    def __init__(self, radius_km: float) -> None:
        super().__init__()
        self.radius_km = radius_km

    def bid(self, db: Session, agent_id: str, listing: FoodListing) -> dict:
        from .logistics import agent_position, recent_jobs  # shared helpers

        acc = db.get(Account, agent_id)
        profile = db.get(NgoProfile, agent_id)
        if acc is None or profile is None or acc.status != AccountStatus.VERIFIED:
            return {"decision": "refuse", "reason": "not a verified NGO"}
        if not profile.available:
            return {"decision": "refuse", "reason": "unavailable"}
        if listings.active_assignment_count(db, agent_id) >= listings.MAX_ACTIVE_ASSIGNMENTS_PER_AGENT:
            return {"decision": "refuse", "reason": "busy with another pickup"}
        start, origin = agent_position(db, agent_id, profile)
        km = road_km(start, (listing.latitude, listing.longitude))
        if km / 1.35 > self.radius_km:
            return {"decision": "refuse", "reason": "too far away"}
        t = trust.ngo_trust(db, agent_id)
        return {"decision": "propose", "name": profile.representative_name, "km": round(km, 2),
                "eta_minutes": round(travel_minutes(km), 1), "origin": origin, "trust": t["score"],
                "trust_confidence": t["confidence"], "recent_jobs": recent_jobs(db, agent_id)}

    def handle(self, db: Session, msg: AgentMessage) -> dict | None:
        if msg.topic != "call_for_proposals" or not msg.recipient.startswith(PREFIX):
            return None
        listing = db.get(FoodListing, msg.listing_id)
        agent_id = msg.recipient[len(PREFIX):]
        reply = self.bid(db, agent_id, listing)
        if msg.payload.get("log_replies", True):
            self.bus.send(db, msg.recipient, msg.sender, reply["decision"], listing_id=listing.id,
                          **{k: v for k, v in reply.items() if k != "decision"})
        return reply
