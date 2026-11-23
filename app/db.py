"""Database engine, session factory, and the declarative ``Base``.

The database URL is taken from the ``DATABASE_URL`` environment variable and
defaults to a local SQLite file. Because the URL is the only thing that changes,
moving from SQLite (local dev) to Postgres (hosted) is a config change, not a
code change — keep all models/queries dialect-neutral.
"""

from __future__ import annotations

import os
import sqlite3

from dotenv import load_dotenv
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# Load variables from a local .env if present (no-op when the file is absent).
load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///app.db")


def app_env() -> str:
    """Which environment this process serves: ``"real"`` or ``"test"``.

    Set by ``scripts/run_api.py`` alongside DATABASE_URL. Defaults to ``"test"``
    so anything that starts the app without the launcher (pytest, ad-hoc uvicorn,
    alembic) can never be mistaken for — or write to — the real environment.
    """
    return "real" if os.environ.get("APP_ENV") == "real" else "test"


class Base(DeclarativeBase):
    """Declarative base shared by every model and by Alembic's metadata."""


# ``future=True`` is the default in SQLAlchemy 2.0; stated for clarity.
engine = create_engine(DATABASE_URL, echo=False, future=True)

# Session factory. ``expire_on_commit=False`` keeps instances usable after a
# commit (convenient for scripts and request handlers alike).
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


# How long a SQLite write waits for another connection's write to commit before it fails
# with "database is locked". The driver's default is 5 s. Two worker threads plus the
# request threads can now write at once (docs/second-worker-plan.md D4), so it is raised to
# 30 s. Writers hold the lock for milliseconds, so this is headroom, not an expected wait.
SQLITE_LOCK_WAIT_MS = 30_000


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record) -> None:
    """Configure every new SQLite connection: foreign keys on, and a 30 s lock wait.

    SQLite ships with FK enforcement *off* by default, but the schema relies on
    ``ON DELETE CASCADE`` / ``SET NULL`` behaviour. ``busy_timeout`` is
    ``SQLITE_LOCK_WAIT_MS`` (above). The journal mode is left at SQLite's default (no WAL).
    This listener is a no-op on every other dialect (Postgres enforces FKs natively and
    makes writers wait for row locks on its own).
    """
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute(f"PRAGMA busy_timeout={SQLITE_LOCK_WAIT_MS:d}")
        cursor.close()
