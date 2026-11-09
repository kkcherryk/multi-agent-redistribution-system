"""Admin / Host portal backend (port 8001). No self-registration: host accounts are created by the project team."""
from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, Query

from portals.common import create_portal_app, default_core_client
from shared.core_client import CoreClient

STATIC = Path(__file__).resolve().parent / "static"


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

    return app


def build_default_app() -> FastAPI:
    core, s = default_core_client("admin")
    return create_admin_app(core, s.jwt_secrets["admin"], cookie_secure=s.cookie_secure,
                            session_ttl_seconds=s.session_ttl_seconds,
                            landing_url=s.landing_url)
