"""Account operations, shared by the HTTP API and the team's CLI scripts."""
from __future__ import annotations

import hashlib
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.security import hash_password, verify_password

from .models import Account, AccountStatus, Event

# Spend roughly the same time on unknown emails as on wrong passwords (reduces account enumeration by timing).
_DUMMY_HASH = hash_password("timing-equaliser-password")


class EmailTaken(Exception):
    pass


def record_event(session: Session, type_: str, *, portal: str | None = None, actor_id: str | None = None,
                 subject_id: str | None = None, **payload) -> Event:
    ev = Event(type=type_, portal=portal, actor_id=actor_id, subject_id=subject_id, payload=payload)
    session.add(ev)
    from .notifications import on_event  # local import: notifications reads models that import nothing from here
    on_event(session, ev)
    return ev


def get_by_email(session: Session, email: str) -> Account | None:
    return session.scalar(select(Account).where(Account.email == email.strip().lower()))


def create_account(session: Session, *, role: str, email: str, password: str, display_name: str,
                   phone: str | None = None, status: str | None = None, portal: str | None = None) -> Account:
    email = email.strip().lower()
    if get_by_email(session, email) is not None:
        raise EmailTaken(email)
    if status is None:
        status = AccountStatus.VERIFIED if role == "host" else AccountStatus.APPLICANT
    account = Account(role=role, email=email, password_hash=hash_password(password),
                      display_name=display_name, phone=phone, status=status)
    session.add(account)
    session.flush()
    record_event(session, "account.registered", portal=portal, actor_id=account.id, subject_id=account.id, role=role)
    return account


def phone_digits(phone: str | None) -> str:
    return re.sub(r"\D", "", phone or "")


def find_for_signin(session: Session, identifier: str, *, role: str) -> Account | None:
    """Find the account of `role` that an email address or a phone number points to.

    Phone numbers are compared on their last 10 digits, so "+91 98765 43210", "098765 43210" and "9876543210"
    all match. A number shared by two accounts of the same role signs in neither (it is ambiguous).
    Erased accounts are never found."""
    ident = (identifier or "").strip()
    if "@" in ident:
        account = get_by_email(session, ident)
        return account if account is not None and account.role == role and not _erased(account) else None
    digits = phone_digits(ident)
    if len(digits) < 10:
        return None
    candidates = session.scalars(select(Account).where(Account.role == role, Account.phone.is_not(None)))
    matches = [a for a in candidates if phone_digits(a.phone)[-10:] == digits[-10:] and not _erased(a)]
    return matches[0] if len(matches) == 1 else None


def _erased(account: Account) -> bool:
    return account.email.endswith("@erased.invalid")


def password_fingerprint(account: Account) -> str:
    """Changes whenever the password changes; lets a reset link work exactly once."""
    return hashlib.sha256(account.password_hash.encode("ascii")).hexdigest()[:24]


def authenticate(session: Session, identifier: str, password: str, *, role: str) -> Account | None:
    """Return the account if the credentials are valid *and* belong to the given role.

    `identifier` is an email address or a phone number."""
    account = find_for_signin(session, identifier, role=role)
    if account is None:
        verify_password(password, _DUMMY_HASH)
        return None
    if not verify_password(password, account.password_hash):
        return None
    return account
