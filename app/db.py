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


class Base(DeclarativeBase):
    """Declarative base shared by every model and by Alembic's metadata."""


# ``future=True`` is the default in SQLAlchemy 2.0; stated for clarity.
engine = create_engine(DATABASE_URL, echo=False, future=True)

# Session factory. ``expire_on_commit=False`` keeps instances usable after a
# commit (convenient for scripts and request handlers alike).
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record) -> None:
    """Turn on foreign-key enforcement for SQLite connections.

    SQLite ships with FK enforcement *off* by default, but the schema relies on
    ``ON DELETE CASCADE`` / ``SET NULL`` behaviour. This listener is a no-op on
    every other dialect (Postgres enforces FKs natively).
    """
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
