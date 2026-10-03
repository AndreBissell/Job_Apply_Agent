"""Tests for the Phase 6 fixed cover-letter workflow (app/llm/letter/workflow.py), the
guardrails helpers it relies on to stop short (best_draft, is_clean, open_issues,
uncovered_count), runner.finish_run's final_version, and the loop-report helpers in
scripts/letter_lab.py.

No LLM and no network: the tools the workflow calls (analyze_job, match_profile,
generate_letter, revise_letter and the CHECKS tuple) are replaced by scripted fakes that
mutate the LetterState the way the real ones do, so what is under test is the order of
steps, the gates, the limits and what the run hands back.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.llm.client import BudgetExceededError, DailyQuotaError
from app.llm.letter import guardrails, workflow
from app.llm.letter.runner import ToolError, finish_run, start_run
from app.llm.letter.state import Check, Claim, JobInfo, LetterState, Requirement, UserDecision, UserQuestion
from app.models import (
    Experience,
    JobListing,
    LetterRun,
    LetterRunStep,
    Match,
    Profile,
    Qualification,
    Skill,
)

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def db():
    eng = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(eng, "connect")
    def _fk_on(conn, _):
        if isinstance(conn, sqlite3.Connection):
            conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(eng)
    with sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)() as session:
        session.add(Profile(
            id=1, name="Bob", email="b@x.com", password_hash="x", visa_status="Australian citizen",
            summary="Grad developer.",
        ))
        session.flush()
        session.add_all([
            Experience(id=12, user_id=1, experience_type="job", title="Developer", organization="Acme",
                       description="Built REST APIs for 3 teams. Deployed services to AWS."),
            Skill(id=7, user_id=1, name="SQL"),
            Qualification(id=4, user_id=1, qualification_type="degree", title="BIT", institution="UQ"),
            JobListing(id=5, source="seek", source_job_id="1", url="u", title="Engineer",
                       company="LogiCo", raw_description="We build logistics software."),
        ])
        session.flush()
        session.add(Match(id=9, user_id=1, job_id=5, score=80))
        session.commit()
        yield session
    Base.metadata.drop_all(eng)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _req(rid, text="x", importance="essential", role="headline", status="supported", evidence=(),
         decision=None):
    return Requirement(
        id=rid, text=text, importance=importance, letter_role=role, status=status,
        evidence=list(evidence), user_decision=decision, theme=f"theme-{rid}",
    )


def _state(requirements=None) -> LetterState:
    reqs = requirements if requirements is not None else [
        _req("R1", "Build REST APIs", evidence=["experience:12#s1"]),
        _req("R2", "SQL databases", role="mention", evidence=["skill:7", "experience:12"]),
    ]
    return LetterState(profile_id=1, job=JobInfo(job_id=5, title="Engineer"), requirements=reqs)


def _issue(rid: str, text: str = "x") -> str:
    return f"{rid} not addressed: {text!r}"  # check_requirements' real issue format


def _draft(state, claims=True, requirements=True, style=True, text=None):
    """Add a draft and record its checks. claims/style: True/False/None (None = never ran).
    requirements: True, False, None, or a list of uncovered ids (a failing check with one
    issue per id)."""
    d = state.add_draft(text or f"draft {len(state.drafts) + 1}", [Claim(text="c", source="skill:7")])
    if claims is not None:
        state.record_check("claims", Check(passed=bool(claims), issues=[] if claims else ["claims broke"]))
    if requirements is not None:
        if isinstance(requirements, list):
            check = Check(passed=False, issues=[_issue(i) for i in requirements])
        else:
            check = Check(passed=bool(requirements), issues=[] if requirements else ["requirements broke"])
        state.record_check("requirements", check)
    if style is not None:
        state.record_check("style", Check(passed=bool(style), issues=[] if style else ["style broke"]))
    return d


# --- scripted fakes ---------------------------------------------------------
_DEFAULT_REQS = (
    dict(id="R1", text="Build REST APIs", importance="essential", letter_role="headline"),
    dict(id="R2", text="SQL databases", importance="essential", letter_role="mention"),
)


class Script:
    """Scripted stand-ins for the five workflow tools.

    ``plan`` maps a draft version to what each check does on it:
        {1: {"claims": True, "requirements": ["R1"], "style": False}, 2: {...}}
    claims/style: True (pass), False (fail), or an Exception instance to raise.
    requirements: True, False, a list of uncovered ids (fail, one issue each), or an Exception.
    A version missing from the plan passes everything.
    """

    def __init__(self, plan=None, reqs=_DEFAULT_REQS, match=None):
        self.plan = plan or {}
        self.reqs = [dict(r) for r in reqs]
        # id -> (status, evidence); default: every requirement supported
        self.match = match or {}
        self.calls: list[str] = []
        self.raise_in: dict[str, Exception] = {}  # tool name -> exception to raise
        self.on_generate = None

    # -- tools -----------------------------------------------------------
    def analyze_job(self, state, ctx):
        self.calls.append("analyze_job")
        if "analyze_job" in self.raise_in:
            raise self.raise_in["analyze_job"]
        state.requirements = [Requirement(**r) for r in self.reqs]
        return {"requirements": len(state.requirements)}

    def match_profile(self, state, ctx):
        self.calls.append("match_profile")
        if "match_profile" in self.raise_in:
            raise self.raise_in["match_profile"]
        for r in state.requirements:
            r.status, r.evidence = self.match.get(r.id, ("supported", ["experience:12"]))
        return {"matched": len(state.requirements)}

    def generate_letter(self, state, ctx):
        self.calls.append("generate_letter")
        state.add_draft("draft 1", [Claim(text="c", source="skill:7")])
        if self.on_generate:
            self.on_generate(state)
        return {"draft": 1}

    def revise_letter(self, state, ctx):
        self.calls.append("revise_letter")
        refusal = guardrails.can_revise(state)
        if refusal:
            raise ToolError(refusal)
        d = state.add_draft(f"draft {len(state.drafts) + 1}", [Claim(text="c", source="skill:7")])
        return {"draft": d.version}

    def _check(self, tool: str, key: str):
        def fn(state, ctx):
            self.calls.append(tool)
            version = state.latest_draft.version
            outcome = self.plan.get(version, {}).get(key, True)
            if isinstance(outcome, Exception):
                raise outcome
            if isinstance(outcome, list):
                check = Check(passed=False, issues=[_issue(i) for i in outcome])
            else:
                check = Check(passed=bool(outcome), issues=[] if outcome else [f"{key} broke on v{version}"])
            state.record_check(key, check)
            return {"passed": check.passed}
        return fn

    def install(self, monkeypatch):
        monkeypatch.setattr(workflow, "analyze_job", self.analyze_job)
        monkeypatch.setattr(workflow, "match_profile", self.match_profile)
        monkeypatch.setattr(workflow, "generate_letter", self.generate_letter)
        monkeypatch.setattr(workflow, "revise_letter", self.revise_letter)
        monkeypatch.setattr(workflow, "CHECKS", (
            ("check_claims", self._check("check_claims", "claims")),
            ("check_requirements", self._check("check_requirements", "requirements")),
            ("style_lint", self._check("style_lint", "style")),
        ))
        return self


@pytest.fixture()
def script(monkeypatch):
    """A default Script (everything passes) already installed; tweak it before running."""
    return Script().install(monkeypatch)


def _plan_script(monkeypatch, plan, **kw) -> Script:
    return Script(plan, **kw).install(monkeypatch)


def _run_row(db, run_id) -> LetterRun:
    db.expire_all()
    return db.get(LetterRun, run_id)


def _steps(db, run_id) -> list[LetterRunStep]:
    db.expire_all()
    return list(db.scalars(select(LetterRunStep).where(LetterRunStep.run_id == run_id).order_by(LetterRunStep.seq)))


CLEAN_CALLS = ["analyze_job", "match_profile", "generate_letter",
               "check_claims", "check_requirements", "style_lint"]
CHECK_CALLS = ["check_claims", "check_requirements", "style_lint"]


# ---------------------------------------------------------------------------
# guardrails: best_draft / is_clean / open_issues / uncovered_count
# ---------------------------------------------------------------------------
def test_best_draft_is_none_without_drafts():
    assert guardrails.best_draft(_state()) is None


def test_claims_pass_beats_claims_fail_even_when_the_failing_draft_covers_more():
    state = _state()
    _draft(state, claims=False, requirements=True, style=True)
    _draft(state, claims=True, requirements=["R1", "R2"], style=False)
    assert guardrails.best_draft(state).version == 2


def test_fewer_uncovered_must_cover_items_wins_among_claims_passing_drafts():
    state = _state()
    _draft(state, requirements=["R1", "R2"])
    _draft(state, requirements=["R1"])
    _draft(state, requirements=["R1", "R2"])
    assert guardrails.best_draft(state).version == 2


def test_style_pass_breaks_a_tie_on_claims_and_coverage():
    state = _state()
    _draft(state, requirements=["R1"], style=True)
    _draft(state, requirements=["R1"], style=False)
    assert guardrails.best_draft(state).version == 1


def test_a_full_tie_goes_to_the_later_draft():
    state = _state()
    _draft(state, requirements=["R1"], style=False)
    _draft(state, requirements=["R1"], style=False)
    assert guardrails.best_draft(state).version == 2


def test_a_draft_whose_claims_check_never_ran_ranks_below_one_that_passed():
    state = _state()
    _draft(state, claims=True, requirements=False, style=False)
    _draft(state, claims=None, requirements=True, style=True)
    assert guardrails.best_draft(state).version == 1


def test_unchecked_requirements_rank_below_missing_every_must_cover_item():
    state = _state()
    assert len(guardrails.must_cover(state)) == 2
    unchecked = _draft(state, requirements=None)
    missing_all = _draft(state, requirements=["R1", "R2"])
    assert guardrails.uncovered_count(state, unchecked) == 3  # len(must_cover) + 1
    assert guardrails.uncovered_count(state, missing_all) == 2
    assert guardrails.best_draft(state).version == 2  # the later one, but ranked on coverage too
    # and the other order: the unchecked draft must not win on being later
    state2 = _state()
    _draft(state2, requirements=["R1", "R2"])
    _draft(state2, requirements=None)
    assert guardrails.best_draft(state2).version == 1


def test_uncovered_count_is_zero_when_requirements_passed():
    state = _state()
    d = _draft(state, requirements=True)
    assert guardrails.uncovered_count(state, d) == 0


def test_clean_latest_draft_is_returned_when_every_check_passed_on_it():
    state = _state()
    _draft(state, requirements=["R1"])
    _draft(state)
    assert guardrails.best_draft(state).version == 2
    state2 = _state()
    _draft(state2)
    _draft(state2)  # both clean: still the latest
    assert guardrails.best_draft(state2).version == 2


def test_is_clean_needs_all_three_checks_run_and_passed():
    state = _state()
    assert guardrails.is_clean(_draft(state))
    assert not guardrails.is_clean(_draft(state, claims=None))
    assert not guardrails.is_clean(_draft(state, requirements=None))
    assert not guardrails.is_clean(_draft(state, style=None))
    assert not guardrails.is_clean(_draft(state, claims=False))
    assert not guardrails.is_clean(_draft(state, requirements=False))
    assert not guardrails.is_clean(_draft(state, style=False))


def test_open_issues_is_empty_for_a_clean_draft():
    assert guardrails.open_issues(_draft(_state())) == []


def test_open_issues_lists_each_claims_issue_prefixed():
    state = _state()
    d = _draft(state)
    d.checks["claims"] = Check(passed=False, issues=["'led a team' has no support", "'5 years' invented"])
    assert guardrails.open_issues(d) == ["claims: 'led a team' has no support", "claims: '5 years' invented"]


def test_open_issues_names_a_check_that_never_ran():
    state = _state()
    _draft(state)
    d = _draft(state, style=None)
    assert guardrails.open_issues(d) == ["style: not checked on draft 2"]


def test_open_issues_says_failed_when_a_failed_check_has_no_issues():
    state = _state()
    d = _draft(state)
    d.checks["requirements"] = Check(passed=False, issues=[])
    assert guardrails.open_issues(d) == ["requirements: failed"]


# ---------------------------------------------------------------------------
# run_workflow: happy paths
# ---------------------------------------------------------------------------
def test_all_checks_pass_on_draft_one(db, script):
    res = workflow.run_workflow(db, 5, 1)
    assert res.status == "done" and res.clean is True
    assert len(res.state.drafts) == 1 and res.draft.version == 1
    assert res.open_issues == [] and res.stop_reason is None and res.account_limit is None
    assert len(res.state.drafts) - 1 == 0 and res.text == "draft 1"
    assert script.calls == CLEAN_CALLS

    row = _run_row(db, res.run_id)
    assert row.status == "done" and row.final_draft_version == 1
    assert row.finished_at is not None
    assert row.engine == "workflow" and row.tool_calls == 6
    steps = _steps(db, res.run_id)
    assert [s.seq for s in steps] == [1, 2, 3, 4, 5, 6]
    assert [s.tool for s in steps] == CLEAN_CALLS
    assert all(s.error is None for s in steps)


def test_fail_on_draft_one_then_pass_on_two(db, monkeypatch):
    s = _plan_script(monkeypatch, {1: {"claims": False}})
    res = workflow.run_workflow(db, 5, 1)
    assert res.status == "done" and res.clean
    assert len(res.state.drafts) - 1 == 1 and res.draft.version == 2
    assert s.calls == CLEAN_CALLS + ["revise_letter"] + CHECK_CALLS


def test_two_revisions_fit_inside_the_default_budget(db, monkeypatch):
    s = _plan_script(monkeypatch, {1: {"claims": False}, 2: {"style": False}})
    res = workflow.run_workflow(db, 5, 1)
    assert res.status == "done" and res.clean and res.draft.version == 3
    assert len(res.state.drafts) - 1 == 2
    assert len(s.calls) == 14  # analyze + match + generate + 3 + 2 x (revise + 3)
    assert res.state.budget.tool_calls == 14
    assert res.state.budget.tool_calls < res.state.budget.max_tool_calls == 15
    assert _run_row(db, res.run_id).tool_calls == 14
    assert s.calls.count("revise_letter") == 2


def test_a_letter_that_never_passes_stops_at_the_revision_limit(db, monkeypatch):
    s = _plan_script(monkeypatch, {v: {"claims": False} for v in (1, 2, 3, 4)})
    res = workflow.run_workflow(db, 5, 1)
    assert workflow.MAX_REVISIONS == 2
    assert res.status == "budget_stopped"
    assert "revision limit" in res.stop_reason
    assert "claims" in res.stop_reason  # names what is still failing
    assert len(res.state.drafts) == 3
    assert s.calls.count("revise_letter") == 2
    assert s.calls.count("generate_letter") == 1
    assert res.clean is False and res.open_issues
    row = _run_row(db, res.run_id)
    assert row.status == "budget_stopped" and row.finished_at is not None


def test_best_draft_is_returned_even_when_it_is_not_the_latest(db, monkeypatch):
    _plan_script(monkeypatch, {1: {"style": False}, 2: {"claims": False}, 3: {"claims": False}})
    res = workflow.run_workflow(db, 5, 1)
    assert res.status == "budget_stopped"
    assert res.draft.version == 1 and res.clean is False
    assert any(i.startswith("style") for i in res.open_issues)
    assert not any(i.startswith("claims") for i in res.open_issues)
    assert _run_row(db, res.run_id).final_draft_version == 1


def test_a_draft_that_failed_claims_is_returned_flagged_never_clean(db, monkeypatch):
    _plan_script(monkeypatch, {v: {"claims": False} for v in (1, 2, 3)})
    res = workflow.run_workflow(db, 5, 1)
    assert res.draft is not None
    assert res.clean is False
    assert any(i.startswith("claims:") and "broke" in i for i in res.open_issues)


def test_max_revisions_zero_stops_after_the_first_drafts_checks(db, monkeypatch):
    s = _plan_script(monkeypatch, {1: {"style": False}})
    res = workflow.run_workflow(db, 5, 1, max_revisions=0)
    assert res.status == "budget_stopped" and "revision limit" in res.stop_reason
    assert len(res.state.drafts) == 1 and "revise_letter" not in s.calls
    assert res.draft.version == 1 and res.clean is False


def test_max_revisions_zero_is_still_done_when_draft_one_is_clean(db, script):
    res = workflow.run_workflow(db, 5, 1, max_revisions=0)
    assert res.status == "done" and res.clean


def test_engine_name_is_written_to_the_run_row(db, script):
    res = workflow.run_workflow(db, 5, 1, engine="agent-x")
    assert _run_row(db, res.run_id).engine == "agent-x"


# ---------------------------------------------------------------------------
# run_workflow: gaps
# ---------------------------------------------------------------------------
def test_default_gap_policy_leaves_an_essential_mention_gap_out_and_drafts(db, monkeypatch):
    s = _plan_script(monkeypatch, {}, match={"R2": ("gap", [])})
    res = workflow.run_workflow(db, 5, 1)
    gap = res.state.requirement("R2")
    assert gap.user_decision is not None and gap.user_decision.choice == "leave_out"
    assert res.status == "done" and "generate_letter" in s.calls


def test_default_gap_policy_handles_an_essential_headline_gap(db, monkeypatch):
    s = _plan_script(monkeypatch, {}, match={"R1": ("gap", [])})
    res = workflow.run_workflow(db, 5, 1)
    assert res.state.requirement("R1").user_decision.choice == "leave_out"
    assert res.status == "done" and res.clean
    assert s.calls[:3] == ["analyze_job", "match_profile", "generate_letter"]


def test_gap_policy_that_posts_a_question_leaves_the_run_waiting_on_the_user(db, monkeypatch):
    s = _plan_script(monkeypatch, {}, match={"R1": ("gap", [])})

    def ask(state, ctx):
        state.user_questions.append(UserQuestion(id="Q1", requirement_id="R1", requirement_text="x", skill_key="x", prompt="?", status="open"))

    res = workflow.run_workflow(db, 5, 1, gap_policy=ask)
    assert res.status == "waiting_user"
    assert res.draft is None and res.clean is False and res.open_issues == []
    assert "generate_letter" not in s.calls
    assert res.state.drafts == []
    row = _run_row(db, res.run_id)
    assert row.status == "waiting_user" and row.finished_at is None
    assert row.final_draft_version is None


def test_gap_policy_that_does_nothing_fails_the_run_naming_the_gap(db, monkeypatch):
    s = _plan_script(monkeypatch, {}, match={"R1": ("gap", [])})
    res = workflow.run_workflow(db, 5, 1, gap_policy=lambda state, ctx: None)
    assert res.status == "failed"
    assert "ask_user" in res.stop_reason and "R1" in res.stop_reason
    assert res.draft is None and "generate_letter" not in s.calls
    assert _run_row(db, res.run_id).status == "failed"


def test_leave_out_gaps_only_decides_needs_user_requirements():
    needs = _req("R1", "Kubernetes", role="headline", status="gap")
    needs_mention = _req("R2", "Terraform", role="mention", status="gap")
    elig = _req("R3", "Work rights", role="not_for_letter", status="gap")
    minor = _req("R4", "Go", importance="important", role="headline", status="gap")
    fine = _req("R5", "SQL", status="supported", evidence=["skill:7"])
    state = _state([needs, needs_mention, elig, minor, fine])
    workflow.leave_out_gaps(state, None)
    assert needs.user_decision.choice == "leave_out"
    assert needs_mention.user_decision.choice == "leave_out"
    assert elig.user_decision is None
    assert minor.user_decision is None
    assert fine.user_decision is None
    assert state.pending_gaps() == []


def test_leave_out_gaps_keeps_an_existing_decision():
    decided = _req("R1", "Kubernetes", status="gap", decision=UserDecision(choice="have_it", answer="did it"))
    state = _state([decided])
    workflow.leave_out_gaps(state, None)
    assert decided.user_decision.choice == "have_it"


# ---------------------------------------------------------------------------
# run_workflow: failures and limits
# ---------------------------------------------------------------------------
def test_a_tool_that_raises_fails_the_run_without_raising(db, script):
    script.raise_in["match_profile"] = RuntimeError("boom")
    res = workflow.run_workflow(db, 5, 1)
    assert res.status == "failed"
    assert "match_profile failed" in res.stop_reason and "boom" in res.stop_reason
    assert res.draft is None and res.clean is False
    assert script.calls == ["analyze_job", "match_profile"]
    steps = _steps(db, res.run_id)
    assert [s.tool for s in steps] == ["analyze_job", "match_profile"]
    assert steps[0].error is None
    assert "boom" in steps[1].error
    assert _run_row(db, res.run_id).status == "failed"


def test_a_check_that_raises_on_a_revision_returns_the_best_earlier_draft(db, monkeypatch):
    _plan_script(monkeypatch, {1: {"claims": False}, 2: {"claims": RuntimeError("judge died")}})
    res = workflow.run_workflow(db, 5, 1)
    assert res.status == "failed"
    assert "check_claims failed" in res.stop_reason and "judge died" in res.stop_reason
    assert len(res.state.drafts) == 2
    assert res.draft.version == 1  # v2 has no checks, so it ranks below v1
    assert res.clean is False
    assert _run_row(db, res.run_id).final_draft_version == 1


def test_per_run_tool_call_limit_stops_the_next_step(db, script):
    def exhaust(state):
        state.budget.max_tool_calls = state.budget.tool_calls

    script.on_generate = exhaust
    res = workflow.run_workflow(db, 5, 1)
    assert res.status == "budget_stopped"
    assert "tool-call limit" in res.stop_reason
    assert script.calls == ["analyze_job", "match_profile", "generate_letter"]
    assert res.draft.version == 1 and res.clean is False
    assert any("not checked" in i for i in res.open_issues)
    assert res.account_limit is None


@pytest.mark.parametrize("exc", [BudgetExceededError("daily cap"), DailyQuotaError("daily cap")])
def test_account_limits_stop_the_run_and_return_the_draft(db, monkeypatch, exc):
    _plan_script(monkeypatch, {1: {"claims": exc}})
    res = workflow.run_workflow(db, 5, 1)
    assert res.status == "budget_stopped"
    assert "daily cap" in res.account_limit
    assert type(exc).__name__ in res.stop_reason
    assert res.draft is not None and res.draft.version == 1
    assert res.clean is False
    steps = _steps(db, res.run_id)
    assert "daily cap" in steps[-1].error
    assert _run_row(db, res.run_id).status == "budget_stopped"


def test_a_gap_policy_that_raises_is_a_failed_ask_user_step(db, monkeypatch):
    # Since Phase 7b the policy runs as the logged ask_user step (as in the agent), so a
    # bug in it ends the run as failed with the error recorded instead of propagating.
    s = _plan_script(monkeypatch, {}, match={"R1": ("gap", [])})

    def bad_policy(state, ctx):
        raise ValueError("policy bug")

    res = workflow.run_workflow(db, 5, 1, gap_policy=bad_policy)
    assert res.status == "failed" and "policy bug" in res.stop_reason
    (run,) = db.scalars(select(LetterRun)).all()
    assert run.status == "failed" and run.finished_at is not None
    step = db.scalars(select(LetterRunStep).where(LetterRunStep.tool == "ask_user")).one()
    assert "policy bug" in step.error
    assert "generate_letter" not in s.calls


def test_the_gap_policy_is_not_called_without_a_pending_gap(db, script):
    called = []
    res = workflow.run_workflow(db, 5, 1, gap_policy=lambda state, ctx: called.append(1))
    assert called == [] and res.status == "done"


def test_missing_job_raises_and_creates_no_run(db, script):
    with pytest.raises(ValueError, match="not found"):
        workflow.run_workflow(db, 404, 1)
    assert db.scalars(select(LetterRun)).all() == []
    assert script.calls == []


def test_job_without_a_match_for_the_profile_raises_and_creates_no_run(db, script):
    with pytest.raises(ValueError, match="no match"):
        workflow.run_workflow(db, 5, 2)  # the match belongs to profile 1
    assert db.scalars(select(LetterRun)).all() == []
    db.add(JobListing(id=6, source="seek", source_job_id="2", url="u2", title="Other", company="X",
                      raw_description="d"))
    db.commit()
    with pytest.raises(ValueError, match="no match"):
        workflow.run_workflow(db, 6, 1)
    assert db.scalars(select(LetterRun)).all() == []


# ---------------------------------------------------------------------------
# runner.finish_run
# ---------------------------------------------------------------------------
def _started(db, drafts: int):
    state = _state()
    for _ in range(drafts):
        state.add_draft("t")
    return state, start_run(db, 9, "workflow", state)


def test_finish_run_stores_the_given_final_version(db):
    state, ctx = _started(db, 3)
    finish_run(ctx, state, "budget_stopped", final_version=1)
    assert _run_row(db, ctx.run.id).final_draft_version == 1


def test_finish_run_defaults_to_the_latest_draft(db):
    state, ctx = _started(db, 3)
    finish_run(ctx, state, "done")
    row = _run_row(db, ctx.run.id)
    assert row.final_draft_version == 3 and row.status == "done" and row.finished_at is not None


def test_finish_run_without_drafts_has_no_final_version(db):
    state, ctx = _started(db, 0)
    finish_run(ctx, state, "failed")
    assert _run_row(db, ctx.run.id).final_draft_version is None


def test_finish_run_leaves_finished_at_empty_while_waiting_on_the_user(db):
    state, ctx = _started(db, 0)
    finish_run(ctx, state, "waiting_user")
    row = _run_row(db, ctx.run.id)
    assert row.status == "waiting_user" and row.finished_at is None


# ---------------------------------------------------------------------------
# scripts/letter_lab.py: loop-report helpers
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def lab():
    """Import scripts/letter_lab.py by path. It points DATABASE_URL/APP_ENV at the eval DB
    and edits sys.path at import, so undo that for the rest of the test session."""
    saved_env = {k: os.environ.get(k) for k in ("DATABASE_URL", "APP_ENV")}
    saved_path = list(sys.path)
    try:
        spec = importlib.util.spec_from_file_location("letter_lab_under_test", ROOT / "scripts" / "letter_lab.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        sys.path[:] = saved_path
    return mod


def _dd(version, text="some words here", claims=None, requirements=None, style=None):
    """A saved-state draft dict. Each check arg: None = never ran, True/False, or for
    requirements a list of uncovered ids."""
    checks = {}
    if claims is not None:
        checks["claims"] = {"passed": claims, "issues": [], "warnings": []}
    if requirements is not None:
        if isinstance(requirements, list):
            checks["requirements"] = {"passed": False, "issues": [_issue(i) for i in requirements], "warnings": []}
        else:
            checks["requirements"] = {"passed": requirements, "issues": [], "warnings": []}
    if style is not None:
        checks["style"] = {"passed": style, "issues": [], "warnings": []}
    return {"version": version, "text": text, "claims": [], "checks": checks}


def test_uncovered_ids_none_when_requirements_never_ran(lab):
    assert lab._uncovered_ids(_dd(1)) is None


def test_uncovered_ids_parses_ids_from_issues(lab):
    d = _dd(1, requirements=["R3", "R10"])
    assert lab._uncovered_ids(d) == {"R3", "R10"}


def test_uncovered_ids_is_empty_on_a_pass(lab):
    assert lab._uncovered_ids(_dd(1, requirements=True)) == set()


def _write_run(tmp_path: Path, results: list[dict], states: dict[str, list[dict]]) -> Path:
    (tmp_path / "states").mkdir()
    (tmp_path / "run.json").write_text(json.dumps({"results": results}), encoding="utf-8")
    for key, drafts in states.items():
        (tmp_path / "states" / f"{key}.json").write_text(json.dumps({"drafts": drafts}), encoding="utf-8")
    return tmp_path


def _result(key, **kw):
    return {"key": key, "tool_checks": {"claims": True}, "cost_usd": 0.1, "seconds": 10, **kw}


def test_loop_rows_report_dropped_over_length_broke_and_fixed(lab, tmp_path):
    short = " ".join(["word"] * 300)
    long_ = " ".join(["word"] * 360)
    drafts = [
        _dd(1, short, claims=True, requirements=["R1"], style=False),
        _dd(2, long_, claims=False, requirements=["R1", "R2"], style=True),  # R2 newly missing
        _dd(3, long_, claims=True, requirements=True, style=True),
    ]
    run = _write_run(tmp_path, [_result("a")], {"a": drafts})
    (row,) = lab._loop_rows(run)
    assert [d["words"] for d in row["per_draft"]] == [300, 360, 360]
    assert row["per_draft"][0]["uncovered"] == {"R1"}
    assert row["per_draft"][0]["checks"] == {"claims": True, "requirements": False, "style": False}
    r1, r2 = row["revisions"]
    assert r1["to"] == 2 and r1["words"] == "300->360"
    assert r1["over_length"] is True
    assert r1["dropped"] == ["R2"]
    assert r1["broke"] == ["claims"] and r1["fixed"] == ["style"]
    assert r2["to"] == 3 and r2["over_length"] is False  # already over before
    assert r2["dropped"] == []
    assert r2["broke"] == [] and sorted(r2["fixed"]) == ["claims", "requirements"]


def test_loop_rows_final_version_defaults_to_the_draft_count(lab, tmp_path):
    drafts = [_dd(1, requirements=True), _dd(2, requirements=True), _dd(3, requirements=True)]
    run = _write_run(tmp_path, [_result("a"), _result("b", final_version=1)], {"a": drafts, "b": drafts})
    rows = {r["key"]: r for r in lab._loop_rows(run)}
    assert rows["a"]["final_version"] == 3
    assert rows["b"]["final_version"] == 1


def test_loop_rows_skip_errored_and_missing_jobs(lab, tmp_path):
    run = _write_run(
        tmp_path,
        [_result("ok"), {"key": "bad", "error": "boom"}, _result("nostate")],
        {"ok": [_dd(1, requirements=True)], "bad": [_dd(1)]},
    )
    assert [r["key"] for r in lab._loop_rows(run)] == ["ok"]


def test_loop_rows_report_no_dropped_when_a_draft_was_never_requirement_checked(lab, tmp_path):
    drafts = [_dd(1, requirements=["R1"]), _dd(2, requirements=None)]
    run = _write_run(tmp_path, [_result("a")], {"a": drafts})
    (row,) = lab._loop_rows(run)
    assert row["revisions"][0]["dropped"] == []


def test_loop_summary_counts_the_regressions(lab, tmp_path):
    long_ = " ".join(["word"] * 360)
    run = _write_run(
        tmp_path,
        [
            _result("a", final_version=1, status="budget_stopped"),
            _result("b", final_version=2, status="done"),
            _result("c", status="done"),
        ],
        {
            "a": [_dd(1, claims=True, requirements=True), _dd(2, long_, claims=False, requirements=["R1"]),
                  _dd(3, long_, claims=False, requirements=["R1"])],
            "b": [_dd(1, claims=True, requirements=["R1"]), _dd(2, claims=True, requirements=True)],
            "c": [_dd(1, claims=True, requirements=True)],
        },
    )
    rows = lab._loop_rows(run)
    s = lab._loop_summary(rows)
    assert s["letters"] == 3
    assert s["revisions"] == 3  # a: 2, b: 1, c: 0
    assert s["not_latest"] == 1  # a returned v1 of 3
    assert s["hit_cap"] == 1
    assert s["dropped"] == 1  # a's v1->v2 newly misses R1
    assert s["over_length"] == 1
    assert s["broke_claims"] == 1
    assert s["clean_on_1"] == 1  # only c: one draft and tool_checks all true
    assert s["cost"] == pytest.approx(0.1) and s["seconds"] == pytest.approx(10)


def test_loop_summary_of_nothing_does_not_divide_by_zero(lab):
    s = lab._loop_summary([])
    assert s["letters"] == 0 and s["revisions"] == 0 and s["changed_pct"] == 0
