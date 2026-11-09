"""Admin / Host portal backend (port 8001). No self-registration: host accounts are created by the project team."""
from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, Query
from pydantic import BaseModel, Field

from portals.common import create_portal_app, default_core_client, document_response
from shared.core_client import CoreClient

STATIC = Path(__file__).resolve().parent / "static"


class ReviewIn(BaseModel):
    decision: str = Field(max_length=20)
    note: str | None = Field(default=None, max_length=2000)


def create_admin_app(core: CoreClient, jwt_secret: str, **kwargs) -> FastAPI:
    app = create_portal_app(portal="admin", core=core, jwt_secret=jwt_secret, static_dir=STATIC,
                            allow_registration=False, title="FRS Admin / Host portal", **kwargs)
    host = app.state.current_account

    @app.get("/api/accounts")
    def accounts(role: str | None = None, status: str | None = None, _: dict = Depends(host)):
        return core.list_accounts(role=role, status=status)

    @app.get("/api/events")
    def events(limit: int = Query(default=50, ge=1, le=500), _: dict = Depends(host)):
        return core.list_events(limit=limit)

    # ---- Phase 2: verification queue, decisions, evidence viewer, agent directory
    @app.get("/api/review-queue")
    def review_queue(status: str = "under_review", _: dict = Depends(host)):
        return core.review_queue(status)

    @app.get("/api/accounts/{account_id}/onboarding")
    def onboarding(account_id: str, _: dict = Depends(host)):
        return core.get_onboarding(account_id)

    @app.post("/api/accounts/{account_id}/review")
    def review(account_id: str, body: ReviewIn, me: dict = Depends(host)):
        return core.review(account_id, body.decision, body.note, reviewer_id=me["id"])

    @app.get("/api/documents/{doc_id}")
    def document(doc_id: str, _: dict = Depends(host)):
        data, ctype = core.document_content(doc_id)
        return document_response(data, ctype)

    @app.get("/api/ngo-directory")
    def ngo_directory(_: dict = Depends(host)):
        return core.ngo_directory()

    # ---- Phase 3: all food listings for host oversight
    @app.get("/api/listings")
    def listings(status: str | None = None, _: dict = Depends(host)):
        return core.all_listings(status)

    @app.get("/api/listings/{listing_id}/photo")
    def listing_photo(listing_id: str, _: dict = Depends(host)):
        return document_response(*core.listing_photo(listing_id))

    return app


def build_default_app() -> FastAPI:
    core, s = default_core_client("admin")
    return create_admin_app(core, s.jwt_secrets["admin"], cookie_secure=s.cookie_secure,
                            session_ttl_seconds=s.session_ttl_seconds,
                            landing_url=s.landing_url)
