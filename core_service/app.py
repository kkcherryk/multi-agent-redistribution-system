"""Core data service: the single authoritative store for records the three portals share.

Only portal backends talk to it. Each portal presents its own service key (``X-Service-Key``); the key identifies
the calling portal and the core enforces what that portal may read or write (data ownership boundaries).
"""
from __future__ import annotations

import secrets

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from shared.config import PORTAL_ROLE, ROLES

from . import accounts
from .db import make_engine
from .models import Account, AccountStatus, Base, Event
from .schemas import AccountOut, CredentialsIn, EventOut, RegisterIn

# Portals whose users may self-register. Host accounts are created by the project team only (scripts/create_host.py).
SELF_REGISTER_PORTALS = {"restaurant", "ngo"}


def create_core_app(db_url: str, service_keys: dict[str, str]) -> FastAPI:
    engine = make_engine(db_url)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(engine, expire_on_commit=False)

    app = FastAPI(title="FRS Core Data Service", version="0.1.0")
    app.state.engine = engine
    app.state.SessionLocal = SessionLocal
    keys = [(key, portal) for portal, key in service_keys.items()]

    def get_db():
        with SessionLocal() as session:
            yield session

    def caller_portal(x_service_key: str | None = Header(default=None)) -> str:
        if x_service_key:
            match = None
            for key, portal in keys:  # check every key so timing does not reveal which one matched
                if secrets.compare_digest(key.encode(), x_service_key.encode()):
                    match = portal
            if match:
                return match
        raise HTTPException(status_code=401, detail="invalid service key")

    def require_admin(portal: str = Depends(caller_portal)) -> str:
        if portal != "admin":
            raise HTTPException(status_code=403, detail="admin portal only")
        return portal

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "core"}

    @app.post("/internal/accounts", response_model=AccountOut, status_code=201)
    def register(body: RegisterIn, portal: str = Depends(caller_portal), db: Session = Depends(get_db)):
        if portal not in SELF_REGISTER_PORTALS:
            raise HTTPException(status_code=403, detail="registration is not available on this portal")
        try:
            account = accounts.create_account(db, role=PORTAL_ROLE[portal], email=body.email, password=body.password,
                                              display_name=body.display_name, phone=body.phone, portal=portal)
        except accounts.EmailTaken:
            raise HTTPException(status_code=409, detail="an account with this email already exists")
        db.commit()
        return account

    @app.post("/internal/auth/verify", response_model=AccountOut)
    def verify_credentials(body: CredentialsIn, portal: str = Depends(caller_portal), db: Session = Depends(get_db)):
        account = accounts.authenticate(db, body.email, body.password, role=PORTAL_ROLE[portal])
        if account is None:
            accounts.record_event(db, "auth.failed", portal=portal, email_domain=body.email.rsplit("@", 1)[-1][:60])
            db.commit()
            raise HTTPException(status_code=401, detail="invalid email or password")
        accounts.record_event(db, "auth.login", portal=portal, actor_id=account.id, subject_id=account.id)
        db.commit()
        return account

    @app.get("/internal/accounts/{account_id}", response_model=AccountOut)
    def get_account(account_id: str, portal: str = Depends(caller_portal), db: Session = Depends(get_db)):
        account = db.get(Account, account_id)
        # Non-admin portals may only read accounts of their own role; respond 404 to avoid leaking existence.
        if account is None or (portal != "admin" and account.role != PORTAL_ROLE[portal]):
            raise HTTPException(status_code=404, detail="account not found")
        return account

    @app.get("/internal/accounts", response_model=list[AccountOut])
    def list_accounts(role: str | None = Query(default=None), status: str | None = Query(default=None),
                      _: str = Depends(require_admin), db: Session = Depends(get_db)):
        if role is not None and role not in ROLES:
            raise HTTPException(status_code=422, detail="unknown role")
        if status is not None and status not in AccountStatus.ALL:
            raise HTTPException(status_code=422, detail="unknown status")
        q = select(Account).order_by(Account.created_at.desc())
        if role:
            q = q.where(Account.role == role)
        if status:
            q = q.where(Account.status == status)
        return db.scalars(q).all()

    @app.get("/internal/events", response_model=list[EventOut])
    def list_events(limit: int = Query(default=50, ge=1, le=500), _: str = Depends(require_admin),
                    db: Session = Depends(get_db)):
        return db.scalars(select(Event).order_by(Event.id.desc()).limit(limit)).all()

    return app


def build_default_app() -> FastAPI:
    from shared.config import get_settings

    s = get_settings()
    return create_core_app(s.db_url, s.service_keys)
