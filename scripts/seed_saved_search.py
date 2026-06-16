"""Seed a test profile + one saved search for the scraper.

Idempotent: re-running won't create duplicates. Creates (if missing):
  * a minimal ``profiles`` row to own the search, and
  * one ``saved_searches`` row: keywords="software engineer",
    location="Brisbane QLD", work_type=NULL (any).

    python scripts/seed_saved_search.py
"""

from __future__ import annotations

import sys

from sqlalchemy import select

sys.path.insert(0, ".")

from app.db import SessionLocal  # noqa: E402
from app.models import Profile, SavedSearch  # noqa: E402

TEST_EMAIL = "scraper-test@example.com"
SEARCH_LABEL = "SWE Brisbane (test)"


def main() -> int:
    with SessionLocal() as session:
        profile = session.scalar(select(Profile).where(Profile.email == TEST_EMAIL))
        if profile is None:
            profile = Profile(
                name="Scraper Test User",
                email=TEST_EMAIL,
                password_hash="not-a-real-hash",
            )
            session.add(profile)
            session.flush()
            print(f"Created test profile id={profile.id} ({TEST_EMAIL})")
        else:
            print(f"Reusing test profile id={profile.id} ({TEST_EMAIL})")

        existing = session.scalar(
            select(SavedSearch).where(
                SavedSearch.user_id == profile.id,
                SavedSearch.label == SEARCH_LABEL,
            )
        )
        if existing is None:
            search = SavedSearch(
                user_id=profile.id,
                label=SEARCH_LABEL,
                keywords="software engineer",
                location="Brisbane QLD",
                work_type=None,  # any
                is_active=True,
            )
            session.add(search)
            session.flush()
            print(f"Created saved search id={search.id} ({SEARCH_LABEL})")
        else:
            print(f"Reusing saved search id={existing.id} ({SEARCH_LABEL})")

        session.commit()

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
