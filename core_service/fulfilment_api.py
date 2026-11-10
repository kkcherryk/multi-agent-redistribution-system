"""Core API routes for Phase 4 (pickup, inspection, delivery, storage, recipients)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import fulfilment
from .models import Account, Recipient, StorageItem


class ReasonIn(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class InspectionIn(BaseModel):
    decision: str = Field(max_length=10)  # collect / reject
    actual_quantity: float | None = Field(default=None, ge=0, le=10000)
    observed_condition: str = Field(max_length=20)
    notes: str | None = Field(default=None, max_length=1000)


class DeliverIn(BaseModel):
    recipient_id: str = Field(max_length=32)
    received_by: str = Field(max_length=120)
    notes: str | None = Field(default=None, max_length=1000)


class StoreIn(BaseModel):
    location: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=1000)


class DistributeIn(DeliverIn):
    quantity: float = Field(gt=0, le=10000)


class RecipientIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    kind: str
    address: str = Field(min_length=2, max_length=300)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    contact_phone: str | None = Field(default=None, max_length=20)


def build_router(*, get_db, caller_portal, require_admin, radius_km: float, pickup_view) -> APIRouter:
    r = APIRouter(prefix="/internal")

    def agent(db: Session, agent_id: str, portal: str) -> Account:
        acc = db.get(Account, agent_id)
        if portal != "ngo" or acc is None or acc.role != "ngo":
            raise HTTPException(status_code=403, detail="this action belongs to the NGO portal")
        return acc

    def done(db: Session, listing_id: str) -> dict:
        db.commit()
        from .models import FoodListing
        return pickup_view(db, db.get(FoodListing, listing_id))

    # -------------------------------------------------------------- pickup steps
    @r.post("/pickups/{listing_id}/start")
    def start(listing_id: str, agent_id: str = Query(...), portal: str = Depends(caller_portal),
              db: Session = Depends(get_db)):
        fulfilment.start_trip(db, agent(db, agent_id, portal), listing_id)
        return done(db, listing_id)

    @r.post("/pickups/{listing_id}/release")
    def release(listing_id: str, body: ReasonIn, agent_id: str = Query(...), portal: str = Depends(caller_portal),
                db: Session = Depends(get_db)):
        listing = fulfilment.release(db, agent(db, agent_id, portal), listing_id, body.reason, radius_km)
        db.commit()
        return {"listing_id": listing.id, "status": listing.status}

    @r.post("/pickups/{listing_id}/inspection")
    def inspection(listing_id: str, body: InspectionIn, agent_id: str = Query(...),
                   portal: str = Depends(caller_portal), db: Session = Depends(get_db)):
        fulfilment.inspect(db, agent(db, agent_id, portal), listing_id, decision=body.decision,
                           actual_quantity=body.actual_quantity, observed_condition=body.observed_condition,
                           notes=body.notes)
        return done(db, listing_id)

    @r.post("/pickups/{listing_id}/deliver")
    def deliver(listing_id: str, body: DeliverIn, agent_id: str = Query(...), portal: str = Depends(caller_portal),
                db: Session = Depends(get_db)):
        fulfilment.deliver(db, agent(db, agent_id, portal), listing_id, recipient_id=body.recipient_id,
                           received_by=body.received_by, notes=body.notes)
        return done(db, listing_id)

    @r.post("/pickups/{listing_id}/store")
    def store(listing_id: str, body: StoreIn, agent_id: str = Query(...), portal: str = Depends(caller_portal),
              db: Session = Depends(get_db)):
        fulfilment.store(db, agent(db, agent_id, portal), listing_id, location=body.location, notes=body.notes)
        return done(db, listing_id)

    @r.post("/pickups/{listing_id}/dispose")
    def dispose(listing_id: str, body: ReasonIn, agent_id: str = Query(...), portal: str = Depends(caller_portal),
                db: Session = Depends(get_db)):
        fulfilment.dispose_pickup(db, agent(db, agent_id, portal), listing_id, body.reason or "")
        return done(db, listing_id)

    # -------------------------------------------------------------- recipients
    @r.get("/agents/{agent_id}/recipients")
    def recipients(agent_id: str, portal: str = Depends(caller_portal), db: Session = Depends(get_db)):
        agent(db, agent_id, portal)
        q = select(Recipient).where(Recipient.ngo_id == agent_id).order_by(Recipient.name)
        return [fulfilment.recipient_dict(x) for x in db.scalars(q)]

    @r.post("/agents/{agent_id}/recipients", status_code=201)
    def add_recipient(agent_id: str, body: RecipientIn, portal: str = Depends(caller_portal),
                      db: Session = Depends(get_db)):
        agent(db, agent_id, portal)
        if body.kind not in fulfilment.RECIPIENT_KINDS:
            raise HTTPException(status_code=422, detail="unknown recipient type")
        rec = Recipient(ngo_id=agent_id, **{k: (v.strip() if isinstance(v, str) else v)
                                            for k, v in body.model_dump().items()})
        db.add(rec)
        db.commit()
        return fulfilment.recipient_dict(rec)

    # -------------------------------------------------------------- storage
    @r.get("/agents/{agent_id}/storage")
    def storage(agent_id: str, portal: str = Depends(caller_portal), db: Session = Depends(get_db)):
        agent(db, agent_id, portal)
        q = select(StorageItem).where(StorageItem.ngo_id == agent_id).order_by(StorageItem.expires_at)
        return [fulfilment.storage_dict(x) for x in db.scalars(q)]

    @r.post("/storage/{item_id}/distribute")
    def distribute(item_id: str, body: DistributeIn, agent_id: str = Query(...), portal: str = Depends(caller_portal),
                   db: Session = Depends(get_db)):
        fulfilment.distribute(db, agent(db, agent_id, portal), item_id, recipient_id=body.recipient_id,
                              quantity=body.quantity, received_by=body.received_by, notes=body.notes)
        db.commit()
        return fulfilment.storage_dict(db.get(StorageItem, item_id))

    @r.post("/storage/{item_id}/dispose")
    def dispose_stock(item_id: str, body: ReasonIn, agent_id: str = Query(...), portal: str = Depends(caller_portal),
                      db: Session = Depends(get_db)):
        item = fulfilment.dispose_stock(db, agent(db, agent_id, portal), item_id, body.reason or "")
        db.commit()
        return fulfilment.storage_dict(item)

    @r.get("/storage")
    def all_storage(_: str = Depends(require_admin), db: Session = Depends(get_db)):
        out = []
        for item in db.scalars(select(StorageItem).order_by(StorageItem.expires_at)):
            ngo = db.get(Account, item.ngo_id)
            out.append({**fulfilment.storage_dict(item), "ngo_name": ngo.display_name if ngo else None})
        return out

    return r
