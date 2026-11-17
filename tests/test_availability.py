"""Background availability checks: which listing to re-visit, what a visit means, and the
"seen live recently" gate in front of the LLM pipeline (app/availability.py).

The backend never contacts Seek; these tests only exercise database logic and the endpoints
the side panel calls. Same in-memory SQLite (StaticPool) pattern as tests/test_expired_jobs.py.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import availability
from app.api.main import app, get_db
from app.db import Base
from app.llm.letter import production
from app.models import CoverLetter, JobListing, Match, Profile
from app.preferences import set_preferences

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(eng, "connect")
    def _fk_on(conn, _):
        if isinstance(conn, sqlite3.Connection):
            conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture()
def db(engine):
    session = sessionmaker(bind=engine)()
    session.add(Profile(id=1, name="Alice", email="alice@example.com", password_hash="x"))
    session.commit()
    yield session
    session.close()


@pytest.fixture()
def client(engine):
    Session = sessionmaker(bind=engine)

    def _db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


def add_job(db, n: int, *, score: float | None = 80, seen_hours_ago: float | None = 1,
            checked_hours_ago: float | None = None, misses: int = 0, description: str | None = "An ad",
            hidden: bool = False, applied: bool = False, expired: bool = False,
            letter: bool = False, user_id: int = 1, extracted: bool = True,
            scraped_days_ago: float | None = None) -> JobListing:
    """``scraped_days_ago`` defaults to ``n`` (so a higher id is an older capture); set it to
    make capture order disagree with id order."""
    job = JobListing(
        id=n, source="seek", source_job_id=f"seek-{n}", url=f"https://au.seek.com/job/{n}?ref=x",
        title=f"Job {n}", raw_description=description,
        extracted_at=NOW if extracted else None,
        last_seen_live_at=NOW - timedelta(hours=seen_hours_ago) if seen_hours_ago is not None else None,
        availability_checked_at=NOW - timedelta(hours=checked_hours_ago) if checked_hours_ago is not None else None,
        availability_misses=misses,
        expired_detected_at=NOW if expired else None,
        date_scraped=NOW - timedelta(days=n if scraped_days_ago is None else scraped_days_ago),
    )
    db.add(job)
    db.flush()
    if score is not None:
        match = Match(
            user_id=user_id, job_id=n, score=score, status="applied" if applied else "new",
            applied_at=NOW if applied else None, hidden_at=NOW if hidden else None,
        )
        db.add(match)
        db.flush()
        if letter:
            db.add(CoverLetter(match_id=match.id, generated_content="Dear", status="draft"))
    db.commit()
    return job


def candidate_ids(db, **kw) -> list[int]:
    """Walk next_candidate to exhaustion, excluding each pick, like the panel does."""
    seen: list[int] = []
    while (found := availability.next_candidate(db, 1, seen, NOW, **kw)):
        seen.append(found[0].id)
    return seen


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------
class TestVerdicts:
    def test_one_miss_is_a_strike_two_flag_it(self, db):
        add_job(db, 1)
        first = availability.record_verdict(db, 1, "missing", NOW)
        assert (first["misses"], first["expired"]) == (1, False)
        second = availability.record_verdict(db, 1, "missing", NOW + timedelta(days=1))
        assert (second["misses"], second["expired"]) == (2, True)
        assert db.get(JobListing, 1).expired_detected_at is not None

    def test_live_clears_strikes_and_unexpires(self, db):
        add_job(db, 1, misses=1, expired=True, seen_hours_ago=100)
        out = availability.record_verdict(db, 1, "live", NOW)
        job = db.get(JobListing, 1)
        assert (out["misses"], out["expired"]) == (0, False)
        assert job.expired_detected_at is None
        assert job.last_seen_live_at.replace(tzinfo=timezone.utc) == NOW

    def test_unknown_only_stamps_the_check(self, db):
        add_job(db, 1, misses=1, seen_hours_ago=100)
        out = availability.record_verdict(db, 1, "unknown", NOW)
        job = db.get(JobListing, 1)
        assert out["misses"] == 1 and not out["expired"]
        assert job.availability_checked_at is not None
        assert job.last_seen_live_at.replace(tzinfo=timezone.utc) == NOW - timedelta(hours=100)

    def test_an_applied_job_is_never_flagged(self, db):
        add_job(db, 1, applied=True)
        for _ in range(3):
            out = availability.record_verdict(db, 1, "missing", NOW)
        assert out["note"] == "applied" and out["misses"] == 0 and out["expired"] is False

    def test_a_job_never_captured_with_a_description_is_never_flagged(self, db):
        add_job(db, 1, description=None)
        for _ in range(3):
            out = availability.record_verdict(db, 1, "missing", NOW)
        assert out["note"] == "never_captured" and out["expired"] is False

    def test_unknown_job_and_bad_verdict(self, client, db):
        add_job(db, 1)
        assert client.post("/availability/999", json={"verdict": "live"}).status_code == 404
        assert client.post("/availability/1", json={"verdict": "gone"}).status_code == 422


# ---------------------------------------------------------------------------
# What to re-visit next
# ---------------------------------------------------------------------------
class TestCandidates:
    # The plan's D1 order. Every case below is built so that a WRONG rule gives a different
    # list: scores that disagree with ids, and capture dates that disagree with ids.
    def test_d1_order_exactly(self, db):
        add_job(db, 1, score=40, letter=True, seen_hours_ago=48)      # letter, low score
        add_job(db, 2, score=85, letter=True, seen_hours_ago=48)      # letter, higher score
        add_job(db, 3, score=70, seen_hours_ago=48)                   # no letter
        add_job(db, 4, score=95, seen_hours_ago=48)                   # no letter, beats every letter job's score
        add_job(db, 5, score=None, seen_hours_ago=48, scraped_days_ago=3)   # unscored, older
        add_job(db, 6, score=None, seen_hours_ago=48, scraped_days_ago=1)   # unscored, newer
        # letters by score [2, 1], then no letter by score [4, 3], then unscored newest first [6, 5]
        assert candidate_ids(db) == [2, 1, 4, 3, 6, 5]

    def test_each_pick_carries_its_group(self, db):
        add_job(db, 1, score=80, letter=True, seen_hours_ago=48)
        add_job(db, 2, score=80, seen_hours_ago=48)
        add_job(db, 3, score=None, seen_hours_ago=48)
        groups = [availability.next_candidate(db, 1, done, NOW)[1] for done in ([], [1], [1, 2])]
        assert groups == ["letter", "no_letter", "unscored"]

    def test_a_letter_job_is_checked_at_any_score_a_no_letter_job_from_60(self, db):
        add_job(db, 1, score=30, letter=True, seen_hours_ago=48)   # the 60 floor would drop it
        add_job(db, 2, score=59, seen_hours_ago=48)                # just under the floor: never
        add_job(db, 3, score=60, seen_hours_ago=48)                # at the floor: checked
        assert candidate_ids(db) == [1, 3]

    def test_equal_scores_go_lower_id_first(self, db):
        # Job 3 is the NEWER capture, so a "newest first" tie-break would give [3, 2].
        add_job(db, 2, score=80, seen_hours_ago=48, scraped_days_ago=5)
        add_job(db, 3, score=80, seen_hours_ago=48, scraped_days_ago=1)
        assert candidate_ids(db) == [2, 3]

    def test_a_fresh_unmatched_job_needs_no_check(self, db):
        add_job(db, 1, score=None, seen_hours_ago=2)
        assert candidate_ids(db) == []

    def test_recheck_after_is_fresh_for(self):
        # D2: one number, so "too stale for a letter" and "due a check" can't drift apart.
        assert availability.RECHECK_AFTER == availability.FRESH_FOR

    def test_due_after_24_hours(self, db):
        add_job(db, 1, score=70, seen_hours_ago=25)                             # seen 25 h ago: due
        add_job(db, 2, score=70, seen_hours_ago=23)                             # seen 23 h ago: not yet
        add_job(db, 3, score=70, seen_hours_ago=48, checked_hours_ago=23)       # checked 23 h ago: not yet
        add_job(db, 4, score=70, seen_hours_ago=48, checked_hours_ago=25)       # checked 25 h ago: due
        add_job(db, 5, score=70, seen_hours_ago=48, checked_hours_ago=12, misses=1)   # strike, < 1 day
        add_job(db, 6, score=70, seen_hours_ago=48, checked_hours_ago=30, misses=1)   # strike, > 1 day
        assert candidate_ids(db) == [1, 4, 6]

    def test_never_offers_what_must_not_be_checked(self, db):
        add_job(db, 1, score=90, seen_hours_ago=200, applied=True)
        add_job(db, 2, score=90, seen_hours_ago=200, hidden=True)
        add_job(db, 3, score=90, seen_hours_ago=200, expired=True)
        add_job(db, 4, score=90, seen_hours_ago=200, description=None)
        add_job(db, 5, score=50, seen_hours_ago=200)                    # no letter, under 60
        add_job(db, 7, score=90, seen_hours_ago=200, letter=True, hidden=True)   # a letter doesn't beat hidden
        db.add(Profile(id=2, name="Bob", email="bob@example.com", password_hash="x"))
        db.commit()
        add_job(db, 6, score=90, seen_hours_ago=200, user_id=2)         # someone else's match, not mine
        # Job 6 has only another profile's match: for this profile it is unscored, so it IS
        # offered (last group). Everything else is never offered.
        assert candidate_ids(db) == [6]
        assert availability.next_candidate(db, 1, now=NOW)[1] == "unscored"

    def test_exclude_skips_ids(self, db):
        add_job(db, 1, score=70, seen_hours_ago=200)
        add_job(db, 2, score=75, seen_hours_ago=200)
        assert availability.next_candidate(db, 1, [2], NOW)[0].id == 1

    def test_daily_cap_counts_verdicts(self, db):
        for n in range(1, availability.DAILY_CAP + 1):
            add_job(db, n, score=70, seen_hours_ago=200, checked_hours_ago=1 + n / 100)
        assert availability.remaining_today(db, NOW) == 0
        add_job(db, 99, score=70, seen_hours_ago=200, checked_hours_ago=30)   # outside the window
        assert availability.remaining_today(db, NOW) == 0

    def test_endpoint_shape_toggle_and_cap(self, client, db):
        add_job(db, 1, score=90, letter=True, seen_hours_ago=200)
        body = client.get("/availability/next").json()
        assert body["enabled"] is True and body["job"]["job_id"] == 1 and body["job"]["group"] == "letter"
        assert "tier" not in body["job"]
        assert body["job"]["source_job_id"] == "seek-1" and body["remaining_today"] == availability.DAILY_CAP
        assert client.get("/availability/next?exclude=1").json()["job"] is None
        set_preferences(db, 1, {"availability_checks_enabled": False})
        off = client.get("/availability/next").json()
        assert off["enabled"] is False and off["job"] is None


# ---------------------------------------------------------------------------
# The gate in front of the LLM
# ---------------------------------------------------------------------------
class TestGate:
    def _ids(self, db, enabled):
        return [j for j in db.scalars(
            select(JobListing.id).where(availability.llm_ready(enabled, NOW)).order_by(JobListing.id)
        )]

    def test_stale_and_expired_jobs_are_held_back(self, db):
        add_job(db, 1, seen_hours_ago=2)
        add_job(db, 2, seen_hours_ago=30)
        add_job(db, 3, seen_hours_ago=2, expired=True)
        assert self._ids(db, True) == [1]

    def test_switched_off_only_skips_flagged_jobs(self, db):
        add_job(db, 1, seen_hours_ago=2)
        add_job(db, 2, seen_hours_ago=30)
        add_job(db, 3, seen_hours_ago=2, expired=True)
        assert self._ids(db, False) == [1, 2]

    def test_letters_wait_for_a_sighting(self, db):
        add_job(db, 1, score=90, seen_hours_ago=2)
        add_job(db, 2, score=95, seen_hours_ago=48)
        add_job(db, 3, score=99, seen_hours_ago=2, expired=True)
        work = production.next_work(db, 1, now=NOW)
        assert work is not None and work.job_id == 1
        add_job(db, 4, score=60, seen_hours_ago=2)
        db.get(JobListing, 1).last_seen_live_at = NOW - timedelta(hours=30)
        db.commit()
        assert production.next_work(db, 1, now=NOW) is None

    def test_letters_ignore_the_gate_when_checks_are_off(self, db):
        add_job(db, 1, score=90, seen_hours_ago=48)
        set_preferences(db, 1, {"availability_checks_enabled": False})
        assert production.next_work(db, 1, now=NOW).job_id == 1


# ---------------------------------------------------------------------------
# Captures and manual flags
# ---------------------------------------------------------------------------
def ingest(client, **over):
    item = {"source_job_id": "seek-1", "url": "https://au.seek.com/job/1", "title": "Job 1", **over}
    res = client.post("/ingest", json={"listings": [item]})
    assert res.status_code == 200
    return res.json()


class TestCaptureAndFlags:
    def test_a_capture_with_a_description_unexpires(self, client, db):
        add_job(db, 1, expired=True, misses=1, seen_hours_ago=500)
        ingest(client, raw_description="The ad is back")
        db.expire_all()
        job = db.get(JobListing, 1)
        assert job.expired_detected_at is None and job.availability_misses == 0
        assert datetime.now(timezone.utc) - job.last_seen_live_at.replace(tzinfo=timezone.utc) < timedelta(minutes=1)

    def test_a_card_or_empty_description_proves_nothing(self, client, db):
        add_job(db, 1, expired=True, misses=1, seen_hours_ago=500)
        ingest(client)                           # a search card: no description
        ingest(client, raw_description="  ")     # a "NO DESCRIPTION" scan
        db.expire_all()
        job = db.get(JobListing, 1)
        assert job.expired_detected_at is not None and job.availability_misses == 1

    def test_a_new_capture_counts_as_seen_live(self, client, db):
        ingest(client, source_job_id="seek-7", raw_description="Brand new")
        job = db.scalar(select(JobListing).where(JobListing.source_job_id == "seek-7"))
        assert datetime.now(timezone.utc) - job.last_seen_live_at.replace(tzinfo=timezone.utc) < timedelta(minutes=1)

    def test_manual_flag_refuses_an_applied_job(self, client, db):
        add_job(db, 1, applied=True)
        res = client.patch("/jobs/1/expired")
        assert res.status_code == 200 and res.json()["expired_detected_at"] is None
        db.expire_all()
        assert db.get(JobListing, 1).expired_detected_at is None

    def test_marking_applied_lifts_an_earlier_flag(self, client, db):
        add_job(db, 1, expired=True, misses=2)
        assert client.patch("/jobs/1/status", json={"status": "applied"}).status_code == 200
        db.expire_all()
        job = db.get(JobListing, 1)
        assert job.expired_detected_at is None and job.availability_misses == 0
