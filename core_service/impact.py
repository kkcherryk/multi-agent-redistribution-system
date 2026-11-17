"""Public impact totals for the landing page.

The only core route that needs no service key besides /health. It returns counts and sums — never names,
places, phone numbers or anything that identifies an account or a listing — so it is safe to show publicly.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Account, AccountStatus, FoodListing, ListingStatus

COMPLETED = (ListingStatus.DELIVERED, ListingStatus.STORED)  # food that reached an NGO


def impact_totals(db: Session) -> dict:
    def total(unit: str) -> float:
        q = select(func.coalesce(func.sum(FoodListing.quantity), 0.0)).where(
            FoodListing.status.in_(COMPLETED), FoodListing.unit == unit)
        return float(db.scalar(q) or 0.0)

    def count_listings(statuses) -> int:
        return int(db.scalar(select(func.count()).select_from(FoodListing).where(FoodListing.status.in_(statuses))) or 0)

    def verified(role: str) -> int:
        q = select(func.count()).select_from(Account).where(
            Account.role == role, Account.status == AccountStatus.VERIFIED,
            Account.email.not_like("%@erased.invalid"))  # erased accounts no longer count
        return int(db.scalar(q) or 0)

    return {
        "meals_delivered": int(round(total("portions"))),
        "kg_delivered": round(total("kg"), 1),
        "donations_completed": count_listings(COMPLETED),
        "restaurants": verified("restaurant"),
        "ngos": verified("ngo"),
        "now_waiting": count_listings((ListingStatus.ACTIVE,)),
        "now_on_the_way": count_listings(ListingStatus.IN_PROGRESS),
    }


def build_router(*, get_db) -> APIRouter:
    router = APIRouter()

    @router.get("/public/impact")
    def public_impact(db: Session = Depends(get_db)):
        return impact_totals(db)

    return router
