"""Tests for app/llm/letter/view.py: what the sidebar shows about cover-letter runs.

``latest_runs`` (one small dict per match for the job cards) and ``letter_info`` (the
story of one letter: open issues, what it doesn't claim and why, eligibility notes,
a run waiting on the user, or why the last run failed). Both are read-only views over
``letter_runs``; in-memory SQLite, no LLM.
"""
from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.llm.letter import view
from app.llm.letter.runner import REFUSED
from app.llm.letter.state import Check, JobInfo, LetterState, Requirement, UserDecision, UserQuestion
from app.models import CoverLetter, JobListing, LetterRun, LetterRunStep, Match, Profile

LETTER = "Dear LogiCo, here is my letter."


@pytest.fixture()
def db():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(eng, "connect")
    def _fk_on(conn, _):
        if isinstance(conn, sqlite3.Connection):
            conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(eng)
    with sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)() as session:
        session.add(Profile(id=1, name="Bob", email="b@x.com", password_hash="x", summary="Grad."))
        session.commit()
        yield session
    Base.metadata.drop_all(eng)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def add_match(db, job_id=5, score=90) -> int:
    """A job and the profile's match for it; the match id equals the job id."""
    db.add(JobListing(id=job_id, source="seek", source_job_id=str(job_id), url=f"u{job_id}",
                      title=f"Job {job_id}", company="LogiCo", raw_description="d"))
    db.flush()
    db.add(Match(id=job_id, user_id=1, job_id=job_id, score=score))
    db.commit()
    return job_id


def _req(rid, text, *, role="headline", importance="essential", status="supported", decision=None):
    return Requirement(id=rid, text=text, importance=importance, letter_role=role, status=status,
                       evidence=["experience:1"] if status == "supported" else [], user_decision=decision,
                       theme=f"t-{rid}")


def _state(job_id=5, requirements=(), questions=(), instructions=()) -> LetterState:
    return LetterState(
        profile_id=1,
        job=JobInfo(job_id=job_id, title="Engineer", application_instructions=list(instructions)),
        requirements=list(requirements), user_questions=list(questions),
    )


def _q(qid, status="open"):
    return UserQuestion(id=qid, requirement_id="R1", requirement_text="Power BI", skill_key="power bi",
                        prompt="?", status=status)


def _check(passed=True, issues=(), warnings=()):
    return Check(passed=passed, issues=list(issues), warnings=list(warnings))


def _checked_state(text=LETTER, claims=True, requirements=True, style=True, **kw) -> LetterState:
    """A state with one draft; each check arg is True / False / None (never ran) or a Check."""
    state = _state(**kw)
    state.add_draft(text)
    for name, result in (("claims", claims), ("requirements", requirements), ("style", style)):
        if result is None:
            continue
        state.record_check(name, result if isinstance(result, Check) else _check(bool(result), [] if result else [f"{name} broke"]))
    return state


def add_run(db, match_id, status, state=None, *, final_version=None, engine="agent", cost=0.0) -> int:
    run = LetterRun(match_id=match_id, engine=engine, status=status,
                    state=(state or _state(match_id)).model_dump_json(),
                    final_draft_version=final_version, cost_usd=cost)
    db.add(run)
    db.commit()
    return run.id


def add_letter(db, match_id, text=LETTER):
    db.add(CoverLetter(match_id=match_id, generated_content=text, status="draft"))
    db.commit()


def add_step(db, run_id, seq, error):
    db.add(LetterRunStep(run_id=run_id, seq=seq, tool="generate_letter", error=error))
    db.commit()


# ---------------------------------------------------------------------------
# latest_runs
# ---------------------------------------------------------------------------
def test_latest_runs_of_no_matches_is_empty(db):
    assert view.latest_runs(db, []) == {}


def test_a_match_with_no_runs_is_absent(db):
    add_match(db, 1)
    add_match(db, 2)
    run_id = add_run(db, 2, "done", _checked_state(job_id=2), final_version=1)
    result = view.latest_runs(db, [1, 2])
    assert 1 not in result
    assert result[2] == {"run_id": run_id, "status": "done", "open_questions": 0}


def test_the_newest_run_per_match_wins(db):
    add_match(db, 1)
    add_match(db, 2)
    add_run(db, 1, "failed")
    add_run(db, 1, "running")
    newest_2 = add_run(db, 2, "done", _checked_state(job_id=2), final_version=1)
    newest_1 = add_run(db, 1, "cancelled")  # the highest id for match 1
    result = view.latest_runs(db, [1, 2])
    assert result[1] == {"run_id": newest_1, "status": "cancelled", "open_questions": 0}
    assert result[2]["run_id"] == newest_2


def test_a_waiting_run_counts_the_questions_still_unanswered(db):
    add_match(db, 1)
    state = _state(1, questions=[_q("Q1"), _q("Q2", "needs_confirm"), _q("Q3", "answered"), _q("Q4")])
    run_id = add_run(db, 1, "waiting_user", state)
    assert view.latest_runs(db, [1]) == {1: {"run_id": run_id, "status": "waiting_user", "open_questions": 3}}


@pytest.mark.parametrize("status", ["running", "answered", "failed", "done", "budget_stopped", "cancelled"])
def test_other_statuses_report_no_open_questions(db, status):
    add_match(db, 1)
    add_run(db, 1, status, _state(1, questions=[_q("Q1"), _q("Q2")]))
    assert view.latest_runs(db, [1])[1]["open_questions"] == 0


def test_runs_of_matches_not_asked_for_are_not_returned(db):
    add_match(db, 1)
    add_match(db, 2)
    add_run(db, 1, "running")
    add_run(db, 2, "running")
    assert list(view.latest_runs(db, [1])) == [1]


# ---------------------------------------------------------------------------
# letter_info: the basics
# ---------------------------------------------------------------------------
def test_no_match_for_the_job_is_none(db):
    assert view.letter_info(db, 404, 1) is None
    add_match(db, 5)
    assert view.letter_info(db, 5, 2) is None  # another profile


def test_a_match_with_no_runs_and_no_letter_has_nothing_to_say(db):
    add_match(db, 5)
    assert view.letter_info(db, 5, 1) == {"job_id": 5, "run": None, "waiting": None, "failure": None}


def test_a_done_run_whose_draft_is_the_letter_reports_everything(db):
    add_match(db, 5)
    reqs = [
        _req("R1", "Build REST APIs"),
        _req("R2", "Power BI", status="gap"),
        _req("R3", "Kubernetes", status="gap", decision=UserDecision(choice="leave_out", answer="no")),
        _req("R4", "Terraform", status="gap", decision=UserDecision(choice="leave_out", remembered=True)),
        _req("R5", "Australian work rights", role="not_for_letter", status="gap"),
        _req("R6", "SQL", role="mention", status="supported"),
    ]
    state = _state(requirements=reqs, instructions=["Attach your transcript"])
    state.add_draft(LETTER)
    state.record_check("claims", _check(False, ["'led a team' has no support"]))
    state.record_check("requirements", _check(True, warnings=["R6 only briefly"]))
    run_id = add_run(db, 5, "done", state, final_version=1, engine="workflow", cost=0.25)
    add_letter(db, 5)

    info = view.letter_info(db, 5, 1)

    run = info["run"]
    assert run["run_id"] == run_id and run["engine"] == "workflow" and run["status"] == "done"
    assert run["draft_version"] == 1 and run["clean"] is False
    assert run["open_issues"] == ["claims: 'led a team' has no support", "style: not checked on draft 1"]
    assert run["warnings"] == ["requirements: R6 only briefly"]
    assert run["cost_usd"] == pytest.approx(0.25)
    assert run["eligibility_notes"] == ["Australian work rights"]
    assert run["application_instructions"] == ["Attach your transcript"]
    assert [(n["id"], n["reason"]) for n in run["not_claimed"]] == [
        ("R2", "Nothing in your profile backs this"),
        ("R3", "You said you don't have this"),
        ("R4", "You said you don't have this (remembered from an earlier ad)"),
    ]
    assert info["waiting"] is None and info["failure"] is None


def test_a_clean_run_has_no_open_issues(db):
    add_match(db, 5)
    add_run(db, 5, "done", _checked_state(), final_version=1)
    add_letter(db, 5)
    run = view.letter_info(db, 5, 1)["run"]
    assert run["clean"] is True and run["open_issues"] == [] and run["warnings"] == []


@pytest.mark.parametrize("claims, requirements, style", [
    (None, True, True), (True, None, True), (True, True, None), (False, True, True),
    (True, False, True), (True, True, False),
])
def test_clean_needs_all_three_checks_to_have_run_and_passed(db, claims, requirements, style):
    add_match(db, 5)
    add_run(db, 5, "done", _checked_state(claims=claims, requirements=requirements, style=style), final_version=1)
    add_letter(db, 5)
    run = view.letter_info(db, 5, 1)["run"]
    assert run["clean"] is False and run["open_issues"]


def test_not_claimed_leaves_out_supported_and_not_for_letter_items(db):
    state = _state(requirements=[
        _req("R1", "Build REST APIs"),
        _req("R2", "Work rights", role="not_for_letter", status="gap"),
        _req("R3", "Licence", role="not_for_letter", status="gap",
             decision=UserDecision(choice="leave_out")),
    ])
    assert view.not_claimed(state) == []


def test_not_claimed_carries_the_requirement_text_and_importance(db):
    state = _state(requirements=[_req("R1", "Power BI", status="gap", importance="important")])
    assert view.not_claimed(state) == [
        {"id": "R1", "text": "Power BI", "importance": "important", "reason": "Nothing in your profile backs this"},
    ]


def test_a_budget_stopped_run_with_a_flagged_draft_is_still_shown(db):
    add_match(db, 5)
    add_run(db, 5, "budget_stopped", _checked_state(claims=False), final_version=1)
    add_letter(db, 5)
    info = view.letter_info(db, 5, 1)
    assert info["run"]["status"] == "budget_stopped"
    assert info["run"]["clean"] is False and info["run"]["open_issues"]
    assert info["failure"] is None


def test_a_runs_findings_are_hidden_when_the_letter_has_moved_on(db):
    add_match(db, 5)
    add_run(db, 5, "done", _checked_state(), final_version=1)
    add_letter(db, 5, "A different letter (a later one-shot or an edit).")
    assert view.letter_info(db, 5, 1)["run"] is None


def test_no_letter_at_all_means_no_run_findings(db):
    add_match(db, 5)
    add_run(db, 5, "done", _checked_state(), final_version=1)
    assert view.letter_info(db, 5, 1)["run"] is None


# ---------------------------------------------------------------------------
# letter_info: waiting
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("status", ["waiting_user", "answered"])
def test_a_newest_waiting_or_answered_run_is_reported(db, status):
    add_match(db, 5)
    questions = [_q("Q1"), _q("Q2"), _q("Q3", "answered")]
    run_id = add_run(db, 5, status, _state(questions=questions))
    info = view.letter_info(db, 5, 1)
    assert info["waiting"] == {"run_id": run_id, "status": status, "open_questions": 2}
    assert info["failure"] is None


def test_an_older_waiting_run_that_is_not_the_newest_is_ignored(db):
    add_match(db, 5)
    add_run(db, 5, "waiting_user", _state(questions=[_q("Q1")]))
    add_run(db, 5, "done", _checked_state(), final_version=1)
    add_letter(db, 5)
    info = view.letter_info(db, 5, 1)
    assert info["waiting"] is None
    assert info["run"] is not None


# ---------------------------------------------------------------------------
# letter_info: failure
# ---------------------------------------------------------------------------
def test_a_failed_newest_run_reports_the_newest_real_step_error(db):
    add_match(db, 5)
    run_id = add_run(db, 5, "failed")
    add_step(db, run_id, 1, "older real error")
    add_step(db, run_id, 2, "the real error")
    add_step(db, run_id, 3, REFUSED + "can't revise yet")
    info = view.letter_info(db, 5, 1)
    assert info["failure"] == {"run_id": run_id, "status": "failed", "error": "the real error"}
    assert info["run"] is None and info["waiting"] is None


def test_a_failure_with_only_refusals_has_no_error_text(db):
    add_match(db, 5)
    run_id = add_run(db, 5, "failed")
    add_step(db, run_id, 1, REFUSED + "not yet")
    add_step(db, run_id, 2, REFUSED + "still not")
    assert view.letter_info(db, 5, 1)["failure"] == {"run_id": run_id, "status": "failed", "error": None}


def test_a_failure_with_no_steps_has_no_error_text(db):
    add_match(db, 5)
    run_id = add_run(db, 5, "failed")
    assert view.letter_info(db, 5, 1)["failure"]["error"] is None
    assert view.letter_info(db, 5, 1)["failure"]["run_id"] == run_id


def test_a_stop_with_no_draft_is_a_failure_too(db):
    add_match(db, 5)
    run_id = add_run(db, 5, "budget_stopped", final_version=None)
    add_step(db, run_id, 1, "BudgetExceededError: daily cap")
    assert view.letter_info(db, 5, 1)["failure"] == {
        "run_id": run_id, "status": "budget_stopped", "error": "BudgetExceededError: daily cap",
    }


@pytest.mark.parametrize("status", ["done", "cancelled", "running"])
def test_other_newest_statuses_are_not_failures(db, status):
    add_match(db, 5)
    add_run(db, 5, status)
    assert view.letter_info(db, 5, 1)["failure"] is None


def test_a_failure_is_absent_when_a_run_explains_the_letter(db):
    add_match(db, 5)
    run_id = add_run(db, 5, "failed", _checked_state(), final_version=1)
    add_step(db, run_id, 1, "a late tool failed")
    add_letter(db, 5)
    info = view.letter_info(db, 5, 1)
    assert info["run"]["run_id"] == run_id and info["run"]["status"] == "failed"
    assert info["failure"] is None


def test_only_the_newest_run_can_be_the_failure(db):
    add_match(db, 5)
    old = add_run(db, 5, "failed")
    add_step(db, old, 1, "old error")
    add_run(db, 5, "running")
    assert view.letter_info(db, 5, 1)["failure"] is None
