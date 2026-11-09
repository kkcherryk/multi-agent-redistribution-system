"""Seed local development accounts (one host, one restaurant, one NGO) with random passwords.

The generated credentials are written to data/dev_credentials.json (git-ignored) — open that file to sign in.
Re-running keeps existing accounts.
"""
from __future__ import annotations

import json
import secrets

from sqlalchemy.orm import sessionmaker

from core_service import accounts
from core_service.db import make_engine
from core_service.models import Base
from shared.config import DATA_DIR, get_settings

DEV_ACCOUNTS = [
    ("host", "host@frs.test", "Dev Host"),
    ("restaurant", "restaurant@frs.test", "Dev Spice Kitchen"),
    ("ngo", "ngo@frs.test", "Dev Food Bank NGO"),
]


def main() -> None:
    path = DATA_DIR / "dev_credentials.json"
    creds = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    engine = make_engine(get_settings().db_url)
    Base.metadata.create_all(engine)
    with sessionmaker(engine)() as session:
        for role, email, name in DEV_ACCOUNTS:
            if accounts.get_by_email(session, email):
                continue
            password = secrets.token_urlsafe(12)
            accounts.create_account(session, role=role, email=email, password=password, display_name=name,
                                    portal="dev-seed")
            creds[role] = {"email": email, "password": password}
        session.commit()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(creds, indent=2), encoding="utf-8")
    print(f"Dev accounts ready; credentials in {path}")


if __name__ == "__main__":
    main()
