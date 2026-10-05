"""Tests for Phase 8's HTTP and idle-loop surface in app/api/main.py and app/api/letters.py:
GET /jobs ``letter_run``, GET /jobs/{id}/letter-info, the three new preferences (and
``preferences.letter_settings``), POST /jobs/{id}/regenerate queued on the single worker,
``_process_listing``'s hand-off to production.generate_for, the idle loop's
``_letters_phase`` and start-up ``_recover_orphaned_runs``.

No network and no LLM: everything that would call a model is monkeypatched on
``app.api.main``. In-memory SQLite only (both ``get_db`` dependencies are overridden and
``SessionLocal`` is replaced), never real.db / app.db.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.api.main as main
import app.api.profile_ui as profile_ui
from app.db import Base
from app.llm.letter import production
from app.llm.letter.production import Landed, Work
from app.llm.letter.state import JobInfo, LetterState, UserQuestion
from app.models import JobListing, LetterRun, Match, Profile
from app.preferences import DEFAULTS, letter_settings, set_preferences


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
def client(Session):
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
    s.flush()
    s.add_all([
        JobListing(id=5, source="seek", source_job_id="5", url="u5", title="Engineer", company="LogiCo",
                   raw_description="We build software."),
        JobListing(id=6, source="seek", source_job_id="6", url="u6", title="Analyst", company="DataCo",
                   raw_description="We analyse data."),
    ])
    s.flush()
    s.add_all([Match(id=9, user_id=1, job_id=5, score=90), Match(id=10, user_id=1, job_id=6, score=80)])
    s.commit()
    yield s
    s.close()


def _q(qid, status="open"):
    return UserQuestion(id=qid, requirement_id="R1", requirement_text="Power BI", skill_key="power bi",
                        prompt="?", status=status)


def add_run(db, match_id, status, questions=()) -> int:
    state = LetterState(profile_id=1, job=JobInfo(job_id=5, title="Engineer"), user_questions=list(questions))
    run = LetterRun(match_id=match_id, engine="agent", status=status, state=state.model_dump_json())
    db.add(run)
    db.commit()
    return run.id


def _by_job(rows):
    return {r["job_id"]: r for r in rows}


# ---------------------------------------------------------------------------
# GET /jobs: letter_run
# ---------------------------------------------------------------------------
class TestJobsLetterRun:
    def test_letter_run_is_null_when_no_run_exists(self, client, db):
        rows = _by_job(client.get("/jobs").json())
        assert rows[5]["letter_run"] is None and rows[6]["letter_run"] is None

    def test_a_waiting_run_reports_its_open_questions(self, client, db):
        run_id = add_run(db, 9, "waiting_user", [_q("Q1"), _q("Q2"), _q("Q3", "answered")])
        rows = _by_job(client.get("/jobs").json())
        assert rows[5]["letter_run"] == {"run_id": run_id, "status": "waiting_user", "open_questions": 2}
        assert rows[6]["letter_run"] is None

    def test_the_newest_run_per_match_is_used(self, client, db):
        add_run(db, 9, "failed")
        newest = add_run(db, 9, "running")
        other = add_run(db, 10, "done")
        rows = _by_job(client.get("/jobs").json())
        assert rows[5]["letter_run"] == {"run_id": newest, "status": "running", "open_questions": 0}
        assert rows[6]["letter_run"]["run_id"] == other

    def test_eligibility_notes_are_still_present(self, client, db):
        db.get(JobListing, 5).requirements_checklist = json.dumps({"requirements": [
            {"text": "Australian work rights", "letter_role": "not_for_letter"},
            {"text": "SQL", "letter_role": "headline"},
        ]})
        db.commit()
        rows = _by_job(client.get("/jobs").json())
        assert rows[5]["eligibility_notes"] == ["Australian work rights"]
        assert rows[6]["eligibility_notes"] == []


# ---------------------------------------------------------------------------
# GET /jobs/{job_id}/letter-info
# ---------------------------------------------------------------------------
class TestLetterInfoEndpoint:
    def test_unknown_job_is_404(self, client, db):
        assert client.get("/jobs/404/letter-info").status_code == 404

    def test_a_job_without_a_match_is_404(self, client, db):
        db.add(JobListing(id=7, source="seek", source_job_id="7", url="u7", title="T", company="C",
                          raw_description="d"))
        db.commit()
        assert client.get("/jobs/7/letter-info").status_code == 404

    def test_a_match_with_no_runs_returns_the_empty_shape(self, client, db):
        res = client.get("/jobs/5/letter-info")
        assert res.status_code == 200
        assert res.json() == {"job_id": 5, "run": None, "waiting": None, "failure": None}

    def test_a_waiting_run_shows_up_under_waiting(self, client, db):
        run_id = add_run(db, 9, "waiting_user", [_q("Q1")])
        body = client.get("/jobs/5/letter-info").json()
        assert body["waiting"] == {"run_id": run_id, "status": "waiting_user", "open_questions": 1}
        assert set(body) == {"job_id", "run", "waiting", "failure"}

    def test_a_failed_run_shows_up_under_failure(self, client, db):
        run_id = add_run(db, 9, "failed")
        body = client.get("/jobs/5/letter-info").json()
        assert body["failure"]["run_id"] == run_id and body["failure"]["status"] == "failed"
        assert set(body) == {"job_id", "run", "waiting", "failure"}


# ---------------------------------------------------------------------------
# Preferences: the three new keys
# ---------------------------------------------------------------------------
class TestLetterPreferences:
    def test_defaults(self, client, db):
        prefs = client.get("/profile/1/preferences").json()
        assert prefs["letter_loop_enabled"] is True
        assert prefs["letter_loop_min_score"] == 85
        assert prefs["letter_engine"] == "agent"

    def test_valid_values_are_accepted_and_persisted(self, client, db):
        res = client.put("/profile/1/preferences", json={
            "letter_loop_enabled": False, "letter_loop_min_score": 92, "letter_engine": "workflow",
        })
        assert res.status_code == 200
        expected = {"letter_loop_enabled": False, "letter_loop_min_score": 92, "letter_engine": "workflow"}
        assert {k: res.json()[k] for k in expected} == expected
        again = client.get("/profile/1/preferences").json()
        assert {k: again[k] for k in expected} == expected

    @pytest.mark.parametrize("bad", [
        {"letter_loop_min_score": -1},
        {"letter_loop_min_score": 101},
        {"letter_engine": "bogus"},
        {"letter_loop_enabled": "maybe"},
    ])
    def test_invalid_values_are_rejected(self, client, db, bad):
        assert client.put("/profile/1/preferences", json=bad).status_code == 422
        assert client.get("/profile/1/preferences").json()["letter_engine"] == "agent"  # nothing was stored

    @pytest.mark.parametrize("edge", [0, 100])
    def test_the_score_bounds_themselves_are_valid(self, client, db, edge):
        res = client.put("/profile/1/preferences", json={"letter_loop_min_score": edge})
        assert res.status_code == 200 and res.json()["letter_loop_min_score"] == edge

    def test_a_partial_update_keeps_the_other_keys(self, client, db):
        client.put("/profile/1/preferences", json={"letter_loop_min_score": 90, "letter_engine": "workflow"})
        res = client.put("/profile/1/preferences", json={"letter_loop_enabled": False})
        body = res.json()
        assert body["letter_loop_enabled"] is False
        assert body["letter_loop_min_score"] == 90 and body["letter_engine"] == "workflow"
        assert body["auto_cover_letter_min_score"] == 75


class TestLetterSettings:
    def test_defaults(self, db):
        assert letter_settings(db, 1) == {
            "auto_min_score": 75, "enabled": True, "loop_min_score": 85, "pipeline_min_score": 85,
            "engine": "agent",
            "side_outputs": ("answer_screening", "suggest_learning", "suggest_resume_tweaks"),
            "limits": {"max_drafts": 3, "max_tool_calls": 15, "max_cost_usd": 0.5},
        }

    def test_the_effective_pipeline_bar_is_the_higher_of_the_two(self, db):
        set_preferences(db, 1, {"auto_cover_letter_min_score": 90, "letter_loop_min_score": 80})
        assert letter_settings(db, 1)["pipeline_min_score"] == 90
        set_preferences(db, 1, {"auto_cover_letter_min_score": 60, "letter_loop_min_score": 80})
        assert letter_settings(db, 1)["pipeline_min_score"] == 80

    @pytest.mark.parametrize("key, bad, field, default", [
        ("letter_loop_min_score", True, "loop_min_score", 85),  # a bool is not an int here
        ("letter_loop_min_score", 150, "loop_min_score", 85),
        ("letter_loop_min_score", -5, "loop_min_score", 85),
        ("letter_loop_min_score", "high", "loop_min_score", 85),
        ("letter_loop_min_score", 80.5, "loop_min_score", 85),
        ("auto_cover_letter_min_score", True, "auto_min_score", 75),
        ("auto_cover_letter_min_score", 101, "auto_min_score", 75),
        ("letter_engine", "bogus", "engine", "agent"),
        ("letter_engine", 3, "engine", "agent"),
        ("letter_loop_enabled", "no", "enabled", True),
        ("letter_loop_enabled", 0, "enabled", True),
    ])
    def test_invalid_stored_values_fall_back_to_the_default(self, db, key, bad, field, default):
        set_preferences(db, 1, {key: bad})
        assert letter_settings(db, 1)[field] == default

    def test_valid_stored_values_are_used(self, db):
        set_preferences(db, 1, {"letter_loop_enabled": False, "letter_loop_min_score": 0,
                                "letter_engine": "workflow"})
        s = letter_settings(db, 1)
        assert s["enabled"] is False and s["loop_min_score"] == 0 and s["engine"] == "workflow"

    def test_the_defaults_table_carries_the_new_keys(self):
        assert DEFAULTS["letter_loop_enabled"] is True
        assert DEFAULTS["letter_loop_min_score"] == 85
        assert DEFAULTS["letter_engine"] == "agent"


# ---------------------------------------------------------------------------
# POST /jobs/{id}/regenerate
# ---------------------------------------------------------------------------
class FakeExecutor:
    def __init__(self):
        self.calls: list[tuple] = []

    def submit(self, fn, *args, **kwargs):
        self.calls.append((fn, args, kwargs))


class TestRegenerate:
    def test_unknown_job_is_404_and_queues_nothing(self, client, db, monkeypatch):
        fake = FakeExecutor()
        monkeypatch.setattr(main, "_bg_executor", fake)
        assert client.post("/jobs/404/regenerate").status_code == 404
        assert fake.calls == []

    def test_it_queues_the_work_on_the_single_worker(self, client, db, monkeypatch):
        fake = FakeExecutor()
        monkeypatch.setattr(main, "_bg_executor", fake)

        res = client.post("/jobs/5/regenerate")

        assert res.status_code == 200 and res.json() == {"status": "queued"}
        ((fn, args, kwargs),) = fake.calls
        assert fn is main._process_listing
        assert args[0] == 5 and args[1] == 1
        assert kwargs == {"with_cover_letter": True, "bypass_threshold": True, "force": True}


# ---------------------------------------------------------------------------
# _process_listing -> production.generate_for
# ---------------------------------------------------------------------------
class TestProcessListing:
    @pytest.fixture()
    def wired(self, Session, monkeypatch):
        calls = {"generate_for": [], "events": []}
        monkeypatch.setattr(main, "extract_job", lambda job_id, force=False: None)
        monkeypatch.setattr(main, "match_job", lambda job_id, profile_id, force=False: None)
        monkeypatch.setattr(main, "SessionLocal", Session)
        monkeypatch.setattr(main, "broadcast_from_thread", lambda event_name, data: calls["events"].append((event_name, data)))
        calls["result"] = None
        calls["raises"] = None

        def fake_generate_for(db, job_id, profile_id, emit=None, *, bypass_threshold=True):
            calls["generate_for"].append((db, job_id, profile_id, emit, bypass_threshold))
            if calls["raises"]:
                raise calls["raises"]
            return calls["result"]

        monkeypatch.setattr(main.letter_production, "generate_for", fake_generate_for)
        return calls

    def test_the_letter_is_handed_to_generate_for_with_the_broadcaster(self, wired):
        main._process_listing(5, 1, True, with_cover_letter=True, bypass_threshold=True, force=True)

        ((db, job_id, profile_id, emit, bypass),) = wired["generate_for"]
        assert (job_id, profile_id, bypass) == (5, 1, True)
        assert emit is main.broadcast_from_thread
        assert db is not None
        assert not any(name == "llm_budget_blocked" for name, _ in wired["events"])

    def test_bypass_threshold_false_is_passed_through(self, wired):
        main._process_listing(5, 1, True, with_cover_letter=True, bypass_threshold=False)
        assert wired["generate_for"][0][4] is False

    def test_an_account_limit_is_broadcast_as_a_budget_block(self, wired):
        wired["result"] = Landed("pipeline", 5, "cancelled", account_limit="daily cap hit")
        main._process_listing(5, 1, True, with_cover_letter=True, bypass_threshold=True, force=True)
        assert ("llm_budget_blocked", {"reason": "daily cap hit"}) in wired["events"]

    def test_a_failure_in_generate_for_is_swallowed(self, wired):
        wired["raises"] = RuntimeError("letter blew up")
        main._process_listing(5, 1, True, with_cover_letter=True, bypass_threshold=True, force=True)  # no raise
        assert len(wired["generate_for"]) == 1

    def test_no_letter_is_written_unless_asked_for(self, wired):
        main._process_listing(5, 1, True)
        assert wired["generate_for"] == []

    def test_a_card_without_a_description_is_deferred(self, wired):
        main._process_listing(5, 1, False, with_cover_letter=True, bypass_threshold=True)
        assert wired["generate_for"] == []


# ---------------------------------------------------------------------------
# _letters_phase: one pass of the idle loop's cover-letter phase
# ---------------------------------------------------------------------------
class TestLettersPhase:
    @pytest.fixture()
    def phase(self, Session, monkeypatch):
        executor = ThreadPoolExecutor(max_workers=1)
        rec = {"next_work": [], "run_work": [], "events": [], "work": None, "landed": None}

        def fake_next_work(db, profile_id, **kw):
            rec["next_work"].append(profile_id)
            return rec["work"]

        def fake_run_work(work, profile_id, emit=None, **kw):
            rec["run_work"].append((work, profile_id, emit))
            return rec["landed"]

        async def fake_broadcast(event_name, data):
            rec["events"].append((event_name, data))

        monkeypatch.setattr(main, "SessionLocal", Session)
        monkeypatch.setattr(main.letter_production, "next_work", fake_next_work)
        monkeypatch.setattr(main.letter_production, "run_work", fake_run_work)
        monkeypatch.setattr(main, "_bg_executor", executor)
        monkeypatch.setattr(main, "_IDLE_INTERVAL_S", 0)
        monkeypatch.setattr(main, "_broadcast", fake_broadcast)
        monkeypatch.setattr(main, "_letters_paused_until", 0.0)
        yield rec
        executor.shutdown(wait=True)
        main._letters_paused_until = 0.0  # never leak a pause into other tests

    def test_no_work_returns_false_without_running_anything(self, phase):
        assert asyncio.run(main._letters_phase()) is False
        assert phase["next_work"] == [1] and phase["run_work"] == []

    def test_work_is_run_on_the_worker_with_the_broadcaster(self, phase):
        phase["work"] = Work("pipeline", 5)
        phase["landed"] = Landed("pipeline", 5, "done", letter="x")

        assert asyncio.run(main._letters_phase()) is True

        ((work, profile_id, emit),) = phase["run_work"]
        assert work == Work("pipeline", 5) and profile_id == 1
        assert emit is main.broadcast_from_thread
        assert phase["events"] == []
        assert main._letters_paused_until == 0.0

    def test_an_account_limit_pauses_letters_and_tells_the_sidebar(self, phase):
        phase["work"] = Work("pipeline", 5)
        phase["landed"] = Landed("pipeline", 5, "cancelled", account_limit="daily cap hit")
        before = time.monotonic()

        assert asyncio.run(main._letters_phase()) is True

        assert main._letters_paused_until > before + 60  # paused well into the future
        assert phase["events"] == [("llm_budget_blocked", {"reason": "daily cap hit"})]

        # the next pass does no letter work at all while paused
        assert asyncio.run(main._letters_phase()) is False
        assert phase["next_work"] == [1]  # still just the first call
        assert len(phase["run_work"]) == 1

    def test_a_run_waiting_on_the_user_does_not_pause_letters(self, phase):
        phase["work"] = Work("pipeline", 5)
        phase["landed"] = Landed("pipeline", 5, "waiting_user", questions=2)

        assert asyncio.run(main._letters_phase()) is True

        assert main._letters_paused_until == 0.0
        assert phase["events"] == []
        phase["work"] = None
        assert asyncio.run(main._letters_phase()) is False  # next_work is consulted again
        assert phase["next_work"] == [1, 1]


# ---------------------------------------------------------------------------
# _recover_orphaned_runs: start-up clean-up
# ---------------------------------------------------------------------------
class TestRecoverOrphanedRuns:
    def test_running_runs_are_marked_failed(self, db, Session, monkeypatch):
        monkeypatch.setattr(main, "SessionLocal", Session)
        running = add_run(db, 9, "running")
        waiting = add_run(db, 10, "waiting_user")

        assert main._recover_orphaned_runs() is None

        db.expire_all()
        assert db.get(LetterRun, running).status == "failed"
        assert db.get(LetterRun, running).finished_at is not None
        assert db.get(LetterRun, waiting).status == "waiting_user"

    def test_it_never_raises(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("db is down")

        monkeypatch.setattr(main, "SessionLocal", boom)
        assert main._recover_orphaned_runs() is None

    def test_it_never_raises_when_recovery_itself_fails(self, Session, monkeypatch):
        monkeypatch.setattr(main, "SessionLocal", Session)

        def boom(db):
            raise RuntimeError("recovery bug")

        monkeypatch.setattr(production, "recover_orphaned_runs", boom)
        assert main._recover_orphaned_runs() is None
