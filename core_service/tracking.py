"""Agent location sharing and the host's operational map (spec Module 10)."""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .listings import expire_due
from .models import (Account, AccountStatus, AgentLocation, Assignment, Document, FoodListing, Handover, ListingStatus,
                     LocationPing, NgoProfile, RestaurantProfile, utcnow)
from .onboarding import RuleError

LIVE_SECONDS = 120  # a position older than this is shown as "last known", not live
MAP_LISTING_STATUSES = (ListingStatus.ACTIVE, ListingStatus.ASSIGNED, ListingStatus.COLLECTED)
TRAIL_POINTS = 50


def _current_pickup(db: Session, agent_id: str) -> tuple[FoodListing, Assignment] | None:
    row = db.execute(select(FoodListing, Assignment).join(Assignment, Assignment.listing_id == FoodListing.id)
                     .where(Assignment.agent_id == agent_id, FoodListing.status.in_(ListingStatus.IN_PROGRESS))
                     .order_by(Assignment.accepted_at.desc())).first()
    return (row[0], row[1]) if row else None


def update_location(db: Session, agent: Account, lat: float, lon: float, accuracy: float | None) -> AgentLocation:
    profile = db.get(NgoProfile, agent.id)
    if agent.status != AccountStatus.VERIFIED or profile is None:
        raise RuleError("only verified NGO agents can share their location", 403)
    pickup = _current_pickup(db, agent.id)
    # Location is collected only for its operational purpose: while available for pickups or doing one.
    if not profile.available and pickup is None:
        raise RuleError("location is shared only while you are Available or on a pickup")
    loc = db.get(AgentLocation, agent.id)
    if loc is None:
        loc = AgentLocation(agent_id=agent.id, latitude=lat, longitude=lon)
        db.add(loc)
    loc.latitude, loc.longitude, loc.accuracy_m, loc.updated_at = lat, lon, accuracy, utcnow()
    if pickup is not None:
        db.add(LocationPing(agent_id=agent.id, listing_id=pickup[0].id, latitude=lat, longitude=lon))
    db.flush()
    return loc


def stop_sharing(db: Session, agent: Account) -> None:
    loc = db.get(AgentLocation, agent.id)
    if loc is not None:
        db.delete(loc)


def location_dict(loc: AgentLocation | None) -> dict | None:
    if loc is None:
        return None
    age = (utcnow() - loc.updated_at).total_seconds()
    return {"latitude": loc.latitude, "longitude": loc.longitude, "accuracy_m": loc.accuracy_m,
            "updated_at": loc.updated_at, "live": age <= LIVE_SECONDS, "age_seconds": int(age)}


def map_data(db: Session) -> dict:
    expire_due(db)
    restaurants = []
    for acc, prof in db.execute(select(Account, RestaurantProfile)
                                .join(RestaurantProfile, RestaurantProfile.account_id == Account.id)
                                .where(Account.status == AccountStatus.VERIFIED)):
        active = db.scalar(select(func.count()).select_from(FoodListing)
                           .where(FoodListing.restaurant_id == acc.id, FoodListing.status.in_(MAP_LISTING_STATUSES)))
        restaurants.append({"id": acc.id, "name": acc.display_name, "address": prof.address, "latitude": prof.latitude,
                            "longitude": prof.longitude, "open_listings": active})

    agents = []
    for acc, prof in db.execute(select(Account, NgoProfile).join(NgoProfile, NgoProfile.account_id == Account.id)
                                .where(Account.status == AccountStatus.VERIFIED)):
        pickup = _current_pickup(db, acc.id)
        counts = dict(db.execute(select(Handover.kind, func.count()).where(Handover.agent_id == acc.id)
                                 .group_by(Handover.kind)).all())
        agents.append({
            "id": acc.id, "ngo_name": acc.display_name, "representative_name": prof.representative_name,
            # Representative photo from NGO registration, so the host can recognise the agent (host-only document).
            "photo_id": db.scalar(select(Document.id).where(Document.account_id == acc.id,
                                                            Document.kind == "representative_photo")
                                  .order_by(Document.created_at.desc()).limit(1)),
            "phone": acc.phone, "available": prof.available, "base": {"latitude": prof.latitude,
                                                                    "longitude": prof.longitude, "address": prof.address},
            "location": location_dict(db.get(AgentLocation, acc.id)),
            "current_pickup": None if pickup is None else {
                "listing_id": pickup[0].id, "food_type": pickup[0].food_type, "status": pickup[0].status,
                "step": pickup[1].status, "accepted_at": pickup[1].accepted_at},
            "stats": {"deliveries": counts.get("delivered", 0) + counts.get("distributed", 0),
                      "stored": counts.get("stored", 0), "disposed": counts.get("disposed", 0)},
        })

    listings = []
    for l in db.scalars(select(FoodListing).where(FoodListing.status.in_(MAP_LISTING_STATUSES))
                        .order_by(FoodListing.created_at.desc())):
        a = db.scalar(select(Assignment).where(Assignment.listing_id == l.id))
        trail = []
        if a is not None:
            pings = db.scalars(select(LocationPing).where(LocationPing.listing_id == l.id)
                               .order_by(LocationPing.id.desc()).limit(TRAIL_POINTS))
            trail = [[p.latitude, p.longitude] for p in reversed(list(pings))]
        rest = db.get(Account, l.restaurant_id)
        listings.append({"id": l.id, "food_type": l.food_type, "quantity": l.quantity, "unit": l.unit,
                         "status": l.status, "latitude": l.latitude, "longitude": l.longitude,
                         "expires_at": l.expires_at, "restaurant_name": rest.display_name if rest else None,
                         "agent_id": a.agent_id if a else None, "step": a.status if a else None, "trail": trail})

    status_counts = dict(db.execute(select(FoodListing.status, func.count()).group_by(FoodListing.status)).all())
    return {
        "generated_at": utcnow(), "live_seconds": LIVE_SECONDS,
        "restaurants": restaurants, "agents": agents, "listings": listings,
        "summary": {
            "open_food": status_counts.get(ListingStatus.ACTIVE, 0),
            "in_progress": status_counts.get(ListingStatus.ASSIGNED, 0) + status_counts.get(ListingStatus.COLLECTED, 0),
            "delivered": status_counts.get(ListingStatus.DELIVERED, 0),
            "stored": status_counts.get(ListingStatus.STORED, 0),
            "agents_available": sum(1 for a in agents if a["available"]),
            "agents_live": sum(1 for a in agents if a["location"] and a["location"]["live"]),
        },
    }


class LocationIn(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    accuracy_m: float | None = Field(default=None, ge=0, le=100000)


def build_router(*, get_db, caller_portal, require_admin) -> APIRouter:
    r = APIRouter(prefix="/internal")

    def agent(db: Session, agent_id: str, portal: str) -> Account:
        acc = db.get(Account, agent_id)
        if portal != "ngo" or acc is None or acc.role != "ngo":
            raise HTTPException(status_code=403, detail="this action belongs to the NGO portal")
        return acc

    @r.put("/agents/{agent_id}/location")
    def put_location(agent_id: str, body: LocationIn, portal: str = Depends(caller_portal),
                     db: Session = Depends(get_db)):
        loc = update_location(db, agent(db, agent_id, portal), body.latitude, body.longitude, body.accuracy_m)
        db.commit()
        return location_dict(loc)

    @r.delete("/agents/{agent_id}/location", status_code=204)
    def delete_location(agent_id: str, portal: str = Depends(caller_portal), db: Session = Depends(get_db)):
        stop_sharing(db, agent(db, agent_id, portal))
        db.commit()

    @r.get("/agents/{agent_id}/location")
    def get_location(agent_id: str, portal: str = Depends(caller_portal), db: Session = Depends(get_db)):
        agent(db, agent_id, portal)
        return {"location": location_dict(db.get(AgentLocation, agent_id))}

    @r.get("/map")
    def host_map(_: str = Depends(require_admin), db: Session = Depends(get_db)):
        data = map_data(db)
        db.commit()
        return data

    return r
