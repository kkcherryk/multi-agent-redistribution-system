"""NGO portal backend (port 8003). Photo verification, availability, collection and delivery come in later phases."""
from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from portals.common import create_portal_app, default_core_client, document_response
from shared.core_client import CoreClient

STATIC = Path(__file__).resolve().parent / "static"


PICKUP_ACTIONS = ("start", "release", "inspection", "deliver", "store", "dispose")


class AvailabilityIn(BaseModel):
    available: bool


def create_ngo_app(core: CoreClient, jwt_secret: str, **kwargs) -> FastAPI:
    app = create_portal_app(portal="ngo", core=core, jwt_secret=jwt_secret, static_dir=STATIC,
                            allow_registration=True, title="FRS NGO portal", **kwargs)
    agent = app.state.current_account

    @app.put("/api/availability")
    def set_availability(body: AvailabilityIn, account: dict = Depends(agent)):
        """Module 3/5: the NGO agent marks themselves available (green) or unavailable (red)."""
        return core.set_availability(account["id"], body.available)

    # ---- Phase 3 (Module 7): nearby food feed and accept
    @app.get("/api/feed")
    def feed(account: dict = Depends(agent)):
        return core.agent_feed(account["id"])

    @app.post("/api/listings/{listing_id}/accept")
    def accept(listing_id: str, account: dict = Depends(agent)):
        return core.accept_listing(listing_id, account["id"])

    @app.get("/api/pickups")
    def pickups(account: dict = Depends(agent)):
        return core.agent_pickups(account["id"])

    @app.get("/api/listings/{listing_id}/photo")
    def photo(listing_id: str, account: dict = Depends(agent)):
        return document_response(*core.listing_photo(listing_id, viewer_id=account["id"]))

    # ---- Phase 4 (Modules 8, 9): pickup steps, food check, delivery, storage
    @app.post("/api/pickups/{listing_id}/{action}")
    def pickup_action(listing_id: str, action: str, body: dict | None = None, account: dict = Depends(agent)):
        if action not in PICKUP_ACTIONS:
            raise HTTPException(status_code=404, detail="unknown action")
        return core.pickup_action(listing_id, action, account["id"], body)

    @app.get("/api/recipients")
    def recipients(account: dict = Depends(agent)):
        return core.recipients(account["id"])

    @app.post("/api/recipients", status_code=201)
    def add_recipient(body: dict, account: dict = Depends(agent)):
        return core.add_recipient(account["id"], body)

    @app.get("/api/storage")
    def storage(account: dict = Depends(agent)):
        return core.storage(account["id"])

    @app.post("/api/storage/{item_id}/{action}")
    def storage_action(item_id: str, action: str, body: dict, account: dict = Depends(agent)):
        if action not in ("distribute", "dispose"):
            raise HTTPException(status_code=404, detail="unknown action")
        return core.storage_action(item_id, action, account["id"], body)

    return app


def build_default_app() -> FastAPI:
    core, s = default_core_client("ngo")
    return create_ngo_app(core, s.jwt_secrets["ngo"], cookie_secure=s.cookie_secure,
                          session_ttl_seconds=s.session_ttl_seconds,
                          landing_url=s.landing_url)
