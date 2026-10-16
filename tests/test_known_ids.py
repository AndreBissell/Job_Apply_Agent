"""GET /jobs/known-ids: what Scan Page skips (docs/overview-applied-rework-plan.md, Step 0/5).

A card-only row (the content script's card capture: no description, never scored) must NOT
count as known, or the scan skips every job on a search page without opening one.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.main import app, get_db
from app.db import Base
from app.models import JobListing, Match, Profile


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _fk_on(conn, _):
        if isinstance(conn, sqlite3.Connection):
            conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture()
def client(engine):
    Session = sessionmaker(bind=engine)

    def _db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _db
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def db(engine):
    Session = sessionmaker(bind=engine)
    session = Session()
    session.merge(Profile(id=1, name="Alice", email="alice@example.com", password_hash="x"))
    session.commit()
    yield session
    session.close()


def _job(db, sid, **fields) -> JobListing:
    job = JobListing(source="seek", source_job_id=sid, url=f"u{sid}", title=f"Job {sid}", **fields)
    db.add(job)
    db.commit()
    return job


def _known(client) -> set[str]:
    return set(client.get("/jobs/known-ids").json()["source_ids"])


def test_a_card_only_job_is_not_known(client, db):
    _job(db, "100")  # captured from a search page's card: no description, no match
    assert _known(client) == set()


def test_a_job_with_its_ad_text_is_known(client, db):
    _job(db, "101", raw_description="the ad")
    assert _known(client) == {"101"}


def test_a_scored_job_without_text_is_known(client, db):
    """A bulk-hidden job lost its text but keeps its score: nothing for the scan to add."""
    job = _job(db, "102")
    db.add(Match(user_id=1, job_id=job.id, score=40, status="new", hidden_at=datetime.now(timezone.utc)))
    db.commit()
    assert _known(client) == {"102"}


def test_a_closed_card_only_job_is_known(client, db):
    _job(db, "103", expired_detected_at=datetime.now(timezone.utc))
    assert _known(client) == {"103"}


def test_a_search_page_mixes_them(client, db):
    _job(db, "1", raw_description="the ad")
    _job(db, "2")
    _job(db, "3")
    assert _known(client) == {"1"}
