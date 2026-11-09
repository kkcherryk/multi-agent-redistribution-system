"""Restaurant portal backend (port 8002). Onboarding details, photos and food listings are added in later phases."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from portals.common import create_portal_app, default_core_client
from shared.core_client import CoreClient

STATIC = Path(__file__).resolve().parent / "static"


def create_restaurant_app(core: CoreClient, jwt_secret: str, **kwargs) -> FastAPI:
    return create_portal_app(portal="restaurant", core=core, jwt_secret=jwt_secret, static_dir=STATIC,
                             allow_registration=True, title="FRS Restaurant portal", **kwargs)


def build_default_app() -> FastAPI:
    core, s = default_core_client("restaurant")
    return create_restaurant_app(core, s.jwt_secrets["restaurant"], cookie_secure=s.cookie_secure,
                                 session_ttl_seconds=s.session_ttl_seconds,
                                 landing_url=s.landing_url)
