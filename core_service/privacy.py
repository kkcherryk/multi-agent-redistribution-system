"""Phase 10: privacy and data-retention rules.

* Retention: personal operational data is kept only as long as it is useful (see ``RETENTION_DAYS``). A small
  housekeeping agent deletes older records once an hour; the host can also run it from the Privacy tab.
* Export: the host can download everything the system holds about one account (a data-subject request).
* Erasure: the host can erase a restaurant or NGO account. Personal details, documents, locations and
  notifications are removed; food records stay (anonymised) so totals and trust history remain correct.
"""
from __future__ import annotations

import secrets
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from shared.security import hash_password

from .agents.runtime import Agent
from .models import (Account, AccountStatus, AgentLocation, AgentMessage, Assignment, AssignmentStatus,
                     BackupVolunteer, Document, Event, FoodListing, ListingStatus, LocationPing, NgoProfile,
                     Notification, Recipient, RestaurantProfile, Review, StorageItem, TrustNote, utcnow)
from .onboarding import profile_dict
from .schemas import AccountOut

RETENTION_DAYS = {
    "location_pings": 7,          # movement trail of a pickup
    "stale_locations": 1,         # last known position of an agent who stopped sharing
    "read_notifications": 30,
    "unread_notifications": 90,
    "agent_messages": 90,         # agent-to-agent audit trail
    "sign_in_events": 90,         # auth.login / auth.failed
}
RULES = [
    "Phone numbers are shared only between the restaurant and the NGO agent on a pickup, and only while that "
    "pickup is in progress. The host always sees them.",
    "An exact restaurant address is shown to NGO agents only for food they are allowed to see (nearby, verified).",
    "Live location is shared only while the agent is available or on a pickup, and stops when they go off duty.",
    "Failed sign-ins record only the email domain, never the full address or the password.",
    "Uploaded documents are readable only by their owner and the host.",
]
ACTIVE_JOBS = (AssignmentStatus.ACCEPTED, AssignmentStatus.EN_ROUTE, AssignmentStatus.COLLECTED)


def purge(db: Session) -> dict:
    """Delete records older than their retention period. Returns how many rows went from each table."""
    now = utcnow()
    days = lambda k: now - timedelta(days=RETENTION_DAYS[k])  # noqa: E731
    out = {
        "location_pings": db.execute(delete(LocationPing).where(LocationPing.created_at < days("location_pings"))),
        "stale_locations": db.execute(delete(AgentLocation).where(AgentLocation.updated_at < days("stale_locations"))),
        "read_notifications": db.execute(delete(Notification).where(
            Notification.read_at.is_not(None), Notification.created_at < days("read_notifications"))),
        "unread_notifications": db.execute(delete(Notification).where(
            Notification.read_at.is_(None), Notification.created_at < days("unread_notifications"))),
        "agent_messages": db.execute(delete(AgentMessage).where(AgentMessage.created_at < days("agent_messages"))),
        "sign_in_events": db.execute(delete(Event).where(Event.type.in_(("auth.login", "auth.failed")),
                                                         Event.created_at < days("sign_in_events"))),
    }
    return {k: v.rowcount or 0 for k, v in out.items()}


class HousekeepingAgent(Agent):
    """Runs the retention rules once an hour."""

    name = "housekeeping"
    description = "Deletes old location trails, notifications, sign-in records and agent messages (retention rules)."
    EVERY = timedelta(hours=1)

    def __init__(self) -> None:
        super().__init__()
        self.last_purge = None
        self.last_result: dict | None = None

    def tick(self, db: Session) -> None:
        if self.last_purge is None or utcnow() - self.last_purge >= self.EVERY:
            self.last_result = purge(db)
            self.last_purge = utcnow()


# ---------------------------------------------------------------- export & erasure

def _rows(objs) -> list[dict]:
    out = []
    for o in objs:
        out.append({c.name: getattr(o, c.name) for c in o.__table__.columns if c.name != "password_hash"})
    return out


def export_account(db: Session, account: Account) -> dict:
    aid = account.id
    profile_model = {"restaurant": RestaurantProfile, "ngo": NgoProfile}.get(account.role)
    profile = db.get(profile_model, aid) if profile_model else None
    data = {
        "exported_at": utcnow(),
        "account": AccountOut.model_validate(account).model_dump(),
        "profile": profile_dict(profile) if profile else None,
        "documents": [{k: v for k, v in d.items() if k != "storage_key"}
                      for d in _rows(db.scalars(select(Document).where(Document.account_id == aid)))],
        "reviews": _rows(db.scalars(select(Review).where(Review.account_id == aid))),
        "trust_notes": _rows(db.scalars(select(TrustNote).where(TrustNote.account_id == aid))),
        "notifications": _rows(db.scalars(select(Notification).where(Notification.account_id == aid))),
        "events": _rows(db.scalars(select(Event).where(or_(Event.actor_id == aid, Event.subject_id == aid))
                                   .order_by(Event.id).limit(2000))),
    }
    if account.role == "restaurant":
        data["food_listings"] = _rows(db.scalars(select(FoodListing).where(FoodListing.restaurant_id == aid)))
    if account.role == "ngo":
        data["pickups"] = _rows(db.scalars(select(Assignment).where(Assignment.agent_id == aid)))
        data["recipients"] = _rows(db.scalars(select(Recipient).where(Recipient.ngo_id == aid)))
        data["storage"] = _rows(db.scalars(select(StorageItem).where(StorageItem.ngo_id == aid)))
        loc = db.get(AgentLocation, aid)
        data["current_location"] = _rows([loc])[0] if loc else None
        data["location_pings"] = _rows(db.scalars(select(LocationPing).where(LocationPing.agent_id == aid)))
    return data


def erase_account(db: Session, account: Account, *, upload_dir: Path, host_id: str | None) -> None:
    if account.role not in ("restaurant", "ngo"):
        raise HTTPException(status_code=422, detail="only restaurant and NGO accounts can be erased here")
    if account.email.endswith("@erased.invalid"):
        raise HTTPException(status_code=409, detail="this account is already erased")
    aid = account.id
    busy = (db.scalar(select(FoodListing.id).where(
                FoodListing.restaurant_id == aid,
                FoodListing.status.in_((ListingStatus.ACTIVE, *ListingStatus.IN_PROGRESS))).limit(1))
            or db.scalar(select(Assignment.id).where(Assignment.agent_id == aid,
                                                     Assignment.status.in_(ACTIVE_JOBS)).limit(1))
            or db.scalar(select(StorageItem.id).where(StorageItem.ngo_id == aid,
                                                      StorageItem.status == "in_stock").limit(1)))
    if busy:
        raise HTTPException(status_code=409, detail="finish or cancel this account's open food, pickups and stored "
                                                    "food before erasing it")
    for doc in db.scalars(select(Document).where(Document.account_id == aid)):
        (upload_dir / doc.storage_key).unlink(missing_ok=True)
    for model, col in ((Document, Document.account_id), (AgentLocation, AgentLocation.agent_id),
                       (LocationPing, LocationPing.agent_id), (Notification, Notification.account_id),
                       (BackupVolunteer, BackupVolunteer.agent_id), (TrustNote, TrustNote.account_id)):
        db.execute(delete(model).where(col == aid))
    rp = db.get(RestaurantProfile, aid)
    if rp:
        rp.applicant_name, rp.licence_number, rp.additional_info = "Erased", "erased", None
    np_ = db.get(NgoProfile, aid)
    if np_:
        np_.representative_name, np_.ngo_registration_id, np_.additional_info, np_.available = \
            "Erased", "erased", None, False
    for rec in db.scalars(select(Recipient).where(Recipient.ngo_id == aid)):
        rec.contact_phone = None
    account.email = f"erased-{aid}@erased.invalid"
    account.display_name = "Erased account"
    account.phone = None
    account.password_hash = hash_password(secrets.token_urlsafe(32))  # nobody knows it: sign-in is impossible
    account.status = AccountStatus.DECLINED
    from .accounts import record_event
    record_event(db, "account.erased", portal="admin", actor_id=host_id, subject_id=aid)


class EraseIn(BaseModel):
    confirm: str = Field(max_length=20)       # must be "ERASE"
    host_id: str | None = Field(default=None, max_length=32)


def build_router(*, get_db, require_admin, upload_dir: Path) -> APIRouter:
    r = APIRouter(prefix="/internal/privacy")

    def account_or_404(db: Session, account_id: str) -> Account:
        acc = db.get(Account, account_id)
        if acc is None:
            raise HTTPException(status_code=404, detail="account not found")
        return acc

    @r.get("")
    def policy(_: str = Depends(require_admin)):
        return {"retention_days": RETENTION_DAYS, "rules": RULES}

    @r.post("/purge")
    def run_purge(_: str = Depends(require_admin), db: Session = Depends(get_db)):
        result = purge(db)
        db.commit()
        return {"deleted": result}

    @r.get("/accounts/{account_id}/export")
    def export(account_id: str, _: str = Depends(require_admin), db: Session = Depends(get_db)):
        return export_account(db, account_or_404(db, account_id))

    @r.post("/accounts/{account_id}/erase")
    def erase(account_id: str, body: EraseIn, _: str = Depends(require_admin), db: Session = Depends(get_db)):
        if body.confirm != "ERASE":
            raise HTTPException(status_code=422, detail='type ERASE to confirm')
        erase_account(db, account_or_404(db, account_id), upload_dir=upload_dir, host_id=body.host_id)
        db.commit()
        return {"erased": True}

    return r
