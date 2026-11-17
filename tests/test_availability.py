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
from app.preferences import get_preferences, set_preferences

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
        cap = availability.DAILY_CAP
        add_job(db, cap + 1, score=70, seen_hours_ago=200, checked_hours_ago=30)   # outside the window
        for n in range(1, cap):
            add_job(db, n, score=70, seen_hours_ago=200, checked_hours_ago=1 + n / 1000)
        assert availability.remaining_today(db, NOW) == 1      # exact: one check left
        add_job(db, cap, score=70, seen_hours_ago=200, checked_hours_ago=2)
        assert availability.remaining_today(db, NOW) == 0

    def test_at_the_daily_cap_nothing_is_offered(self, client, db):
        # The endpoint reads the real clock, so these checks are stamped from it, not NOW.
        for n in range(1, availability.DAILY_CAP + 1):
            add_job(db, n, score=70, seen_hours_ago=None)
            db.get(JobListing, n).availability_checked_at = datetime.now(timezone.utc) - timedelta(minutes=n)
        db.commit()
        add_job(db, availability.DAILY_CAP + 1, score=95, letter=True, seen_hours_ago=None)   # due, top of the queue
        body = client.get("/availability/next").json()
        assert body["remaining_today"] == 0 and body["job"] is None and body["mode"] is None   # neither mode

    def test_endpoint_shape_toggle_and_cap(self, client, db):
        add_job(db, 1, score=90, letter=True, seen_hours_ago=200)
        body = client.get("/availability/next").json()
        assert body["enabled"] is True and body["job"]["job_id"] == 1 and body["job"]["group"] == "letter"
        assert "tier" not in body["job"] and body["mode"] == "fast"   # nothing seen live: fast mode
        assert body["job"]["source_job_id"] == "seek-1" and body["remaining_today"] == availability.DAILY_CAP
        none_left = client.get("/availability/next?exclude=1").json()
        assert none_left["job"] is None and none_left["mode"] is None
        set_preferences(db, 1, {"availability_checks_enabled": False})
        off = client.get("/availability/next").json()
        assert off["enabled"] is False and off["job"] is None and off["mode"] is None


# ---------------------------------------------------------------------------
# Fast mode (plan D3, P1, D8): the backend decides the mode and enforces its caps
# ---------------------------------------------------------------------------
FAST, BG = availability.MODE_FAST, availability.MODE_BACKGROUND


def stale_jobs(db, ids, **kw) -> None:
    """Scored, no letter, last seen two days ago: due a visit. Lower id = higher score, so the
    D1 order is id order."""
    for n in ids:
        add_job(db, n, score=99 - n, seen_hours_ago=48, **kw)


def drive(db, verdict_for, steps: int, start=NOW, gap=timedelta(seconds=5)) -> list[tuple[int, str]]:
    """Play one panel session: ask what's next, "visit" it, record the verdict in the mode it
    was opened in. Returns [(job id, mode)] in visiting order."""
    tried: list[int] = []
    out: list[tuple[int, str]] = []
    for i in range(steps):
        now = start + gap * i
        pick = availability.choose_next(db, 1, tried, now)
        if pick is None:
            break
        job, _, mode = pick
        tried.append(job.id)
        availability.record_verdict(db, job.id, verdict_for(job.id), now, mode=mode)
        out.append((job.id, mode))
    return out


def run_start(db):
    raw = get_preferences(db, 1)[availability.FAST_RUN_KEY]
    return datetime.fromisoformat(raw) if raw else None


class TestFastMode:
    def test_fires_with_fewer_than_10_live_and_follows_the_d1_order(self, db):
        stale_jobs(db, range(1, 13))
        add_job(db, 30, score=65, letter=True, seen_hours_ago=48)   # a letter job outranks every score
        job, group, mode = availability.choose_next(db, 1, [], NOW)
        assert (job.id, group, mode) == (30, "letter", FAST)

    def test_no_fast_mode_once_10_are_live(self, db):
        for n in range(1, 11):
            add_job(db, n, score=90, seen_hours_ago=2)   # 10 live
        stale_jobs(db, range(11, 14))
        job, _, mode = availability.choose_next(db, 1, [], NOW)
        assert (job.id, mode) == (11, BG)                 # still checked, at the background pace

    def test_stops_at_exactly_10_live(self, db):
        stale_jobs(db, range(1, 21))
        out = drive(db, lambda _: "live", steps=11)
        assert [m for _, m in out] == [FAST] * 10 + [BG]
        assert [j for j, _ in out[:10]] == list(range(1, 11))

    def test_stops_at_exactly_15_pages_when_few_are_live(self, db):
        stale_jobs(db, range(1, 21))
        # Live only every 5th job: 3 live in 15 pages, never near the goal of 10.
        out = drive(db, lambda n: "live" if n % 5 == 0 else "missing", steps=16)
        assert [m for _, m in out] == [FAST] * 15 + [BG]

    def test_a_closed_ad_checked_an_hour_ago_is_skipped_and_the_11th_taken(self, db):
        stale_jobs(db, range(1, 12))
        db.get(JobListing, 3).availability_checked_at = NOW - timedelta(hours=1)   # its first strike
        db.get(JobListing, 3).availability_misses = 1
        db.commit()
        out = drive(db, lambda _: "live", steps=10)
        assert out == [(n, FAST) for n in (1, 2, 4, 5, 6, 7, 8, 9, 10, 11)]

    def test_a_fresh_capture_counts_and_is_never_visited(self, db):
        stale_jobs(db, range(1, 11))
        db.get(JobListing, 1).last_seen_live_at = NOW - timedelta(hours=2)   # captured this morning
        db.commit()
        out = drive(db, lambda _: "live", steps=20)
        # 9 visits + the fresh one = 10 live; after that nothing is due at all.
        assert out == [(n, FAST) for n in range(2, 11)]

    def test_fewer_than_10_known_jobs_means_all_of_them(self, db):
        stale_jobs(db, range(1, 5))
        assert drive(db, lambda _: "live", steps=10) == [(n, FAST) for n in range(1, 5)]

    def test_unscored_live_jobs_dont_count_towards_the_10(self, db):
        for n in range(20, 30):
            add_job(db, n, score=None, seen_hours_ago=2)   # 10 fresh captures, not scored yet
        stale_jobs(db, [1])
        job, _, mode = availability.choose_next(db, 1, [], NOW)
        assert (job.id, mode) == (1, FAST)

    def test_unscored_jobs_are_never_visited_in_fast_mode(self, db):
        add_job(db, 1, score=None, seen_hours_ago=48)
        _, group, mode = availability.choose_next(db, 1, [], NOW)
        assert (group, mode) == ("unscored", BG)

    def test_at_most_one_run_an_hour(self, db):
        stale_jobs(db, range(1, 41))
        first = drive(db, lambda _: "missing", steps=15)
        assert [m for _, m in first] == [FAST] * 15
        # 59 minutes after the run started: a new session gets the background trickle only.
        job, _, mode = availability.choose_next(db, 1, [], NOW + timedelta(minutes=59))
        assert (job.id, mode) == (16, BG)
        # 61 minutes: a new run, and it skips the 15 pages checked within 24 h (P1).
        job, _, mode = availability.choose_next(db, 1, [], NOW + timedelta(minutes=61))
        assert (job.id, mode) == (16, FAST)

    def test_a_run_cut_short_carries_on_within_the_hour_up_to_15_pages_in_all(self, db):
        stale_jobs(db, range(1, 41))
        first = drive(db, lambda _: "missing", steps=6)            # e.g. the panel was closed
        second = drive(db, lambda _: "missing", steps=10, start=NOW + timedelta(minutes=30))
        assert [m for _, m in first] == [FAST] * 6
        assert [m for _, m in second] == [FAST] * 9 + [BG]

    def test_pages_the_panel_has_not_reported_yet_count_towards_the_cap(self, db):
        # The panel holds a "missing" back until it sees a live page (the canary), so the run's
        # count includes the session's tried ids (``exclude``) even before their verdicts arrive.
        stale_jobs(db, range(1, 21))
        drive(db, lambda _: "missing", steps=13)                   # 13 recorded pages
        later = NOW + timedelta(minutes=5)
        assert availability.choose_next(db, 1, [14], later)[2] == FAST    # 14 opened: one left
        assert availability.choose_next(db, 1, [14, 15], later)[2] == BG  # 15 opened: run over

    def test_only_a_fast_verdict_starts_the_run_and_only_once_an_hour(self, db):
        stale_jobs(db, range(1, 5))
        availability.record_verdict(db, 1, "live", NOW, mode=BG)
        assert run_start(db) is None
        availability.record_verdict(db, 2, "missing", NOW, mode=FAST)
        assert run_start(db) == NOW
        availability.record_verdict(db, 3, "live", NOW + timedelta(minutes=10), mode=FAST)
        assert run_start(db) == NOW                                # the same run
        availability.record_verdict(db, 4, "live", NOW + timedelta(minutes=61), mode=FAST)
        assert run_start(db) == NOW + timedelta(minutes=61)         # a new hour, a new run

    def test_an_unreadable_start_time_counts_as_no_run(self, db):
        set_preferences(db, 1, {availability.FAST_RUN_KEY: "not a date"})
        assert availability.fast_run_started(db, 1, NOW) is None

    def test_a_bad_mode_is_refused(self, db):
        stale_jobs(db, [1])
        with pytest.raises(ValueError):
            availability.record_verdict(db, 1, "live", NOW, mode="turbo")


class TestFastModeEndpoints:
    def test_asking_never_changes_anything(self, client, db):
        stale_jobs(db, range(1, 13))
        first = client.get("/availability/next").json()
        again = client.get("/availability/next").json()
        assert first == again and first["mode"] == "fast" and first["job"]["job_id"] == 1
        db.expire_all()
        assert get_preferences(db, 1)[availability.FAST_RUN_KEY] is None       # no run started
        assert all(db.get(JobListing, n).availability_checked_at is None for n in range(1, 13))

    def test_the_first_fast_verdict_starts_the_run(self, client, db):
        stale_jobs(db, [1, 2])
        assert client.post("/availability/1", json={"verdict": "live"}).status_code == 200   # background by default
        db.expire_all()
        assert get_preferences(db, 1)[availability.FAST_RUN_KEY] is None
        assert client.post("/availability/2", json={"verdict": "live", "mode": "fast"}).status_code == 200
        db.expire_all()
        assert get_preferences(db, 1)[availability.FAST_RUN_KEY] is not None

    def test_an_unknown_mode_is_a_422(self, client, db):
        stale_jobs(db, [1])
        assert client.post("/availability/1", json={"verdict": "live", "mode": "turbo"}).status_code == 422


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
