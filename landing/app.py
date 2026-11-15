"""Public landing page (port 8004): the story of the project and the way into the three portals (Module 1)."""
from __future__ import annotations

import html
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

STATIC = Path(__file__).resolve().parent / "static"

# The landing page loads free photos from Unsplash and fonts from Google Fonts, so it gets its own CSP.
LANDING_CSP = ("default-src 'self'; script-src 'self'; "
               "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
               "img-src 'self' data: https://images.unsplash.com; font-src https://fonts.gstatic.com; "
               "connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")


def create_landing_app(portal_urls: dict[str, str]) -> FastAPI:
    app = FastAPI(title="FRS landing")
    from portals import security
    security.install(app)
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    for key, token in (("restaurant", "RESTAURANT_URL"), ("ngo", "NGO_URL"), ("admin", "ADMIN_URL")):
        page = page.replace("{{" + token + "}}", html.escape(portal_urls[key]))

    @app.get("/", response_class=HTMLResponse)
    def index():
        return HTMLResponse(page, headers={"Content-Security-Policy": LANDING_CSP})

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "landing"}

    app.mount("/static", StaticFiles(directory=STATIC), name="landing-static")
    return app


def build_default_app() -> FastAPI:
    from shared.config import get_settings

    return create_landing_app(get_settings().portal_urls)
