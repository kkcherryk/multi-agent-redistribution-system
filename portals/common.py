"""Factory for a portal backend. Each portal (Admin/Host, Restaurant, NGO) is a separate FastAPI app with its own
frontend, session secret and service key; this module only holds the code they have in common."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from shared.config import PORTAL_ROLE
from shared.core_client import CoreClient, CoreError, CoreUnavailable
from shared.security import decode_session_token, issue_session_token

from . import security

COMMON_STATIC = Path(__file__).resolve().parent / "common_static"
MAX_UPLOAD_BYTES = 5 * 1024 * 1024


class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=200)


class ReadIn(BaseModel):
    ids: list[int] | None = Field(default=None, max_length=200)


class RegisterIn(BaseModel):
    # Validation of these fields is authoritative in the core service; limits here just bound request size.
    email: str = Field(max_length=254)
    password: str = Field(max_length=200)
    display_name: str = Field(max_length=200)
    phone: str | None = Field(default=None, max_length=40)


def create_portal_app(*, portal: str, core: CoreClient, jwt_secret: str, static_dir: Path,
                      allow_registration: bool, cookie_secure: bool = False,
                      session_ttl_seconds: int = 8 * 3600, title: str | None = None,
                      landing_url: str = "/") -> FastAPI:
    role = PORTAL_ROLE[portal]
    cookie_name = f"frs_{portal}_session"
    app = FastAPI(title=title or f"FRS {portal} portal", version="0.1.0")
    app.state.portal = portal
    app.state.core = core
    security.install(app)
    # Phase 10: slow down password guessing and bulk sign-ups (per portal process, in memory).
    login_fails = security.AttemptLimiter(limit=5, window=15 * 60)      # per email + IP
    login_fails_ip = security.AttemptLimiter(limit=30, window=15 * 60)  # per IP, any email
    signups = security.AttemptLimiter(limit=10, window=60 * 60)         # per IP
    app.state.limiters = (login_fails, login_fails_ip, signups)

    @app.middleware("http")
    async def revalidate_pages_and_scripts(request: Request, call_next):
        # Without this, browsers keep using cached copies of the portal's scripts after an update.
        # "no-cache" still lets them reuse unchanged files after a quick check (304 Not Modified).
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.exception_handler(CoreError)
    def _core_error(_request: Request, exc: CoreError):
        # Surface the core's 4xx decisions, but never forward a 401 about *service* auth as a user 401.
        status = exc.status_code if exc.status_code < 500 else 502
        if exc.detail == "invalid service key":
            status = 502
        return JSONResponse(status_code=status, content={"detail": exc.detail})

    @app.exception_handler(CoreUnavailable)
    def _core_down(_request: Request, _exc: CoreUnavailable):
        return JSONResponse(status_code=503, content={"detail": "core data service unavailable"})

    def _set_session(response: Response, account: dict) -> None:
        token = issue_session_token(jwt_secret, account_id=account["id"], role=account["role"], portal=portal,
                                    ttl_seconds=session_ttl_seconds)
        response.set_cookie(cookie_name, token, httponly=True, samesite="lax", secure=cookie_secure,
                            max_age=session_ttl_seconds, path="/")

    def current_account(request: Request) -> dict:
        token = request.cookies.get(cookie_name)
        claims = decode_session_token(jwt_secret, token, portal=portal) if token else None
        if not claims or claims.get("role") != role:
            raise HTTPException(status_code=401, detail="not signed in")
        try:
            account = core.get_account(claims["sub"])  # always fetch fresh status from the authoritative store
        except CoreError as exc:
            if exc.status_code == 404:
                raise HTTPException(status_code=401, detail="not signed in")
            raise
        if account["email"].endswith("@erased.invalid"):  # Phase 10: an erased account is signed out everywhere
            raise HTTPException(status_code=401, detail="not signed in")
        return account

    app.state.current_account = current_account
    api = APIRouter(prefix="/api")

    @api.get("/portal")
    def portal_info():
        return {"portal": portal, "role": role, "allow_registration": allow_registration, "landing_url": landing_url}

    @api.post("/login")
    def login(body: LoginIn, request: Request, response: Response):
        ip = security.client_ip(request)
        key = f"{body.email.strip().lower()}|{ip}"
        wait = max(login_fails.retry_after(key), login_fails_ip.retry_after(ip))
        if wait:
            return security.too_many(wait, "sign-in attempts")
        try:
            account = core.verify_credentials(body.email, body.password)
        except CoreError as exc:
            if exc.status_code == 401:
                login_fails.hit(key)
                login_fails_ip.hit(ip)
            raise
        login_fails.reset(key)
        _set_session(response, account)
        return account

    if allow_registration:
        @api.post("/register", status_code=201)
        def register(body: RegisterIn, request: Request, response: Response):
            ip = security.client_ip(request)
            if wait := signups.retry_after(ip):
                return security.too_many(wait, "registrations from this network")
            signups.hit(ip)
            account = core.register(**body.model_dump())
            _set_session(response, account)
            return account

    @api.post("/logout", status_code=204)
    def logout(response: Response):
        response.delete_cookie(cookie_name, path="/")
        response.status_code = 204
        return response

    @api.get("/me")
    def me(account: dict = Depends(current_account)):
        return account

    # Phase 10: every signed-in user has an inbox (the bell in the page header).
    @api.get("/notifications")
    def notifications(limit: int = Query(default=20, ge=1, le=50), account: dict = Depends(current_account)):
        return core.notifications(account["id"], limit)

    @api.post("/notifications/read")
    def notifications_read(body: ReadIn, account: dict = Depends(current_account)):
        return core.mark_notifications_read(account["id"], body.ids)

    if allow_registration:  # applicant portals (restaurant, NGO): onboarding for the signed-in account only
        @api.get("/onboarding")
        def get_onboarding(account: dict = Depends(current_account)):
            return core.get_onboarding(account["id"])

        @api.put("/profile")
        def put_profile(body: dict, account: dict = Depends(current_account)):
            return core.put_profile(account["id"], body)

        @api.post("/documents", status_code=201)
        async def upload_document(kind: str = Form(..., max_length=40), file: UploadFile = File(...),
                                  account: dict = Depends(current_account)):
            data = await file.read(MAX_UPLOAD_BYTES + 1)
            if len(data) > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail="the file is larger than 5 MB")
            return core.upload_document(account["id"], kind, file.filename or "upload", data, file.content_type)

        @api.delete("/documents/{doc_id}", status_code=204)
        def delete_document(doc_id: str, account: dict = Depends(current_account)):
            core.delete_document(account["id"], doc_id)
            return Response(status_code=204)

        @api.get("/documents/{doc_id}")
        def document(doc_id: str, account: dict = Depends(current_account)):
            data, ctype = core.document_content(doc_id, account_id=account["id"])
            return document_response(data, ctype)

        @api.post("/submit")
        def submit(account: dict = Depends(current_account)):
            return core.submit_application(account["id"])

    app.include_router(api)

    @app.get("/health")
    def health():
        try:
            core.health()
            core_ok = True
        except (CoreError, CoreUnavailable):
            core_ok = False
        return {"status": "ok", "service": f"{portal}-portal", "core_reachable": core_ok}

    app.mount("/static/common", StaticFiles(directory=COMMON_STATIC), name="common-static")
    app.mount("/static/portal", StaticFiles(directory=static_dir), name="portal-static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(static_dir / "index.html")

    return app


def document_response(data: bytes, content_type: str) -> Response:
    # nosniff + a sandboxing CSP so an uploaded file can never run script in the portal's origin.
    return Response(data, media_type=content_type, headers={
        "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store",
        "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; sandbox"})


def default_core_client(portal: str) -> tuple[CoreClient, object]:
    from shared.config import get_settings

    s = get_settings()
    return CoreClient(s.core_url, s.service_keys[portal]), s
