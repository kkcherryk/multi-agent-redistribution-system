"""Trust assessment (spec Module 12) - decision support for the host, never an automatic block.

Each score is a Beta-reputation estimate: score = (successes + 1) / (successes + failures + 2) x 100.
With no history the score is 50; the +1/+2 keep one bad event from wrecking a newcomer's score.
Confidence grows with the number of events. Every score comes with plain-language reasons and review flags.

NGO agents:   success = pickup ended delivered or stored
              failure = pickup given back, accepted but expired before collection, or collected and then disposed
Restaurants:  success = food check found the full quantity
              failure = food rejected at the check; a partial quantity counts as half a failure
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import (Account, AccountStatus, Assignment, Event, FoodListing, Handover, Inspection, ListingStatus,
                     TrustNote, utcnow)

FLAG_WINDOW = timedelta(days=7)
LOW_SCORE = 40


def beta_score(successes: float, failures: float) -> float:
    return round((successes + 1) / (successes + failures + 2) * 100, 1)


def confidence(events: float) -> str:
    if events < 5:
        return "low"
    if events < 20:
        return "medium"
    return "high"


def _plural(n: float, word: str) -> str:
    n = int(n) if float(n).is_integer() else n
    return f"{n} {word}" + ("" if n == 1 else "s")


def ngo_trust(db: Session, agent_id: str) -> dict:
    succeeded = db.scalar(select(func.count()).select_from(Assignment)
                          .join(FoodListing, FoodListing.id == Assignment.listing_id)
                          .where(Assignment.agent_id == agent_id,
                                 FoodListing.status.in_((ListingStatus.DELIVERED, ListingStatus.STORED)))) or 0
    released_events = list(db.scalars(select(Event).where(Event.type == "pickup.released", Event.actor_id == agent_id)))
    disposed = db.scalar(select(func.count()).select_from(Assignment)
                         .join(FoodListing, FoodListing.id == Assignment.listing_id)
                         .where(Assignment.agent_id == agent_id, FoodListing.status == ListingStatus.DISPOSED)) or 0
    expired = db.scalar(select(func.count()).select_from(Assignment)
                        .where(Assignment.agent_id == agent_id, Assignment.status == "expired")) or 0
    released = len(released_events)
    failures = released + disposed + expired
    total = succeeded + failures

    accept_minutes = [(a.accepted_at - l.created_at).total_seconds() / 60 for a, l in db.execute(
        select(Assignment, FoodListing).join(FoodListing, FoodListing.id == Assignment.listing_id)
        .where(Assignment.agent_id == agent_id))]
    reasons = [f"{succeeded} of {total} finished pickups delivered or stored" if total else "No finished pickups yet"]
    if released:
        reasons.append(f"{_plural(released, 'pickup')} given back before collection")
    if disposed:
        reasons.append(f"{_plural(disposed, 'collected pickup')} had to be disposed")
    if expired:
        reasons.append(f"{_plural(expired, 'accepted pickup')} expired before collection")
    if accept_minutes:
        reasons.append(f"Accepts food on average {sum(accept_minutes) / len(accept_minutes):.0f} min after it is posted")

    since = utcnow() - FLAG_WINDOW
    flags = []
    recent_releases = sum(1 for e in released_events if e.created_at >= since)
    if recent_releases >= 2:
        flags.append(f"{recent_releases} pickups given back in the last 7 days")
    return _result(succeeded, failures, reasons, flags, count=total, unit="finished pickup")


def restaurant_trust(db: Session, restaurant_id: str) -> dict:
    rows = list(db.scalars(select(Inspection).join(FoodListing, FoodListing.id == Inspection.listing_id)
                           .where(FoodListing.restaurant_id == restaurant_id)))
    full = sum(1 for i in rows if i.outcome == "full")
    partial = sum(1 for i in rows if i.outcome == "partial")
    rejected = sum(1 for i in rows if i.outcome == "rejected")
    cancelled = db.scalar(select(func.count()).select_from(FoodListing)
                          .where(FoodListing.restaurant_id == restaurant_id,
                                 FoodListing.status == ListingStatus.CANCELLED)) or 0
    failures = rejected + 0.5 * partial
    reasons = [f"{full} of {len(rows)} food checks matched the listing" if rows else "No food checks yet"]
    if partial:
        reasons.append(f"{_plural(partial, 'food check')} found less food than listed")
    if rejected:
        reasons.append(f"{_plural(rejected, 'listing')} rejected at pickup (spoiled or wrong food)")
    if cancelled:
        reasons.append(f"{_plural(cancelled, 'listing')} cancelled by the restaurant (not counted in the score)")

    since = utcnow() - FLAG_WINDOW
    recent = [i for i in rows if i.created_at >= since]
    flags = []
    if sum(1 for i in recent if i.outcome == "rejected") >= 3:
        flags.append(f"{sum(1 for i in recent if i.outcome == 'rejected')} rejections in the last 7 days")
    if sum(1 for i in recent if i.outcome == "partial") >= 2:
        flags.append(f"{sum(1 for i in recent if i.outcome == 'partial')} short quantities in the last 7 days")
    return _result(full, failures, reasons, flags, count=len(rows), unit="food check")


def _result(successes: float, failures: float, reasons: list[str], flags: list[str], *, count: int,
            unit: str) -> dict:
    score = beta_score(successes, failures)
    conf = confidence(successes + failures)
    if score < LOW_SCORE and conf != "low":
        flags.append(f"Low score ({score:.0f}) — review the history before deciding anything")
    return {"score": score, "confidence": conf, "events": successes + failures, "based_on": _plural(count, unit),
            "reasons": reasons, "flags": flags,
            "method": "(successes + 1) / (total + 2) x 100; decision support only"}


def trust_for(db: Session, account: Account) -> dict | None:
    if account.role == "ngo":
        return ngo_trust(db, account.id)
    if account.role == "restaurant":
        return restaurant_trust(db, account.id)
    return None


def bid_trust_penalty(score: float) -> float:
    """Minutes added to an NGO's bid in the negotiation: (100 - score) / 10, at most 10. Never a veto."""
    return min(10.0, max(0.0, (100.0 - score) / 10.0))


def overview(db: Session) -> dict:
    from .listings import expire_due  # make sure expired pickups are counted before scoring
    expire_due(db)
    db.commit()
    out = {"ngos": [], "restaurants": []}
    for acc in db.scalars(select(Account).where(Account.role.in_(("ngo", "restaurant")),
                                                Account.status == AccountStatus.VERIFIED)
                          .order_by(Account.display_name)):
        notes = [{"note": n.note, "created_at": n.created_at} for n in
                 db.scalars(select(TrustNote).where(TrustNote.account_id == acc.id).order_by(TrustNote.id.desc()))]
        out["ngos" if acc.role == "ngo" else "restaurants"].append(
            {"account_id": acc.id, "name": acc.display_name, **trust_for(db, acc), "notes": notes})
    return out


def add_note(db: Session, account: Account, author_id: str | None, note: str) -> TrustNote:
    rec = TrustNote(account_id=account.id, author_id=author_id, note=note.strip()[:2000])
    db.add(rec)
    db.flush()
    return rec


# ---------------------------------------------------------------- API (host only)

class NoteIn(BaseModel):  # module level, so FastAPI can resolve it as the request body
    note: str = Field(min_length=2, max_length=2000)
    author_id: str | None = Field(default=None, max_length=32)


def build_router(*, get_db, require_admin):
    r = APIRouter(prefix="/internal/trust")

    def subject(db: Session, account_id: str) -> Account:
        acc = db.get(Account, account_id)
        if acc is None or acc.role not in ("ngo", "restaurant"):
            raise HTTPException(status_code=404, detail="no trust record for this account")
        return acc

    @r.get("")
    def all_scores(_: str = Depends(require_admin), db: Session = Depends(get_db)):
        return overview(db)

    @r.get("/{account_id}")
    def one(account_id: str, _: str = Depends(require_admin), db: Session = Depends(get_db)):
        acc = subject(db, account_id)
        return {"account_id": acc.id, "name": acc.display_name, "role": acc.role, **trust_for(db, acc)}

    @r.post("/{account_id}/notes", status_code=201)
    def note(account_id: str, body: NoteIn, _: str = Depends(require_admin), db: Session = Depends(get_db)):
        rec = add_note(db, subject(db, account_id), body.author_id, body.note)
        db.commit()
        return {"id": rec.id, "note": rec.note, "created_at": rec.created_at}

    return r
