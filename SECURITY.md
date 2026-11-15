# Security review (Phase 10)

This is a review of the whole system as it stood at the end of Phase 9, with what was fixed in Phase 10.
Every item marked **Tested** has an automated test in `tests/test_hardening.py`, `tests/test_privacy.py`,
or `tests/test_end_to_end.py`.

## Found and fixed

| # | Finding | Risk | Fix | |
|---|---|---|---|---|
| 1 | Unlimited sign-in attempts | Password guessing | 5 failed sign-ins per email + network in 15 min, then a wait (HTTP 429 + `Retry-After`); 30 per network in 15 min for any email. A correct sign-in resets the count. | Tested |
| 2 | Unlimited self-registration | Bulk fake applications | 10 registrations per network per hour on each portal | Tested |
| 3 | No security headers on pages | Click-jacking, content sniffing | `X-Frame-Options: DENY`, CSP with `frame-ancestors 'none'`, `nosniff`, `Referrer-Policy`, `Permissions-Policy` (location only for the portal itself) | Tested |
| 4 | Cross-site requests relied only on `SameSite=Lax` cookies | Cross-site request forgery | State-changing requests with a foreign `Origin` are refused (403) | Tested |
| 5 | API answers could be cached by the browser | Personal data left on shared computers | `Cache-Control: no-store` on every `/api/` answer | Tested |
| 6 | Phone numbers stayed visible after a pickup finished | Over-sharing personal data | Restaurant and NGO see each other's phone only while the pickup is in progress; the host always sees it | Tested |
| 7 | No retention rules | Location trails and logs kept forever | Retention periods (Privacy tab), applied hourly by the housekeeping agent | Tested |
| 8 | No way to answer a data request | Privacy compliance | Host can export everything about an account (JSON, without password hash or file paths) or erase it | Tested |
| 9 | An erased account could keep using an open session | Access after erasure | Erased accounts are signed out everywhere; their password is replaced by an unknown random one | Tested |
| 10 | `python-multipart` missing from `requirements.txt` | Uploads broken on a fresh install | Added (Phase 9 commit) | — |

## Checked and already correct

- **Every core route needs a service key.** Each portal presents its own key, which is compared in constant time. *Tested by walking every route.*
- **Every portal API needs a signed-in user** of that portal's role, except `/api/portal`, `/api/login`, `/api/register` and `/api/logout`. *Tested by walking every route.*
- **Portals cannot reach each other's data.** The core enforces ownership: a portal sees only accounts of its own role, an NGO sees only its own pickups, and inboxes are private. *Tested.*
- **Host accounts cannot be self-registered.** They are created only with `scripts/create_host.py` or `scripts/setup.py`. *Tested.*
- **Passwords:**
  - stored with bcrypt;
  - must be 8 to 72 bytes (bcrypt ignores anything longer, so longer passwords are refused);
  - an unknown email takes as long to check as a wrong password.
- **Sessions:**
  - each portal has its own JWT secret and audience, so a session from one portal is useless on another;
  - session cookies are `HttpOnly` and `SameSite=Lax`, plus `Secure` when `FRS_COOKIE_SECURE=1`;
  - sessions expire after 8 hours.
- **Uploads:**
  - limited to 5 MB;
  - the file type is detected from the file's contents, not its name;
  - files are served with `nosniff` and a sandboxing CSP, so an uploaded file can never run script. *Tested.*
- **Failed sign-ins** record only the email domain, never the address or the password. *Tested.*
- **Secrets** live in `data/.secrets.json` or `.env`, and both are git-ignored.
- **Learned dispatch** is host-only, off by default, and falls back to the normal rule if its file is damaged.

## Known limits (for a real deployment)

- **Rate limits are kept in memory** in each portal process. They reset on restart, and each extra worker process counts separately. For several servers, use a shared store such as Redis.
- **SQLite** suits one server. For many users at once, use PostgreSQL by setting `FRS_DB_URL`.
- **HTTPS must come from a reverse proxy** such as Caddy or nginx. Then set `FRS_COOKIE_SECURE=1` and the public `FRS_*_URL` values.
- **The page CSP allows inline scripts**, because each page has a small inline configuration block. Moving those blocks into files would allow a stricter policy.
- **Notifications are in-app only.** Email or SMS delivery would need a provider account.
- **The host has no two-factor sign-in.**
- **Package versions are minimums (`>=`), not pinned.** Pin them (`pip freeze`) for a reproducible deployment.
