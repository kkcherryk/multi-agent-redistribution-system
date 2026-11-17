"""Extra ways to sign in, shared by the three portals:

* Continue with Google      POST /api/login/google        {credential}
* One-time code            POST /api/login/code/start    {identifier}   (email address or phone number)
                            POST /api/login/code/verify   {identifier, code}
  The code goes by email (free, through the configured mailbox). It goes by SMS only when an SMS
  provider is configured and the person typed a phone number.
* Forgot password (email)   POST /api/password/forgot     {email}
                            POST /api/password/reset      {token, password}

Answers never reveal whether an email or number has an account. Codes and reset links expire quickly and
are rate limited. Email/password and phone/password sign-in stay in /api/login.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time

import jwt
from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field

from shared.core_client import CoreClient, CoreError
from shared.messaging import SignInServices

from . import security

CODE_TTL = 5 * 60
CODE_TRIES = 5
RESET_TTL = 30 * 60


class GoogleIn(BaseModel):
    credential: str = Field(min_length=20, max_length=4096)


class CodeStartIn(BaseModel):
    identifier: str = Field(min_length=3, max_length=254)


class CodeVerifyIn(BaseModel):
    identifier: str = Field(min_length=3, max_length=254)
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


class ForgotIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class ResetIn(BaseModel):
    token: str = Field(min_length=20, max_length=2048)
    password: str = Field(min_length=8, max_length=72)


def _code_key(identifier: str) -> str:
    """Email addresses are keyed as typed (lower case); phone numbers by their last 10 digits."""
    ident = identifier.strip().lower()
    return ident if "@" in ident else "".join(ch for ch in ident if ch.isdigit())[-10:]


def install(app: FastAPI, *, core: CoreClient, portal: str, role: str, jwt_secret: str, set_session,
            allow_registration: bool, public_url: str | None, services: SignInServices) -> None:
    app.state.signin = services
    codes: dict[str, tuple[str, float, int]] = {}  # email / phone digits -> (code hash, expires at, tries left)
    code_sends = security.AttemptLimiter(limit=3, window=15 * 60)        # per email / phone number
    code_sends_ip = security.AttemptLimiter(limit=10, window=15 * 60)    # per network
    forgot_mail = security.AttemptLimiter(limit=3, window=60 * 60)       # per email address
    forgot_ip = security.AttemptLimiter(limit=10, window=60 * 60)        # per network
    google_ip = security.AttemptLimiter(limit=30, window=15 * 60)
    reset_aud = f"frs-{portal}-reset"

    def code_hash(digits: str, code: str) -> str:
        return hmac.new(jwt_secret.encode(), f"{digits}:{code}".encode(), hashlib.sha256).hexdigest()

    def find(identifier: str, method: str | None = None) -> dict | None:
        try:
            return core.find_for_signin(identifier, method)
        except CoreError as exc:
            if exc.status_code in (404, 422):
                return None
            raise

    # ------------------------------------------------------------------ Google
    @app.post("/api/login/google")
    def login_google(body: GoogleIn, request: Request, response: Response):
        if not services.google_verify:
            raise HTTPException(status_code=404, detail="Google sign-in is not set up")
        ip = security.client_ip(request)
        if wait := google_ip.retry_after(ip):
            return security.too_many(wait, "sign-in attempts")
        google_ip.hit(ip)
        try:
            who = services.google_verify(body.credential)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc))
        found = find(who["email"], "google")
        if found:
            account = found["account"]
        elif allow_registration:
            # first time with Google: start an application under the Google email, with an unusable random
            # password (they can set one later with "Forgot password?")
            account = core.register(email=who["email"], password=secrets.token_urlsafe(24),
                                    display_name=who["name"][:120] if len(who["name"]) >= 2 else who["email"],
                                    phone=None)
        else:
            raise HTTPException(status_code=401, detail="no host account uses this Google email")
        set_session(response, account)
        return account

    # ------------------------------------------------------------------ one-time code (email, or SMS if set up)
    @app.post("/api/login/code/start")
    def code_start(body: CodeStartIn, request: Request):
        key, ip = _code_key(body.identifier), security.client_ip(request)
        if "@" not in key and len(key) < 10:
            raise HTTPException(status_code=422, detail="enter your email address or a 10-digit phone number")
        wait = max(code_sends.retry_after(key), code_sends_ip.retry_after(ip))
        if wait:
            return security.too_many(wait, "codes for this account")
        code_sends.hit(key)
        code_sends_ip.hit(ip)
        by_sms = "@" not in key and services.sms is not None
        found = find(body.identifier)
        if found:
            account = found["account"]
            code = f"{secrets.randbelow(10 ** 6):06d}"
            codes[key] = (code_hash(key, code), time.time() + CODE_TTL, CODE_TRIES)
            text = (f"{code} is your Food Redistribution sign-in code. It expires in 5 minutes. "
                    "Never share it with anyone.")
            if by_sms and account.get("phone"):
                services.sms(account["phone"], text)
            else:
                services.mailer(account["email"], f"Your sign-in code: {code}",
                                f"Hello {account['display_name']},\n\n{text}\n\n"
                                "If you didn't try to sign in, you can ignore this email.\n")
        # same answer either way, so nobody can test which emails or numbers are registered
        where = "phone" if by_sms else "email address"
        return {"sent": True, "channel": "sms" if by_sms else "email",
                "detail": f"If this belongs to an account, a 6-digit code has been sent to its {where}."}

    @app.post("/api/login/code/verify")
    def code_verify(body: CodeVerifyIn, response: Response):
        key = _code_key(body.identifier)
        entry = codes.get(key)
        if not entry or entry[1] < time.time():
            codes.pop(key, None)
            raise HTTPException(status_code=401, detail="this code has expired - ask for a new one")
        expected, expires, tries = entry
        if not hmac.compare_digest(expected, code_hash(key, body.code)):
            tries -= 1
            if tries <= 0:
                codes.pop(key, None)
                raise HTTPException(status_code=401, detail="too many wrong codes - ask for a new one")
            codes[key] = (expected, expires, tries)
            raise HTTPException(status_code=401, detail=f"wrong code ({tries} tries left)")
        codes.pop(key, None)
        found = find(body.identifier, "code")
        if not found:
            raise HTTPException(status_code=401, detail="this code has expired - ask for a new one")
        set_session(response, found["account"])
        return found["account"]

    # ------------------------------------------------------------------ forgot password (email link)
    @app.post("/api/password/forgot")
    def forgot(body: ForgotIn, request: Request):
        email, ip = body.email.strip().lower(), security.client_ip(request)
        wait = max(forgot_mail.retry_after(email), forgot_ip.retry_after(ip))
        if wait:
            return security.too_many(wait, "reset requests")
        forgot_mail.hit(email)
        forgot_ip.hit(ip)
        found = find(email) if "@" in email else None
        if found:
            now = int(time.time())
            token = jwt.encode({"sub": found["account"]["id"], "fp": found["password_fp"], "aud": reset_aud,
                                "iat": now, "exp": now + RESET_TTL}, jwt_secret, algorithm="HS256")
            base = (public_url or str(request.base_url)).rstrip("/")
            link = f"{base}/#reset={token}"
            services.mailer(found["account"]["email"], "Reset your Food Redistribution password",
                            f"Hello {found['account']['display_name']},\n\n"
                            f"Someone asked to reset the password for your {portal} account.\n"
                            f"Open this link within 30 minutes to choose a new password:\n\n{link}\n\n"
                            "If it wasn't you, ignore this email - your password stays the same.\n")
        return {"sent": True, "detail": "If this email belongs to an account, a reset link is on its way."}

    @app.post("/api/password/reset")
    def reset(body: ResetIn, response: Response):
        if len(body.password.encode("utf-8")) > 72:
            raise HTTPException(status_code=422, detail="password must be at most 72 bytes")
        try:
            claims = jwt.decode(body.token, jwt_secret, algorithms=["HS256"], audience=reset_aud)
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="this reset link has expired - ask for a new one")
        except jwt.PyJWTError:
            raise HTTPException(status_code=401, detail="this reset link is not valid")
        account = core.set_password(claims["sub"], body.password, claims["fp"])
        set_session(response, account)
        return account
