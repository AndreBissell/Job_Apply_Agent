"""Scoring beside letters (docs/second-worker-plan.md): the job claims, and the two loops.

No network and no LLM: extraction, matching, the quick screen and the letter step are
fakes, and the database is an in-memory SQLite shared by every thread, never real.db /
app.db. Every cross-thread wait is a threading.Event or join with a timeout (WAIT_S),
so a failure fails fast instead of hanging.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import job_claims
from app.api import events, idle_loop, worker
from app.db import Base
from app.models import JobListing, Match, Profile

WAIT_S = 10


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _clean_state():
    job_claims._held.clear()
    idle_loop._match_failures.clear()
    yield
    job_claims._held.clear()
    idle_loop._match_failures.clear()


@pytest.fixture()
def Session(monkeypatch):
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(eng, "connect")
    def _fk_on(conn, _):
        if isinstance(conn, sqlite3.Connection):
            conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(eng)
    factory = sessionmaker(bind=eng, expire_on_commit=False)
    with factory() as s:
        s.add(Profile(id=1, name="Bob", email="b@example.com", password_hash="x"))
        s.commit()
    monkeypatch.setattr(worker, "SessionLocal", factory)
    monkeypatch.setattr(idle_loop, "SessionLocal", factory)
    monkeypatch.setattr(events, "broadcast_from_thread", lambda name, data: None)
    yield factory
    Base.metadata.drop_all(eng)
    eng.dispose()


def add_job(Session, job_id: int, *, screened: bool = True, extracted: bool = False) -> int:
    with Session() as s:
        s.add(JobListing(
            id=job_id, source="seek", source_job_id=str(job_id), url=f"u{job_id}",
            title=f"Job {job_id}", company="Acme", raw_description="We build software.",
            date_scraped=datetime(2026, 11, 1, 9, job_id % 60, tzinfo=timezone.utc),
            last_seen_live_at=now_utc(),
            quick_screen_at=now_utc() if screened else None,
            extracted_at=now_utc() if extracted else None,
        ))
        s.commit()
    return job_id


def match_rows(Session, job_id: int) -> list[Match]:
    with Session() as s:
        return s.scalars(select(Match).where(Match.job_id == job_id)).all()


class FakePipeline:
    """Stands in for extract_job / match_job (patched on ``worker``): records each call,
    writes what the real ones write (``extracted_at``, the match row), and can hold the
    first call on an Event so another thread can meet the job mid-work."""

    def __init__(self, Session):
        self.Session = Session
        self.calls: list[tuple[str, int, bool]] = []
        self.inside = threading.Event()  # set when a held call has started
        self.go = threading.Event()  # the held call waits for this
        self.hold_first_match = False
        self._lock = threading.Lock()
        self.busy = 0
        self.max_busy = 0

    def extract_job(self, job_id, force=False):
        self._enter("extract", job_id, force)
        try:
            with self.Session() as s:
                s.get(JobListing, job_id).extracted_at = now_utc()
                s.commit()
        finally:
            self._leave()

    def match_job(self, job_id, profile_id, force=False):
        self._enter("match", job_id, force)
        try:
            if self.hold_first_match and len([c for c in self.calls if c[0] == "match"]) == 1:
                self.inside.set()
                assert self.go.wait(WAIT_S), "the test never let the held call finish"
            with self.Session() as s:
                row = s.scalar(select(Match).where(Match.user_id == profile_id, Match.job_id == job_id))
                if row is None:
                    s.add(Match(user_id=profile_id, job_id=job_id, score=70, reasoning="fake", gaps="[]", status="new"))
                elif force:
                    row.score = 71
                s.commit()
        finally:
            self._leave()

    def _enter(self, name, job_id, force):
        with self._lock:
            self.calls.append((name, job_id, force))
            self.busy += 1
            self.max_busy = max(self.max_busy, self.busy)

    def _leave(self):
        with self._lock:
            self.busy -= 1


@pytest.fixture()
def pipeline(Session, monkeypatch):
    fake = FakePipeline(Session)
    monkeypatch.setattr(worker, "extract_job", fake.extract_job)
    monkeypatch.setattr(worker, "match_job", fake.match_job)
    return fake


@pytest.fixture()
def letters(monkeypatch):
    """The letter step of /regenerate: records each call, writes nothing."""
    calls: list[int] = []

    def fake_generate_for(db, job_id, profile_id, emit=None, *, bypass_threshold=True):
        calls.append(job_id)

    monkeypatch.setattr(worker.letter_production, "generate_for", fake_generate_for)
    return calls


def in_thread(fn, *args, **kwargs) -> tuple[threading.Thread, dict]:
    out: dict = {}

    def run():
        try:
            out["result"] = fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - handed to the test's assertions
            out["error"] = exc

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t, out


# ---------------------------------------------------------------------------
# The claims, wired in (Step 3, D3 / D6)
# ---------------------------------------------------------------------------
class TestScorerClaims:
    def test_each_scoring_step_holds_the_claim_while_it_runs(self, Session, monkeypatch):
        add_job(Session, 5)
        seen: list[str] = []

        def probe(step):
            def fake(job_id, *args, **kwargs):
                seen.append(step if job_id in job_claims.held_ids() else f"{step} WITHOUT the claim")
                return "passed"
            return fake

        monkeypatch.setattr(idle_loop, "quick_screen", probe("screen"))
        monkeypatch.setattr(worker, "extract_job", probe("extract"))
        monkeypatch.setattr(worker, "match_job", probe("match"))
        monkeypatch.setattr(idle_loop, "match_job", probe("match again"))

        assert idle_loop._screen_job(5) == "passed"
        assert idle_loop._extract_and_match_job(5) == "done"
        assert idle_loop._match_attempt(5) == "ok"

        assert seen == ["screen", "extract", "match", "match again"]
        assert job_claims.held_ids() == frozenset()  # all released afterwards

    def test_a_claimed_job_is_skipped_by_every_scoring_step(self, Session, pipeline, monkeypatch):
        add_job(Session, 5)
        screened: list[int] = []
        monkeypatch.setattr(idle_loop, "quick_screen", lambda job_id, profile_id: screened.append(job_id) or "passed")
        job_claims.try_claim(5)  # another worker has it

        assert idle_loop._screen_job(5) == "claimed"
        assert idle_loop._extract_and_match_job(5) == "claimed"
        assert idle_loop._match_attempt(5) == "claimed"

        assert screened == [] and pipeline.calls == []
        assert idle_loop._match_failures == {}  # a skip is not a failed attempt
        assert job_claims.held_ids() == {5}  # the holder keeps its claim

    def test_the_claim_is_released_when_the_work_raises(self, Session, monkeypatch):
        add_job(Session, 5)

        def boom(job_id, profile_id):
            raise RuntimeError("model call blew up")

        monkeypatch.setattr(idle_loop, "quick_screen", boom)
        with pytest.raises(RuntimeError):
            idle_loop._screen_job(5)
        assert job_claims.held_ids() == frozenset()

    def test_candidate_queries_leave_claimed_jobs_out(self, Session):
        add_job(Session, 5)
        with Session() as s:
            ids = lambda: s.scalars(select(JobListing.id).where(idle_loop._unclaimed())).all()  # noqa: E731
            assert ids() == [5]
            job_claims.try_claim(5)
            assert ids() == []
            job_claims.release(5)
            assert ids() == [5]


class TestRegenerateWaitsForTheScorer:
    def test_regenerate_waits_then_runs_once_the_scorer_releases(self, Session, pipeline, letters):
        add_job(Session, 5)
        pipeline.hold_first_match = True

        scorer, scorer_out = in_thread(idle_loop._extract_and_match_job, 5)
        assert pipeline.inside.wait(WAIT_S)  # the scorer is mid-match, holding the claim

        regen, regen_out = in_thread(
            worker._process_listing, 5, 1, True, with_cover_letter=True, bypass_threshold=True, force=True,
        )
        regen.join(timeout=0.3)
        assert regen.is_alive(), "regenerate did not wait for the scorer"
        assert pipeline.calls == [("extract", 5, False), ("match", 5, False)]

        pipeline.go.set()
        scorer.join(WAIT_S)
        regen.join(WAIT_S)
        assert not scorer.is_alive() and not regen.is_alive()
        assert "error" not in scorer_out and "error" not in regen_out

        assert pipeline.calls == [
            ("extract", 5, False), ("match", 5, False),  # the scorer
            ("extract", 5, True), ("match", 5, True),  # then the user's regenerate
        ]
        assert pipeline.max_busy == 1  # never both at once
        assert len(match_rows(Session, 5)) == 1
        assert letters == [5]
        assert job_claims.held_ids() == frozenset()

    def test_after_the_timeout_it_goes_ahead_with_one_warning(self, Session, pipeline, letters, monkeypatch, caplog):
        add_job(Session, 5)
        monkeypatch.setattr(worker, "_CLAIM_WAIT_S", 0.2)
        job_claims.try_claim(5)  # a claim that is never released (a leak, or a hung call)

        with caplog.at_level(logging.WARNING, logger=worker.logger.name):
            worker._process_listing(5, 1, True, with_cover_letter=True, bypass_threshold=True, force=True)

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        text = warnings[0].getMessage()
        assert "job 5" in text and "claim held" in text and " s" in text
        assert pipeline.calls == [("extract", 5, True), ("match", 5, True)]
        assert letters == [5]  # the letter still lands
        assert job_claims.held_ids() == {5}  # it never freed the holder's claim

    def test_a_free_job_runs_at_once_and_is_released(self, Session, pipeline, letters, caplog):
        add_job(Session, 5)
        with caplog.at_level(logging.WARNING, logger=worker.logger.name):
            worker._process_listing(5, 1, True, with_cover_letter=True, bypass_threshold=True, force=True)
        assert [r for r in caplog.records if r.levelno == logging.WARNING] == []
        assert len(pipeline.calls) == 2 and letters == [5]
        assert job_claims.held_ids() == frozenset()

    def test_the_letter_is_written_after_the_claim_is_released(self, Session, pipeline, monkeypatch):
        add_job(Session, 5)
        held_during_letter: list[bool] = []

        def fake_generate_for(db, job_id, profile_id, emit=None, *, bypass_threshold=True):
            held_during_letter.append(job_id in job_claims.held_ids())

        monkeypatch.setattr(worker.letter_production, "generate_for", fake_generate_for)
        worker._process_listing(5, 1, True, with_cover_letter=True, bypass_threshold=True, force=True)
        assert held_during_letter == [False]  # the scorer is not kept off the job for minutes
