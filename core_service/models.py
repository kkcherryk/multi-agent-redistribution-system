"""Database models owned by the core data service (the authoritative store for shared records)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.types import TypeDecorator
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


class UTCDateTime(TypeDecorator):
    """Stores datetimes as UTC and always returns timezone-aware UTC values.

    SQLite drops tzinfo, which made the API emit naive timestamps that browsers read as *local* time.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


class Base(DeclarativeBase):
    pass


class AccountStatus:
    """Account lifecycle from the spec: applicant -> under review / on hold -> verified or declined.

    * applicant     registered, onboarding not yet submitted
    * under_review  submitted, waiting for the host
    * on_hold       host asked for more information; applicant edits and resubmits
    * verified      approved by the host
    * declined      rejected by the host (the host can still change the decision later)
    """

    APPLICANT = "applicant"
    UNDER_REVIEW = "under_review"
    ON_HOLD = "on_hold"
    VERIFIED = "verified"
    DECLINED = "declined"
    ALL = (APPLICANT, UNDER_REVIEW, ON_HOLD, VERIFIED, DECLINED)
    EDITABLE = (APPLICANT, ON_HOLD)  # the applicant may change profile/documents only in these states


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    role: Mapped[str] = mapped_column(String(20), index=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(100))
    display_name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=AccountStatus.UNDER_REVIEW, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class Event(Base):
    """Append-only audit/event log. Later phases feed the host map and the agents from these events."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(60), index=True)
    portal: Mapped[str | None] = mapped_column(String(20), nullable=True)
    actor_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    subject_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)


class RestaurantProfile(Base):
    """Module 2 onboarding details. One per restaurant account."""

    __tablename__ = "restaurant_profiles"

    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True)
    applicant_name: Mapped[str] = mapped_column(String(120))
    restaurant_name: Mapped[str] = mapped_column(String(120))
    address: Mapped[str] = mapped_column(String(300))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    licence_number: Mapped[str] = mapped_column(String(60))
    additional_info: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class NgoProfile(Base):
    """Module 3 onboarding details: the NGO organisation plus its representative (the collecting agent).

    Phase 2 models one representative/agent per NGO account; multiple agents per NGO can be added later.
    Availability is stored separately from verification (Module 5: availability is not proof of verification).
    """

    __tablename__ = "ngo_profiles"

    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True)
    ngo_name: Mapped[str] = mapped_column(String(120))
    ngo_registration_id: Mapped[str] = mapped_column(String(60))
    representative_name: Mapped[str] = mapped_column(String(120))
    address: Mapped[str] = mapped_column(String(300))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    additional_info: Mapped[str | None] = mapped_column(Text, nullable=True)
    available: Mapped[bool] = mapped_column(Boolean, default=False)
    availability_changed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class Document(Base):
    """Uploaded photo or verification evidence. File bytes live on disk; only the owner and the host may read them."""

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    filename: Mapped[str] = mapped_column(String(200))
    content_type: Mapped[str] = mapped_column(String(60))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class Review(Base):
    """Host decisions on an application (history kept for audit and appeals)."""

    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    reviewer_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    decision: Mapped[str] = mapped_column(String(20))
    from_status: Mapped[str] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class ListingStatus:
    """Food ticket lifecycle (spec section 5):
    active -> assigned -> collected -> delivered | stored, with exits cancelled, expired, rejected (at inspection)
    and disposed (expired before it could be handed over)."""

    ACTIVE = "active"
    ASSIGNED = "assigned"
    COLLECTED = "collected"
    DELIVERED = "delivered"
    STORED = "stored"
    REJECTED = "rejected"
    DISPOSED = "disposed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    ALL = (ACTIVE, ASSIGNED, COLLECTED, DELIVERED, STORED, REJECTED, DISPOSED, CANCELLED, EXPIRED)
    IN_PROGRESS = (ASSIGNED, COLLECTED)  # the collecting agent is busy with this listing


class AssignmentStatus:
    """Collection assignment (spec section 5): accepted -> en_route -> collected | rejected;
    collected -> delivered | stored | disposed. Released assignments are removed and logged as events."""

    ACCEPTED = "accepted"
    EN_ROUTE = "en_route"
    COLLECTED = "collected"
    REJECTED = "rejected"
    DELIVERED = "delivered"
    STORED = "stored"
    DISPOSED = "disposed"


class FoodListing(Base):
    """Module 6: a surplus-food offer posted by a verified restaurant."""

    __tablename__ = "food_listings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    restaurant_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    food_type: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    dietary: Mapped[str] = mapped_column(String(20))  # veg / non_veg / mixed
    quantity: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(20))  # portions / kg
    condition: Mapped[str] = mapped_column(String(20))  # fresh / good / fair
    prepared_at: Mapped[datetime] = mapped_column(UTCDateTime())
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    pickup_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Pickup point copied from the restaurant profile when the listing is posted.
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default=ListingStatus.ACTIVE, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class ListingPhoto(Base):
    __tablename__ = "listing_photos"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    listing_id: Mapped[str] = mapped_column(ForeignKey("food_listings.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(200))
    content_type: Mapped[str] = mapped_column(String(60))
    size_bytes: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class ListingNotification(Base):
    """Module 7: an alert sent to a nearby eligible NGO agent about a listing."""

    __tablename__ = "listing_notifications"
    __table_args__ = (UniqueConstraint("listing_id", "agent_id", name="uq_notification_listing_agent"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    listing_id: Mapped[str] = mapped_column(ForeignKey("food_listings.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    distance_km: Mapped[float] = mapped_column(Float)
    seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class Assignment(Base):
    """Collector assignment. ``listing_id`` is unique, so the database itself prevents double assignment.
    Phase 3 creates it as ``accepted``; Phase 4 adds en-route / inspection / collected states."""

    __tablename__ = "assignments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    listing_id: Mapped[str] = mapped_column(ForeignKey("food_listings.id", ondelete="CASCADE"), unique=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="accepted", index=True)
    distance_km: Mapped[float] = mapped_column(Float)
    accepted_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class Inspection(Base):
    """Module 8: what the collector found at the restaurant. Food counts as collected only once this exists."""

    __tablename__ = "inspections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    listing_id: Mapped[str] = mapped_column(ForeignKey("food_listings.id", ondelete="CASCADE"), unique=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    outcome: Mapped[str] = mapped_column(String(20))  # full / partial / rejected
    listed_quantity: Mapped[float] = mapped_column(Float)
    actual_quantity: Mapped[float] = mapped_column(Float)
    observed_condition: Mapped[str] = mapped_column(String(20))  # fresh / good / fair / spoiled
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class Recipient(Base):
    """A recipient organisation an NGO delivers to (orphanage, shelter, old-age home, ...)."""

    __tablename__ = "recipients"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    ngo_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(30))
    address: Mapped[str] = mapped_column(String(300))
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class Handover(Base):
    """Module 9: where collected food went. ``kind`` is delivered (to a recipient), stored (at the NGO),
    distributed (from storage to a recipient) or disposed."""

    __tablename__ = "handovers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    listing_id: Mapped[str] = mapped_column(ForeignKey("food_listings.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    quantity: Mapped[float] = mapped_column(Float)
    recipient_id: Mapped[str | None] = mapped_column(ForeignKey("recipients.id"), nullable=True)
    storage_item_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    received_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class StorageItem(Base):
    """Food kept at an NGO location for later distribution, with expiry tracking."""

    __tablename__ = "storage_items"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    ngo_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    listing_id: Mapped[str] = mapped_column(ForeignKey("food_listings.id", ondelete="CASCADE"), unique=True)
    food_type: Mapped[str] = mapped_column(String(120))
    unit: Mapped[str] = mapped_column(String(20))
    stored_quantity: Mapped[float] = mapped_column(Float)
    remaining_quantity: Mapped[float] = mapped_column(Float)
    location: Mapped[str] = mapped_column(String(300))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    status: Mapped[str] = mapped_column(String(20), default="in_stock", index=True)  # in_stock/distributed/disposed
    stored_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class AgentLocation(Base):
    """Module 10: an NGO agent's most recent shared position (one row per agent)."""

    __tablename__ = "agent_locations"

    agent_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    accuracy_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class LocationPing(Base):
    """Movement trail, recorded only while the agent is working on a pickup (operational purpose only)."""

    __tablename__ = "location_pings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    listing_id: Mapped[str] = mapped_column(ForeignKey("food_listings.id", ondelete="CASCADE"), index=True)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
