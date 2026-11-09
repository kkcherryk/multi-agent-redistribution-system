from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^\+?[0-9][0-9 \-]{6,18}$")


def _normalise_email(value: str) -> str:
    value = value.strip().lower()
    if not EMAIL_RE.match(value) or len(value) > 254:
        raise ValueError("invalid email address")
    return value


class RegisterIn(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=72)
    display_name: str = Field(min_length=2, max_length=120)
    phone: str | None = Field(default=None, max_length=20)

    _email = field_validator("email")(_normalise_email)

    @field_validator("password")
    @classmethod
    def _password_bytes(cls, v: str) -> str:
        if len(v.encode("utf-8")) > 72:
            raise ValueError("password must be at most 72 bytes")
        return v

    @field_validator("display_name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 2:
            raise ValueError("name is too short")
        return v

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        v = v.strip()
        if not PHONE_RE.match(v):
            raise ValueError("invalid phone number")
        return v


class CredentialsIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=200)


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    role: str
    email: str
    display_name: str
    phone: str | None
    status: str
    created_at: datetime


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: str
    portal: str | None
    actor_id: str | None
    subject_id: str | None
    payload: dict
    created_at: datetime
