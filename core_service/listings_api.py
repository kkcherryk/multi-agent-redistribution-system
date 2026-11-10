"""Core API routes for Phase 3 (food listings, NGO feed, accept)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import listings
from .models import Account, Assignment, FoodListing, ListingStatus, RestaurantProfile


class ListingIn(BaseModel):
    food_type: str = Field(min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    dietary: str
    quantity: float = Field(gt=0, le=10000)
    unit: str
    condition: str
    prepared_at: datetime
    expires_at: datetime
    pickup_notes: str | None = Field(default=None, max_length=500)

    @field_validator("food_type", "description", "pickup_notes", mode="before")
    @classmethod
    def _strip(cls, v):
        return (v.strip() or None) if isinstance(v, str) else v

    @field_validator("dietary")
    @classmethod
    def _dietary(cls, v):
        if v not in listings.DIETARY:
            raise ValueError("dietary must be veg, non_veg or mixed")
        return v

    @field_validator("unit")
    @classmethod
    def _unit(cls, v):
        if v not in listings.UNITS:
            raise ValueError("unit must be portions or kg")
        return v

    @field_validator("condition")
    @classmethod
    def _condition(cls, v):
        if v not in listings.CONDITIONS:
            raise ValueError("condition must be fresh, good or fair")
        return v


def build_router(*, get_db, caller_portal, require_admin, upload_dir: Path, radius_km: float) -> APIRouter:
    r = APIRouter(prefix="/internal")
    r.pickup_view = None  # set below; reused by the Phase 4 router

    def actor(db: Session, actor_id: str, portal: str, role: str) -> Account:
        acc = db.get(Account, actor_id)
        if portal != role or acc is None or acc.role != role:
            raise HTTPException(status_code=403, detail=f"this action belongs to the {role} portal")
        return acc

    def get_listing(db: Session, listing_id: str) -> FoodListing:
        listing = db.get(FoodListing, listing_id)
        if listing is None:
            raise HTTPException(status_code=404, detail="listing not found")
        return listing

    # -------------------------------------------------------------- restaurant
    @r.post("/listings", status_code=201)
    async def create(restaurant_id: str = Form(...), data: str = Form(...), photo: UploadFile = File(...),
                     portal: str = Depends(caller_portal), db: Session = Depends(get_db)):
        restaurant = actor(db, restaurant_id, portal, "restaurant")
        try:
            body = ListingIn.model_validate_json(data)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=[
                {"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()])
        blob = await photo.read(listings.MAX_UPLOAD_BYTES + 1)
        listing = listings.create_listing(db, restaurant, body.model_dump(), (photo.filename or "food", blob),
                                          upload_dir=upload_dir, radius_km=radius_km, portal=portal)
        db.commit()
        return listings.listing_view(db, listing, audience="restaurant")

    @r.get("/restaurants/{restaurant_id}/listings")
    def restaurant_listings(restaurant_id: str, portal: str = Depends(caller_portal), db: Session = Depends(get_db)):
        actor(db, restaurant_id, portal, "restaurant")
        listings.expire_due(db)
        db.commit()
        q = select(FoodListing).where(FoodListing.restaurant_id == restaurant_id).order_by(FoodListing.created_at.desc())
        return [listings.listing_view(db, x, audience="restaurant") for x in db.scalars(q)]

    @r.post("/listings/{listing_id}/cancel")
    def cancel(listing_id: str, restaurant_id: str = Query(...), portal: str = Depends(caller_portal),
               db: Session = Depends(get_db)):
        restaurant = actor(db, restaurant_id, portal, "restaurant")
        listing = get_listing(db, listing_id)
        listings.cancel_listing(db, restaurant, listing, portal=portal)
        db.commit()
        return listings.listing_view(db, listing, audience="restaurant")

    # -------------------------------------------------------------- NGO agent
    @r.get("/agents/{agent_id}/feed")
    def feed(agent_id: str, portal: str = Depends(caller_portal), db: Session = Depends(get_db)):
        agent = actor(db, agent_id, portal, "ngo")
        rows = listings.feed_for_agent(db, agent, radius_km)
        db.commit()
        return {"radius_km": radius_km, "listings": rows}

    @r.post("/listings/{listing_id}/accept")
    def accept(listing_id: str, agent_id: str = Query(...), portal: str = Depends(caller_portal),
               db: Session = Depends(get_db)):
        agent = actor(db, agent_id, portal, "ngo")
        listings.accept_listing(db, agent, listing_id, radius_km, portal=portal)
        db.commit()
        return pickup_view(db, get_listing(db, listing_id))

    @r.get("/agents/{agent_id}/pickups")
    def pickups(agent_id: str, portal: str = Depends(caller_portal), db: Session = Depends(get_db)):
        actor(db, agent_id, portal, "ngo")
        q = (select(FoodListing).join(Assignment, Assignment.listing_id == FoodListing.id)
             .where(Assignment.agent_id == agent_id).order_by(Assignment.accepted_at.desc()))
        return [pickup_view(db, x) for x in db.scalars(q)]

    def pickup_view(db: Session, listing: FoodListing) -> dict:
        """An accepted pickup: the collecting agent now gets the restaurant's contact details."""
        view = listings.listing_view(db, listing, audience="ngo")
        restaurant = db.get(Account, listing.restaurant_id)
        rprofile = db.get(RestaurantProfile, listing.restaurant_id)
        view["restaurant"].update(phone=restaurant.phone if restaurant else None,
                                  contact_name=rprofile.applicant_name if rprofile else None)
        return view

    # -------------------------------------------------------------- shared / host
    @r.get("/listings/{listing_id}/photo")
    def photo(listing_id: str, viewer_id: str | None = Query(default=None), portal: str = Depends(caller_portal),
              db: Session = Depends(get_db)):
        listing = get_listing(db, listing_id)
        p = listings.photo_for(db, listing_id)
        if p is None or not listings.can_view_photo(db, listing, portal, viewer_id, radius_km):
            raise HTTPException(status_code=404, detail="photo not found")
        path = upload_dir / p.storage_key
        if not path.exists():
            raise HTTPException(status_code=404, detail="photo file missing")
        return Response(path.read_bytes(), media_type=p.content_type,
                        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})

    @r.get("/listings")
    def all_listings(status: str | None = Query(default=None), _: str = Depends(require_admin),
                     db: Session = Depends(get_db)):
        if status is not None and status not in ListingStatus.ALL:
            raise HTTPException(status_code=422, detail="unknown status")
        listings.expire_due(db)
        db.commit()
        q = select(FoodListing).order_by(FoodListing.created_at.desc())
        if status:
            q = q.where(FoodListing.status == status)
        return [listings.listing_view(db, x, audience="admin") for x in db.scalars(q)]

    r.pickup_view = pickup_view
    return r
