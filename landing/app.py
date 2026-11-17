"""Public landing page (port 8004): the story of the project and the way into the three portals (Module 1)."""
from __future__ import annotations

import html
import time
from pathlib import Path
from typing import Callable

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

STATIC = Path(__file__).resolve().parent / "static"

# The landing page loads free photos from Unsplash and fonts from Google Fonts, so it gets its own CSP.
LANDING_CSP = ("default-src 'self'; script-src 'self'; "
               "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
               "img-src 'self' data: https://images.unsplash.com; font-src https://fonts.gstatic.com; "
               "connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")


def core_impact_fetcher(core_url: str) -> Callable[[], dict]:
    """Read the public impact totals from the core service."""
    import httpx

    def fetch() -> dict:
        r = httpx.get(core_url.rstrip("/") + "/public/impact", timeout=5)
        r.raise_for_status()
        return r.json()
    return fetch


def create_landing_app(portal_urls: dict[str, str], fetch_impact: Callable[[], dict] | None = None,
                       cache_seconds: float = 30) -> FastAPI:
    app = FastAPI(title="FRS landing")
    from portals import security
    security.install(app)

    @app.middleware("http")
    async def revalidate_page_and_assets(request, call_next):
        # Without this, browsers keep an old landing.css/js after an update and the new page looks broken.
        # "no-cache" still lets them reuse unchanged files after a quick check (304 Not Modified).
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    for key, token in (("restaurant", "RESTAURANT_URL"), ("ngo", "NGO_URL")):  # the host portal is never linked publicly
        page = page.replace("{{" + token + "}}", html.escape(portal_urls[key]))

    @app.get("/", response_class=HTMLResponse)
    def index():
        return HTMLResponse(page, headers={"Content-Security-Policy": LANDING_CSP})

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "landing"}

    cache: dict = {"at": 0.0, "data": None}

    @app.get("/api/impact")
    def impact():
        """Totals for the impact counters, cached briefly so page views don't hammer the core."""
        if fetch_impact is None:
            return JSONResponse({"detail": "Impact totals are not available."}, status_code=503)
        now = time.monotonic()
        if cache["data"] is None or now - cache["at"] > cache_seconds:
            try:
                cache["data"], cache["at"] = fetch_impact(), now
            except Exception:  # core down: serve the last good totals if we have them
                if cache["data"] is None:
                    return JSONResponse({"detail": "Impact totals are not available right now."}, status_code=503)
        return cache["data"]

    app.mount("/static", StaticFiles(directory=STATIC), name="landing-static")
    return app


def build_default_app() -> FastAPI:
    from shared.config import get_settings

    s = get_settings()
    return create_landing_app(s.portal_urls, fetch_impact=core_impact_fetcher(s.core_url))
