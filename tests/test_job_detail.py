"""GET /jobs/{job_id}: the record fields the Applied tab reads, and the older keys it keeps.

Same in-memory SQLite (StaticPool) pattern as tests/test_search_endpoints.py.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.main import app, get_db
from app.db import Base
from app.models import CoverLetter, JobListing, JobSkill, Match, Profile


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


def _job(db, **fields) -> JobListing:
    job = JobListing(
        id=1, source="seek", source_job_id="j1", url="https://example.test/job/1",
        title="Data Analyst", **fields,
    )
    db.add(job)
    db.commit()
    return job


class TestRecordFields:
    def test_every_new_field_is_returned_and_old_keys_stay(self, client, db):
        _profile(db)
        seen = datetime(2026, 10, 1, 9, 30, tzinfo=timezone.utc)
        gone = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
        applied = datetime(2026, 10, 2, 8, 0, tzinfo=timezone.utc)
        interview = datetime(2026, 10, 9, 15, 0, tzinfo=timezone.utc)
        job = _job(
            db,
            company="Acme",
            raw_description="the ad text",
            summary="Analyse data for the ops team.",
            key_responsibilities=json.dumps(["Build dashboards", "Clean data"]),
            seniority="junior",
            discovered_query="data analyst",
            expired_detected_at=gone,
            last_seen_live_at=seen,
        )
        db.add_all([
            JobSkill(job_id=job.id, name="SQL", skill_type="hard"),
            JobSkill(job_id=job.id, name="Python", skill_type="hard"),
            JobSkill(job_id=job.id, name="Communication", skill_type="soft"),
            JobSkill(job_id=job.id, name="Odd", skill_type="other"),
        ])
        match = Match(
            user_id=1, job_id=job.id, score=88, status="applied",
            applied_at=applied, interview_at=interview,
        )
        db.add(match)
        db.commit()
        db.add(CoverLetter(match_id=match.id, generated_content="gen", edited_content="edited"))
        db.commit()

        data = client.get("/jobs/1").json()

        assert data["summary"] == "Analyse data for the ops team."
        assert data["key_responsibilities"] == ["Build dashboards", "Clean data"]
        assert data["seniority"] == "junior"
        assert data["discovered_query"] == "data analyst"
        assert data["skills"] == {"hard": ["SQL", "Python"], "soft": ["Communication"]}
        assert data["expired_detected_at"].startswith("2026-10-05T12:00")
        assert data["last_seen_live_at"].startswith("2026-10-01T09:30")
        assert data["match"]["interview_at"].startswith("2026-10-09T15:00")
        # SQLite drops the zone; the panel's new Date() must still read these as UTC.
        assert data["expired_detected_at"].endswith("+00:00")
        assert data["last_seen_live_at"].endswith("+00:00")
        assert data["cover_letter"]["created_at"] is not None
        assert data["cover_letter"]["updated_at"] is not None

        # The keys the extension already reads.
        assert data["title"] == "Data Analyst"
        assert data["company"] == "Acme"
        assert data["raw_description"] == "the ad text"
        assert data["match"]["score"] == 88
        assert data["match"]["status"] == "applied"
        assert data["match"]["applied_at"].startswith("2026-10-02T08:00")
        assert data["cover_letter"]["generated_content"] == "gen"
        assert data["cover_letter"]["edited_content"] == "edited"
        assert data["cover_letter"]["status"] == "draft"

    def test_applied_job_with_nothing_extracted(self, client, db):
        _profile(db)
        job = _job(db, raw_description=None)
        job.last_seen_live_at = None  # the column defaults to "now" on insert
        db.add(Match(
            user_id=1, job_id=job.id, score=50, status="applied",
            applied_at=datetime(2026, 10, 2, tzinfo=timezone.utc),
        ))
        db.commit()

        data = client.get("/jobs/1").json()

        assert data["raw_description"] is None
        assert data["cover_letter"] is None
        assert data["summary"] is None
        assert data["key_responsibilities"] == []
        assert data["seniority"] is None
        assert data["skills"] == {"hard": [], "soft": []}
        assert data["expired_detected_at"] is None
        assert data["last_seen_live_at"] is None
        assert data["match"]["interview_at"] is None

    @pytest.mark.parametrize("raw", ["not json {", json.dumps({"a": 1}), json.dumps("text"), ""])
    def test_bad_key_responsibilities_become_an_empty_list(self, client, db, raw):
        _profile(db)
        _job(db, key_responsibilities=raw)
        assert client.get("/jobs/1").json()["key_responsibilities"] == []

    def test_non_string_items_are_dropped(self, client, db):
        _profile(db)
        _job(db, key_responsibilities=json.dumps(["Keep", 3, None, "Also keep"]))
        assert client.get("/jobs/1").json()["key_responsibilities"] == ["Keep", "Also keep"]
