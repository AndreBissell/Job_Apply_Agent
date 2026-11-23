"""Scoring beside letters (docs/second-worker-plan.md): the job claims, and the two loops.

No network and no LLM: extraction, matching, the quick screen and the letter step are
fakes, and the database is an in-memory SQLite shared by every thread, never real.db /
app.db. Every cross-thread wait is a threading.Event or join with a timeout (WAIT_S),
so a failure fails fast instead of hanging.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.api.main as main
import app.api.profile_ui as profile_ui
from app import job_claims, letter_bands
from app.api import events, idle_loop, worker
from app.db import Base
from app.llm.letter.production import Landed, Work
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


# ---------------------------------------------------------------------------
# Two executors, two loops (Step 4, D1)
# ---------------------------------------------------------------------------
@pytest.fixture()
def executors(monkeypatch):
    """Real single-thread pools in place of the app's, with names the tests can check."""
    letters = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-letters")
    scoring = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-scoring")
    monkeypatch.setattr(worker, "_bg_executor", letters)
    monkeypatch.setattr(worker, "_score_executor", scoring)
    yield letters, scoring
    letters.shutdown(wait=True)
    scoring.shutdown(wait=True)


@pytest.fixture()
def letter_loop_fakes(monkeypatch):
    """The letter pass with no real letter: no retention sweep, no pacing sleep, no pause.
    ``next_work`` offers one letter for job 9; the returned list records each letter run."""
    monkeypatch.setattr(idle_loop, "_retention_tick", lambda: None)
    monkeypatch.setattr(idle_loop, "_IDLE_INTERVAL_S", 0)
    monkeypatch.setattr(idle_loop, "_letters_paused_until", 0.0)
    monkeypatch.setattr(idle_loop.letter_production, "next_work", lambda db, profile_id, **kw: Work("pipeline", 9))
    ran: list[int] = []

    def run_work(work, profile_id, emit=None, **kw):
        ran.append(work.job_id)
        return Landed("pipeline", work.job_id, "done", letter="x")

    monkeypatch.setattr(idle_loop.letter_production, "run_work", run_work)
    return ran


class TestScoringBesideLetters:
    def test_a_new_job_is_scored_while_a_letter_is_being_written(
        self, Session, pipeline, executors, letter_loop_fakes, monkeypatch,
    ):
        """The goal of the plan: a letter blocks the letter worker, and a scoring pass still
        extracts and scores a fresh job; the letter is still running afterwards."""
        add_job(Session, 5)
        letter_started, letter_go = threading.Event(), threading.Event()

        def slow_letter(work, profile_id, emit=None, **kw):
            letter_started.set()
            assert letter_go.wait(WAIT_S), "the test never let the letter finish"
            return Landed("pipeline", work.job_id, "done", letter="x")

        monkeypatch.setattr(idle_loop.letter_production, "run_work", slow_letter)

        async def scenario():
            letter = asyncio.create_task(idle_loop._letter_pass())
            assert await asyncio.to_thread(letter_started.wait, WAIT_S)
            await asyncio.wait_for(idle_loop._scoring_pass(), WAIT_S)
            letter_still_running = not letter.done()
            letter_go.set()
            await asyncio.wait_for(letter, WAIT_S)
            return letter_still_running

        try:
            assert asyncio.run(scenario()) is True
        finally:
            letter_go.set()
        assert pipeline.calls == [("extract", 5, False), ("match", 5, False)]
        assert len(match_rows(Session, 5)) == 1

    def test_the_scoring_pass_runs_on_the_scoring_worker(self, Session, executors, monkeypatch):
        add_job(Session, 5, screened=False)
        threads: list[str] = []

        def fake_screen(job_id, profile_id):
            threads.append(threading.current_thread().name)
            return "passed"

        monkeypatch.setattr(idle_loop, "quick_screen", fake_screen)
        assert asyncio.run(idle_loop._scoring_pass()) == idle_loop._IDLE_INTERVAL_S
        assert len(threads) == 1 and threads[0].startswith("test-scoring")

    def test_the_scorer_skips_a_claimed_job_and_takes_the_next(self, Session, pipeline, executors):
        add_job(Session, 5)  # the older one: first in line
        add_job(Session, 6)
        job_claims.try_claim(5)  # another worker (a user's /regenerate) has it

        asyncio.run(idle_loop._scoring_pass())

        assert pipeline.calls == [("extract", 6, False), ("match", 6, False)]
        assert match_rows(Session, 5) == [] and len(match_rows(Session, 6)) == 1
        assert job_claims.held_ids() == {5}

    def test_a_job_claimed_after_the_query_ends_the_pass_at_once(self, Session, pipeline, executors, monkeypatch):
        add_job(Session, 5)

        def picked_then_claimed_elsewhere():
            assert job_claims.try_claim(5)
            return 5

        monkeypatch.setattr(idle_loop, "_next_to_screen", lambda: None)
        monkeypatch.setattr(idle_loop, "_next_to_extract", picked_then_claimed_elsewhere)
        assert asyncio.run(idle_loop._scoring_pass()) == 0
        assert pipeline.calls == []

    def test_a_letter_budget_pause_does_not_stop_scoring(
        self, Session, pipeline, executors, letter_loop_fakes, monkeypatch,
    ):
        add_job(Session, 5)
        monkeypatch.setattr(idle_loop, "_letters_paused_until", time.monotonic() + 600)

        assert asyncio.run(idle_loop._letter_pass()) == idle_loop._NOTHING_TO_DO_S
        assert letter_loop_fakes == []  # no letter while paused
        assert asyncio.run(idle_loop._scoring_pass()) == idle_loop._IDLE_INTERVAL_S
        assert len(match_rows(Session, 5)) == 1

    def test_a_scoring_back_off_does_not_stop_letters(self, Session, executors, letter_loop_fakes, monkeypatch):
        add_job(Session, 5)
        monkeypatch.setattr(worker, "extract_job", lambda job_id, force=False: None)  # extraction fails
        monkeypatch.setattr(worker, "match_job", lambda job_id, profile_id, force=False: None)

        assert asyncio.run(idle_loop._scoring_pass()) == idle_loop._SCORING_BACKOFF_S
        assert asyncio.run(idle_loop._letter_pass()) == 0
        assert letter_loop_fakes == [9]


async def _run_both_loops_until(done: asyncio.Event) -> None:
    """Run the real ``_processing_idle_loop`` until ``done`` is set (at most WAIT_S), then
    cancel it the way main's lifespan does on shutdown."""
    task = asyncio.create_task(idle_loop._processing_idle_loop())
    try:
        await asyncio.wait_for(done.wait(), WAIT_S)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


class TestTheLoopsAreIndependent:
    @pytest.fixture(autouse=True)
    def _fast_start(self, monkeypatch):
        monkeypatch.setattr(idle_loop, "_STARTUP_DELAY_S", 0)
        monkeypatch.setattr(idle_loop, "_recover_orphaned_runs", lambda: None)

    def test_a_scoring_back_off_does_not_hold_up_the_letter_loop(self, executors, monkeypatch):
        counts = {"letters": 0, "scoring": 0}

        async def scenario():
            done = asyncio.Event()

            async def letter_pass():
                counts["letters"] += 1
                if counts["letters"] == 3:
                    done.set()
                return 0

            async def scoring_pass():
                counts["scoring"] += 1
                return 180  # backing off: the next scoring pass is 3 minutes away

            monkeypatch.setattr(idle_loop, "_letter_pass", letter_pass)
            monkeypatch.setattr(idle_loop, "_scoring_pass", scoring_pass)
            await _run_both_loops_until(done)

        asyncio.run(scenario())
        assert counts["letters"] >= 3  # kept going (it may finish a pass or two more before the cancel lands)
        assert counts["scoring"] == 1  # still backing off

    @pytest.mark.parametrize("failing", ["letters", "scoring"])
    def test_a_pass_that_raises_does_not_stop_the_other_loop(self, executors, monkeypatch, caplog, failing):
        counts = {"letters": 0, "scoring": 0}
        healthy = "scoring" if failing == "letters" else "letters"

        def make(name, done):
            async def one_pass():
                counts[name] += 1
                if name == failing:
                    raise RuntimeError(f"{name} pass blew up")
                if counts[name] == 3:
                    done.set()
                return 0
            return one_pass

        async def scenario():
            done = asyncio.Event()
            monkeypatch.setattr(idle_loop, "_letter_pass", make("letters", done))
            monkeypatch.setattr(idle_loop, "_scoring_pass", make("scoring", done))
            await _run_both_loops_until(done)

        with caplog.at_level(logging.ERROR, logger=idle_loop.logger.name):
            asyncio.run(scenario())
        assert counts[healthy] >= 3
        assert counts[failing] == 1  # the failing loop is pausing (_LOOP_ERROR_PAUSE_S), not dead
        loop_name = "Letter loop" if failing == "letters" else "Scoring loop"
        assert [r.getMessage() for r in caplog.records] == [f"{loop_name} error"]

    def test_orphaned_runs_are_recovered_on_the_letter_worker_before_the_first_letter(
        self, executors, monkeypatch,
    ):
        order: list[str] = []

        def recover():
            order.append(f"recover on {threading.current_thread().name.rsplit('_', 1)[0]}")

        async def scenario():
            done = asyncio.Event()

            async def letter_pass():
                order.append("letter pass")
                done.set()
                return 600

            async def scoring_pass():
                order.append("scoring pass")
                return 600

            monkeypatch.setattr(idle_loop, "_recover_orphaned_runs", recover)
            monkeypatch.setattr(idle_loop, "_letter_pass", letter_pass)
            monkeypatch.setattr(idle_loop, "_scoring_pass", scoring_pass)
            await _run_both_loops_until(done)

        asyncio.run(scenario())
        assert [o for o in order if o != "scoring pass"] == ["recover on test-letters", "letter pass"]

    def test_lifespan_starts_both_loops_and_cancels_both_on_shutdown(self, monkeypatch):
        started = {"letters": asyncio.Event, "scoring": asyncio.Event}
        cancelled: list[str] = []

        def fake_loop(name):
            async def loop():
                started[name].set()
                try:
                    await asyncio.sleep(3600)
                except asyncio.CancelledError:
                    cancelled.append(name)
                    raise
            return loop

        monkeypatch.setattr(idle_loop, "_letter_loop", fake_loop("letters"))
        monkeypatch.setattr(idle_loop, "_scoring_loop", fake_loop("scoring"))

        async def scenario():
            for name in started:
                started[name] = asyncio.Event()
            async with main.lifespan(main.app):
                for event_ in started.values():
                    await asyncio.wait_for(event_.wait(), WAIT_S)
            for _ in range(10):  # shutdown cancelled the task: let the cancellation reach both loops
                await asyncio.sleep(0)

        asyncio.run(scenario())
        assert sorted(cancelled) == ["letters", "scoring"]


# ---------------------------------------------------------------------------
# Letters never reach the scoring worker (D1)
# ---------------------------------------------------------------------------
class RecordingExecutor:
    def __init__(self):
        self.calls: list = []

    def submit(self, fn, *args, **kwargs):
        self.calls.append(fn)


class TestLettersStayOnTheLetterWorker:
    @pytest.fixture()
    def client(self, Session, monkeypatch):
        async def _no_idle_loop():
            return None

        monkeypatch.setattr(idle_loop, "_processing_idle_loop", _no_idle_loop)

        def _db():
            s = Session()
            try:
                yield s
            finally:
                s.close()

        deps = (main.get_db, profile_ui.get_db)
        for dep in deps:
            main.app.dependency_overrides[dep] = _db
        letter_bands.queued.clear()
        try:
            with TestClient(main.app, raise_server_exceptions=True) as c:
                yield c
        finally:
            for dep in deps:
                main.app.dependency_overrides.pop(dep, None)
            letter_bands.queued.clear()

    def test_regenerate_band_letters_and_write_a_letter_use_the_letter_worker(self, Session, client, monkeypatch):
        letters, scoring = RecordingExecutor(), RecordingExecutor()
        monkeypatch.setattr(worker, "_bg_executor", letters)
        monkeypatch.setattr(worker, "_score_executor", scoring)
        for job_id, score in ((10, 62), (11, 57), (12, 90)):
            add_job(Session, job_id, extracted=True)
            with Session() as s:
                s.add(Match(user_id=1, job_id=job_id, score=score, reasoning="r", gaps="[]", status="new"))
                s.commit()

        assert client.post("/jobs/12/regenerate").json() == {"status": "queued"}
        assert client.post("/jobs/letter-bands/write", json={"lo": 60, "hi": 64}).json()["queued"] == 1
        assert client.post("/jobs/11/write-letter").json()["queued"] == 1

        assert letters.calls == [worker._rewrite_letter, worker._write_band_letters, worker._write_band_letters]
        assert scoring.calls == []
