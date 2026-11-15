"""Phase 10: in-app notifications.

Most notifications are derived from the event log: ``accounts.record_event`` calls ``on_event`` for every event,
and the table below decides who (if anyone) is told. "New food nearby" alerts are created by
``listings.notify_nearby`` directly, because it knows which agents were alerted.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from shared.config import PORTAL_ROLE

from .models import Account, Assignment, Event, FoodListing, Notification, utcnow

MAX_LIST = 50


def notify(db: Session, account_id: str, kind: str, title: str, body: str | None = None,
           listing_id: str | None = None) -> Notification:
    n = Notification(account_id=account_id, kind=kind, title=title[:160], body=(body or None) and body[:500],
                     listing_id=listing_id)
    db.add(n)
    return n


def notify_hosts(db: Session, kind: str, title: str, body: str | None = None, listing_id: str | None = None) -> None:
    for host_id in db.scalars(select(Account.id).where(Account.role == "host")):
        notify(db, host_id, kind, title, body, listing_id)


def _food(listing: FoodListing) -> str:
    qty = f"{listing.quantity:g} {listing.unit}"
    return f"{listing.food_type} ({qty})"


def _name(db: Session, account_id: str | None) -> str:
    acc = db.get(Account, account_id) if account_id else None
    return acc.display_name if acc else "An NGO"


REVIEW_MESSAGES = {
    "verify": ("account_verified", "Your account is approved",
               "You are now verified. You can start using the portal."),
    "hold": ("account_on_hold", "Your application needs changes",
             "The host asked for more information. Open your application to see the note and resubmit."),
    "decline": ("account_declined", "Your application was declined",
                "Open your application to read the host's note."),
}


def on_event(db: Session, ev: Event) -> None:
    """Turn an event into notifications for the people it concerns. Unknown events are ignored."""
    t, p = ev.type, ev.payload or {}
    if t.startswith("review.") and ev.subject_id:
        kind, title, body = REVIEW_MESSAGES.get(t.split(".", 1)[1], (None, None, None))
        if kind:
            notify(db, ev.subject_id, kind, title, body)
        return
    if t == "application.submitted":
        notify_hosts(db, "application_submitted", "New application to review", _name(db, ev.subject_id))
        return

    listing = db.get(FoodListing, ev.subject_id) if ev.subject_id else None
    if listing is None:
        return
    food, rid = _food(listing), listing.restaurant_id
    if t == "listing.accepted":
        notify(db, rid, "pickup_accepted", f"{_name(db, ev.actor_id)} will collect your food", food, listing.id)
    elif t == "pickup.en_route":
        notify(db, rid, "pickup_en_route", f"{_name(db, ev.actor_id)} is on the way", food, listing.id)
    elif t == "pickup.collected":
        notify(db, rid, "pickup_collected", "Your food was collected", food, listing.id)
    elif t == "pickup.rejected":
        notify(db, rid, "pickup_rejected", "Your food did not pass the pickup check", food, listing.id)
    elif t == "pickup.released":
        notify(db, rid, "pickup_released", "The NGO gave back your pickup",
               f"{food} is being offered to other NGOs again.", listing.id)
    elif t == "pickup.expired":
        notify(db, ev.actor_id, "pickup_expired", "A pickup you accepted expired before collection", food, listing.id)
    elif t == "listing.expired":
        notify(db, rid, "listing_expired", "Your food expired before anyone collected it", food, listing.id)
    elif t == "food.delivered":
        notify(db, rid, "food_delivered", "Your food reached people", food, listing.id)
    elif t == "food.stored":
        notify(db, rid, "food_stored", "Your food was stored safely at the NGO", food, listing.id)
    elif t == "listing.cancelled":
        a = db.scalar(select(Assignment).where(Assignment.listing_id == listing.id))
        if a is not None:
            notify(db, a.agent_id, "listing_cancelled", "The restaurant cancelled a pickup", food, listing.id)
    elif t == "agent.escalation" and p.get("stage") == "backup_requested" and p.get("volunteer_id"):
        notify(db, p["volunteer_id"], "backup_requested", "Backup collection requested",
               f"{food} needs a collector. Please answer within 10 minutes.", listing.id)
    elif t == "agent.escalation" and p.get("stage") == "needs_host":
        notify_hosts(db, "needs_host", "Food needs your attention", f"No collector found for {food}.", listing.id)


def notification_dict(n: Notification) -> dict:
    return {"id": n.id, "kind": n.kind, "title": n.title, "body": n.body, "listing_id": n.listing_id,
            "read": n.read_at is not None, "created_at": n.created_at}


class ReadIn(BaseModel):
    ids: list[int] | None = Field(default=None, max_length=200)  # None = mark all as read


def build_router(*, get_db, caller_portal) -> APIRouter:
    r = APIRouter(prefix="/internal/accounts/{account_id}/notifications")

    def owner(db: Session, account_id: str, portal: str) -> Account:
        acc = db.get(Account, account_id)
        if acc is None or acc.role != PORTAL_ROLE[portal]:  # each portal reads only its own role's inboxes
            raise HTTPException(status_code=404, detail="account not found")
        return acc

    @r.get("")
    def inbox(account_id: str, limit: int = Query(default=20, ge=1, le=MAX_LIST),
              portal: str = Depends(caller_portal), db: Session = Depends(get_db)):
        owner(db, account_id, portal)
        q = select(Notification).where(Notification.account_id == account_id)
        unread = len(list(db.scalars(q.where(Notification.read_at.is_(None)).with_only_columns(Notification.id))))
        rows = db.scalars(q.order_by(Notification.id.desc()).limit(limit))
        return {"unread": unread, "items": [notification_dict(n) for n in rows]}

    @r.post("/read")
    def mark_read(account_id: str, body: ReadIn, portal: str = Depends(caller_portal), db: Session = Depends(get_db)):
        owner(db, account_id, portal)
        stmt = (update(Notification).where(Notification.account_id == account_id, Notification.read_at.is_(None))
                .values(read_at=utcnow()))
        if body.ids is not None:
            stmt = stmt.where(Notification.id.in_(body.ids))
        db.execute(stmt)
        db.commit()
        return inbox(account_id, 20, portal, db)

    return r
