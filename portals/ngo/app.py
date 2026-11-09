"""NGO portal backend (port 8003). Photo verification, availability, collection and delivery come in later phases."""
from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI
from pydantic import BaseModel

from portals.common import create_portal_app, default_core_client
from shared.core_client import CoreClient

STATIC = Path(__file__).resolve().parent / "static"


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

    return app


def build_default_app() -> FastAPI:
    core, s = default_core_client("ngo")
    return create_ngo_app(core, s.jwt_secrets["ngo"], cookie_secure=s.cookie_secure,
                          session_ttl_seconds=s.session_ttl_seconds,
                          landing_url=s.landing_url)
