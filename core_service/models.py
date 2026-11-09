"""Database models owned by the core data service (the authoritative store for shared records)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Integer, String
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

    Phase 1 only creates ``under_review`` applicants and ``verified`` hosts; review transitions arrive in Phase 2.
    """

    UNDER_REVIEW = "under_review"
    ON_HOLD = "on_hold"
    VERIFIED = "verified"
    DECLINED = "declined"
    ALL = (UNDER_REVIEW, ON_HOLD, VERIFIED, DECLINED)


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
