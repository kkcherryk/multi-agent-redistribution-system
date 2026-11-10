"""Pickup, inspection, delivery and temporary storage (spec Modules 8 and 9)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .accounts import record_event
from .listings import notify_nearby
from .models import (Account, Assignment, AssignmentStatus, FoodListing, Handover, Inspection, ListingStatus,
                     NgoProfile, Recipient, StorageItem, utcnow)
from .onboarding import RuleError

RECIPIENT_KINDS = ("orphanage", "shelter", "old_age_home", "community_kitchen", "slum_community", "other")
OBSERVED_CONDITIONS = ("fresh", "good", "fair", "spoiled")


def _assignment_for(db: Session, agent: Account, listing_id: str) -> tuple[FoodListing, Assignment]:
    listing = db.get(FoodListing, listing_id)
    assignment = db.scalar(select(Assignment).where(Assignment.listing_id == listing_id))
    if listing is None or assignment is None or assignment.agent_id != agent.id:
        raise RuleError("pickup not found", 404)
    return listing, assignment


def _require(assignment: Assignment, *allowed: str, action: str) -> None:
    if assignment.status not in allowed:
        raise RuleError(f"cannot {action} while the pickup is '{assignment.status}'")


def _not_expired(listing: FoodListing, action: str) -> None:
    if listing.expires_at <= utcnow():
        raise RuleError(f"this food passed its best-before time; it cannot be {action} — record it as disposed", 409)


# ---------------------------------------------------------------- before collection

def start_trip(db: Session, agent: Account, listing_id: str) -> Assignment:
    listing, a = _assignment_for(db, agent, listing_id)
    _require(a, AssignmentStatus.ACCEPTED, action="start the trip")
    a.status = AssignmentStatus.EN_ROUTE
    record_event(db, "pickup.en_route", portal="ngo", actor_id=agent.id, subject_id=listing.id)
    return a


def release(db: Session, agent: Account, listing_id: str, reason: str | None, radius_km: float) -> FoodListing:
    """The agent gives the pickup back before collecting. The food is offered to other nearby agents again."""
    listing, a = _assignment_for(db, agent, listing_id)
    _require(a, AssignmentStatus.ACCEPTED, AssignmentStatus.EN_ROUTE, action="give back the pickup")
    db.delete(a)
    db.flush()
    expired = listing.expires_at <= utcnow()
    listing.status = ListingStatus.EXPIRED if expired else ListingStatus.ACTIVE
    record_event(db, "pickup.released", portal="ngo", actor_id=agent.id, subject_id=listing.id,
                 reason=(reason or "").strip()[:300] or None, reoffered=not expired)
    if not expired:
        notify_nearby(db, listing, radius_km)
    return listing


# ---------------------------------------------------------------- inspection (Module 8)

def inspect(db: Session, agent: Account, listing_id: str, *, decision: str, actual_quantity: float | None,
            observed_condition: str, notes: str | None) -> Inspection:
    listing, a = _assignment_for(db, agent, listing_id)
    _require(a, AssignmentStatus.ACCEPTED, AssignmentStatus.EN_ROUTE, action="record the food check")
    if observed_condition not in OBSERVED_CONDITIONS:
        raise RuleError("condition must be fresh, good, fair or spoiled", 422)
    notes = (notes or "").strip() or None
    if decision == "reject":
        if not notes:
            raise RuleError("give a reason when rejecting food", 422)
        outcome, qty = "rejected", float(actual_quantity or 0)
        a.status, listing.status = AssignmentStatus.REJECTED, ListingStatus.REJECTED
    elif decision == "collect":
        if observed_condition == "spoiled":
            raise RuleError("spoiled food cannot be collected — reject it instead", 422)
        _not_expired(listing, "collected")
        if actual_quantity is None or actual_quantity <= 0:
            raise RuleError("enter the quantity you actually collected", 422)
        if actual_quantity > listing.quantity:
            raise RuleError(f"collected quantity cannot exceed the listed {listing.quantity:g} {listing.unit}", 422)
        qty = float(actual_quantity)
        outcome = "full" if qty == listing.quantity else "partial"
        if outcome == "partial" and not notes:
            raise RuleError("add a note explaining the quantity difference", 422)
        a.status, listing.status = AssignmentStatus.COLLECTED, ListingStatus.COLLECTED
    else:
        raise RuleError("decision must be collect or reject", 422)
    rec = Inspection(listing_id=listing.id, agent_id=agent.id, outcome=outcome, listed_quantity=listing.quantity,
                     actual_quantity=qty, observed_condition=observed_condition, notes=notes)
    db.add(rec)
    record_event(db, f"pickup.{'rejected' if outcome == 'rejected' else 'collected'}", portal="ngo", actor_id=agent.id,
                 subject_id=listing.id, outcome=outcome, listed=listing.quantity, actual=qty)
    db.flush()
    return rec


def _collected_qty(db: Session, listing_id: str) -> float:
    return db.scalar(select(Inspection.actual_quantity).where(Inspection.listing_id == listing_id)) or 0.0


# ---------------------------------------------------------------- handover (Module 9)

def _recipient(db: Session, agent: Account, recipient_id: str) -> Recipient:
    r = db.get(Recipient, recipient_id)
    if r is None or r.ngo_id != agent.id:
        raise RuleError("recipient not found", 404)
    return r


def deliver(db: Session, agent: Account, listing_id: str, *, recipient_id: str, received_by: str,
            notes: str | None) -> Handover:
    listing, a = _assignment_for(db, agent, listing_id)
    _require(a, AssignmentStatus.COLLECTED, action="deliver")
    _not_expired(listing, "delivered")
    recipient = _recipient(db, agent, recipient_id)
    received_by = (received_by or "").strip()
    if len(received_by) < 2:
        raise RuleError("enter the name of the person who received the food", 422)
    qty = _collected_qty(db, listing.id)
    h = Handover(listing_id=listing.id, agent_id=agent.id, kind="delivered", quantity=qty, recipient_id=recipient.id,
                 received_by=received_by[:120], notes=(notes or "").strip() or None)
    db.add(h)
    a.status, listing.status = AssignmentStatus.DELIVERED, ListingStatus.DELIVERED
    record_event(db, "food.delivered", portal="ngo", actor_id=agent.id, subject_id=listing.id,
                 recipient=recipient.name, quantity=qty, unit=listing.unit)
    db.flush()
    return h


def store(db: Session, agent: Account, listing_id: str, *, location: str | None, notes: str | None) -> StorageItem:
    listing, a = _assignment_for(db, agent, listing_id)
    _require(a, AssignmentStatus.COLLECTED, action="store")
    _not_expired(listing, "stored")
    profile = db.get(NgoProfile, agent.id)
    qty = _collected_qty(db, listing.id)
    item = StorageItem(ngo_id=agent.id, listing_id=listing.id, food_type=listing.food_type, unit=listing.unit,
                       stored_quantity=qty, remaining_quantity=qty, expires_at=listing.expires_at,
                       location=((location or "").strip() or (profile.address if profile else "NGO"))[:300])
    db.add(item)
    db.flush()
    db.add(Handover(listing_id=listing.id, agent_id=agent.id, kind="stored", quantity=qty, storage_item_id=item.id,
                    notes=(notes or "").strip() or None))
    a.status, listing.status = AssignmentStatus.STORED, ListingStatus.STORED
    record_event(db, "food.stored", portal="ngo", actor_id=agent.id, subject_id=listing.id, quantity=qty,
                 location=item.location)
    db.flush()
    return item


def dispose_pickup(db: Session, agent: Account, listing_id: str, reason: str) -> FoodListing:
    listing, a = _assignment_for(db, agent, listing_id)
    _require(a, AssignmentStatus.COLLECTED, action="dispose")
    reason = (reason or "").strip()
    if not reason:
        raise RuleError("give a reason for disposal", 422)
    db.add(Handover(listing_id=listing.id, agent_id=agent.id, kind="disposed", quantity=_collected_qty(db, listing.id),
                    notes=reason[:500]))
    a.status, listing.status = AssignmentStatus.DISPOSED, ListingStatus.DISPOSED
    record_event(db, "food.disposed", portal="ngo", actor_id=agent.id, subject_id=listing.id, reason=reason[:300])
    return listing


# ---------------------------------------------------------------- storage inventory

def _storage_item(db: Session, agent: Account, item_id: str) -> StorageItem:
    item = db.get(StorageItem, item_id)
    if item is None or item.ngo_id != agent.id:
        raise RuleError("storage item not found", 404)
    if item.status != "in_stock":
        raise RuleError(f"this stock is already {item.status}")
    return item


def distribute(db: Session, agent: Account, item_id: str, *, recipient_id: str, quantity: float,
               received_by: str, notes: str | None) -> Handover:
    item = _storage_item(db, agent, item_id)
    if item.expires_at <= utcnow():
        raise RuleError("this stock is past its best-before time — record it as disposed", 409)
    recipient = _recipient(db, agent, recipient_id)
    if quantity is None or quantity <= 0 or quantity > item.remaining_quantity + 1e-9:
        raise RuleError(f"quantity must be between 0 and the {item.remaining_quantity:g} {item.unit} in stock", 422)
    received_by = (received_by or "").strip()
    if len(received_by) < 2:
        raise RuleError("enter the name of the person who received the food", 422)
    item.remaining_quantity = round(item.remaining_quantity - quantity, 3)
    if item.remaining_quantity <= 0:
        item.remaining_quantity, item.status = 0.0, "distributed"
    h = Handover(listing_id=item.listing_id, agent_id=agent.id, kind="distributed", quantity=quantity,
                 recipient_id=recipient.id, storage_item_id=item.id, received_by=received_by[:120],
                 notes=(notes or "").strip() or None)
    db.add(h)
    record_event(db, "food.distributed", portal="ngo", actor_id=agent.id, subject_id=item.listing_id,
                 recipient=recipient.name, quantity=quantity, remaining=item.remaining_quantity)
    db.flush()
    return h


def dispose_stock(db: Session, agent: Account, item_id: str, reason: str) -> StorageItem:
    item = _storage_item(db, agent, item_id)
    reason = (reason or "").strip()
    if not reason:
        raise RuleError("give a reason for disposal", 422)
    db.add(Handover(listing_id=item.listing_id, agent_id=agent.id, kind="disposed", quantity=item.remaining_quantity,
                    storage_item_id=item.id, notes=reason[:500]))
    record_event(db, "food.disposed", portal="ngo", actor_id=agent.id, subject_id=item.listing_id,
                 quantity=item.remaining_quantity, reason=reason[:300], from_storage=True)
    item.remaining_quantity, item.status = 0.0, "disposed"
    return item


# ---------------------------------------------------------------- views

def recipient_dict(r: Recipient) -> dict:
    return {"id": r.id, "name": r.name, "kind": r.kind, "address": r.address, "latitude": r.latitude,
            "longitude": r.longitude, "contact_phone": r.contact_phone}


def storage_dict(item: StorageItem) -> dict:
    return {"id": item.id, "listing_id": item.listing_id, "food_type": item.food_type, "unit": item.unit,
            "stored_quantity": item.stored_quantity, "remaining_quantity": item.remaining_quantity,
            "location": item.location, "expires_at": item.expires_at, "status": item.status,
            "expired": item.status == "in_stock" and item.expires_at <= utcnow(), "stored_at": item.stored_at}


def fulfilment_view(db: Session, listing_id: str) -> dict:
    """Inspection and handover history for a listing (shown to restaurant, collecting agent and host)."""
    insp = db.scalar(select(Inspection).where(Inspection.listing_id == listing_id))
    handovers = []
    for h in db.scalars(select(Handover).where(Handover.listing_id == listing_id).order_by(Handover.id)):
        r = db.get(Recipient, h.recipient_id) if h.recipient_id else None
        handovers.append({"kind": h.kind, "quantity": h.quantity, "received_by": h.received_by, "notes": h.notes,
                          "recipient": {"name": r.name, "kind": r.kind} if r else None, "created_at": h.created_at})
    return {
        "inspection": None if insp is None else {
            "outcome": insp.outcome, "listed_quantity": insp.listed_quantity, "actual_quantity": insp.actual_quantity,
            "observed_condition": insp.observed_condition, "notes": insp.notes, "created_at": insp.created_at},
        "handovers": handovers,
    }
