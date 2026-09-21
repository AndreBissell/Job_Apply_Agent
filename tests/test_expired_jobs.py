"""Tests for the closed/expired-listing filtering feature:
GET /jobs/by-source-id/{id}, PATCH /jobs/{id}/expired, and GET /jobs's
default-view exclusion of expired listings.

Same in-memory SQLite (StaticPool) pattern as tests/test_screenshot_upload.py.
"""
from __future__ import annotations

import sqlite3

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


def _seed_job(db, job_id: int, source_job_id: str, user_id: int = 1, score: int = 80, status: str = "new") -> JobListing:
    db.merge(Profile(id=user_id, name="Alice", email="alice@example.com", password_hash="x"))
    job = JobListing(
        id=job_id, source="seek", source_job_id=source_job_id,
        url="https://example.com", title="Test Job",
    )
    db.add(job)
    db.add(Match(user_id=user_id, job_id=job_id, score=score, status=status))
    db.commit()
    db.refresh(job)
    return job


class TestJobBySourceId:
    def test_resolves_known_job(self, client, db):
        job = _seed_job(db, job_id=1, source_job_id="seek-123")
        res = client.get("/jobs/by-source-id/seek-123")
        assert res.status_code == 200
        assert res.json()["job_id"] == job.id

    def test_unknown_source_id_returns_404(self, client, db):
        res = client.get("/jobs/by-source-id/does-not-exist")
        assert res.status_code == 404


class TestMarkExpired:
    def test_stamps_timestamp_and_is_idempotent(self, client, db):
        job = _seed_job(db, job_id=1, source_job_id="seek-123")

        res1 = client.patch(f"/jobs/{job.id}/expired")
        assert res1.status_code == 200
        stamped_at = res1.json()["expired_detected_at"]
        assert stamped_at

        res2 = client.patch(f"/jobs/{job.id}/expired")
        assert res2.status_code == 200
        assert res2.json()["expired_detected_at"] == stamped_at  # unchanged on 2nd call

    def test_unknown_job_returns_404(self, client, db):
        db.add(Profile(id=1, name="Alice", email="alice@example.com", password_hash="x"))
        db.commit()
        res = client.patch("/jobs/999/expired")
        assert res.status_code == 404


class TestListJobsExcludesExpired:
    def test_expired_job_hidden_from_default_view(self, client, db):
        open_job = _seed_job(db, job_id=1, source_job_id="seek-open")
        closed_job = _seed_job(db, job_id=2, source_job_id="seek-closed")
        client.patch(f"/jobs/{closed_job.id}/expired")

        res = client.get("/jobs?profile_id=1")
        assert res.status_code == 200
        ids = [j["job_id"] for j in res.json()]
        assert open_job.id in ids
        assert closed_job.id not in ids

    def test_include_expired_lifts_the_filter(self, client, db):
        closed_job = _seed_job(db, job_id=1, source_job_id="seek-closed")
        client.patch(f"/jobs/{closed_job.id}/expired")

        res = client.get("/jobs?profile_id=1&include_expired=true")
        ids = [j["job_id"] for j in res.json()]
        assert closed_job.id in ids

    def test_applied_status_view_ignores_expired_flag(self, client, db):
        job = _seed_job(db, job_id=1, source_job_id="seek-closed", status="applied")
        client.patch(f"/jobs/{job.id}/expired")

        res = client.get("/jobs?profile_id=1&status=applied")
        ids = [j["job_id"] for j in res.json()]
        assert job.id in ids  # already-applied evidence stays visible even after closing
