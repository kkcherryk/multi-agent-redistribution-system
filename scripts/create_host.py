"""Create a Host (admin) account. Host accounts are managed by the project team, never self-registered.

Usage:  python -m scripts.create_host --email host@example.org --name "Ops Host"
The password is prompted for (not echoed) unless FRS_HOST_PASSWORD is set.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys

from sqlalchemy.orm import sessionmaker

from core_service import accounts
from core_service.db import make_engine
from core_service.models import Base
from core_service.schemas import RegisterIn
from shared.config import get_settings


def create_host(db_url: str, email: str, name: str, password: str) -> str:
    data = RegisterIn(email=email, password=password, display_name=name)  # same validation as public sign-up
    engine = make_engine(db_url)
    Base.metadata.create_all(engine)
    with sessionmaker(engine)() as session:
        acc = accounts.create_account(session, role="host", email=data.email, password=data.password,
                                      display_name=data.display_name, portal="team-cli")
        session.commit()
        return acc.id


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--email", required=True)
    p.add_argument("--name", required=True)
    args = p.parse_args(argv)
    password = os.environ.get("FRS_HOST_PASSWORD") or getpass.getpass("Host password: ")
    try:
        acc_id = create_host(get_settings().db_url, args.email, args.name, password)
    except accounts.EmailTaken:
        print("An account with that email already exists.", file=sys.stderr)
        return 1
    print(f"Host account created: {acc_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
