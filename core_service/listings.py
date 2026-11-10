"""Food listings, nearby-agent notification and collector claims (spec Modules 6 and 7, basic matching)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from shared.geo import haversine_km

from .accounts import record_event
from .models import (Account, AccountStatus, Assignment, FoodListing, ListingNotification, ListingPhoto,
                     ListingStatus, NgoProfile, RestaurantProfile, new_id, utcnow)
from .onboarding import IMAGE_TYPES, MAX_UPLOAD_BYTES, RuleError, _safe_filename, sniff_content_type

DEFAULT_RADIUS_KM = 5.0
MAX_ACTIVE_ASSIGNMENTS_PER_AGENT = 1  # ride-hailing style: one pickup at a time
MAX_LISTING_HOURS = 72
MIN_REMAINING_MINUTES = 15  # a listing must leave enough time to collect the food
PREP_CLOCK_SKEW = timedelta(minutes=5)

DIETARY = ("veg", "non_veg", "mixed")
UNITS = ("portions", "kg")
CONDITIONS = ("fresh", "good", "fair")


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- expiry

def expire_due(db: Session) -> int:
    """Mark active listings whose expiry time has passed as expired (lazy expiry on every read/claim)."""
    now = utcnow()
    due = list(db.scalars(select(FoodListing).where(FoodListing.status == ListingStatus.ACTIVE,
                                                    FoodListing.expires_at <= now)))
    for listing in due:
        listing.status = ListingStatus.EXPIRED
        record_event(db, "listing.expired", subject_id=listing.id, restaurant_id=listing.restaurant_id)
    if due:
        db.flush()
    return len(due)


# ---------------------------------------------------------------- eligibility

def eligible_agents(db: Session, listing: FoodListing, radius_km: float) -> list[tuple[Account, NgoProfile, float]]:
    """Verified + available NGO agents within the radius, nearest first, excluding agents already at capacity."""
    rows = db.execute(select(Account, NgoProfile).join(NgoProfile, NgoProfile.account_id == Account.id)
                      .where(Account.role == "ngo", Account.status == AccountStatus.VERIFIED,
                             NgoProfile.available.is_(True))).all()
    out = []
    for account, profile in rows:
        d = haversine_km(listing.latitude, listing.longitude, profile.latitude, profile.longitude)
        if d <= radius_km and active_assignment_count(db, account.id) < MAX_ACTIVE_ASSIGNMENTS_PER_AGENT:
            out.append((account, profile, d))
    return sorted(out, key=lambda r: r[2])


def active_assignment_count(db: Session, agent_id: str) -> int:
    return len(list(db.scalars(select(Assignment.id).join(FoodListing, FoodListing.id == Assignment.listing_id)
                               .where(Assignment.agent_id == agent_id,
                                      FoodListing.status.in_(ListingStatus.IN_PROGRESS)))))


def require_verified_agent(db: Session, agent: Account) -> NgoProfile:
    profile = db.get(NgoProfile, agent.id)
    if agent.status != AccountStatus.VERIFIED or profile is None:
        raise RuleError("only verified NGO agents can see and accept food listings", 403)
    return profile


# ---------------------------------------------------------------- restaurant actions

def create_listing(db: Session, restaurant: Account, data: dict, photo: tuple[str, bytes], *, upload_dir: Path,
                   radius_km: float, portal: str) -> FoodListing:
    if restaurant.status != AccountStatus.VERIFIED:
        raise RuleError("only verified restaurants can post food", 403)
    profile = db.get(RestaurantProfile, restaurant.id)
    if profile is None:
        raise RuleError("restaurant profile missing", 409)

    now = utcnow()
    prepared, expires = _aware(data["prepared_at"]), _aware(data["expires_at"])
    if prepared > now + PREP_CLOCK_SKEW:
        raise RuleError("preparation time cannot be in the future", 422)
    if expires <= prepared:
        raise RuleError("expiry time must be after the preparation time", 422)
    if expires < now + timedelta(minutes=MIN_REMAINING_MINUTES):
        raise RuleError(f"expiry must be at least {MIN_REMAINING_MINUTES} minutes from now to allow collection", 422)
    if expires > now + timedelta(hours=MAX_LISTING_HOURS):
        raise RuleError(f"expiry cannot be more than {MAX_LISTING_HOURS} hours from now", 422)

    filename, blob = photo
    if not blob:
        raise RuleError("a food photo is required", 422)
    if len(blob) > MAX_UPLOAD_BYTES:
        raise RuleError("the photo is larger than 5 MB", 413)
    ctype = sniff_content_type(blob)
    if ctype not in IMAGE_TYPES:
        raise RuleError("the food photo must be a JPEG, PNG or WEBP image", 415)

    listing = FoodListing(restaurant_id=restaurant.id, latitude=profile.latitude, longitude=profile.longitude,
                          prepared_at=prepared, expires_at=expires, **{k: v for k, v in data.items()
                                                                       if k not in ("prepared_at", "expires_at")})
    db.add(listing)
    db.flush()
    key = new_id()
    upload_dir.mkdir(parents=True, exist_ok=True)
    (upload_dir / key).write_bytes(blob)
    db.add(ListingPhoto(listing_id=listing.id, filename=_safe_filename(filename), content_type=ctype,
                        size_bytes=len(blob), storage_key=key))
    record_event(db, "listing.created", portal=portal, actor_id=restaurant.id, subject_id=listing.id,
                 quantity=listing.quantity, unit=listing.unit)
    notify_nearby(db, listing, radius_km)
    return listing


def notify_nearby(db: Session, listing: FoodListing, radius_km: float) -> int:
    """Send an alert to every eligible agent who has not already been alerted about this listing."""
    already = set(db.scalars(select(ListingNotification.agent_id).where(ListingNotification.listing_id == listing.id)))
    sent = 0
    for agent, _profile, dist in eligible_agents(db, listing, radius_km):
        if agent.id in already:
            continue
        db.add(ListingNotification(listing_id=listing.id, agent_id=agent.id, distance_km=round(dist, 3)))
        sent += 1
    record_event(db, "listing.notified", subject_id=listing.id, agents=sent, radius_km=radius_km)
    db.flush()
    return sent


def cancel_listing(db: Session, restaurant: Account, listing: FoodListing, *, portal: str) -> None:
    if listing.restaurant_id != restaurant.id:
        raise RuleError("listing not found", 404)
    expire_due(db)
    if listing.status != ListingStatus.ACTIVE:
        raise RuleError(f"only active listings can be cancelled (this one is '{listing.status}')")
    listing.status = ListingStatus.CANCELLED
    record_event(db, "listing.cancelled", portal=portal, actor_id=restaurant.id, subject_id=listing.id)


# ---------------------------------------------------------------- NGO agent actions

def feed_for_agent(db: Session, agent: Account, radius_km: float) -> list[dict]:
    """Active listings within the agent's radius, nearest first. Opening the feed marks alerts as seen."""
    profile = require_verified_agent(db, agent)
    expire_due(db)
    notes = {n.listing_id: n for n in db.scalars(select(ListingNotification)
                                                 .where(ListingNotification.agent_id == agent.id))}
    from .models import AgentRecommendation  # Phase 6: logistics agent suggestions
    recs = {r.listing_id: r for r in db.scalars(select(AgentRecommendation)
                                                .where(AgentRecommendation.agent_id == agent.id))}
    out = []
    for listing in db.scalars(select(FoodListing).where(FoodListing.status == ListingStatus.ACTIVE)):
        d = haversine_km(profile.latitude, profile.longitude, listing.latitude, listing.longitude)
        if d > radius_km:
            continue
        note = notes.get(listing.id)
        rec = recs.get(listing.id)
        out.append({**listing_view(db, listing, audience="ngo"), "distance_km": round(d, 2),
                    "alerted": note is not None, "new": note is not None and note.seen_at is None,
                    "recommended": None if rec is None else {"rank": rec.rank, "eta_minutes": rec.eta_minutes,
                                                             "reason": rec.reason}})
        if note is not None and note.seen_at is None:
            note.seen_at = utcnow()
    out.sort(key=lambda r: r["distance_km"])
    return out


def accept_listing(db: Session, agent: Account, listing_id: str, radius_km: float, *, portal: str,
                   require_available: bool = True) -> Assignment:
    """First accept wins. Backup collections (Phase 6) pass require_available=False and their own distance limit."""
    profile = require_verified_agent(db, agent)
    if require_available and not profile.available:
        raise RuleError("set yourself as Available before accepting a pickup")
    expire_due(db)
    listing = db.get(FoodListing, listing_id)
    if listing is None:
        raise RuleError("listing not found", 404)
    if listing.status != ListingStatus.ACTIVE:
        raise RuleError("this food has already been taken or is no longer available")
    dist = haversine_km(profile.latitude, profile.longitude, listing.latitude, listing.longitude)
    if dist > radius_km:
        raise RuleError("this listing is outside your pickup area", 403)
    if active_assignment_count(db, agent.id) >= MAX_ACTIVE_ASSIGNMENTS_PER_AGENT:
        raise RuleError("finish your current pickup before accepting another one")

    # Compare-and-set: only one concurrent accept can move the listing out of 'active'.
    claimed = db.execute(update(FoodListing).where(FoodListing.id == listing.id,
                                                   FoodListing.status == ListingStatus.ACTIVE)
                         .values(status=ListingStatus.ASSIGNED, updated_at=utcnow())).rowcount
    if claimed != 1:
        db.rollback()
        raise RuleError("this food has already been taken or is no longer available")
    assignment = Assignment(listing_id=listing.id, agent_id=agent.id, distance_km=round(dist, 3))
    db.add(assignment)
    try:
        db.flush()
    except IntegrityError:  # unique(listing_id): second line of defence against double assignment
        db.rollback()
        raise RuleError("this food has already been taken or is no longer available")
    db.refresh(listing)
    record_event(db, "listing.accepted", portal=portal, actor_id=agent.id, subject_id=listing.id,
                 distance_km=round(dist, 2))
    return assignment


# ---------------------------------------------------------------- views

def photo_for(db: Session, listing_id: str) -> ListingPhoto | None:
    return db.scalar(select(ListingPhoto).where(ListingPhoto.listing_id == listing_id))


def listing_view(db: Session, listing: FoodListing, *, audience: str) -> dict:
    """Serialise a listing. Restaurant verification badges are never shown to NGOs (Module 2 visibility rule);
    restaurant contact details are shared with the NGO only once that NGO's agent has accepted."""
    restaurant = db.get(Account, listing.restaurant_id)
    rprofile = db.get(RestaurantProfile, listing.restaurant_id)
    photo = photo_for(db, listing.id)
    assignment = db.scalar(select(Assignment).where(Assignment.listing_id == listing.id))
    view = {
        "id": listing.id, "food_type": listing.food_type, "description": listing.description,
        "dietary": listing.dietary, "quantity": listing.quantity, "unit": listing.unit,
        "condition": listing.condition, "prepared_at": listing.prepared_at, "expires_at": listing.expires_at,
        "pickup_notes": listing.pickup_notes, "latitude": listing.latitude, "longitude": listing.longitude,
        "status": listing.status, "created_at": listing.created_at, "photo_id": photo.id if photo else None,
        "restaurant": {"id": listing.restaurant_id, "name": restaurant.display_name if restaurant else None,
                       "address": rprofile.address if rprofile else None},
        "assignment": None,
    }
    if assignment is not None:
        agent = db.get(Account, assignment.agent_id)
        ngo = db.get(NgoProfile, assignment.agent_id)
        view["assignment"] = {"id": assignment.id, "status": assignment.status, "accepted_at": assignment.accepted_at,
                              "distance_km": assignment.distance_km, "agent_id": assignment.agent_id,
                              "ngo_name": agent.display_name if agent else None,
                              "representative_name": ngo.representative_name if ngo else None,
                              "phone": agent.phone if agent else None}
    from .fulfilment import fulfilment_view  # local import: fulfilment depends on this module
    view.update(fulfilment_view(db, listing.id))
    if audience == "admin":
        view["notified_agents"] = len(list(db.scalars(select(ListingNotification.id)
                                                      .where(ListingNotification.listing_id == listing.id))))
    return view


def can_view_photo(db: Session, listing: FoodListing, portal: str, viewer_id: str | None, radius_km: float) -> bool:
    if portal == "admin":
        return True
    if portal == "restaurant":
        return viewer_id == listing.restaurant_id
    if portal == "ngo" and viewer_id:
        assignment = db.scalar(select(Assignment).where(Assignment.listing_id == listing.id))
        if assignment is not None:
            return assignment.agent_id == viewer_id
        agent = db.get(Account, viewer_id)
        profile = db.get(NgoProfile, viewer_id)
        return (listing.status == ListingStatus.ACTIVE and agent is not None and agent.role == "ngo"
                and agent.status == AccountStatus.VERIFIED and profile is not None
                and haversine_km(profile.latitude, profile.longitude, listing.latitude, listing.longitude) <= radius_km)
    return False
