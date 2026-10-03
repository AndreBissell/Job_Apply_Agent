"""Tests for the sidebar's Personalise-metrics preferences and the
GET /jobs/pending-count "still analysing" count.

Same in-memory SQLite (StaticPool) pattern as tests/test_expired_jobs.py.
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


def _profile(db):
    db.merge(Profile(id=1, name="Alice", email="alice@example.com", password_hash="x"))
    db.commit()


class TestPreferences:
    def test_defaults_then_update_roundtrip(self, client, db):
        _profile(db)
        assert client.get("/profile/1/preferences").json()["auto_cover_letter_min_score"] == 75
        res = client.put("/profile/1/preferences", json={"auto_cover_letter_min_score": 60})
        assert res.json()["auto_cover_letter_min_score"] == 60
        assert client.get("/profile/1/preferences").json()["auto_cover_letter_min_score"] == 60

    def test_rejects_out_of_range(self, client, db):
        _profile(db)
        assert client.put("/profile/1/preferences", json={"auto_cover_letter_min_score": 101}).status_code == 422

    def test_scan_max_pages_default_roundtrip_and_cap(self, client, db):
        _profile(db)
        assert client.get("/profile/1/preferences").json()["scan_max_pages"] == 10
        assert client.put("/profile/1/preferences", json={"scan_max_pages": 20}).json()["scan_max_pages"] == 20
        assert client.get("/profile/1/preferences").json()["scan_max_pages"] == 20
        # Bounds are the Seek access policy's cap — 0 and 26 must both be refused.
        for bad in (0, 26):
            assert client.put("/profile/1/preferences", json={"scan_max_pages": bad}).status_code == 422
        assert client.get("/profile/1/preferences").json()["scan_max_pages"] == 20

    def test_unknown_profile_404(self, client):
        assert client.get("/profile/99/preferences").status_code == 404

    def test_generate_gate_follows_preference(self, db):
        from app.llm.cover_letter import generate_cover_letter
        from app.preferences import set_preferences

        _profile(db)
        db.add(JobListing(id=1, source="seek", source_job_id="a", url="u", title="T"))
        db.add(Match(user_id=1, job_id=1, score=80, status="new"))
        db.commit()
        # 80 clears the default 75, so only the raised preference can gate it
        # (a gated call makes no LLM request, so nothing needs patching).
        set_preferences(db, 1, {"auto_cover_letter_min_score": 90})
        assert generate_cover_letter(1, 1, session=db) is None


class TestPendingCount:
    def test_counts_only_unmatched_jobs_with_description(self, client, db):
        _profile(db)
        db.add_all([
            JobListing(id=1, source="seek", source_job_id="a", url="u", title="T", raw_description="d"),
            JobListing(id=2, source="seek", source_job_id="b", url="u", title="T", raw_description="d"),
            JobListing(id=3, source="seek", source_job_id="c", url="u", title="T"),  # no description
            JobListing(id=4, source="seek", source_job_id="d", url="u", title="T", raw_description="d"),
        ])
        db.add(Match(user_id=1, job_id=2, score=50, status="new"))  # already processed
        db.commit()
        assert client.get("/jobs/pending-count").json() == {"pending": 2}
