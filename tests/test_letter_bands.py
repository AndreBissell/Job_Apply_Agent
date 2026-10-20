"""Tests for the Overview's "no letter yet" bands (docs/overview-applied-rework-plan.md,
Step 3, D3-D6): ``app/letter_bands.py``, ``GET /jobs/letter-bands``,
``POST /jobs/letter-bands/write``, ``worker._write_band_letters`` and the one-shot's
``below_bar`` hand-off in ``production._run_oneshot``.

No network and no LLM: ``worker._bg_executor`` is a recording fake, ``run_work`` and
``generate_cover_letter`` are monkeypatched, and the database is an in-memory SQLite (both
``get_db`` dependencies are overridden and ``worker.SessionLocal`` is replaced), never
real.db / app.db.
"""
from __future__ import annotations

import itertools
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.api.main as main
import app.api.profile_ui as profile_ui
from app import letter_bands
from app.api import events, idle_loop, worker
from app.db import Base
from app.llm.letter import production
from app.llm.letter.production import Landed, Work
from app.models import CoverLetter, JobListing, LetterRun, Match, Profile
from app.preferences import set_preferences

_ids = itertools.count(1000)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _clean_queue():
    """``letter_bands.queued`` is module state: never let one test see another's."""
    letter_bands.queued.clear()
    yield
    letter_bands.queued.clear()


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
def Session(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def client(Session, monkeypatch):
    async def _no_idle_loop():  # the real one would work on whatever SessionLocal points at
        return None

    monkeypatch.setattr(idle_loop, "_processing_idle_loop", _no_idle_loop)

    def _db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    main.app.dependency_overrides[profile_ui.get_db] = _db
    main.app.dependency_overrides[main.get_db] = _db
    with TestClient(main.app, raise_server_exceptions=True) as c:
        yield c
    main.app.dependency_overrides.pop(profile_ui.get_db, None)
    main.app.dependency_overrides.pop(main.get_db, None)


@pytest.fixture()
def db(Session):
    s = Session()
    s.add(Profile(id=1, name="Bob", email="b@x.com", password_hash="x", summary="Grad."))
    s.add(Profile(id=2, name="Other", email="o@x.com", password_hash="x", summary="Other."))
    s.commit()
    yield s
    s.close()


class FakeExecutor:
    def __init__(self):
        self.calls: list[tuple] = []

    def submit(self, fn, *args, **kwargs):
        self.calls.append((fn, args, kwargs))


@pytest.fixture()
def fake_exec(monkeypatch):
    fake = FakeExecutor()
    monkeypatch.setattr(worker, "_bg_executor", fake)
    return fake


def seed(db, score, *, user_id=1, seen=None, extracted=True, **job_fields):
    """One JobListing (seen live now unless ``seen`` is given) plus a Match. Returns (job, match)."""
    n = next(_ids)
    job = JobListing(
        source="seek", source_job_id=str(n), url=f"u{n}", title=f"Job {n}", company="Co",
        raw_description="desc",
        extracted_at=now_utc() if extracted else None,
        last_seen_live_at=seen if seen is not None else now_utc(),
        **job_fields,
    )
    db.add(job)
    db.flush()
    match = Match(user_id=user_id, job_id=job.id, score=score, status="new")
    db.add(match)
    db.commit()
    return job, match


def bands_of(client, **params):
    res = client.get("/jobs/letter-bands", params=params)
    assert res.status_code == 200, res.text
    return res.json()


def ranges(data):
    return [(b["lo"], b["hi"]) for b in data["bands"]]


# ---------------------------------------------------------------------------
# 1. band_of maths
# ---------------------------------------------------------------------------
class TestBandOf:
    def test_bar_80(self):
        assert letter_bands.band_of(79, 80) == (75, 79)
        assert letter_bands.band_of(75, 80) == (75, 79)
        assert letter_bands.band_of(74, 80) == (70, 74)

    def test_bar_77(self):
        assert letter_bands.band_of(76, 77) == (72, 76)
        assert letter_bands.band_of(72, 77) == (72, 76)
        assert letter_bands.band_of(71, 77) == (67, 71)

    def test_bar_75_matches_d3_example(self):
        assert letter_bands.band_of(64, 75) == (60, 64)
        assert letter_bands.band_of(60, 75) == (60, 64)
        assert letter_bands.band_of(49, 75) == (45, 49)

    def test_the_lowest_band_clamps_lo_at_zero(self):
        # bar 77: 76-72, ..., 6-2, then 1-(-3) clamped to 0-1
        assert letter_bands.band_of(6, 77) == (2, 6)
        assert letter_bands.band_of(1, 77) == (0, 1)
        assert letter_bands.band_of(0, 77) == (0, 1)

    def test_a_bar_that_is_a_multiple_of_width_has_no_clamp(self):
        assert letter_bands.band_of(0, 75) == (0, 4)

    def test_a_tiny_bar_gives_a_clamped_single_band(self):
        assert letter_bands.band_of(0, 3) == (0, 2)

    def test_every_score_below_the_bar_has_a_band_containing_it(self):
        for bar in (1, 3, 5, 77, 80, 100):
            for score in range(bar):
                lo, hi = letter_bands.band_of(score, bar)
                assert 0 <= lo <= score <= hi < bar


# ---------------------------------------------------------------------------
# 2. GET /jobs/letter-bands
# ---------------------------------------------------------------------------
class TestGetBands:
    def test_empty(self, client, db):
        assert bands_of(client) == {
            "bar": 75, "width": 5, "total": 0, "writing": 0,
            "est_usd_per_letter": letter_bands.ESTIMATE_DEFAULT_USD, "est_basis": "default", "bands": [],
            "rewrite_est_usd": letter_bands.REWRITE_DEFAULT_USD,
        }

    def test_the_d3_real_db_example(self, client, db):
        for s in (60, 62, 64, 55, 56, 57, 58, 47, 42, 37):
            seed(db, s)
        data = bands_of(client)
        assert data["bar"] == 75 and data["width"] == 5 and data["total"] == 10
        assert ranges(data) == [(60, 64), (55, 59), (45, 49), (40, 44), (35, 39)]
        assert [b["count"] for b in data["bands"]] == [3, 4, 1, 1, 1]
        # every key the card reads
        assert data["bands"][0] == {"lo": 60, "hi": 64, "count": 3, "ready": 3, "waiting_check": 0}

    def test_empty_bands_are_skipped(self, client, db):
        seed(db, 72)
        seed(db, 52)
        assert ranges(bands_of(client)) == [(70, 74), (50, 54)]

    def test_only_the_six_highest_bands_come_back_but_total_counts_all(self, client, db):
        for s in (60, 62, 64, 55, 56, 57, 58, 47, 42, 37, 32, 27, 22):
            seed(db, s)
        data = bands_of(client)
        assert ranges(data) == [(60, 64), (55, 59), (45, 49), (40, 44), (35, 39), (30, 34)]
        assert data["total"] == 13

    def test_a_fractional_score_bands_by_its_floor(self, client, db):
        seed(db, 64.9)
        data = bands_of(client)
        assert ranges(data) == [(60, 64)] and data["bands"][0]["count"] == 1

    def test_a_score_equal_to_the_bar_is_excluded(self, client, db):
        seed(db, 75)
        seed(db, 80)
        data = bands_of(client)
        assert data["total"] == 0 and data["bands"] == []

    def test_a_score_just_under_the_bar_is_in(self, client, db):
        seed(db, 74.9)
        assert ranges(bands_of(client)) == [(70, 74)]

    def test_the_bar_comes_from_the_auto_letter_preference(self, client, db):
        set_preferences(db, 1, {"auto_cover_letter_min_score": 80})
        for s in (79, 75, 74, 80):
            seed(db, s)
        data = bands_of(client)
        assert data["bar"] == 80
        assert ranges(data) == [(75, 79), (70, 74)]
        assert [b["count"] for b in data["bands"]] == [2, 1]
        assert data["total"] == 3

    def test_bar_77_shifts_the_bands(self, client, db):
        set_preferences(db, 1, {"auto_cover_letter_min_score": 77})
        for s in (76, 72, 71):
            seed(db, s)
        assert ranges(bands_of(client)) == [(72, 76), (67, 71)]

    def test_the_lowest_band_is_clamped_in_the_endpoint(self, client, db):
        set_preferences(db, 1, {"auto_cover_letter_min_score": 77})
        seed(db, 1)
        seed(db, 0)
        data = bands_of(client)
        assert ranges(data) == [(0, 1)] and data["bands"][0]["count"] == 2

    def test_a_queued_job_leaves_its_band(self, client, db):
        """Being written, it shows in Keep Applying instead (GET /jobs ``letter_queued``)."""
        a, _ = seed(db, 62)
        seed(db, 61)
        letter_bands.queued.add(a.id)
        data = bands_of(client)
        assert data["bands"][0]["count"] == 1 and data["total"] == 1 and data["writing"] == 1

    def test_a_band_whose_jobs_are_all_queued_disappears(self, client, db):
        a, _ = seed(db, 77)
        seed(db, 62)
        letter_bands.queued.add(a.id)
        assert ranges(bands_of(client)) == [(60, 64)]

    def test_profile_id_param_selects_the_profile(self, client, db):
        seed(db, 62, user_id=2)
        assert bands_of(client)["total"] == 0
        assert bands_of(client, profile_id=2)["total"] == 1


# ---------------------------------------------------------------------------
# 4. D4 exclusions
# ---------------------------------------------------------------------------
def _add_letter(db, job, match):
    db.add(CoverLetter(match_id=match.id, generated_content="Dear...", status="draft"))
    db.commit()


def _apply(db, job, match):
    match.applied_at = now_utc()
    db.commit()


def _hide(db, job, match):
    match.hidden_at = now_utc()
    db.commit()


def _expire(db, job, match):
    job.expired_detected_at = now_utc()
    db.commit()


def _unextract(db, job, match):
    job.extracted_at = None
    db.commit()


def _unscore(db, job, match):
    match.score = None
    db.commit()


EXCLUSIONS = {
    "has_cover_letter": _add_letter,
    "applied": _apply,
    "hidden": _hide,
    "expired": _expire,
    "not_extracted": _unextract,
    "no_score": _unscore,
}


class TestExclusions:
    @pytest.mark.parametrize("name", list(EXCLUSIONS))
    def test_excluded_from_counts_and_from_a_write(self, client, db, fake_exec, name):
        control, _ = seed(db, 62)
        job, match = seed(db, 63)
        EXCLUSIONS[name](db, job, match)

        data = bands_of(client)
        assert data["total"] == 1
        assert data["bands"][0]["count"] == 1 and data["bands"][0]["ready"] == 1

        res = client.post("/jobs/letter-bands/write", json={"lo": 60, "hi": 64})
        assert res.json() == {"queued": 1, "waiting_check": 0, "skipped": 0}
        assert fake_exec.calls[0][1][0] == [control.id]
        assert letter_bands.queued == {control.id}

    def test_another_profiles_match_is_excluded(self, client, db, fake_exec):
        control, _ = seed(db, 62)
        seed(db, 63, user_id=2)
        data = bands_of(client)
        assert data["total"] == 1 and data["bands"][0]["count"] == 1
        res = client.post("/jobs/letter-bands/write", json={"lo": 60, "hi": 64})
        assert res.json()["queued"] == 1
        assert fake_exec.calls[0][1][0] == [control.id]

    def test_a_job_that_only_other_profile_scored_does_not_hide_mine(self, client, db):
        # same listing, two profiles' matches: only profile 1's counts for profile 1
        job, _ = seed(db, 62)
        db.add(Match(user_id=2, job_id=job.id, score=40, status="new"))
        db.commit()
        assert bands_of(client)["total"] == 1
        assert ranges(bands_of(client, profile_id=2)) == [(40, 44)]


# ---------------------------------------------------------------------------
# 5. The freshness gate (D6)
# ---------------------------------------------------------------------------
class TestGate:
    def test_a_stale_sighting_waits_for_a_check_when_checks_are_on(self, client, db, fake_exec):
        seed(db, 62, seen=now_utc() - timedelta(days=2))
        b = bands_of(client)["bands"][0]
        assert (b["count"], b["ready"], b["waiting_check"]) == (1, 0, 1)

        res = client.post("/jobs/letter-bands/write", json={"lo": 60, "hi": 64})
        assert res.json() == {"queued": 0, "waiting_check": 1, "skipped": 0}
        assert fake_exec.calls == [] and letter_bands.queued == set()

    def test_a_job_never_seen_live_waits_too(self, client, db):
        job, _ = seed(db, 62)
        job.last_seen_live_at = None  # the column default stamps "now" on insert, so null it after
        db.commit()
        b = bands_of(client)["bands"][0]
        assert (b["ready"], b["waiting_check"]) == (0, 1)

    def test_with_checks_off_a_stale_job_is_ready(self, client, db, fake_exec):
        set_preferences(db, 1, {"availability_checks_enabled": False})
        job, _ = seed(db, 62, seen=now_utc() - timedelta(days=2))
        b = bands_of(client)["bands"][0]
        assert (b["count"], b["ready"], b["waiting_check"]) == (1, 1, 0)

        res = client.post("/jobs/letter-bands/write", json={"lo": 60, "hi": 64})
        assert res.json() == {"queued": 1, "waiting_check": 0, "skipped": 0}
        assert fake_exec.calls[0][1][0] == [job.id]

    def test_a_flagged_closed_job_is_never_counted_even_with_checks_off(self, client, db):
        set_preferences(db, 1, {"availability_checks_enabled": False})
        seed(db, 62, expired_detected_at=now_utc())
        assert bands_of(client)["total"] == 0

    def test_fresh_and_stale_in_one_band(self, client, db, fake_exec):
        fresh, _ = seed(db, 63)
        seed(db, 62, seen=now_utc() - timedelta(days=2))
        b = bands_of(client)["bands"][0]
        assert (b["count"], b["ready"], b["waiting_check"]) == (2, 1, 1)
        res = client.post("/jobs/letter-bands/write", json={"lo": 60, "hi": 64})
        assert res.json() == {"queued": 1, "waiting_check": 1, "skipped": 0}
        assert fake_exec.calls[0][1][0] == [fresh.id]


# ---------------------------------------------------------------------------
# 6-9. POST /jobs/letter-bands/write
# ---------------------------------------------------------------------------
class TestWrite:
    def test_happy_path(self, client, db, fake_exec):
        low, _ = seed(db, 60)
        high, _ = seed(db, 64)
        mid, _ = seed(db, 62)

        res = client.post("/jobs/letter-bands/write", json={"lo": 60, "hi": 64})

        assert res.status_code == 200
        assert res.json() == {"queued": 3, "waiting_check": 0, "skipped": 0}
        ((fn, args, kwargs),) = fake_exec.calls
        assert fn is worker._write_band_letters
        assert args == ([high.id, mid.id, low.id], 1) and kwargs == {}
        assert letter_bands.queued == {low.id, mid.id, high.id}
        data = bands_of(client)
        assert data["bands"] == [] and data["writing"] == 3  # all three moved to Keep Applying

    def test_profile_id_query_param_is_passed_on(self, client, db, fake_exec):
        job, _ = seed(db, 62, user_id=2)
        res = client.post("/jobs/letter-bands/write", params={"profile_id": 2}, json={"lo": 60, "hi": 64})
        assert res.json()["queued"] == 1
        assert fake_exec.calls[0][1] == ([job.id], 2)

    def test_nothing_to_write_submits_nothing(self, client, db, fake_exec):
        res = client.post("/jobs/letter-bands/write", json={"lo": 60, "hi": 64})
        assert res.status_code == 200
        assert res.json() == {"queued": 0, "waiting_check": 0, "skipped": 0}
        assert fake_exec.calls == []

    def test_a_job_outside_the_band_is_not_queued(self, client, db, fake_exec):
        inside, _ = seed(db, 62)
        seed(db, 58)
        client.post("/jobs/letter-bands/write", json={"lo": 60, "hi": 64})
        assert fake_exec.calls[0][1][0] == [inside.id]

    def test_the_write_cap_is_ten_best_first(self, client, db, fake_exec):
        assert letter_bands.WRITE_CAP == 10
        jobs = [seed(db, 64.9 - 0.1 * i)[0] for i in range(12)]  # all floor to 63/64
        res = client.post("/jobs/letter-bands/write", json={"lo": 60, "hi": 64})
        assert res.json() == {"queued": 10, "waiting_check": 0, "skipped": 2}
        assert fake_exec.calls[0][1][0] == [j.id for j in jobs[:10]]
        assert letter_bands.queued == {j.id for j in jobs[:10]}

    def test_a_second_click_does_not_double_queue(self, client, db, fake_exec):
        for s in (60, 62, 64):
            seed(db, s)
        first = client.post("/jobs/letter-bands/write", json={"lo": 60, "hi": 64})
        assert first.json()["queued"] == 3 and len(fake_exec.calls) == 1

        second = client.post("/jobs/letter-bands/write", json={"lo": 60, "hi": 64})
        assert second.json() == {"queued": 0, "waiting_check": 0, "skipped": 3}
        assert len(fake_exec.calls) == 1

    def test_the_cap_is_per_click_so_the_rest_can_be_queued_next(self, client, db, fake_exec):
        jobs = [seed(db, 64.9 - 0.1 * i)[0] for i in range(12)]
        client.post("/jobs/letter-bands/write", json={"lo": 60, "hi": 64})
        second = client.post("/jobs/letter-bands/write", json={"lo": 60, "hi": 64})
        # "skipped" counts ready jobs that were already queued (the first click's 10)
        assert second.json() == {"queued": 2, "waiting_check": 0, "skipped": 10}
        assert fake_exec.calls[1][1][0] == [j.id for j in jobs[10:]]

    @pytest.mark.parametrize("lo,hi", [(60, 63), (61, 65), (75, 79), (70, 75), (64, 60), (-5, -1), (-1, 4), (0, 200)])
    def test_not_a_band_is_400(self, client, db, fake_exec, lo, hi):
        seed(db, 62)
        res = client.post("/jobs/letter-bands/write", json={"lo": lo, "hi": hi})
        assert res.status_code == 400
        assert fake_exec.calls == [] and letter_bands.queued == set()

    def test_a_band_for_the_old_bar_is_400_after_the_bar_moves(self, client, db, fake_exec):
        set_preferences(db, 1, {"auto_cover_letter_min_score": 80})
        assert client.post("/jobs/letter-bands/write", json={"lo": 75, "hi": 79}).status_code == 200
        # (60,64) is no longer a band when the bar is 77: its bands are 72-76, 67-71, ...
        set_preferences(db, 1, {"auto_cover_letter_min_score": 77})
        assert client.post("/jobs/letter-bands/write", json={"lo": 60, "hi": 64}).status_code == 400

    def test_the_lowest_clamped_band_is_writable(self, client, db, fake_exec):
        set_preferences(db, 1, {"auto_cover_letter_min_score": 77})
        job, _ = seed(db, 1)
        res = client.post("/jobs/letter-bands/write", json={"lo": 0, "hi": 1})
        assert res.status_code == 200 and res.json()["queued"] == 1
        assert fake_exec.calls[0][1][0] == [job.id]

    def test_a_missing_body_field_is_422(self, client, db, fake_exec):
        assert client.post("/jobs/letter-bands/write", json={"lo": 60}).status_code == 422

    def test_a_failing_submit_releases_the_ids(self, client, db, monkeypatch):
        class Boom:
            def submit(self, *a, **k):
                raise RuntimeError("executor is shut down")

        monkeypatch.setattr(worker, "_bg_executor", Boom())
        seed(db, 62)
        # the app's own handler re-raises: TestClient is configured to propagate it
        with pytest.raises(RuntimeError):
            client.post("/jobs/letter-bands/write", json={"lo": 60, "hi": 64})
        assert letter_bands.queued == set()


# ---------------------------------------------------------------------------
# 10. worker._write_band_letters
# ---------------------------------------------------------------------------
@pytest.fixture()
def runner(Session, monkeypatch):
    """Patch the worker's session factory, run_work and the event broadcast. ``runner.calls``
    lists (work, profile_id, emit, db) per run_work call; ``runner.events`` the broadcasts;
    ``runner.results`` maps job_id -> Landed or an Exception to raise."""
    r = SimpleNamespace(calls=[], events=[], results={})

    def fake_run_work(work, profile_id, emit=None, *, db=None):
        r.calls.append((work, profile_id, emit, db))
        out = r.results.get(work.job_id)
        if isinstance(out, Exception):
            raise out
        return out or Landed("oneshot", work.job_id, "done")

    monkeypatch.setattr(worker, "SessionLocal", Session)
    monkeypatch.setattr(worker.letter_production, "run_work", fake_run_work)
    monkeypatch.setattr(events, "broadcast_from_thread", lambda ev, data: r.events.append((ev, data)))
    return r


def queue(*jobs):
    letter_bands.queued.update(j.id for j in jobs)
    return [j.id for j in jobs]


class TestWriteBandLetters:
    def test_kind_is_the_oneshot_below_the_bar_even_with_the_loop_on(self, db, runner):
        set_preferences(db, 1, {"letter_loop_enabled": True, "letter_loop_min_score": 75,
                                "auto_cover_letter_min_score": 75})
        job, _ = seed(db, 62)
        worker._write_band_letters(queue(job), 1)

        ((work, profile_id, emit, session),) = runner.calls
        assert work == Work("oneshot", job.id, below_bar=True)
        assert work.kind == production.ONESHOT and work.below_bar is True
        assert profile_id == 1
        assert emit is events.broadcast_from_thread
        assert session is not None  # run on the worker's own session
        assert letter_bands.queued == set()

    def test_every_job_is_run_best_first_in_the_given_order(self, db, runner):
        a, _ = seed(db, 63)
        b, _ = seed(db, 61)
        worker._write_band_letters(queue(a, b), 1)
        assert [c[0].job_id for c in runner.calls] == [a.id, b.id]
        assert letter_bands.queued == set()

    def test_a_job_that_gained_a_letter_since_the_click_is_skipped(self, db, runner):
        gone, gm = seed(db, 63)
        kept, _ = seed(db, 62)
        ids = queue(gone, kept)
        db.add(CoverLetter(match_id=gm.id, generated_content="x", status="draft"))
        db.commit()
        worker._write_band_letters(ids, 1)
        assert [c[0].job_id for c in runner.calls] == [kept.id]
        assert letter_bands.queued == set()

    def test_a_job_applied_meanwhile_is_skipped(self, db, runner):
        gone, gm = seed(db, 63)
        kept, _ = seed(db, 62)
        ids = queue(gone, kept)
        gm.applied_at = now_utc()
        db.commit()
        worker._write_band_letters(ids, 1)
        assert [c[0].job_id for c in runner.calls] == [kept.id]
        assert letter_bands.queued == set()

    def test_a_job_hidden_or_closed_meanwhile_is_skipped(self, db, runner):
        hidden, hm = seed(db, 63)
        closed, _ = seed(db, 62)
        kept, _ = seed(db, 61)
        ids = queue(hidden, closed, kept)
        hm.hidden_at = now_utc()
        closed.expired_detected_at = now_utc()
        db.commit()
        worker._write_band_letters(ids, 1)
        assert [c[0].job_id for c in runner.calls] == [kept.id]

    def test_a_sighting_gone_stale_meanwhile_is_skipped(self, db, runner):
        stale, _ = seed(db, 63)
        ids = queue(stale)
        stale.last_seen_live_at = now_utc() - timedelta(days=2)
        db.commit()
        worker._write_band_letters(ids, 1)
        assert runner.calls == []

    def test_a_job_whose_score_reached_the_bar_meanwhile_is_skipped(self, db, runner):
        job, m = seed(db, 63)
        ids = queue(job)
        m.score = 80
        db.commit()
        worker._write_band_letters(ids, 1)
        assert runner.calls == []

    @pytest.mark.parametrize("status", ["waiting_user", "running", "answered"])
    def test_a_job_with_a_live_letter_run_is_skipped(self, db, runner, status):
        job, m = seed(db, 63)
        ids = queue(job)
        db.add(LetterRun(match_id=m.id, engine="agent", status=status))
        db.commit()
        worker._write_band_letters(ids, 1)
        assert runner.calls == []
        assert letter_bands.queued == set()

    def test_a_finished_run_does_not_block(self, db, runner):
        job, m = seed(db, 63)
        ids = queue(job)
        db.add(LetterRun(match_id=m.id, engine="agent", status="failed"))
        db.commit()
        worker._write_band_letters(ids, 1)
        assert [c[0].job_id for c in runner.calls] == [job.id]

    def test_a_budget_stop_ends_the_batch_and_releases_everything(self, db, runner):
        a, _ = seed(db, 63)
        b, _ = seed(db, 62)
        c, _ = seed(db, 61)
        runner.results[a.id] = Landed("oneshot", a.id, "budget_stopped", account_limit="daily cap")
        worker._write_band_letters(queue(a, b, c), 1)

        assert [x[0].job_id for x in runner.calls] == [a.id]
        assert runner.events == [("llm_budget_blocked", {"reason": "daily cap"})]
        assert letter_bands.queued == set()

    def test_a_budget_exception_also_ends_the_batch(self, db, runner):
        from app.llm.client import BudgetExceededError

        a, _ = seed(db, 63)
        b, _ = seed(db, 62)
        runner.results[a.id] = BudgetExceededError("over the cap")
        worker._write_band_letters(queue(a, b), 1)
        assert [x[0].job_id for x in runner.calls] == [a.id]
        assert [e[0] for e in runner.events] == ["llm_budget_blocked"]
        assert letter_bands.queued == set()

    def test_a_generic_failure_does_not_stop_the_next_job(self, db, runner):
        a, _ = seed(db, 63)
        b, _ = seed(db, 62)
        runner.results[a.id] = RuntimeError("boom")
        ids = queue(a, b)
        worker._write_band_letters(ids, 1)
        assert [x[0].job_id for x in runner.calls] == [a.id, b.id]
        assert runner.events == []
        assert letter_bands.queued == set()

    def test_a_failed_landing_is_not_a_stop(self, db, runner):
        a, _ = seed(db, 63)
        b, _ = seed(db, 62)
        runner.results[a.id] = Landed("oneshot", a.id, "failed", reason="no letter written")
        worker._write_band_letters(queue(a, b), 1)
        assert [x[0].job_id for x in runner.calls] == [a.id, b.id]
        assert runner.events == []

    def test_ids_leave_the_queue_as_each_turn_ends(self, db, runner, monkeypatch):
        a, _ = seed(db, 63)
        b, _ = seed(db, 62)
        ids = queue(a, b)
        seen_during = []

        def spy(work, profile_id, emit=None, *, db=None):
            seen_during.append(set(letter_bands.queued))
            return Landed("oneshot", work.job_id, "done")

        monkeypatch.setattr(worker.letter_production, "run_work", spy)
        worker._write_band_letters(ids, 1)
        assert seen_during == [{a.id, b.id}, {b.id}]


# ---------------------------------------------------------------------------
# 11. production._run_oneshot: below_bar bypasses only the score gate
# ---------------------------------------------------------------------------
class TestRunOneshotBelowBar:
    @pytest.fixture()
    def fake_gen(self, monkeypatch):
        calls = []

        def fake(job_id, profile_id, **kwargs):
            calls.append((job_id, profile_id, kwargs))
            return SimpleNamespace(generated_content="Dear hiring manager")

        monkeypatch.setattr("app.llm.letter.production.generate_cover_letter", fake)
        return calls

    def test_below_bar_bypasses_the_threshold_but_does_not_force(self, db, fake_gen):
        landed = production._run_oneshot(db, 7, 1, None, below_bar=True)
        ((job_id, profile_id, kwargs),) = fake_gen
        assert (job_id, profile_id) == (7, 1)
        assert kwargs["bypass_threshold"] is True and kwargs["force"] is False
        assert kwargs["session"] is db
        assert landed.status == "done" and landed.letter == "Dear hiring manager"

    def test_without_below_bar_the_threshold_applies(self, db, fake_gen):
        production._run_oneshot(db, 7, 1, None, below_bar=False)
        ((_, _, kwargs),) = fake_gen
        assert kwargs["bypass_threshold"] is False and kwargs["force"] is False

    def test_default_is_not_below_bar(self, db, fake_gen):
        production._run_oneshot(db, 7, 1, None)
        assert fake_gen[0][2]["bypass_threshold"] is False

    def test_force_still_bypasses_and_forces(self, db, fake_gen):
        production._run_oneshot(db, 7, 1, None, force=True)
        kwargs = fake_gen[0][2]
        assert kwargs["bypass_threshold"] is True and kwargs["force"] is True

    def test_run_work_passes_below_bar_through(self, db, fake_gen):
        production.run_work(Work(production.ONESHOT, 7, below_bar=True), 1, db=db)
        assert fake_gen[0][2]["bypass_threshold"] is True and fake_gen[0][2]["force"] is False
        production.run_work(Work(production.ONESHOT, 8), 1, db=db)
        assert fake_gen[1][2]["bypass_threshold"] is False

    def test_a_none_letter_is_a_failed_landing(self, db, monkeypatch):
        monkeypatch.setattr("app.llm.letter.production.generate_cover_letter", lambda *a, **k: None)
        landed = production._run_oneshot(db, 7, 1, None, below_bar=True)
        assert landed.status == "failed"


# ---------------------------------------------------------------------------
# The "Write 3? ≈ X" estimate, from what letters really cost here
# ---------------------------------------------------------------------------
class TestEstimate:
    def _usage(self, db, task, *costs):
        from app.models import LlmUsage
        for c in costs:
            db.add(LlmUsage(task=task, tier="strong", model="stub", cost_usd=c))
        db.commit()

    def test_default_with_no_letter_logged(self, db):
        assert letter_bands.letter_cost_estimate(db) == (letter_bands.ESTIMATE_DEFAULT_USD, "default")

    def test_the_pipeline_draft_call_until_there_are_one_pass_letters(self, db):
        self._usage(db, "generate_letter", 0.10, 0.12, 0.14)
        self._usage(db, "cover_letter", 0.05, 0.05)  # fewer than ESTIMATE_MIN: not used yet
        assert letter_bands.letter_cost_estimate(db) == (pytest.approx(0.12), "draft")

    def test_one_pass_letters_once_there_are_enough(self, db):
        self._usage(db, "generate_letter", 0.10, 0.12, 0.14)
        self._usage(db, "cover_letter", 0.06, 0.08, 0.10)
        assert letter_bands.letter_cost_estimate(db) == (pytest.approx(0.08), "one_pass")

    def test_only_the_latest_calls_count(self, db):
        self._usage(db, "cover_letter", *([1.00] * 5 + [0.10] * letter_bands.ESTIMATE_SAMPLE))
        assert letter_bands.letter_cost_estimate(db)[0] == pytest.approx(0.10)

    def test_the_endpoint_carries_it(self, client, db):
        self._usage(db, "generate_letter", 0.12, 0.12, 0.12)
        data = bands_of(client)
        assert data["est_usd_per_letter"] == pytest.approx(0.12) and data["est_basis"] == "draft"


# ---------------------------------------------------------------------------
# GET /jobs: a job being written shows in Keep Applying (ready=true)
# ---------------------------------------------------------------------------
class TestKeepApplyingShowsWriting:
    def _ids(self, client):
        return {j["job_id"]: j for j in client.get("/jobs", params={"ready": "true"}).json()}

    def test_a_band_queued_job_is_listed_and_flagged(self, client, db):
        job, _ = seed(db, 62)
        assert job.id not in self._ids(client)
        letter_bands.queued.add(job.id)
        row = self._ids(client)[job.id]
        assert row["letter_queued"] is True and row["has_cover_letter"] is False

    def test_a_running_pipeline_job_is_listed(self, client, db):
        job, m = seed(db, 88)
        db.add(LetterRun(match_id=m.id, engine="agent", status="running"))
        db.commit()
        row = self._ids(client)[job.id]
        assert row["letter_queued"] is False and row["letter_run"]["status"] == "running"

    def test_a_job_waiting_on_a_question_is_not_listed(self, client, db):
        job, m = seed(db, 88)
        db.add(LetterRun(match_id=m.id, engine="agent", status="waiting_user"))
        db.commit()
        assert job.id not in self._ids(client)

    def test_an_applied_job_is_not_listed_even_if_queued(self, client, db):
        job, m = seed(db, 62)
        m.applied_at = now_utc()
        db.commit()
        letter_bands.queued.add(job.id)
        assert job.id not in self._ids(client)

    def test_letter_queued_is_false_elsewhere(self, client, db):
        job, _ = seed(db, 62)
        assert client.get("/jobs").json()[0]["letter_queued"] is False
