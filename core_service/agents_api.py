"""Core API routes for Phase 6: agent system status, backup collections, pickup routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import listings
from .agents.runtime import AgentRuntime
from .models import (Account, AgentMessage, Assignment, BackupRequest, BackupVolunteer, FoodListing,
                     ListingEscalation, utcnow)
from .onboarding import RuleError


class BackupOptIn(BaseModel):
    active: bool
    max_distance_km: float = Field(default=10.0, ge=1, le=30)


def build_router(*, get_db, caller_portal, require_admin, runtime: AgentRuntime, pickup_view) -> APIRouter:
    r = APIRouter(prefix="/internal")
    logistics = runtime.get("logistics")
    backup = runtime.get("backup")

    def agent(db: Session, agent_id: str, portal: str) -> Account:
        acc = db.get(Account, agent_id)
        if portal != "ngo" or acc is None or acc.role != "ngo":
            raise HTTPException(status_code=403, detail="this action belongs to the NGO portal")
        return acc

    def request_for(db: Session, request_id: str, agent_id: str) -> BackupRequest:
        req = db.get(BackupRequest, request_id)
        if req is None or req.volunteer_id != agent_id:
            raise HTTPException(status_code=404, detail="backup request not found")
        if req.status != "offered":
            raise RuleError(f"this backup request is already {req.status}")
        return req

    # -------------------------------------------------------------- NGO: backup collector opt-in and requests
    @r.get("/agents/{agent_id}/backup")
    def get_backup(agent_id: str, portal: str = Depends(caller_portal), db: Session = Depends(get_db)):
        agent(db, agent_id, portal)
        vol = db.get(BackupVolunteer, agent_id)
        return {"volunteer": None if vol is None else {"active": vol.active, "max_distance_km": vol.max_distance_km}}

    @r.put("/agents/{agent_id}/backup")
    def put_backup(agent_id: str, body: BackupOptIn, portal: str = Depends(caller_portal),
                   db: Session = Depends(get_db)):
        acc = agent(db, agent_id, portal)
        listings.require_verified_agent(db, acc)
        vol = db.get(BackupVolunteer, agent_id)
        if vol is None:
            vol = BackupVolunteer(agent_id=agent_id)
            db.add(vol)
        vol.active, vol.max_distance_km = body.active, body.max_distance_km
        db.commit()
        return {"volunteer": {"active": vol.active, "max_distance_km": vol.max_distance_km}}

    @r.get("/agents/{agent_id}/backup-requests")
    def backup_requests(agent_id: str, portal: str = Depends(caller_portal), db: Session = Depends(get_db)):
        agent(db, agent_id, portal)
        out = []
        for req in db.scalars(select(BackupRequest).where(BackupRequest.volunteer_id == agent_id,
                                                          BackupRequest.status == "offered")
                              .order_by(BackupRequest.created_at)):
            listing = db.get(FoodListing, req.listing_id)
            out.append({"id": req.id, "distance_km": req.distance_km, "eta_minutes": req.eta_minutes,
                        "created_at": req.created_at, "listing": listings.listing_view(db, listing, audience="ngo")})
        return out

    @r.post("/backup-requests/{request_id}/accept")
    def accept_backup(request_id: str, agent_id: str = Query(...), portal: str = Depends(caller_portal),
                      db: Session = Depends(get_db)):
        acc = agent(db, agent_id, portal)
        req = request_for(db, request_id, agent_id)
        vol = db.get(BackupVolunteer, agent_id)
        limit = vol.max_distance_km if vol else 10.0
        listing_id = req.listing_id
        listings.accept_listing(db, acc, listing_id, limit, portal=portal, require_available=False)
        req = db.get(BackupRequest, request_id)  # accept may have rolled back and reloaded state
        req.status, req.responded_at = "accepted", utcnow()
        esc = db.get(ListingEscalation, listing_id)
        if esc is not None:
            esc.stage, esc.updated_at = "resolved", utcnow()
        backup.send(db, "host", "backup_accepted", listing_id=listing_id, volunteer_id=agent_id)
        db.commit()
        return pickup_view(db, db.get(FoodListing, listing_id))

    @r.post("/backup-requests/{request_id}/decline")
    def decline_backup(request_id: str, agent_id: str = Query(...), portal: str = Depends(caller_portal),
                       db: Session = Depends(get_db)):
        agent(db, agent_id, portal)
        req = request_for(db, request_id, agent_id)
        req.status, req.responded_at = "declined", utcnow()
        db.flush()
        backup.on_declined(db, req)  # offer it to the next backup collector straight away
        db.commit()
        return {"id": req.id, "status": req.status}

    # -------------------------------------------------------------- NGO: route for an accepted pickup
    @r.get("/pickups/{listing_id}/route")
    def route(listing_id: str, agent_id: str = Query(...), portal: str = Depends(caller_portal),
              db: Session = Depends(get_db)):
        agent(db, agent_id, portal)
        listing = db.get(FoodListing, listing_id)
        a = db.scalar(select(Assignment).where(Assignment.listing_id == listing_id))
        if listing is None or a is None or a.agent_id != agent_id:
            raise HTTPException(status_code=404, detail="pickup not found")
        return logistics.pickup_route(db, listing, a)

    # -------------------------------------------------------------- host: agent system oversight
    @r.get("/agent-system")
    def agent_system(_: str = Depends(require_admin), db: Session = Depends(get_db)):
        escalations = []
        for esc in db.scalars(select(ListingEscalation).order_by(ListingEscalation.updated_at.desc()).limit(50)):
            listing = db.get(FoodListing, esc.listing_id)
            rest = db.get(Account, listing.restaurant_id) if listing else None
            reqs = [{"volunteer": (db.get(Account, q.volunteer_id).display_name if db.get(Account, q.volunteer_id) else None),
                     "status": q.status, "eta_minutes": q.eta_minutes, "created_at": q.created_at}
                    for q in db.scalars(select(BackupRequest).where(BackupRequest.listing_id == esc.listing_id)
                                        .order_by(BackupRequest.created_at))]
            escalations.append({"listing_id": esc.listing_id, "stage": esc.stage, "reason": esc.reason,
                                "updated_at": esc.updated_at,
                                "food": f"{listing.food_type} — {listing.quantity:g} {listing.unit}" if listing else None,
                                "restaurant": rest.display_name if rest else None,
                                "listing_status": listing.status if listing else None, "backup_requests": reqs})
        messages = [{"id": m.id, "sender": m.sender, "recipient": m.recipient, "topic": m.topic,
                     "listing_id": m.listing_id, "payload": m.payload, "created_at": m.created_at}
                    for m in db.scalars(select(AgentMessage).order_by(AgentMessage.id.desc()).limit(60))]
        volunteers = len(list(db.scalars(select(BackupVolunteer.agent_id).where(BackupVolunteer.active.is_(True)))))
        return {"agents": runtime.status(), "escalations": escalations, "messages": messages,
                "backup_volunteers": volunteers}

    @r.post("/agent-system/run")
    def run_agents(_: str = Depends(require_admin), db: Session = Depends(get_db)):
        runtime.tick(db)
        return {"agents": runtime.status()}

    @r.post("/listings/{listing_id}/escalate")
    def escalate(listing_id: str, _: str = Depends(require_admin), db: Session = Depends(get_db)):
        listing = db.get(FoodListing, listing_id)
        if listing is None:
            raise HTTPException(status_code=404, detail="listing not found")
        listings.expire_due(db)
        try:
            esc = backup.escalate_now(db, listing, by="host")
        except ValueError as exc:
            raise RuleError(str(exc))
        db.commit()
        return {"listing_id": listing_id, "stage": esc.stage, "reason": esc.reason}

    return r
