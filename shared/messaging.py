"""Outside services the sign-in screen can use: email (password reset links), SMS (sign-in codes) and Google.

Everything is configured in .env and is optional:

* Email:  FRS_SMTP_HOST / FRS_SMTP_PORT / FRS_SMTP_USER / FRS_SMTP_PASSWORD / FRS_SMTP_FROM
          (for Gmail: smtp.gmail.com, port 587, your address, and a Gmail *app password*).
          Without FRS_SMTP_HOST, emails are printed in the terminal instead, for testing on your own computer.
* SMS:    FRS_SMS_PROVIDER=twilio with FRS_TWILIO_ACCOUNT_SID / FRS_TWILIO_AUTH_TOKEN / FRS_TWILIO_FROM,
          or FRS_SMS_PROVIDER=console to print codes in the terminal (testing only).
          Unset: SMS sign-in is switched off and its button is hidden.
* Google: FRS_GOOGLE_CLIENT_ID (an OAuth "Web application" client from Google Cloud).
          Unset: the "Continue with Google" button is hidden.
"""
from __future__ import annotations

import os
import smtplib
import sys
import time
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Callable

import httpx

Mailer = Callable[[str, str, str], None]          # (to, subject, text)
SmsSender = Callable[[str, str], None]            # (phone, text)
GoogleVerifier = Callable[[str], dict]            # credential -> {"email", "name"}; raises ValueError if invalid


def _console(kind: str, lines: list[str]) -> None:
    bar = "=" * 64
    print(f"\n{bar}\n[{kind} - shown here because no real {kind.lower()} service is configured]", file=sys.stderr)
    for line in lines:
        print(line, file=sys.stderr)
    print(bar, file=sys.stderr, flush=True)


# ------------------------------------------------------------------ email
def smtp_mailer(host: str, port: int, user: str | None, password: str | None, sender: str) -> Mailer:
    def send(to: str, subject: str, text: str) -> None:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = sender, to, subject
        msg.set_content(text)
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            smtp.starttls()
            if user:
                smtp.login(user, password or "")
            smtp.send_message(msg)
    return send


def console_mailer(to: str, subject: str, text: str) -> None:
    _console("EMAIL", [f"To: {to}", f"Subject: {subject}", "", text])


# ------------------------------------------------------------------ SMS
def to_e164(phone: str, default_country: str = "+91") -> str:
    digits = "".join(ch for ch in phone if ch.isdigit())
    if phone.strip().startswith("+"):
        return "+" + digits
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return (default_country + digits) if len(digits) == 10 else "+" + digits


def twilio_sender(account_sid: str, auth_token: str, sender: str, default_country: str = "+91") -> SmsSender:
    def send(phone: str, text: str) -> None:
        r = httpx.post(f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json",
                       auth=(account_sid, auth_token), timeout=15,
                       data={"To": to_e164(phone, default_country), "From": sender, "Body": text})
        r.raise_for_status()
    return send


def console_sms(phone: str, text: str) -> None:
    _console("SMS", [f"To: {phone}", text])


# ------------------------------------------------------------------ Google
GOOGLE_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}


def google_verifier(client_id: str) -> GoogleVerifier:
    """Check a Google Identity Services ID token with Google's tokeninfo endpoint."""
    def verify(credential: str) -> dict:
        r = httpx.get("https://oauth2.googleapis.com/tokeninfo", params={"id_token": credential}, timeout=10)
        if r.status_code != 200:
            raise ValueError("Google did not accept this sign-in")
        info = r.json()
        if info.get("aud") != client_id or info.get("iss") not in GOOGLE_ISSUERS:
            raise ValueError("this Google sign-in was not made for this site")
        if int(info.get("exp", 0)) < time.time():
            raise ValueError("this Google sign-in has expired; please try again")
        if str(info.get("email_verified")).lower() != "true" or not info.get("email"):
            raise ValueError("your Google account's email address is not verified")
        return {"email": info["email"].lower(), "name": info.get("name") or info["email"].split("@")[0]}
    return verify


# ------------------------------------------------------------------ settings
@dataclass
class SignInServices:
    mailer: Mailer = console_mailer
    mail_is_real: bool = False
    sms: SmsSender | None = None
    sms_is_real: bool = False
    google_client_id: str | None = None
    google_verify: GoogleVerifier | None = None

    @classmethod
    def from_env(cls) -> "SignInServices":
        s = cls()
        host = os.environ.get("FRS_SMTP_HOST", "").strip()
        if host:
            user = os.environ.get("FRS_SMTP_USER") or None
            s.mailer = smtp_mailer(host, int(os.environ.get("FRS_SMTP_PORT", "587")), user,
                                   os.environ.get("FRS_SMTP_PASSWORD"), os.environ.get("FRS_SMTP_FROM") or user or "")
            s.mail_is_real = True
        provider = os.environ.get("FRS_SMS_PROVIDER", "").strip().lower()
        if provider == "twilio":
            s.sms = twilio_sender(os.environ["FRS_TWILIO_ACCOUNT_SID"], os.environ["FRS_TWILIO_AUTH_TOKEN"],
                                  os.environ["FRS_TWILIO_FROM"], os.environ.get("FRS_SMS_DEFAULT_COUNTRY", "+91"))
            s.sms_is_real = True
        elif provider == "console":
            s.sms = console_sms
        client_id = os.environ.get("FRS_GOOGLE_CLIENT_ID", "").strip()
        if client_id:
            s.google_client_id = client_id
            s.google_verify = google_verifier(client_id)
        return s
