"""Account operations, shared by the HTTP API and the team's CLI scripts."""
from __future__ import annotations

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


def authenticate(session: Session, email: str, password: str, *, role: str) -> Account | None:
    """Return the account if the credentials are valid *and* belong to the given role."""
    account = get_by_email(session, email)
    if account is None:
        verify_password(password, _DUMMY_HASH)
        return None
    if not verify_password(password, account.password_hash) or account.role != role:
        return None
    return account
