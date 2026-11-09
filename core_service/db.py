from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool


def make_engine(db_url: str) -> Engine:
    if db_url.startswith("sqlite"):
        kwargs: dict = {"connect_args": {"check_same_thread": False}}
        if ":memory:" in db_url or db_url == "sqlite://":
            kwargs["poolclass"] = StaticPool
        else:
            Path(db_url.split("///", 1)[1]).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(db_url, **kwargs)

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        return engine
    return create_engine(db_url, pool_pre_ping=True)
