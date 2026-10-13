"""FastAPI dependencies shared by every router.

``get_db`` is the ONE database-session dependency. Tests key
``app.dependency_overrides`` on it (reached as ``main.get_db`` or ``profile_ui.get_db``;
both are this same object), so there must never be a second copy.
"""

from __future__ import annotations

from typing import Iterator

from sqlalchemy.orm import Session

from app.db import SessionLocal


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
