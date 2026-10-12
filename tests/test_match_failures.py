"""A failed score must not become a permanent 0, and must never replace a real score.

match_job used to turn ANY failure (network, daily quota, bad JSON) into a
score=0 / "Parse error" row. The idle loop only picks jobs with no match row, so
that job was never retried; with force=True (Regenerate) it overwrote a real score.

Now a failure raises and leaves no row (the idle loop retries). Only the idle loop's
last attempt asks for ``record_failure=True``, which writes today's 0 row so one bad
ad can't block the queue. Even then an existing score is never overwritten.

No LLM: complete_json is monkeypatched. In-memory SQLite only.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.llm.match as match_module
from app.db import Base
from app.llm.client import DailyQuotaError, LLMError
from app.models import JobListing, Match, Profile

SCORED_AT = datetime(2026, 10, 1, 9, 30, tzinfo=timezone.utc)


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
                     raw_description="We build software.", extracted_at=SCORED_AT))
    s.commit()
    yield s
    s.close()
    Base.metadata.drop_all(eng)


def _raiser(exc):
    def fake(*a, **k):
        raise exc
    return fake


def _matches(db) -> list[Match]:
    db.expire_all()
    return db.query(Match).filter_by(user_id=1, job_id=5).all()


def test_llm_failure_raises_and_leaves_no_row(db, monkeypatch):
    monkeypatch.setattr(match_module, "complete_json", _raiser(LLMError("call failed after 3 retries")))
    with pytest.raises(LLMError):
        match_module.match_job(5, 1, session=db)
    assert _matches(db) == []


def test_daily_quota_raises_and_leaves_no_row(db, monkeypatch):
    monkeypatch.setattr(match_module, "complete_json", _raiser(DailyQuotaError("daily quota")))
    with pytest.raises(DailyQuotaError):
        match_module.match_job(5, 1, session=db)
    assert _matches(db) == []


def test_forced_rescore_failure_keeps_the_real_score(db, monkeypatch):
    db.add(Match(user_id=1, job_id=5, score=88, reasoning="good fit", gaps="[]",
                 status="new", scored_at=SCORED_AT))
    db.commit()
    monkeypatch.setattr(match_module, "complete_json", _raiser(LLMError("call failed after 3 retries")))
    with pytest.raises(LLMError):
        match_module.match_job(5, 1, session=db, force=True)
    (m,) = _matches(db)
    assert m.score == 88 and m.reasoning == "good fit"
    assert m.scored_at.replace(tzinfo=None) == SCORED_AT.replace(tzinfo=None)


def test_record_failure_writes_the_terminal_zero_row(db, monkeypatch):
    monkeypatch.setattr(match_module, "complete_json", lambda *a, **k: {"score": "high"})
    match_module.match_job(5, 1, session=db, record_failure=True)
    (m,) = _matches(db)
    assert m.score == 0 and m.reasoning == "Parse error" and m.status == "new"


def test_record_failure_never_overwrites_an_existing_score(db, monkeypatch):
    db.add(Match(user_id=1, job_id=5, score=88, reasoning="good fit", gaps="[]", status="new"))
    db.commit()
    monkeypatch.setattr(match_module, "complete_json", lambda *a, **k: {"score": "high"})
    with pytest.raises(Exception):
        match_module.match_job(5, 1, session=db, force=True, record_failure=True)
    (m,) = _matches(db)
    assert m.score == 88


def test_record_failure_still_raises_on_quota(db, monkeypatch):
    monkeypatch.setattr(match_module, "complete_json", _raiser(DailyQuotaError("daily quota")))
    with pytest.raises(DailyQuotaError):
        match_module.match_job(5, 1, session=db, record_failure=True)
    assert _matches(db) == []


def test_good_response_still_writes_the_row(db, monkeypatch):
    monkeypatch.setattr(match_module, "complete_json",
                        lambda *a, **k: {"score": 72, "reasoning": "ok", "gaps": ["SQL"]})
    match_module.match_job(5, 1, session=db)
    (m,) = _matches(db)
    assert m.score == 72 and m.reasoning == "ok"


# ---------------------------------------------------------------------------
# The idle loop's retry policy (_match_attempt). The loop itself isn't tested.
# ---------------------------------------------------------------------------
import app.api.main as api  # noqa: E402


@pytest.fixture()
def idle(db, monkeypatch):
    """Point match_job and the loop helper at the test engine; reset the counter."""
    factory = sessionmaker(bind=db.get_bind())
    monkeypatch.setattr(match_module, "SessionLocal", factory)
    monkeypatch.setattr(api, "SessionLocal", factory)
    api._match_failures.clear()
    yield
    api._match_failures.clear()


def test_attempts_fail_twice_then_the_third_records_the_zero_row(db, idle, monkeypatch):
    monkeypatch.setattr(match_module, "complete_json", _raiser(LLMError("call failed after 3 retries")))

    assert api._match_attempt(5) == "failed"
    assert _matches(db) == []
    assert api._match_attempt(5) == "failed"
    assert _matches(db) == []
    assert api._match_attempt(5) == "gave_up"

    (m,) = _matches(db)
    assert m.score == 0 and m.reasoning == "Parse error"
    assert 5 not in api._match_failures


def test_quota_stop_is_not_counted_against_the_job(db, idle, monkeypatch):
    monkeypatch.setattr(match_module, "complete_json", _raiser(DailyQuotaError("daily quota")))
    for _ in range(5):
        assert api._match_attempt(5) == "quota"
    assert _matches(db) == []
    assert api._match_failures.get(5, 0) == 0


def test_success_after_a_failure_clears_the_count(db, idle, monkeypatch):
    monkeypatch.setattr(match_module, "complete_json", _raiser(LLMError("blip")))
    assert api._match_attempt(5) == "failed"
    monkeypatch.setattr(match_module, "complete_json",
                        lambda *a, **k: {"score": 72, "reasoning": "ok", "gaps": []})
    assert api._match_attempt(5) == "ok"
    assert 5 not in api._match_failures
    (m,) = _matches(db)
    assert m.score == 72
