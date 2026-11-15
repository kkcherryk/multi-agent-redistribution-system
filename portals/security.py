"""Phase 10 hardening shared by the three portals: security headers, a same-origin check for state-changing
requests, and limits on repeated sign-in / registration attempts."""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# Pages load Leaflet (unpkg), Chart.js (jsdelivr) and OpenStreetMap tiles; everything else comes from the portal.
PAGE_CSP = ("default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://unpkg.com https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://unpkg.com https://fonts.googleapis.com; "
            "img-src 'self' data: blob: https://tile.openstreetmap.org https://*.tile.openstreetmap.org https://unpkg.com https://images.unsplash.com; "
            "connect-src 'self'; font-src 'self' data: https://fonts.gstatic.com; object-src 'none'; base-uri 'self'; "
            "form-action 'self'; frame-ancestors 'none'")
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "geolocation=(self), camera=(), microphone=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class AttemptLimiter:
    """Sliding-window counter: at most ``limit`` events per key within ``window`` seconds."""

    def __init__(self, limit: int, window: float, clock=time.monotonic) -> None:
        self.limit, self.window, self.clock = limit, window, clock
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def _trim(self, key: str) -> deque:
        q, cutoff = self._hits[key], self.clock() - self.window
        while q and q[0] <= cutoff:
            q.popleft()
        return q

    def retry_after(self, key: str) -> int:
        """Seconds until another attempt is allowed (0 = allowed now)."""
        with self._lock:
            q = self._trim(key)
            if len(q) < self.limit:
                return 0
            return max(1, int(q[0] + self.window - self.clock()) + 1)

    def hit(self, key: str) -> None:
        with self._lock:
            self._trim(key).append(self.clock())

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def too_many(retry_after: int, what: str) -> JSONResponse:
    minutes = max(1, round(retry_after / 60))
    return JSONResponse(status_code=429, headers={"Retry-After": str(retry_after)},
                        content={"detail": f"too many {what}; try again in about {minutes} minute(s)"})


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def harden(request: Request, call_next):
        # Same-origin check: a browser always sends Origin on cross-site POST/PUT/DELETE; refuse those.
        origin = request.headers.get("origin")
        if request.method in UNSAFE_METHODS and origin and origin != "null":
            host = request.headers.get("host", "")
            if urlsplit(origin).netloc != host:
                return JSONResponse(status_code=403, content={"detail": "cross-site request refused"})
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        response.headers.setdefault("Content-Security-Policy", PAGE_CSP)  # documents keep their stricter sandbox
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")  # personal data is never cached
        return response
