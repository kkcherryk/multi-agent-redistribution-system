"""Central configuration shared by the core data service, the three portals and the landing page.

Secrets (per-portal service keys and session-signing secrets) are generated on first use and stored in
``data/.secrets.json`` (git-ignored). Any value can be overridden with environment variables.
"""
from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("FRS_DATA_DIR", ROOT / "data"))

PORTALS = ("admin", "restaurant", "ngo")
# Which account role each portal is allowed to authenticate.
PORTAL_ROLE = {"admin": "host", "restaurant": "restaurant", "ngo": "ngo"}
ROLES = tuple(PORTAL_ROLE.values())

DEFAULT_PORTS = {"core": 8000, "admin": 8001, "restaurant": 8002, "ngo": 8003, "landing": 8004}


def _load_or_create_secrets(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    changed = False
    for portal in PORTALS:
        for kind in ("service_key", "jwt_secret"):
            key = f"{portal}_{kind}"
            if not data.get(key):
                data[key] = secrets.token_urlsafe(32)
                changed = True
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


@dataclass
class Settings:
    core_url: str
    db_url: str
    service_keys: dict[str, str]  # portal -> key the portal presents to the core service
    jwt_secrets: dict[str, str]  # portal -> secret used to sign that portal's session tokens
    portal_urls: dict[str, str] = field(default_factory=dict)
    landing_url: str = "/"
    cookie_secure: bool = False
    session_ttl_seconds: int = 8 * 3600


def get_settings() -> Settings:
    host = os.environ.get("FRS_HOST", "127.0.0.1")
    ports = {name: int(os.environ.get(f"FRS_{name.upper()}_PORT", port)) for name, port in DEFAULT_PORTS.items()}
    stored = _load_or_create_secrets(DATA_DIR / ".secrets.json")
    return Settings(
        core_url=os.environ.get("FRS_CORE_URL", f"http://{host}:{ports['core']}"),
        db_url=os.environ.get("FRS_DB_URL", f"sqlite:///{(DATA_DIR / 'core.db').as_posix()}"),
        service_keys={p: os.environ.get(f"FRS_{p.upper()}_SERVICE_KEY", stored[f"{p}_service_key"]) for p in PORTALS},
        jwt_secrets={p: os.environ.get(f"FRS_{p.upper()}_JWT_SECRET", stored[f"{p}_jwt_secret"]) for p in PORTALS},
        portal_urls={p: os.environ.get(f"FRS_{p.upper()}_URL", f"http://{host}:{ports[p]}") for p in PORTALS},
        landing_url=os.environ.get("FRS_LANDING_URL", f"http://{host}:{ports['landing']}"),
        cookie_secure=os.environ.get("FRS_COOKIE_SECURE", "0") == "1",
    )
