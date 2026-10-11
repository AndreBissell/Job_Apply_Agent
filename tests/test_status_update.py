"""Tests for PATCH /jobs/{id}/status.

An applied match is Centrelink evidence: the status must be one of the documented
values, and once a match has applied_at set its status can only be "applied".
No AI is involved. Same in-memory SQLite (StaticPool) pattern as
tests/test_expired_jobs.py.
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
    yield session
    session.close()


APPLIED_AT = datetime(2026, 9, 1, 3, 0, tzinfo=timezone.utc)


def _seed(db, status: str = "new", applied_at=None) -> int:
    """One job (id 1) with a match for profile 1. Returns the job id."""
    db.merge(Profile(id=1, name="Alice", email="alice@example.com", password_hash="x"))
    db.add(JobListing(
        id=1, source="seek", source_job_id="seek-1",
        url="https://example.com", title="Test Job",
    ))
    db.add(Match(user_id=1, job_id=1, score=80, status=status, applied_at=applied_at))
    db.commit()
    return 1


def _match(db) -> Match:
    db.expire_all()
    return db.query(Match).filter_by(user_id=1, job_id=1).one()


def _patch(client, status: str):
    return client.patch("/jobs/1/status", json={"status": status})


def test_applied_stamps_applied_at_and_returns_it(client, db):
    _seed(db)
    r = _patch(client, "applied")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "applied"
    assert body["applied_at"] is not None
    assert _match(db).applied_at is not None


def test_second_applied_keeps_the_first_timestamp(client, db):
    _seed(db)
    first = _patch(client, "applied").json()["applied_at"]
    second = _patch(client, "applied")
    assert second.status_code == 200
    assert second.json()["applied_at"] == first


def test_unknown_status_is_rejected_and_row_unchanged(client, db):
    _seed(db)
    r = _patch(client, "banana")
    assert r.status_code == 422
    m = _match(db)
    assert m.status == "new"
    assert m.applied_at is None


def test_unknown_job_is_404(client, db):
    _seed(db)
    r = client.patch("/jobs/999/status", json={"status": "applied"})
    assert r.status_code == 404


@pytest.mark.parametrize("status", ["withdrawn", "new"])
def test_applied_match_cannot_change_status(client, db, status):
    _seed(db, status="applied", applied_at=APPLIED_AT)
    r = _patch(client, status)
    assert r.status_code == 409
    m = _match(db)
    assert m.status == "applied"
    assert m.applied_at is not None


def test_drifted_row_cannot_move_away_from_applied(client, db):
    # applied_at set but status never moved to 'applied' (possible before 49f54ee).
    _seed(db, status="new", applied_at=APPLIED_AT)
    r = _patch(client, "new")
    assert r.status_code == 409
    m = _match(db)
    assert m.status == "new"
    assert m.applied_at is not None


def test_drifted_row_is_repaired_by_applied_and_keeps_its_date(client, db):
    _seed(db, status="new", applied_at=APPLIED_AT)
    r = _patch(client, "applied")
    assert r.status_code == 200
    m = _match(db)
    assert m.status == "applied"
    # SQLite drops tzinfo on read; compare the wall-clock value.
    assert m.applied_at.replace(tzinfo=None) == APPLIED_AT.replace(tzinfo=None)
