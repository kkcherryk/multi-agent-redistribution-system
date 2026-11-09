"""Restaurant portal backend (port 8002). Onboarding details, photos and food listings are added in later phases."""
from __future__ import annotations

from pathlib import Path

import json

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile

from portals.common import MAX_UPLOAD_BYTES, create_portal_app, default_core_client, document_response
from shared.core_client import CoreClient

STATIC = Path(__file__).resolve().parent / "static"


def create_restaurant_app(core: CoreClient, jwt_secret: str, **kwargs) -> FastAPI:
    app = create_portal_app(portal="restaurant", core=core, jwt_secret=jwt_secret, static_dir=STATIC,
                            allow_registration=True, title="FRS Restaurant portal", **kwargs)
    me = app.state.current_account

    # ---- Phase 3 (Module 6): surplus food listings
    @app.post("/api/listings", status_code=201)
    async def create_listing(data: str = Form(..., max_length=5000), photo: UploadFile = File(...),
                             account: dict = Depends(me)):
        try:
            json.loads(data)
        except ValueError:
            raise HTTPException(status_code=422, detail="listing data must be JSON")
        blob = await photo.read(MAX_UPLOAD_BYTES + 1)
        if len(blob) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="the photo is larger than 5 MB")
        return core.create_listing(account["id"], data, photo.filename or "food", blob, photo.content_type)

    @app.get("/api/listings")
    def my_listings(account: dict = Depends(me)):
        return core.restaurant_listings(account["id"])

    @app.post("/api/listings/{listing_id}/cancel")
    def cancel(listing_id: str, account: dict = Depends(me)):
        return core.cancel_listing(listing_id, account["id"])

    @app.get("/api/listings/{listing_id}/photo")
    def photo(listing_id: str, account: dict = Depends(me)):
        return document_response(*core.listing_photo(listing_id, viewer_id=account["id"]))

    return app


def build_default_app() -> FastAPI:
    core, s = default_core_client("restaurant")
    return create_restaurant_app(core, s.jwt_secrets["restaurant"], cookie_secure=s.cookie_secure,
                                 session_ttl_seconds=s.session_ttl_seconds,
                                 landing_url=s.landing_url)
