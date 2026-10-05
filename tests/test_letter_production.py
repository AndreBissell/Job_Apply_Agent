"""Tests for Phase 8's production wiring of the cover-letter pipeline
(app/llm/letter/production.py): what the worker does next (next_work), how a finished
run lands in cover_letters (land_letter / run_work), the explicit regenerate
(generate_for), start-up recovery, and one end-to-end smoke through the real workflow
engine with scripted tools.

No LLM and no network: the engines (run_letter / resume_letter) and the one-shot writer
(generate_cover_letter) are replaced by fakes on the production module, and the smoke
test reuses the scripted tool fakes from tests/test_letter_workflow.py. In-memory SQLite
only. Nothing here depends on wall-clock time except through the explicit ``now=``.
"""
from __future__ import annotations

import datetime
import sqlite3
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.llm import cover_letter
from app.llm.client import BudgetExceededError, DailyQuotaError
from app.llm.letter import production
from app.llm.letter.outcome import LetterResult
from app.llm.letter.production import Landed, Work
from app.llm.letter.runner import start_run
from app.llm.letter.state import JobInfo, LetterState, UserQuestion
from app.models import CoverLetter, JobListing, LetterRun, Match, Profile
from app.preferences import letter_settings, set_preferences
from tests.test_letter_workflow import Script

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
EXTRACTED = datetime.datetime(2026, 10, 3, 9, 0, tzinfo=UTC)


@pytest.fixture()
def db():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(eng, "connect")
    def _fk_on(conn, _):
        if isinstance(conn, sqlite3.Connection):
            conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(eng)
    with sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)() as session:
        session.add(Profile(id=1, name="Bob", email="b@x.com", password_hash="x", summary="Grad developer."))
        session.commit()
        yield session
    Base.metadata.drop_all(eng)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def add_match(db, job_id, score, *, extracted=True, hidden=False) -> int:
    """A job plus the profile's match for it. The match id equals the job id."""
    db.add(JobListing(
        id=job_id, source="seek", source_job_id=str(job_id), url=f"u{job_id}", title=f"Job {job_id}",
        company="LogiCo", raw_description="We build software.", extracted_at=EXTRACTED if extracted else None,
    ))
    db.flush()
    db.add(Match(id=job_id, user_id=1, job_id=job_id, score=score, hidden_at=NOW if hidden else None))
    db.commit()
    return job_id


def _state(job_id=1, questions=()) -> LetterState:
    return LetterState(profile_id=1, job=JobInfo(job_id=job_id, title="Engineer"), user_questions=list(questions))


def add_run(db, match_id, status, *, final_version=None, finished_ago=None, engine="agent") -> int:
    run = LetterRun(
        match_id=match_id, engine=engine, status=status, state=_state(match_id).model_dump_json(),
        final_draft_version=final_version,
        finished_at=NOW - finished_ago if finished_ago is not None else None,
    )
    db.add(run)
    db.commit()
    return run.id


def _minutes(n):
    return datetime.timedelta(minutes=n)


def _hours(n):
    return datetime.timedelta(hours=n)


def _question(qid, status="open"):
    return UserQuestion(id=qid, requirement_id="R1", requirement_text="Power BI", skill_key="power bi",
                        prompt="?", status=status)


def _run_row(db, run_id) -> LetterRun:
    db.expire_all()
    return db.get(LetterRun, run_id)


def _letters(db) -> list[CoverLetter]:
    db.expire_all()
    return list(db.scalars(select(CoverLetter)))


class Events:
    """An ``emit`` callback that records (event, data) pairs."""

    def __init__(self):
        self.items: list[tuple[str, dict]] = []

    def __call__(self, event_name, data):
        self.items.append((event_name, data))

    @property
    def names(self):
        return [n for n, _ in self.items]

    def data(self, name):
        (found,) = [d for n, d in self.items if n == name]
        return found


def make_result(db, job_id, *, status="done", draft_text="Dear team, hire me.", clean=True, open_issues=(),
                stop_reason=None, account_limit=None, questions=(), run_id=None) -> LetterResult:
    """What a real engine hands back, including the LetterRun row it would have written."""
    state = _state(job_id, questions)
    draft = state.add_draft(draft_text) if draft_text is not None else None
    if run_id is None:
        match = db.scalar(select(Match).where(Match.user_id == 1, Match.job_id == job_id))
        run = start_run(db, match.id, "agent", state).run
    else:
        run = db.get(LetterRun, run_id)
    run.status = status
    run.final_draft_version = draft.version if draft else None
    if status != "waiting_user":
        run.finished_at = NOW
    db.commit()
    return LetterResult(
        run_id=run.id, status=status, state=state, draft=draft, clean=clean, open_issues=list(open_issues),
        stop_reason=stop_reason, account_limit=account_limit,
    )


class FakeEngines:
    """Replaces production.run_letter / resume_letter and records how they were called."""

    def __init__(self, monkeypatch, db, **result_kw):
        self.db, self.result_kw = db, result_kw
        self.run_calls: list[dict] = []
        self.resume_calls: list[int] = []
        self.raises: Exception | None = None
        self.seen_statuses: dict[int, str] = {}  # run id -> status at the moment the engine started
        self.watch: list[int] = []
        monkeypatch.setattr(production, "run_letter", self._run)
        monkeypatch.setattr(production, "resume_letter", self._resume)

    def _run(self, db, job_id, profile_id, *, engine, side_outputs=(), limits=None):
        self.run_calls.append({"job_id": job_id, "profile_id": profile_id, "engine": engine})
        self.side_outputs = side_outputs
        self.limits = limits
        for run_id in self.watch:
            db.expire_all()
            self.seen_statuses[run_id] = db.get(LetterRun, run_id).status
        if self.raises:
            raise self.raises
        return make_result(db, job_id, **self.result_kw)

    def _resume(self, db, run_id):
        self.resume_calls.append(run_id)
        if self.raises:
            raise self.raises
        job_id = db.scalar(select(Match.job_id).join(LetterRun, LetterRun.match_id == Match.id).where(LetterRun.id == run_id))
        return make_result(db, job_id, run_id=run_id, **self.result_kw)


class FakeOneShot:
    """Replaces production.generate_cover_letter."""

    def __init__(self, monkeypatch, text="One-shot letter."):
        self.text, self.calls, self.raises, self.returns_none = text, [], None, False
        monkeypatch.setattr(production, "generate_cover_letter", self)

    def __call__(self, job_id, profile_id, **kwargs):
        self.calls.append({"job_id": job_id, "profile_id": profile_id, **kwargs})
        if self.raises:
            raise self.raises
        return None if self.returns_none else SimpleNamespace(generated_content=self.text)


# ---------------------------------------------------------------------------
# next_work: what there is to do
# ---------------------------------------------------------------------------
def test_no_matches_means_no_work(db):
    assert production.next_work(db, 1, now=NOW) is None


def test_a_score_at_or_above_the_pipeline_bar_gets_the_pipeline(db):
    add_match(db, 1, 90)
    assert production.next_work(db, 1, now=NOW) == Work("pipeline", 1)


def test_a_score_between_the_two_bars_gets_the_one_shot(db):
    add_match(db, 1, 80)
    assert production.next_work(db, 1, now=NOW) == Work("oneshot", 1)


def test_a_score_below_the_auto_letter_minimum_gets_nothing(db):
    add_match(db, 1, 70)
    assert production.next_work(db, 1, now=NOW) is None


def test_the_pipeline_bar_itself_counts_as_pipeline(db):
    add_match(db, 1, 85)
    assert production.next_work(db, 1, now=NOW).kind == "pipeline"


def test_hidden_matches_are_skipped(db):
    add_match(db, 1, 95, hidden=True)
    assert production.next_work(db, 1, now=NOW) is None


def test_a_job_that_has_not_been_extracted_is_skipped(db):
    add_match(db, 1, 95, extracted=False)
    assert production.next_work(db, 1, now=NOW) is None


def test_a_match_that_already_has_a_letter_is_skipped(db):
    add_match(db, 1, 95)
    db.add(CoverLetter(match_id=1, generated_content="done already", status="draft"))
    db.commit()
    assert production.next_work(db, 1, now=NOW) is None


def test_the_best_scored_eligible_match_goes_first(db):
    add_match(db, 1, 78)
    add_match(db, 2, 93)
    add_match(db, 3, 88)
    add_match(db, 4, 99, hidden=True)
    add_match(db, 5, 97, extracted=False)
    assert production.next_work(db, 1, now=NOW) == Work("pipeline", 2)


def test_another_profiles_matches_are_not_work(db):
    db.add(Profile(id=2, name="Eve", email="e@x.com", password_hash="x"))
    add_match(db, 1, 95)
    db.get(Match, 1).user_id = 2
    db.commit()
    assert production.next_work(db, 1, now=NOW) is None


# --- preferences --------------------------------------------------------------
def test_with_the_master_switch_off_every_letter_is_a_one_shot(db):
    add_match(db, 1, 90)
    set_preferences(db, 1, {"letter_loop_enabled": False})
    assert production.next_work(db, 1, now=NOW) == Work("oneshot", 1)


def test_a_lower_loop_bar_than_the_auto_minimum_changes_nothing(db):
    add_match(db, 1, 80)
    set_preferences(db, 1, {"letter_loop_min_score": 70})
    assert letter_settings(db, 1)["pipeline_min_score"] == 75
    assert production.next_work(db, 1, now=NOW) == Work("pipeline", 1)


def test_a_higher_loop_bar_sends_the_middle_band_to_the_one_shot(db):
    set_preferences(db, 1, {"letter_loop_min_score": 95})
    add_match(db, 1, 90)
    assert production.next_work(db, 1, now=NOW) == Work("oneshot", 1)
    add_match(db, 2, 96)
    assert production.next_work(db, 1, now=NOW) == Work("pipeline", 2)


def test_garbage_stored_preferences_fall_back_to_the_defaults(db):
    set_preferences(db, 1, {"letter_loop_min_score": "x", "letter_engine": "bogus"})
    settings = letter_settings(db, 1)
    assert settings["loop_min_score"] == 85 and settings["engine"] == "agent"
    add_match(db, 1, 90)
    add_match(db, 2, 80)
    assert production.next_work(db, 1, now=NOW) == Work("pipeline", 1)
    db.get(Match, 1).hidden_at = NOW
    db.commit()
    assert production.next_work(db, 1, now=NOW) == Work("oneshot", 2)


# --- resuming ----------------------------------------------------------------
def test_an_answered_run_wins_over_a_fresh_candidate(db):
    add_match(db, 1, 99)
    add_match(db, 2, 80)
    run_id = add_run(db, 2, "answered")
    assert production.next_work(db, 1, now=NOW) == Work("resume", 2, run_id=run_id)


def test_the_oldest_answered_run_resumes_first(db):
    add_match(db, 1, 80)
    add_match(db, 2, 90)
    first = add_run(db, 1, "answered")
    add_run(db, 2, "answered")
    assert production.next_work(db, 1, now=NOW) == Work("resume", 1, run_id=first)


def test_an_answered_run_on_a_hidden_match_is_ignored(db):
    add_match(db, 1, 90, hidden=True)
    add_run(db, 1, "answered")
    assert production.next_work(db, 1, now=NOW) is None
    add_match(db, 2, 80)
    assert production.next_work(db, 1, now=NOW) == Work("oneshot", 2)


# --- live runs never block the loop -------------------------------------------
@pytest.mark.parametrize("status", ["waiting_user", "running"])
def test_a_match_with_a_live_run_is_skipped_for_the_next_best(db, status):
    add_match(db, 1, 95)
    add_match(db, 2, 80)
    add_run(db, 1, status)
    assert production.next_work(db, 1, now=NOW) == Work("oneshot", 2)


@pytest.mark.parametrize("status", ["waiting_user", "running"])
def test_a_live_run_on_the_only_candidate_means_no_work(db, status):
    add_match(db, 1, 95)
    add_run(db, 1, status)
    assert production.next_work(db, 1, now=NOW) is None


# --- failure rules ------------------------------------------------------------
def test_a_recent_failure_blocks_the_match(db):
    add_match(db, 1, 90)
    add_run(db, 1, "failed", finished_ago=_minutes(5))
    assert production.next_work(db, 1, now=NOW) is None


def test_a_failure_older_than_the_cooldown_is_retried(db):
    add_match(db, 1, 90)
    add_run(db, 1, "failed", finished_ago=_minutes(31))
    assert production.next_work(db, 1, now=NOW) == Work("pipeline", 1)


def test_two_failures_block_the_match_for_good(db):
    add_match(db, 1, 90)
    add_run(db, 1, "failed", finished_ago=_hours(10))
    add_run(db, 1, "failed", finished_ago=_hours(5))
    assert production.next_work(db, 1, now=NOW) is None
    assert production.next_work(db, 1, now=NOW + _hours(500)) is None


def test_a_stop_with_no_draft_counts_as_a_failed_attempt(db):
    add_match(db, 1, 90)
    add_run(db, 1, "budget_stopped", final_version=None, finished_ago=_minutes(5))
    assert production.next_work(db, 1, now=NOW) is None
    assert production.next_work(db, 1, now=NOW + _minutes(60)) == Work("pipeline", 1)


def test_a_stop_with_a_draft_does_not_count_as_a_failure(db):
    add_match(db, 1, 90)
    add_run(db, 1, "budget_stopped", final_version=2, finished_ago=_minutes(5))
    assert production.next_work(db, 1, now=NOW) == Work("pipeline", 1)


@pytest.mark.parametrize("status", ["cancelled", "done"])
def test_cancelled_and_done_runs_never_block(db, status):
    add_match(db, 1, 90)
    add_run(db, 1, status, final_version=1, finished_ago=_minutes(1))
    add_run(db, 1, status, final_version=1, finished_ago=_minutes(1))
    assert production.next_work(db, 1, now=NOW) == Work("pipeline", 1)


# ---------------------------------------------------------------------------
# recover_orphaned_runs
# ---------------------------------------------------------------------------
def test_recover_marks_running_runs_failed_and_returns_the_count(db):
    add_match(db, 1, 90)
    add_match(db, 2, 90)
    a = add_run(db, 1, "running")
    b = add_run(db, 2, "running")
    assert production.recover_orphaned_runs(db) == 2
    for run_id in (a, b):
        row = _run_row(db, run_id)
        assert row.status == "failed" and row.finished_at is not None


def test_recover_leaves_every_other_status_alone(db):
    add_match(db, 1, 90)
    ids = {s: add_run(db, 1, s) for s in ("waiting_user", "answered", "done", "failed", "cancelled")}
    assert production.recover_orphaned_runs(db) == 0
    for status, run_id in ids.items():
        assert _run_row(db, run_id).status == status


def test_recover_returns_zero_when_nothing_is_running(db):
    assert production.recover_orphaned_runs(db) == 0


# ---------------------------------------------------------------------------
# land_letter
# ---------------------------------------------------------------------------
def test_land_letter_creates_a_draft_row(db):
    add_match(db, 1, 90)
    cl = production.land_letter(db, 1, "Dear team")
    assert cl.status == "draft" and cl.generated_content == "Dear team" and cl.edited_content is None
    assert len(_letters(db)) == 1


def test_land_letter_on_an_unedited_row_replaces_the_text_and_resets_to_draft(db):
    add_match(db, 1, 90)
    db.add(CoverLetter(match_id=1, generated_content="old", status="final"))
    db.commit()
    production.land_letter(db, 1, "new")
    (row,) = _letters(db)
    assert row.generated_content == "new" and row.status == "draft"


def test_land_letter_never_touches_edited_content_and_keeps_the_edited_status(db):
    add_match(db, 1, 90)
    edited = "  My own words, exactly.\n\nWith a trailing space \n"
    db.add(CoverLetter(match_id=1, generated_content="old", edited_content=edited, status="edited"))
    db.commit()
    production.land_letter(db, 1, "new generated text")
    (row,) = _letters(db)
    assert row.edited_content == edited
    assert row.generated_content == "new generated text"
    assert row.status == "edited"


def test_land_letter_never_makes_a_second_row_for_a_match(db):
    add_match(db, 1, 90)
    production.land_letter(db, 1, "one")
    production.land_letter(db, 1, "two")
    production.land_letter(db, 1, "three")
    (row,) = _letters(db)
    assert row.generated_content == "three"


# ---------------------------------------------------------------------------
# supersede_open_runs
# ---------------------------------------------------------------------------
def test_supersede_cancels_waiting_and_answered_runs(db):
    add_match(db, 1, 90)
    waiting = add_run(db, 1, "waiting_user")
    answered = add_run(db, 1, "answered")
    assert production.supersede_open_runs(db, 1) == 2
    for run_id in (waiting, answered):
        row = _run_row(db, run_id)
        assert row.status == "cancelled" and row.finished_at is not None


def test_supersede_leaves_finished_runs_and_other_matches_alone(db):
    add_match(db, 1, 90)
    add_match(db, 2, 90)
    done = add_run(db, 1, "done", final_version=1)
    failed = add_run(db, 1, "failed", finished_ago=_hours(1))
    other = add_run(db, 2, "waiting_user")
    assert production.supersede_open_runs(db, 1) == 0
    assert _run_row(db, done).status == "done"
    assert _run_row(db, failed).status == "failed"
    assert _run_row(db, other).status == "waiting_user"


# ---------------------------------------------------------------------------
# run_work: the pipeline
# ---------------------------------------------------------------------------
def test_a_clean_pipeline_run_lands_the_letter_and_tells_the_sidebar(db, monkeypatch):
    add_match(db, 1, 90)
    FakeEngines(monkeypatch, db, draft_text="Dear LogiCo, hello.")
    events = Events()

    landed = production.run_work(Work("pipeline", 1), 1, events, db=db)

    (row,) = _letters(db)
    assert row.generated_content == "Dear LogiCo, hello." and row.status == "draft"
    assert events.names == ["letter_run_started", "cover_letter_ready", "letter_run_done"]
    started = events.data("letter_run_started")
    assert started["job_id"] == 1 and started["engine"] == "agent" and started["resumed"] is False
    assert events.data("cover_letter_ready") == {"job_id": 1, "content": "Dear LogiCo, hello."}
    done = events.data("letter_run_done")
    assert done["job_id"] == 1 and done["run_id"] == landed.run_id and done["status"] == "done"
    assert done["clean"] is True and done["open_issues"] == []
    assert landed.kind == "pipeline" and landed.status == "done" and landed.clean is True
    assert landed.letter == "Dear LogiCo, hello."


def test_a_run_that_stopped_short_still_lands_its_best_draft_with_the_issues(db, monkeypatch):
    add_match(db, 1, 90)
    FakeEngines(monkeypatch, db, status="budget_stopped", clean=False, open_issues=["claims: led a team"],
                stop_reason="revision limit reached")
    events = Events()

    landed = production.run_work(Work("pipeline", 1), 1, events, db=db)

    assert len(_letters(db)) == 1
    done = events.data("letter_run_done")
    assert done["clean"] is False and done["open_issues"] == ["claims: led a team"]
    assert landed.status == "budget_stopped" and landed.clean is False
    assert landed.open_issues == ["claims: led a team"] and landed.letter


def test_a_run_waiting_on_the_user_writes_no_letter_and_reports_the_questions(db, monkeypatch):
    add_match(db, 1, 90)
    questions = [_question("Q1"), _question("Q2"), _question("Q3", "answered")]
    FakeEngines(monkeypatch, db, status="waiting_user", draft_text=None, clean=False, questions=questions)
    events = Events()

    landed = production.run_work(Work("pipeline", 1), 1, events, db=db)

    assert _letters(db) == []
    assert events.names == ["letter_run_started", "letter_run_waiting"]
    waiting = events.data("letter_run_waiting")
    assert waiting["job_id"] == 1 and waiting["run_id"] == landed.run_id and waiting["questions"] == 2
    assert landed.status == "waiting_user" and landed.questions == 2 and landed.letter is None


def test_a_failed_run_with_no_draft_writes_no_letter_and_reports_why(db, monkeypatch):
    add_match(db, 1, 90)
    FakeEngines(monkeypatch, db, status="failed", draft_text=None, clean=False, stop_reason="analyze_job failed: boom")
    events = Events()

    landed = production.run_work(Work("pipeline", 1), 1, events, db=db)

    assert _letters(db) == []
    assert events.names == ["letter_run_started", "letter_run_failed"]
    assert events.data("letter_run_failed")["reason"] == "analyze_job failed: boom"
    assert landed.status == "failed" and landed.letter is None


def test_an_account_limit_with_no_draft_cancels_the_run_instead_of_failing_it(db, monkeypatch):
    add_match(db, 1, 90)
    FakeEngines(monkeypatch, db, status="budget_stopped", draft_text=None, clean=False,
                stop_reason="BudgetExceededError: daily cap", account_limit="daily cap")
    events = Events()

    landed = production.run_work(Work("pipeline", 1), 1, events, db=db)

    assert _run_row(db, landed.run_id).status == "cancelled"
    assert landed.status == "cancelled" and landed.account_limit == "daily cap"
    assert "letter_run_failed" not in events.names
    assert _letters(db) == []
    # a cancelled run does not count against the match
    assert production.next_work(db, 1, now=NOW) == Work("pipeline", 1)


def test_an_engine_that_raises_comes_back_as_a_failed_landing(db, monkeypatch):
    add_match(db, 1, 90)
    engines = FakeEngines(monkeypatch, db)
    engines.raises = RuntimeError("provider exploded")
    events = Events()

    landed = production.run_work(Work("pipeline", 1), 1, events, db=db)

    assert landed.status == "failed" and "provider exploded" in landed.reason
    failed = events.data("letter_run_failed")
    assert failed["job_id"] == 1 and "provider exploded" in failed["reason"]
    assert db.scalar(select(func.count(Match.id))) == 1  # the session is still usable


def test_the_engine_comes_from_the_preference_and_defaults_to_the_agent(db, monkeypatch):
    add_match(db, 1, 90)
    engines = FakeEngines(monkeypatch, db)
    production.run_work(Work("pipeline", 1), 1, None, db=db)
    assert engines.run_calls[-1]["engine"] == "agent"

    set_preferences(db, 1, {"letter_engine": "workflow"})
    db.query(CoverLetter).delete()
    db.commit()
    production.run_work(Work("pipeline", 1), 1, None, db=db)
    assert engines.run_calls[-1]["engine"] == "workflow"
    assert engines.run_calls[-1]["job_id"] == 1 and engines.run_calls[-1]["profile_id"] == 1


def test_resume_work_continues_the_given_run_rather_than_starting_one(db, monkeypatch):
    add_match(db, 1, 90)
    run_id = add_run(db, 1, "answered")
    engines = FakeEngines(monkeypatch, db)
    events = Events()

    landed = production.run_work(Work("resume", 1, run_id=run_id), 1, events, db=db)

    assert engines.resume_calls == [run_id] and engines.run_calls == []
    started = events.data("letter_run_started")
    assert started["resumed"] is True and started["run_id"] == run_id
    assert landed.kind == "resume" and landed.status == "done" and landed.run_id == run_id
    assert len(_letters(db)) == 1


def test_a_raising_emit_callback_never_breaks_the_run(db, monkeypatch):
    add_match(db, 1, 90)
    FakeEngines(monkeypatch, db, draft_text="Still lands.")

    def broken(event_name, data):
        raise RuntimeError("sse listener died")

    landed = production.run_work(Work("pipeline", 1), 1, broken, db=db)

    assert landed.status == "done"
    (row,) = _letters(db)
    assert row.generated_content == "Still lands."


def test_emit_none_is_fine(db, monkeypatch):
    add_match(db, 1, 90)
    FakeEngines(monkeypatch, db)
    assert production.run_work(Work("pipeline", 1), 1, None, db=db).status == "done"
    assert len(_letters(db)) == 1


# ---------------------------------------------------------------------------
# run_work: the one-shot
# ---------------------------------------------------------------------------
def test_oneshot_work_calls_the_one_shot_writer_and_emits_only_cover_letter_ready(db, monkeypatch):
    add_match(db, 1, 80)
    engines = FakeEngines(monkeypatch, db)
    oneshot = FakeOneShot(monkeypatch, "Short and sweet.")
    events = Events()

    landed = production.run_work(Work("oneshot", 1), 1, events, db=db)

    assert len(oneshot.calls) == 1 and oneshot.calls[0]["job_id"] == 1 and oneshot.calls[0]["profile_id"] == 1
    assert engines.run_calls == [] and engines.resume_calls == []
    assert events.items == [("cover_letter_ready", {"job_id": 1, "content": "Short and sweet."})]
    assert landed.kind == "oneshot" and landed.status == "done" and landed.clean is False
    assert landed.letter == "Short and sweet."


@pytest.mark.parametrize("exc", [BudgetExceededError("daily cap hit"), DailyQuotaError("quota gone")])
def test_a_one_shot_stopped_by_the_account_limit_reports_it(db, monkeypatch, exc):
    add_match(db, 1, 80)
    FakeOneShot(monkeypatch).raises = exc
    landed = production.run_work(Work("oneshot", 1), 1, Events(), db=db)
    assert landed.status == "budget_stopped" and landed.account_limit == str(exc)


def test_a_one_shot_that_writes_nothing_is_a_failure(db, monkeypatch):
    add_match(db, 1, 80)
    FakeOneShot(monkeypatch).returns_none = True
    events = Events()
    landed = production.run_work(Work("oneshot", 1), 1, events, db=db)
    assert landed.status == "failed" and events.items == []


# ---------------------------------------------------------------------------
# generate_for: the explicit regenerate
# ---------------------------------------------------------------------------
def test_regenerate_runs_the_pipeline_even_for_a_low_score_and_cancels_open_runs_first(db, monkeypatch):
    add_match(db, 1, 40)
    waiting = add_run(db, 1, "waiting_user")
    answered = add_run(db, 1, "answered")
    engines = FakeEngines(monkeypatch, db)
    engines.watch = [waiting, answered]

    landed = production.generate_for(db, 1, 1, Events())

    assert len(engines.run_calls) == 1
    assert engines.seen_statuses == {waiting: "cancelled", answered: "cancelled"}  # before the new run started
    assert landed.kind == "pipeline" and landed.status == "done"
    assert len(_letters(db)) == 1


def test_regenerate_with_the_master_switch_off_forces_the_one_shot(db, monkeypatch):
    add_match(db, 1, 95)
    waiting = add_run(db, 1, "waiting_user")
    set_preferences(db, 1, {"letter_loop_enabled": False})
    engines = FakeEngines(monkeypatch, db)
    oneshot = FakeOneShot(monkeypatch)

    landed = production.generate_for(db, 1, 1, Events())

    assert engines.run_calls == []
    assert len(oneshot.calls) == 1
    assert oneshot.calls[0]["force"] is True and oneshot.calls[0]["bypass_threshold"] is True
    assert _run_row(db, waiting).status == "cancelled"
    assert landed.kind == "oneshot"


def test_regenerate_with_no_match_does_nothing(db, monkeypatch):
    engines = FakeEngines(monkeypatch, db)
    oneshot = FakeOneShot(monkeypatch)
    db.add(JobListing(id=9, source="seek", source_job_id="9", url="u", title="T", company="C", raw_description="d"))
    db.commit()
    assert production.generate_for(db, 9, 1, Events()) is None
    assert engines.run_calls == [] and oneshot.calls == []


def test_without_bypass_a_score_below_the_auto_minimum_is_not_written(db, monkeypatch):
    add_match(db, 1, 70)
    engines = FakeEngines(monkeypatch, db)
    oneshot = FakeOneShot(monkeypatch)
    assert production.generate_for(db, 1, 1, Events(), bypass_threshold=False) is None
    assert engines.run_calls == [] and oneshot.calls == []


def test_without_bypass_the_usual_bars_pick_the_writer(db, monkeypatch):
    add_match(db, 1, 80)
    add_match(db, 2, 85)
    engines = FakeEngines(monkeypatch, db)
    oneshot = FakeOneShot(monkeypatch)

    assert production.generate_for(db, 1, 1, Events(), bypass_threshold=False).kind == "oneshot"
    assert len(oneshot.calls) == 1 and engines.run_calls == []

    assert production.generate_for(db, 2, 1, Events(), bypass_threshold=False).kind == "pipeline"
    assert len(engines.run_calls) == 1


def test_a_pipeline_regenerate_keeps_the_users_edits(db, monkeypatch):
    add_match(db, 1, 90)
    db.add(CoverLetter(match_id=1, generated_content="old", edited_content="My edit", status="edited"))
    db.commit()
    FakeEngines(monkeypatch, db, draft_text="Fresh draft")

    production.generate_for(db, 1, 1, Events())

    (row,) = _letters(db)
    assert row.edited_content == "My edit" and row.status == "edited" and row.generated_content == "Fresh draft"


def test_a_one_shot_regenerate_keeps_the_users_edits(db, monkeypatch):
    """The real one-shot (only its LLM call is faked): an edited letter stays 'edited'."""
    add_match(db, 1, 90)
    db.add(CoverLetter(match_id=1, generated_content="old", edited_content="My edit", status="edited"))
    db.commit()
    set_preferences(db, 1, {"letter_loop_enabled": False})
    monkeypatch.setattr(cover_letter, "complete_text", lambda *a, **k: "Fresh one-shot")

    landed = production.generate_for(db, 1, 1, Events())

    assert landed.status == "done"
    (row,) = _letters(db)
    assert row.edited_content == "My edit" and row.status == "edited" and row.generated_content == "Fresh one-shot"


def test_a_one_shot_regenerate_of_an_unedited_letter_is_a_fresh_draft(db, monkeypatch):
    add_match(db, 1, 90)
    db.add(CoverLetter(match_id=1, generated_content="old", status="final"))
    db.commit()
    set_preferences(db, 1, {"letter_loop_enabled": False})
    monkeypatch.setattr(cover_letter, "complete_text", lambda *a, **k: "Fresh one-shot")
    production.generate_for(db, 1, 1, Events())
    (row,) = _letters(db)
    assert row.status == "draft" and row.generated_content == "Fresh one-shot"


# ---------------------------------------------------------------------------
# End to end through the real workflow engine (scripted tools, no LLM)
# ---------------------------------------------------------------------------
def test_the_workflow_engine_lands_its_final_draft_through_the_production_path(db, monkeypatch):
    Script().install(monkeypatch)
    # Side outputs off: Script doesn't script them, and on (the default) the résumé notes
    # reached the real provider, a paid mid call on every test run (found 2026-10-05).
    set_preferences(db, 1, {"letter_engine": "workflow", "resume_advice_enabled": False,
                            "learning_suggestions_enabled": False, "screening_answers_enabled": False})
    add_match(db, 5, 90)
    events = Events()

    landed = production.run_work(Work("pipeline", 5), 1, events, db=db)

    assert landed.status == "done" and landed.clean is True
    (row,) = _letters(db)
    assert row.generated_content == "draft 1" and row.status == "draft"
    run = _run_row(db, landed.run_id)
    assert run.status == "done" and run.engine == "workflow" and run.final_draft_version == 1
    assert events.names == ["letter_run_started", "cover_letter_ready", "letter_run_done"]
    assert events.data("letter_run_started")["engine"] == "workflow"
    assert events.data("letter_run_done")["clean"] is True


def test_a_must_have_gap_pauses_the_run_for_the_user_and_the_loop_moves_on(db, monkeypatch):
    # ask_user_gaps is the default production policy: the essential headline gap becomes a question.
    script = Script(match={"R1": ("gap", [])}).install(monkeypatch)
    set_preferences(db, 1, {"letter_engine": "workflow"})
    add_match(db, 5, 90)
    events = Events()

    landed = production.run_work(Work("pipeline", 5), 1, events, db=db)

    assert landed.status == "waiting_user" and landed.questions == 1
    assert _letters(db) == []
    assert "generate_letter" not in script.calls
    assert events.names == ["letter_run_started", "letter_run_waiting"]
    assert events.data("letter_run_waiting")["questions"] == 1
    assert _run_row(db, landed.run_id).status == "waiting_user"
    assert production.next_work(db, 1, now=NOW) is None
