"""Backup / storage collection agent (spec Module 11).

Watches open food and escalates when it is at risk of not being collected:

    at risk  ->  widened (alert agents up to EXTENDED_RADIUS_KM)
             ->  backup_requested (offer to the nearest opted-in backup NGO, one at a time)
             ->  needs_host (nobody left to ask)

Food is "at risk" when nobody accepted it within NO_ACCEPT_MINUTES, when no agent could be alerted within
NO_AGENT_MINUTES, or when less than EXPIRY_RISK_MINUTES remain. A backup collector collects the food and stores it
at their NGO for later distribution. Escalation ends as soon as anyone accepts the food, or it closes.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import listings
from ..accounts import record_event
from ..models import (AgentMessage, BackupRequest, FoodListing, ListingEscalation, ListingNotification,
                      ListingStatus, utcnow)
from .runtime import Agent

NO_ACCEPT_MINUTES = 20
NO_AGENT_MINUTES = 5
EXPIRY_RISK_MINUTES = 60
EXTENDED_RADIUS_KM = 10.0
WIDEN_WAIT_MINUTES = 10        # how long the widened alert gets before backup is requested
BACKUP_RESPONSE_MINUTES = 10   # how long a backup volunteer has to answer


def _minutes(delta: timedelta) -> float:
    return delta.total_seconds() / 60.0


class BackupAgent(Agent):
    name = "backup"
    description = "Escalates food nobody is collecting: wider alerts, then backup collection to NGO storage."

    # ---------------------------------------------------------------- risk assessment
    def risk_reasons(self, db: Session, listing: FoodListing) -> list[str]:
        now = utcnow()
        age, left = _minutes(now - listing.created_at), _minutes(listing.expires_at - now)
        notified = db.scalar(select(func.count()).select_from(ListingNotification)
                             .where(ListingNotification.listing_id == listing.id)) or 0
        reasons = []
        if age >= NO_ACCEPT_MINUTES:
            reasons.append(f"not accepted after {age:.0f} min")
        if notified == 0 and age >= NO_AGENT_MINUTES:
            reasons.append("no available NGO agent nearby")
        if left <= EXPIRY_RISK_MINUTES:
            reasons.append(f"only {max(left, 0):.0f} min left before expiry")
        return reasons

    # ---------------------------------------------------------------- escalation steps
    def _widen(self, db: Session, listing: FoodListing, esc: ListingEscalation | None, reasons: list[str]) -> int:
        sent = listings.notify_nearby(db, listing, EXTENDED_RADIUS_KM)
        reason = "; ".join(reasons)
        if esc is None:
            esc = ListingEscalation(listing_id=listing.id, stage="widened", reason=reason)
            db.add(esc)
        else:
            esc.stage, esc.reason, esc.updated_at = "widened", reason, utcnow()
        db.flush()
        self.send(db, "host", "food_at_risk", listing_id=listing.id, reasons=reasons, action="widened_alert",
                  radius_km=EXTENDED_RADIUS_KM, newly_alerted=sent)
        record_event(db, "agent.escalation", subject_id=listing.id, stage="widened", reasons=reasons)
        return sent

    def _offer_next(self, db: Session, listing: FoodListing, esc: ListingEscalation, why: str) -> BackupRequest | None:
        asked = set(db.scalars(select(BackupRequest.volunteer_id).where(BackupRequest.listing_id == listing.id)))
        reply = self.send(db, "logistics", "rank_backup", listing_id=listing.id, exclude=sorted(asked)) or {}
        candidates = reply.get("candidates", [])
        if not candidates:
            esc.stage, esc.reason, esc.updated_at = "needs_host", f"{why}; no backup collector available", utcnow()
            self.send(db, "host", "needs_host_attention", listing_id=listing.id, reason=esc.reason)
            record_event(db, "agent.escalation", subject_id=listing.id, stage="needs_host")
            return None
        best = candidates[0]
        req = BackupRequest(listing_id=listing.id, volunteer_id=best["agent_id"], distance_km=best["km"],
                            eta_minutes=best["eta"])
        db.add(req)
        esc.stage, esc.reason, esc.updated_at = "backup_requested", why, utcnow()
        db.flush()
        self.send(db, best["agent_id"], "backup_request", listing_id=listing.id, request_id=req.id,
                  eta_minutes=best["eta"], respond_within_minutes=BACKUP_RESPONSE_MINUTES)
        self.send(db, "host", "backup_requested", listing_id=listing.id, volunteer_id=best["agent_id"],
                  eta_minutes=best["eta"], reason=why)
        record_event(db, "agent.escalation", subject_id=listing.id, stage="backup_requested",
                     volunteer_id=best["agent_id"])
        return req

    def escalate_now(self, db: Session, listing: FoodListing, by: str) -> ListingEscalation:
        """Host override: skip waiting and request a backup collector immediately."""
        if listing.status != ListingStatus.ACTIVE:
            raise ValueError("only open food can be escalated")
        esc = db.get(ListingEscalation, listing.id)
        if esc is None:
            esc = ListingEscalation(listing_id=listing.id, stage="widened", reason=f"escalated by {by}")
            db.add(esc)
            db.flush()
        open_req = db.scalar(select(BackupRequest).where(BackupRequest.listing_id == listing.id,
                                                         BackupRequest.status == "offered"))
        if open_req is None:
            self._offer_next(db, listing, esc, f"escalated by {by}")
        return esc

    def on_declined(self, db: Session, request: BackupRequest) -> None:
        listing = db.get(FoodListing, request.listing_id)
        esc = db.get(ListingEscalation, request.listing_id)
        if listing is not None and esc is not None and listing.status == ListingStatus.ACTIVE:
            self._offer_next(db, listing, esc, "previous backup collector declined")

    # ---------------------------------------------------------------- periodic round
    def tick(self, db: Session) -> None:
        listings.expire_due(db)
        now = utcnow()
        # 1. Close escalations whose food is no longer open, and withdraw their open backup offers.
        for esc in db.scalars(select(ListingEscalation).where(ListingEscalation.stage != "resolved")):
            listing = db.get(FoodListing, esc.listing_id)
            if listing is None or listing.status != ListingStatus.ACTIVE:
                esc.stage, esc.updated_at = "resolved", now
                for req in db.scalars(select(BackupRequest).where(BackupRequest.listing_id == esc.listing_id,
                                                                  BackupRequest.status == "offered")):
                    req.status, req.responded_at = "cancelled", now
                self.send(db, "host", "escalation_resolved", listing_id=esc.listing_id,
                          outcome=listing.status if listing else "deleted")
        # 2. Assess every open listing.
        for listing in db.scalars(select(FoodListing).where(FoodListing.status == ListingStatus.ACTIVE)):
            esc = db.get(ListingEscalation, listing.id)
            if esc is None:
                reasons = self.risk_reasons(db, listing)
                if reasons:
                    newly = self._widen(db, listing, None, reasons)
                    esc = db.get(ListingEscalation, listing.id)
                    if newly == 0 and listings.eligible_agents(db, listing, EXTENDED_RADIUS_KM) == []:
                        self._offer_next(db, listing, esc, "; ".join(reasons) + "; nobody available within 10 km")
                continue
            if esc.stage == "widened":
                waited = _minutes(now - esc.updated_at)
                left = _minutes(listing.expires_at - now)
                if waited >= WIDEN_WAIT_MINUTES or left <= EXPIRY_RISK_MINUTES - 15:
                    self._offer_next(db, listing, esc, esc.reason)
            elif esc.stage == "backup_requested":
                req = db.scalar(select(BackupRequest).where(BackupRequest.listing_id == listing.id,
                                                            BackupRequest.status == "offered"))
                if req is None:
                    self._offer_next(db, listing, esc, "previous backup collector did not take it")
                elif _minutes(now - req.created_at) >= BACKUP_RESPONSE_MINUTES:
                    req.status, req.responded_at = "timed_out", now
                    self.send(db, "host", "backup_timed_out", listing_id=listing.id, volunteer_id=req.volunteer_id)
                    self._offer_next(db, listing, esc, "previous backup collector did not respond")

    def handle(self, db: Session, msg: AgentMessage) -> dict | None:
        return None
