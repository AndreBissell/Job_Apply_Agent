"""Re-scoring an existing match must never change its lifecycle status.

POST /jobs/{id}/regenerate re-runs match_job with force=True on the single worker,
so it can land AFTER the user pressed Mark Applied. It used to reset status to
'new', which dropped the job from the evidence export. The quick-screen skip branch
had the same reset. A NEW row still starts at 'new'.

No LLM: complete_json is monkeypatched in each module. In-memory SQLite only.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.llm.match as match_module
import app.llm.quickscreen as quickscreen_module
from app.db import Base
from app.models import JobListing, Match, Profile

APPLIED_AT = datetime(2026, 10, 1, 9, 30, tzinfo=timezone.utc)


@pytest.fixture()
def db():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(eng, "connect")
    def _fk_on(conn, _):
        if isinstance(conn, sqlite3.Connection):
            conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    s.add(Profile(id=1, name="Bob", email="b@x.com", password_hash="x"))
    s.add(JobListing(id=5, source="seek", source_job_id="5", url="u5", title="Engineer",
                     raw_description="We build software.", extracted_at=APPLIED_AT))
    s.commit()
    yield s
    s.close()
    Base.metadata.drop_all(eng)


def _applied_match(db) -> Match:
    m = Match(user_id=1, job_id=5, score=90, status="applied", applied_at=APPLIED_AT)
    db.add(m)
    db.commit()
    return m


def _reload(db) -> Match:
    db.expire_all()
    return db.query(Match).filter_by(user_id=1, job_id=5).one()


def _same_instant(a: datetime, b: datetime) -> bool:
    # SQLite hands back naive datetimes; compare the wall-clock value.
    return a.replace(tzinfo=None) == b.replace(tzinfo=None)


class TestMatchJob:
    @pytest.fixture(autouse=True)
    def fake_llm(self, monkeypatch):
        monkeypatch.setattr(match_module, "complete_json",
                            lambda *a, **k: {"score": 70, "reasoning": "re-scored", "gaps": ["SQL"]})

    def test_forced_rematch_keeps_applied_status_and_applied_at(self, db):
        _applied_match(db)

        match_module.match_job(5, 1, session=db, force=True)

        m = _reload(db)
        assert m.score == 70 and m.reasoning == "re-scored"  # the re-score did happen
        assert m.status == "applied"
        assert _same_instant(m.applied_at, APPLIED_AT)

    def test_a_new_match_still_starts_as_new(self, db):
        match_module.match_job(5, 1, session=db)
        m = _reload(db)
        assert m.status == "new" and m.applied_at is None


class TestQuickScreen:
    @pytest.fixture(autouse=True)
    def fake_llm(self, monkeypatch):
        # Below SKIP_THRESHOLD, so the skip branch writes the match row.
        monkeypatch.setattr(quickscreen_module, "complete_json",
                            lambda *a, **k: {"score": 5, "reason": "unrelated field"})

    def test_forced_rescreen_keeps_applied_status_and_applied_at(self, db):
        _applied_match(db)

        assert quickscreen_module.quick_screen(5, 1, session=db, force=True) == "skipped"

        m = _reload(db)
        assert m.score == 5  # the re-screen did overwrite the score
        assert m.status == "applied"
        assert _same_instant(m.applied_at, APPLIED_AT)

    def test_a_new_skipped_match_still_starts_as_new(self, db):
        assert quickscreen_module.quick_screen(5, 1, session=db) == "skipped"
        m = _reload(db)
        assert m.status == "new" and m.applied_at is None
