"""One-time setup:  python -m scripts.setup --host-email you@example.org --host-name "Ops Host"

1. installs the Python packages from requirements.txt (skip with --no-install)
2. creates the data folder and its secrets (data/.secrets.json)
3. creates the database tables
4. creates the host account if it does not exist yet (password asked for, or FRS_HOST_PASSWORD)
Safe to run again: it never changes existing accounts or data.
"""
from __future__ import annotations

import argparse
import getpass
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Set up the Food Redistribution System on this computer.")
    p.add_argument("--host-email")
    p.add_argument("--host-name", default="Host")
    p.add_argument("--no-install", action="store_true", help="skip pip install")
    args = p.parse_args(argv)

    if sys.version_info < (3, 11):
        print("Python 3.11 or newer is needed.", file=sys.stderr)
        return 1
    if not args.no_install:
        print("1/4 Installing packages…")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "-r", str(ROOT / "requirements.txt")])
    else:
        print("1/4 Skipped package install")

    from shared.config import DATA_DIR, get_settings  # imported after install
    s = get_settings()
    print(f"2/4 Data folder ready: {DATA_DIR}")

    from core_service.db import make_engine
    from core_service.models import Base
    Base.metadata.create_all(make_engine(s.db_url))
    print("3/4 Database ready")

    if args.host_email:
        from sqlalchemy.orm import sessionmaker

        from core_service import accounts
        from scripts.create_host import create_host
        with sessionmaker(make_engine(s.db_url))() as db:
            exists = accounts.get_by_email(db, args.host_email) is not None
        if exists:
            print(f"4/4 Host account {args.host_email} already exists (unchanged)")
        else:
            password = os.environ.get("FRS_HOST_PASSWORD") or getpass.getpass("Choose the host password (8+ characters): ")
            create_host(s.db_url, args.host_email, args.host_name, password)
            print(f"4/4 Host account created: {args.host_email}")
    else:
        print("4/4 No --host-email given; create one later with: python -m scripts.create_host --email … --name …")
    print("\nDone. Start everything with:  python -m scripts.run_all")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
