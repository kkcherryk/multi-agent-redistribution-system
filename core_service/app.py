"""Core data service: the single authoritative store for records the three portals share.

Only portal backends talk to it. Each portal presents its own service key (``X-Service-Key``); the key identifies
the calling portal and the core enforces what that portal may read or write (data ownership boundaries).
"""
from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from shared.config import DATA_DIR, PORTAL_ROLE, ROLES

from . import accounts, listings, onboarding
from .listings_api import build_router as build_listings_router
from .db import make_engine
from .models import Account, AccountStatus, Base, Document, Event, FoodListing, NgoProfile
from .schemas import (PROFILE_SCHEMAS, AccountOut, AvailabilityIn, CredentialsIn, EventOut, RegisterIn,
                      ReviewIn)

# Portals whose users may self-register. Host accounts are created by the project team only (scripts/create_host.py).
SELF_REGISTER_PORTALS = {"restaurant", "ngo"}


def account_out(account: Account, ngo_profile: NgoProfile | None = None) -> dict:
    data = AccountOut.model_validate(account).model_dump()
    data["badges"] = onboarding.badges(account, ngo_profile)
    return data


def create_core_app(db_url: str, service_keys: dict[str, str], upload_dir: Path | None = None,
                    match_radius_km: float = listings.DEFAULT_RADIUS_KM) -> FastAPI:
    engine = make_engine(db_url)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(engine, expire_on_commit=False)
    upload_dir = Path(upload_dir or DATA_DIR / "uploads")

    app = FastAPI(title="FRS Core Data Service", version="0.3.0")
    app.state.engine = engine
    app.state.SessionLocal = SessionLocal
    app.state.upload_dir = upload_dir
    keys = [(key, portal) for portal, key in service_keys.items()]

    @app.exception_handler(onboarding.RuleError)
    def _rule_error(_request, exc: onboarding.RuleError):
        return JSONResponse(status_code=exc.status, content={"detail": exc.message})

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

    def visible_account(db: Session, account_id: str, portal: str) -> Account:
        account = db.get(Account, account_id)
        # Non-admin portals may only see accounts of their own role; respond 404 to avoid leaking existence.
        if account is None or (portal != "admin" and account.role != PORTAL_ROLE[portal]):
            raise HTTPException(status_code=404, detail="account not found")
        return account

    def own_account(db: Session, account_id: str, portal: str) -> Account:
        """Accounts the *applicant's* portal may modify (the host reviews but never edits applications)."""
        if portal == "admin":
            raise HTTPException(status_code=403, detail="applicants edit their own applications")
        return visible_account(db, account_id, portal)

    def ngo_profile_of(db: Session, account: Account) -> NgoProfile | None:
        return db.get(NgoProfile, account.id) if account.role == "ngo" else None

    # ------------------------------------------------------------------ Phase 1: accounts & auth

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "core"}

    @app.post("/internal/accounts", status_code=201)
    def register(body: RegisterIn, portal: str = Depends(caller_portal), db: Session = Depends(get_db)):
        if portal not in SELF_REGISTER_PORTALS:
            raise HTTPException(status_code=403, detail="registration is not available on this portal")
        try:
            account = accounts.create_account(db, role=PORTAL_ROLE[portal], email=body.email, password=body.password,
                                              display_name=body.display_name, phone=body.phone, portal=portal)
        except accounts.EmailTaken:
            raise HTTPException(status_code=409, detail="an account with this email already exists")
        db.commit()
        return account_out(account)

    @app.post("/internal/auth/verify")
    def verify_credentials(body: CredentialsIn, portal: str = Depends(caller_portal), db: Session = Depends(get_db)):
        account = accounts.authenticate(db, body.email, body.password, role=PORTAL_ROLE[portal])
        if account is None:
            accounts.record_event(db, "auth.failed", portal=portal, email_domain=body.email.rsplit("@", 1)[-1][:60])
            db.commit()
            raise HTTPException(status_code=401, detail="invalid email or password")
        accounts.record_event(db, "auth.login", portal=portal, actor_id=account.id, subject_id=account.id)
        db.commit()
        return account_out(account, ngo_profile_of(db, account))

    @app.get("/internal/accounts/{account_id}")
    def get_account(account_id: str, portal: str = Depends(caller_portal), db: Session = Depends(get_db)):
        account = visible_account(db, account_id, portal)
        return account_out(account, ngo_profile_of(db, account))

    @app.get("/internal/accounts")
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
        ngo_profiles = {p.account_id: p for p in db.scalars(select(NgoProfile))}
        return [account_out(a, ngo_profiles.get(a.id)) for a in db.scalars(q)]

    @app.get("/internal/events", response_model=list[EventOut])
    def list_events(limit: int = Query(default=50, ge=1, le=500), _: str = Depends(require_admin),
                    db: Session = Depends(get_db)):
        return db.scalars(select(Event).order_by(Event.id.desc()).limit(limit)).all()

    # ------------------------------------------------------------------ Phase 2: onboarding (applicant side)

    @app.get("/internal/accounts/{account_id}/onboarding")
    def get_onboarding(account_id: str, portal: str = Depends(caller_portal), db: Session = Depends(get_db)):
        account = visible_account(db, account_id, portal)
        if account.role not in onboarding.PROFILE_MODELS:
            raise HTTPException(status_code=404, detail="no onboarding for this account type")
        return onboarding.onboarding_view(db, account)

    @app.put("/internal/accounts/{account_id}/profile")
    def put_profile(account_id: str, body: dict, portal: str = Depends(caller_portal), db: Session = Depends(get_db)):
        account = own_account(db, account_id, portal)
        try:
            data = PROFILE_SCHEMAS[account.role].model_validate(body).model_dump()
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=[
                {"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()])
        onboarding.save_profile(db, account, data, portal=portal)
        db.commit()
        return onboarding.onboarding_view(db, account)

    @app.post("/internal/accounts/{account_id}/documents", status_code=201)
    async def upload_document(account_id: str, kind: str = Form(...), file: UploadFile = File(...),
                              portal: str = Depends(caller_portal), db: Session = Depends(get_db)):
        account = own_account(db, account_id, portal)
        data = await file.read(onboarding.MAX_UPLOAD_BYTES + 1)
        doc = onboarding.add_document(db, account, kind=kind, filename=file.filename or "upload", data=data,
                                      upload_dir=upload_dir, portal=portal)
        db.commit()
        return onboarding.document_dict(doc)

    @app.delete("/internal/accounts/{account_id}/documents/{doc_id}", status_code=204)
    def delete_document(account_id: str, doc_id: str, portal: str = Depends(caller_portal),
                        db: Session = Depends(get_db)):
        account = own_account(db, account_id, portal)
        doc = db.get(Document, doc_id)
        if doc is None or doc.account_id != account.id:
            raise HTTPException(status_code=404, detail="document not found")
        onboarding.delete_document(db, account, doc, upload_dir=upload_dir, portal=portal)
        db.commit()
        return Response(status_code=204)

    @app.get("/internal/documents/{doc_id}/content")
    def document_content(doc_id: str, account_id: str | None = Query(default=None),
                         portal: str = Depends(caller_portal), db: Session = Depends(get_db)):
        doc = db.get(Document, doc_id)
        # Only the host, or the owner (whose portal passes the signed-in account id), may read a document.
        if doc is None or (portal != "admin" and account_id != doc.account_id):
            raise HTTPException(status_code=404, detail="document not found")
        visible_account(db, doc.account_id, portal)
        path = upload_dir / doc.storage_key
        if not path.exists():
            raise HTTPException(status_code=404, detail="document file missing")
        return Response(path.read_bytes(), media_type=doc.content_type,
                        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})

    @app.post("/internal/accounts/{account_id}/submit")
    def submit_application(account_id: str, portal: str = Depends(caller_portal), db: Session = Depends(get_db)):
        account = own_account(db, account_id, portal)
        onboarding.submit(db, account, portal=portal)
        db.commit()
        return onboarding.onboarding_view(db, account)

    @app.put("/internal/accounts/{account_id}/availability")
    def put_availability(account_id: str, body: AvailabilityIn, portal: str = Depends(caller_portal),
                         db: Session = Depends(get_db)):
        if portal != "ngo":
            raise HTTPException(status_code=403, detail="availability is set from the NGO portal")
        account = own_account(db, account_id, portal)
        profile = onboarding.set_availability(db, account, body.available, portal=portal)
        if body.available and account.status == AccountStatus.VERIFIED:
            # Alert a newly available agent about food already waiting nearby (notify_nearby skips duplicates).
            listings.expire_due(db)
            for active in db.scalars(select(FoodListing).where(FoodListing.status == listings.ListingStatus.ACTIVE)):
                listings.notify_nearby(db, active, match_radius_km)
        db.commit()
        return account_out(account, profile)

    # ------------------------------------------------------------------ Phase 2: host review & directory

    @app.get("/internal/review-queue")
    def review_queue(status: str = Query(default=AccountStatus.UNDER_REVIEW), _: str = Depends(require_admin),
                     db: Session = Depends(get_db)):
        if status not in AccountStatus.ALL:
            raise HTTPException(status_code=422, detail="unknown status")
        q = (select(Account).where(Account.role.in_(tuple(onboarding.PROFILE_MODELS)), Account.status == status)
             .order_by(Account.updated_at))
        return [{"account": account_out(a, ngo_profile_of(db, a)), **onboarding.onboarding_view(db, a)}
                for a in db.scalars(q)]

    @app.post("/internal/accounts/{account_id}/review")
    def review_application(account_id: str, body: ReviewIn, _: str = Depends(require_admin),
                           db: Session = Depends(get_db)):
        account = visible_account(db, account_id, "admin")
        if body.reviewer_id:
            reviewer = db.get(Account, body.reviewer_id)
            if reviewer is None or reviewer.role != "host":
                raise HTTPException(status_code=422, detail="reviewer must be a host account")
        rec = onboarding.review(db, account, decision=body.decision, note=body.note, reviewer_id=body.reviewer_id)
        db.commit()
        return {"review": onboarding.review_dict(rec), "account": account_out(account, ngo_profile_of(db, account))}

    @app.get("/internal/ngo-directory")
    def ngo_directory(_: str = Depends(require_admin), db: Session = Depends(get_db)):
        """Module 5 agent directory: every NGO agent with verification and availability shown separately."""
        rows = []
        for a in db.scalars(select(Account).where(Account.role == "ngo").order_by(Account.display_name)):
            p = db.get(NgoProfile, a.id)
            rows.append({"account": account_out(a, p), "profile": onboarding.profile_dict(p)})
        return rows

    # ------------------------------------------------------------------ Phase 3: food listings & matching
    app.include_router(build_listings_router(get_db=get_db, caller_portal=caller_portal, require_admin=require_admin,
                                             upload_dir=upload_dir / "listings", radius_km=match_radius_km))
    return app


def build_default_app() -> FastAPI:
    from shared.config import get_settings

    s = get_settings()
    return create_core_app(s.db_url, s.service_keys)
