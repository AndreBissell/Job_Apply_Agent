"""Tests for the Phase 7a cover-letter agent: the loop in app/llm/letter/agent.py, the tool
registry and its gates (registry.py), the run opening/ending shared with the workflow
(outcome.py), the new runner helpers (record_step, persist), the guardrails additions
(can_check, out_of_drafts) and the agent helpers in scripts/letter_lab.py.

No LLM and no network. The seven tools the registry builds are replaced by scripted fakes
that mutate the LetterState the way the real ones do (``Fakes``), and the orchestrator's
``complete_tools`` is replaced by ``Orch``: a scripted list of ToolSteps, an auto-pilot
that picks the step the fixed workflow would, or both. So what is under test is the loop:
gates and refusals, limits, what each ending returns and what gets logged.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.llm.client import BudgetExceededError, DailyQuotaError, LLMError, ToolSpec, ToolStep
from app.llm.letter import agent, guardrails, outcome, registry, workflow
from app.llm.letter.outcome import conclude, leave_out_gaps, open_run
from app.llm.letter.runner import REFUSED, ToolError, execute_tool, persist, record_step, start_run
from app.llm.letter.state import Check, Claim, JobInfo, LetterState, Requirement
from app.models import (
    Experience,
    JobListing,
    LetterRun,
    LetterRunStep,
    LlmUsage,
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


def _draft(state, claims=True, requirements=True, style=True):
    """Add a draft and record its checks (True/False, or None = never ran)."""
    d = state.add_draft(f"draft {len(state.drafts) + 1}", [Claim(text="c", source="skill:7")])
    for key, outcome_ in (("claims", claims), ("requirements", requirements), ("style", style)):
        if outcome_ is not None:
            state.record_check(key, Check(passed=bool(outcome_), issues=[] if outcome_ else [f"{key} broke"]))
    return d


def _run_row(db, run_id) -> LetterRun:
    db.expire_all()
    return db.get(LetterRun, run_id)


def _steps(db, run_id) -> list[LetterRunStep]:
    db.expire_all()
    return list(db.scalars(select(LetterRunStep).where(LetterRunStep.run_id == run_id).order_by(LetterRunStep.seq)))


def _tools_run(steps) -> list[str]:
    """Tool names of the steps that actually ran a tool (no refusals, no finish)."""
    return [s.tool for s in steps if not (s.error or "").startswith(REFUSED) and s.tool != "finish"]


CHECK_CALLS = ["check_claims", "check_requirements", "style_lint"]
CLEAN_CALLS = ["analyze_job", "match_profile", "generate_letter", *CHECK_CALLS]
_TOOL_OF_CHECK = {v: k for k, v in guardrails.CHECK_TOOLS.items()}


# --- scripted tool fakes ----------------------------------------------------
_DEFAULT_REQS = (
    dict(id="R1", text="Build REST APIs", importance="essential", letter_role="headline"),
    dict(id="R2", text="SQL databases", importance="essential", letter_role="mention"),
)


class Fakes:
    """Stand-ins for the seven tool functions the registry builds.

    ``plan`` maps a draft version to what each check does on it:
        {1: {"claims": True, "requirements": ["R1"], "style": False}}
    claims/style: True (pass), False (fail) or an Exception instance to raise;
    requirements: True, False, a list of uncovered ids, or an Exception.
    A version missing from the plan passes everything. generate/revise apply the same
    guardrail the real tools apply and raise ToolError.
    """

    def __init__(self, plan=None, reqs=_DEFAULT_REQS, match=None):
        self.plan = plan or {}
        self.reqs = [dict(r) for r in reqs]
        self.match = match or {}  # id -> (status, evidence); default: supported
        self.calls: list[str] = []

    def analyze_job(self, state, ctx):
        self.calls.append("analyze_job")
        state.requirements = [Requirement(**r) for r in self.reqs]
        return {"requirements": len(state.requirements)}

    def match_profile(self, state, ctx):
        self.calls.append("match_profile")
        for r in state.requirements:
            r.status, r.evidence = self.match.get(r.id, ("supported", ["experience:12"]))
        return {"matched": len(state.requirements)}

    def generate_letter(self, state, ctx):
        self.calls.append("generate_letter")
        refusal = guardrails.can_generate(state)
        if refusal:
            raise ToolError(refusal)
        state.add_draft("draft 1", [Claim(text="c", source="skill:7")])
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
            result = self.plan.get(version, {}).get(key, True)
            if isinstance(result, Exception):
                raise result
            if isinstance(result, list):
                check = Check(passed=False, issues=[f"{i} not addressed: 'x'" for i in result])
            else:
                check = Check(passed=bool(result), issues=[] if result else [f"{key} broke on v{version}"])
            state.record_check(key, check)
            return {"passed": check.passed}
        return fn

    def install(self, monkeypatch):
        monkeypatch.setattr(registry, "analyze_job", self.analyze_job)
        monkeypatch.setattr(registry, "match_profile", self.match_profile)
        monkeypatch.setattr(registry, "generate_letter", self.generate_letter)
        monkeypatch.setattr(registry, "revise_letter", self.revise_letter)
        monkeypatch.setattr(registry, "check_claims", self._check("check_claims", "claims"))
        monkeypatch.setattr(registry, "check_requirements", self._check("check_requirements", "requirements"))
        monkeypatch.setattr(registry, "style_lint", self._check("style_lint", "style"))
        return self


class Orch:
    """A fake for ``agent.complete_tools``.

    ``script`` is a list of ToolSteps (or exception instances to raise) consumed in order.
    When it runs out, ``autopilot=True`` picks the step the fixed workflow would from the
    run's state; otherwise the fake raises AssertionError (the loop should have stopped).
    ``cost`` inserts an ``llm_usage`` row for the run on every call, like a real call does.
    ``tweak`` is called on the run's fresh state, to lower a limit.
    """

    def __init__(self, script=None, autopilot=False, cost=None, tweak=None, max_calls=60):
        self.script = list(script or [])
        self.autopilot = autopilot
        self.cost = Decimal(str(cost)) if cost is not None else None
        self.tweak = tweak
        self.max_calls = max_calls
        self.calls: list[dict] = []
        self.state: LetterState | None = None
        self.ctx = None
        self.db = None

    def install(self, monkeypatch, db):
        self.db = db
        monkeypatch.setattr(agent, "complete_tools", self)
        real_open = agent.open_run

        def open_run_(*a, **kw):
            state, ctx = real_open(*a, **kw)
            self.state, self.ctx = state, ctx
            if self.tweak:
                self.tweak(state)
            return state, ctx

        monkeypatch.setattr(agent, "open_run", open_run_)
        return self

    def prompts(self) -> list[str]:
        return [c["messages"][0]["content"] for c in self.calls]

    def pick(self) -> str:
        """What the fixed workflow would do next, from the state."""
        s = self.state
        if not s.requirements:
            return "analyze_job"
        if any(r.status == "unknown" for r in s.requirements):
            return "match_profile"
        if s.pending_gaps():
            return "ask_user"
        if not s.drafts:
            return "generate_letter"
        missing = guardrails.checks_not_run(s)
        if missing:
            return _TOOL_OF_CHECK[missing[0]]
        if guardrails.can_finish(s) is None:
            return "finish"
        if len(s.drafts) < s.budget.max_drafts:
            return "revise_letter"
        return "finish"

    def __call__(self, system_prompt, messages, tools, **kw):
        self.calls.append({"system": system_prompt, "messages": copy.deepcopy(messages), "tools": tools, "kw": kw})
        if self.cost is not None:
            self.db.add(LlmUsage(task="orchestrate", tier="mid", model="fake", cost_usd=self.cost,
                                 run_id=kw["run_id"]))
            self.db.commit()
        if self.script:
            item = self.script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        assert self.autopilot, "the orchestrator was called after the loop should have stopped"
        assert len(self.calls) <= self.max_calls, "auto-pilot is looping"
        return ToolStep(tool=self.pick())


def _t(name: str) -> ToolStep:
    return ToolStep(tool=name)


def _go(db, monkeypatch, *, fakes=None, orch=None, **kw):
    """Install fakes + orchestrator (defaults: everything passes, auto-pilot) and run."""
    fakes = fakes or Fakes()
    fakes.install(monkeypatch)
    orch = orch or Orch(autopilot=True)
    orch.install(monkeypatch, db)
    res = agent.run_agent(db, 5, 1, **kw)
    return res, fakes, orch


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------
NON_FINISH = ["analyze_job", "match_profile", "ask_user", "generate_letter",
              "check_claims", "check_requirements", "style_lint", "revise_letter"]


def test_registry_has_exactly_the_nine_tools_with_matching_specs():
    reg = registry.build_registry(leave_out_gaps)
    assert list(reg) == [*NON_FINISH, "finish"]
    for name, tool in reg.items():
        assert isinstance(tool.spec, ToolSpec) and tool.spec.name == name and tool.name == name
        assert (tool.fn is None) == (name == "finish")


def test_every_working_tool_description_says_when_not_to_use_it():
    reg = registry.build_registry(leave_out_gaps)
    for name in NON_FINISH:
        d = reg[name].spec.description.lower()
        assert "do not" in d or "never" in d, name


@pytest.mark.parametrize("name", ["check_claims", "match_profile", "revise_letter"])
def test_descriptions_say_a_listed_skill_is_weak_evidence(name):
    d = registry.build_registry(leave_out_gaps)[name].spec.description.lower()
    assert "lists" in d or "listed" in d


def test_analyze_gate():
    gate = registry.build_registry(leave_out_gaps)["analyze_job"].gate
    assert gate(_state([])) is None
    assert "already analysed" in gate(_state())


def test_match_gate():
    gate = registry.build_registry(leave_out_gaps)["match_profile"].gate
    assert "analyze_job" in gate(_state([]))
    assert gate(_state([_req("R1", status="unknown"), _req("R2")])) is None
    assert "already matched" in gate(_state())  # all supported


def test_ask_user_gate():
    gate = registry.build_registry(leave_out_gaps)["ask_user"].gate
    assert "analyze_job" in gate(_state([]))
    assert "match_profile" in gate(_state([_req("R1", status="unknown")]))
    assert "nothing to ask" in gate(_state())  # no pending gap
    gap = _state([_req("R1", status="gap")])
    assert gate(gap) is None
    gap.user_questions.append({"status": "open"})
    assert "already out" in gate(gap)


def test_ask_user_gate_does_not_block_on_a_closed_question():
    state = _state([_req("R1", status="gap")])
    state.user_questions.append({"status": "answered"})
    assert registry.build_registry(leave_out_gaps)["ask_user"].gate(state) is None


def test_generate_gate_is_can_generate():
    gate = registry.build_registry(leave_out_gaps)["generate_letter"].gate
    assert gate(_state()) is None
    gap = _state([_req("R1", status="gap")])
    assert "ask_user" in gate(gap) and gate(gap) == guardrails.can_generate(gap)
    drafted = _state()
    _draft(drafted)
    assert "already exists" in gate(drafted) and gate(drafted) == guardrails.can_generate(drafted)


@pytest.mark.parametrize("tool", CHECK_CALLS)
def test_check_gates_run_once_per_draft(tool):
    gate = registry.build_registry(leave_out_gaps)[tool].gate
    state = _state()
    assert "no draft" in gate(state)
    state.add_draft("t")
    assert gate(state) is None
    state.record_check(guardrails.CHECK_TOOLS[tool], Check(passed=True))
    msg = gate(state)
    assert tool in msg and "draft 1" in msg
    state.add_draft("t2")
    assert gate(state) is None


def test_revise_gate_is_can_revise():
    gate = registry.build_registry(leave_out_gaps)["revise_letter"].gate
    state = _state()
    _draft(state, claims=False)
    assert gate(state) is None
    assert gate(state) == guardrails.can_revise(state)
    clean = _state()
    _draft(clean)
    assert "nothing to revise" in gate(clean)


# --- ask_user_tool -----------------------------------------------------------
def test_ask_user_tool_summarises_what_the_policy_decided():
    state = _state([_req("R1", status="gap"), _req("R2", status="gap", role="mention"), _req("R3")])
    fn = registry.ask_user_tool(leave_out_gaps)
    summary = fn(state, None)
    assert summary == {"asked_about": ["R1", "R2"], "decided": {"R1": "leave_out", "R2": "leave_out"},
                       "waiting_on_user": False}
    assert state.pending_gaps() == []


def test_ask_user_tool_reports_a_policy_that_opens_a_question():
    state = _state([_req("R1", status="gap")])

    def ask(state, ctx):
        state.user_questions.append({"requirement": "R1", "status": "open"})

    summary = registry.ask_user_tool(ask)(state, None)
    assert summary["waiting_on_user"] is True
    assert summary["asked_about"] == ["R1"] and summary["decided"] == {}


# ---------------------------------------------------------------------------
# guardrails additions
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tool", CHECK_CALLS)
def test_can_check(tool):
    state = _state()
    assert "no draft to check yet" in guardrails.can_check(state, tool) and tool in guardrails.can_check(state, tool)
    state.add_draft("t")
    assert guardrails.can_check(state, tool) is None
    state.record_check(guardrails.CHECK_TOOLS[tool], Check(passed=False, issues=["x"]))
    msg = guardrails.can_check(state, tool)
    assert tool in msg and "draft 1" in msg and "FAIL" in msg
    state.add_draft("t2")
    assert guardrails.can_check(state, tool) is None


def test_out_of_drafts():
    state = _state()
    assert guardrails.out_of_drafts(state) is False  # no drafts
    _draft(state, claims=False)
    _draft(state, claims=False)
    assert len(state.drafts) < state.budget.max_drafts
    assert guardrails.out_of_drafts(state) is False  # below the cap

    unchecked = _state()
    _draft(unchecked, claims=False)
    _draft(unchecked, claims=False)
    _draft(unchecked, claims=False, requirements=None)  # at the cap, a check hasn't run
    assert guardrails.out_of_drafts(unchecked) is False

    passed = _state()
    for _ in range(3):
        _draft(passed)
    assert guardrails.out_of_drafts(passed) is False  # at the cap, all passed

    failed = _state()
    for _ in range(3):
        _draft(failed, style=False)
    assert guardrails.out_of_drafts(failed) is True


def test_can_revise_names_the_tools_to_run_not_the_check_keys():
    state = _state()
    _draft(state, claims=True, requirements=None, style=None)
    assert guardrails.can_revise(state) == "run check_requirements, style_lint on draft 1 before revising it"
    only_claims_missing = _state()
    _draft(only_claims_missing, claims=None, requirements=False, style=True)
    assert guardrails.can_revise(only_claims_missing) == "run check_claims on draft 1 before revising it"


def test_check_tools_map_to_the_required_check_keys():
    assert set(guardrails.CHECK_TOOLS.values()) == set(("claims", "requirements", "style"))


# ---------------------------------------------------------------------------
# runner: record_step, persist
# ---------------------------------------------------------------------------
def _started(db):
    state = _state()
    return state, start_run(db, 9, "agent", state)


def test_record_step_refused_logs_the_reason_and_costs_no_tool_call(db):
    state, ctx = _started(db)
    execute_tool(ctx, state, "analyze_job", lambda s, c: {"ok": 1})
    assert state.budget.tool_calls == 1
    record_step(ctx, state, "analyze_job", {"x": 1}, refused="already analysed")
    steps = _steps(db, ctx.run.id)
    assert [s.seq for s in steps] == [1, 2]
    assert steps[1].tool == "analyze_job" and steps[1].error == "refused: already analysed"
    assert json.loads(steps[1].args) == {"x": 1}
    assert state.budget.tool_calls == 1
    assert _run_row(db, ctx.run.id).tool_calls == 1


def test_record_step_with_a_summary_logs_it_without_an_error(db):
    state, ctx = _started(db)
    record_step(ctx, state, "finish", summary={"accepted": True})
    (step,) = _steps(db, ctx.run.id)
    assert json.loads(step.result_summary) == {"accepted": True}
    assert step.error is None and step.args is None
    assert state.budget.tool_calls == 0


def test_persist_refreshes_the_cost_from_llm_usage_rows_for_this_run(db):
    state, ctx = _started(db)
    db.add_all([
        LlmUsage(task="orchestrate", tier="mid", model="m", cost_usd=Decimal("0.10"), run_id=ctx.run.id),
        LlmUsage(task="check_claims", tier="mid", model="m", cost_usd=Decimal("0.025"), run_id=ctx.run.id),
        LlmUsage(task="orchestrate", tier="mid", model="m", cost_usd=Decimal("9"), run_id=ctx.run.id + 100),
    ])
    db.commit()
    assert state.budget.cost_usd == 0
    persist(ctx, state)
    assert state.budget.cost_usd == pytest.approx(0.125)
    assert float(_run_row(db, ctx.run.id).cost_usd) == pytest.approx(0.125)
    assert json.loads(_run_row(db, ctx.run.id).state)["budget"]["cost_usd"] == pytest.approx(0.125)


# ---------------------------------------------------------------------------
# outcome
# ---------------------------------------------------------------------------
def test_open_run_raises_for_a_missing_job(db):
    with pytest.raises(ValueError, match="not found"):
        open_run(db, 404, 1, "agent")
    assert db.scalars(select(LetterRun)).all() == []


def test_open_run_raises_for_a_job_with_no_match_for_the_profile(db):
    with pytest.raises(ValueError, match="no match"):
        open_run(db, 5, 2, "agent")
    assert db.scalars(select(LetterRun)).all() == []


def test_open_run_creates_a_running_row_and_a_state_for_the_job(db):
    state, ctx = open_run(db, 5, 1, "agent-x")
    row = _run_row(db, ctx.run.id)
    assert row.engine == "agent-x" and row.status == "running" and row.match_id == 9
    assert state.job.job_id == 5 and state.job.title == "Engineer" and state.job.company == "LogiCo"
    assert state.profile_id == 1 and state.drafts == []


def test_conclude_done_with_a_clean_latest_draft(db):
    state, ctx = open_run(db, 5, 1, "agent")
    _draft(state, claims=False)
    _draft(state)
    res = conclude(ctx, state, "done")
    assert res.status == "done" and res.clean is True and res.open_issues == []
    assert res.draft.version == 2 and res.stop_reason is None and res.account_limit is None
    row = _run_row(db, ctx.run.id)
    assert row.status == "done" and row.final_draft_version == 2 and row.finished_at is not None


def test_conclude_budget_stopped_returns_the_better_earlier_draft(db):
    state, ctx = open_run(db, 5, 1, "agent")
    _draft(state, claims=True, style=False)
    _draft(state, claims=False)
    res = conclude(ctx, state, "budget_stopped", "draft limit")
    assert res.status == "budget_stopped" and res.draft.version == 1  # claims passed beats claims failed
    assert res.clean is False and res.open_issues and res.stop_reason == "draft limit"
    assert _run_row(db, ctx.run.id).final_draft_version == 1


def test_conclude_turns_done_with_a_non_clean_draft_into_failed(db):
    state, ctx = open_run(db, 5, 1, "agent")
    _draft(state, style=False)
    res = conclude(ctx, state, "done")
    assert res.status == "failed" and res.clean is False
    assert "without a clean draft" in res.stop_reason
    assert _run_row(db, ctx.run.id).status == "failed"


def test_conclude_without_drafts(db):
    state, ctx = open_run(db, 5, 1, "agent")
    res = conclude(ctx, state, "budget_stopped", "nothing happened")
    assert res.draft is None and res.clean is False and res.text is None and res.open_issues == []
    assert _run_row(db, ctx.run.id).final_draft_version is None


def test_conclude_passes_the_account_limit_through(db):
    state, ctx = open_run(db, 5, 1, "agent")
    _draft(state)
    res = conclude(ctx, state, "budget_stopped", "BudgetExceededError: cap", "cap")
    assert res.account_limit == "cap"


@pytest.mark.parametrize("status,finished", [
    ("done", True), ("budget_stopped", True), ("failed", True), ("waiting_user", False),
])
def test_conclude_sets_finished_at_except_while_waiting_on_the_user(db, status, finished):
    state, ctx = open_run(db, 5, 1, "agent")
    _draft(state)
    conclude(ctx, state, status)
    assert (_run_row(db, ctx.run.id).finished_at is not None) is finished


def test_the_workflow_reuses_the_shared_result_and_policy():
    assert workflow.WorkflowResult is outcome.LetterResult
    assert workflow.leave_out_gaps is outcome.leave_out_gaps


# ---------------------------------------------------------------------------
# agent loop: happy paths
# ---------------------------------------------------------------------------
def test_happy_path_runs_the_workflow_steps_then_finish(db, monkeypatch):
    res, fakes, orch = _go(db, monkeypatch)
    assert res.status == "done" and res.clean is True and res.draft.version == 1
    assert res.open_issues == [] and res.stop_reason is None and res.account_limit is None
    assert fakes.calls == CLEAN_CALLS
    steps = _steps(db, res.run_id)
    assert [s.tool for s in steps] == [*CLEAN_CALLS, "finish"]
    assert all(s.error is None for s in steps)
    assert json.loads(steps[-1].result_summary) == {"accepted": True}
    assert res.state.budget.tool_calls == 6  # finish is not a tool call
    row = _run_row(db, res.run_id)
    assert row.engine == "agent" and row.status == "done" and row.tool_calls == 6
    assert row.final_draft_version == 1 and row.finished_at is not None
    assert len(orch.calls) == 7


def test_one_revision_then_clean(db, monkeypatch):
    res, fakes, _ = _go(db, monkeypatch, fakes=Fakes({1: {"style": False}}))
    assert res.status == "done" and res.clean and res.draft.version == 2
    assert fakes.calls == CLEAN_CALLS + ["revise_letter"] + CHECK_CALLS
    assert res.state.budget.tool_calls == 10


def test_draft_limit_finishes_with_the_best_draft_flagged(db, monkeypatch):
    res, fakes, _ = _go(db, monkeypatch, fakes=Fakes({v: {"claims": False} for v in (1, 2, 3)}))
    assert res.status == "budget_stopped"
    assert "draft limit reached" in res.stop_reason and "claims" in res.stop_reason
    assert fakes.calls.count("revise_letter") == 2 and len(res.state.drafts) == 3
    assert res.clean is False and res.open_issues and res.draft is not None
    assert res.state.budget.tool_calls == 14
    steps = _steps(db, res.run_id)
    assert steps[-1].tool == "finish" and steps[-1].error is None
    assert json.loads(steps[-1].result_summary)["out_of_drafts"] is True
    row = _run_row(db, res.run_id)
    assert row.status == "budget_stopped" and row.finished_at is not None
    assert row.final_draft_version == res.draft.version


# ---------------------------------------------------------------------------
# agent loop: refusals
# ---------------------------------------------------------------------------
def test_a_repeated_tool_call_is_refused_and_the_reason_goes_into_the_next_prompt(db, monkeypatch):
    script = [_t("analyze_job"), _t("analyze_job"), _t("match_profile"), _t("generate_letter"),
              _t("check_claims"), _t("check_claims"), _t("check_requirements"), _t("style_lint"), _t("finish")]
    res, fakes, orch = _go(db, monkeypatch, orch=Orch(script))
    assert res.status == "done" and res.clean
    assert fakes.calls == CLEAN_CALLS  # the refused calls never reached the tools
    steps = _steps(db, res.run_id)
    refused = [s for s in steps if (s.error or "").startswith("refused: ")]
    assert [s.tool for s in refused] == ["analyze_job", "check_claims"]
    assert len(steps) == 9 and res.state.budget.tool_calls == 6
    prompts = orch.prompts()
    assert "REFUSED" in prompts[2] and "already analysed" in prompts[2]  # the turn after the 2nd analyze_job
    assert "REFUSED" in prompts[6] and "check_claims already ran on draft 1" in prompts[6]
    assert "REFUSED" not in prompts[3]
    assert "analyze_job(refused)" in prompts[2]


def test_finish_before_the_checks_ran_is_refused_with_the_guardrail_reason(db, monkeypatch):
    script = [_t("analyze_job"), _t("match_profile"), _t("generate_letter"), _t("finish")]
    res, fakes, orch = _go(db, monkeypatch, orch=Orch(script, autopilot=True))
    assert res.status == "done" and res.clean
    steps = _steps(db, res.run_id)
    early = steps[3]
    assert early.tool == "finish" and early.error.startswith("refused: ")
    assert "claims, requirements, style not checked on draft 1" in early.error
    assert "REFUSED" in orch.prompts()[4] and "not checked on draft 1" in orch.prompts()[4]
    assert steps[-1].tool == "finish" and steps[-1].error is None  # the real one, later


def test_too_many_refusals_stop_the_run(db, monkeypatch):
    orch = Orch([_t("finish")] * 6)
    res, fakes, _ = _go(db, monkeypatch, orch=orch)
    assert agent.MAX_REFUSALS == 4
    assert res.status == "budget_stopped" and "too many refused calls" in res.stop_reason
    assert len(orch.calls) == agent.MAX_REFUSALS + 1 == 5
    assert len(orch.script) == 1  # the sixth was never asked for
    steps = _steps(db, res.run_id)
    assert len(steps) == 5 and all(s.error.startswith("refused: ") for s in steps)
    assert fakes.calls == [] and res.draft is None
    assert _run_row(db, res.run_id).status == "budget_stopped"


def test_a_text_reply_and_an_unknown_tool_each_count_as_a_refusal(db, monkeypatch):
    orch = Orch([ToolStep(text="I think we should start"), _t("write_poem")], autopilot=True)
    res, fakes, _ = _go(db, monkeypatch, orch=orch)
    assert res.status == "done"
    steps = _steps(db, res.run_id)
    assert steps[0].tool == "(text)" and steps[0].error.startswith("refused: ")
    assert "text reply" in steps[0].error and "call one of" in steps[0].error
    assert steps[1].tool == "write_poem" and "unknown tool 'write_poem'" in steps[1].error
    assert res.state.budget.tool_calls == 6  # refusals cost no tool call
    assert fakes.calls == CLEAN_CALLS
    assert "REFUSED" in orch.prompts()[1] and "REFUSED" in orch.prompts()[2]


def test_refusals_over_the_cap_count_text_replies_too(db, monkeypatch):
    orch = Orch([ToolStep(text="hmm")] * 5)
    res, _, _ = _go(db, monkeypatch, orch=orch)
    assert res.status == "budget_stopped" and "too many refused calls" in res.stop_reason
    assert len(orch.calls) == 5


# ---------------------------------------------------------------------------
# agent loop: limits
# ---------------------------------------------------------------------------
def test_tool_call_limit_stops_the_run_before_another_orchestrator_call(db, monkeypatch):
    orch = Orch(autopilot=True, tweak=lambda s: setattr(s.budget, "max_tool_calls", 4))
    res, fakes, _ = _go(db, monkeypatch, orch=orch)
    assert res.status == "budget_stopped" and "tool-call limit" in res.stop_reason
    assert len(orch.calls) == 4  # not asked again once the limit was hit
    assert fakes.calls == ["analyze_job", "match_profile", "generate_letter", "check_claims"]
    assert res.clean is False and res.draft.version == 1 and res.account_limit is None
    assert any("not checked" in i for i in res.open_issues)


def test_hitting_the_limit_with_a_clean_draft_ends_done_with_a_code_finish(db, monkeypatch):
    orch = Orch(autopilot=True, tweak=lambda s: setattr(s.budget, "max_tool_calls", 6))
    res, fakes, _ = _go(db, monkeypatch, orch=orch)
    assert res.status == "done" and res.clean and res.stop_reason is None
    assert len(orch.calls) == 6 and fakes.calls == CLEAN_CALLS
    steps = _steps(db, res.run_id)
    assert steps[-1].tool == "finish" and steps[-1].error is None
    summary = json.loads(steps[-1].result_summary)
    assert summary["by"] == "code" and "tool-call limit" in summary["reason"]


def test_orchestrator_spend_counts_towards_the_run_budget(db, monkeypatch):
    orch = Orch(autopilot=True, cost=0.30, tweak=lambda s: setattr(s.budget, "max_cost_usd", 0.50))
    res, fakes, _ = _go(db, monkeypatch, orch=orch)
    assert res.status == "budget_stopped" and "run budget reached" in res.stop_reason
    assert len(orch.calls) == 2  # stopped before a third
    assert res.state.budget.cost_usd == pytest.approx(0.60)
    assert float(_run_row(db, res.run_id).cost_usd) == pytest.approx(0.60)
    assert fakes.calls == ["analyze_job", "match_profile"]


# ---------------------------------------------------------------------------
# agent loop: failures
# ---------------------------------------------------------------------------
def test_a_tool_that_raises_fails_the_run_and_returns_the_best_draft_so_far(db, monkeypatch):
    res, fakes, _ = _go(db, monkeypatch, fakes=Fakes({1: {"claims": RuntimeError("judge died")}}))
    assert res.status == "failed"
    assert "check_claims failed" in res.stop_reason and "judge died" in res.stop_reason
    assert res.draft is not None and res.draft.version == 1 and res.clean is False
    assert fakes.calls == [*CLEAN_CALLS[:3], "check_claims"]
    steps = _steps(db, res.run_id)
    assert steps[-1].tool == "check_claims" and "judge died" in steps[-1].error
    assert not steps[-1].error.startswith("refused")
    assert _run_row(db, res.run_id).status == "failed"


@pytest.mark.parametrize("exc", [BudgetExceededError("daily cap"), DailyQuotaError("daily cap")])
def test_an_account_limit_in_a_tool_stops_the_run_and_is_reported(db, monkeypatch, exc):
    res, _, _ = _go(db, monkeypatch, fakes=Fakes({1: {"claims": exc}}))
    assert res.status == "budget_stopped"
    assert "daily cap" in res.account_limit and type(exc).__name__ in res.stop_reason
    assert res.draft is not None and res.clean is False


@pytest.mark.parametrize("exc", [BudgetExceededError("daily cap"), DailyQuotaError("daily cap")])
def test_an_account_limit_in_the_orchestrator_stops_the_run_and_is_reported(db, monkeypatch, exc):
    res, fakes, orch = _go(db, monkeypatch, orch=Orch([_t("analyze_job"), exc]))
    assert res.status == "budget_stopped"
    assert "daily cap" in res.account_limit and type(exc).__name__ in res.stop_reason
    assert fakes.calls == ["analyze_job"] and res.draft is None


def test_an_orchestrator_llm_error_fails_the_run(db, monkeypatch):
    res, _, _ = _go(db, monkeypatch, orch=Orch([LLMError("boom")]))
    assert res.status == "failed" and "orchestrator" in res.stop_reason and "boom" in res.stop_reason
    assert res.account_limit is None and res.draft is None


def test_an_unexpected_exception_propagates_and_marks_the_run_failed(db, monkeypatch):
    fakes = Fakes().install(monkeypatch)
    Orch([_t("analyze_job"), KeyError("bug")]).install(monkeypatch, db)
    with pytest.raises(KeyError):
        agent.run_agent(db, 5, 1)
    (run,) = db.scalars(select(LetterRun)).all()
    assert run.status == "failed" and run.finished_at is not None
    assert fakes.calls == ["analyze_job"]


def test_missing_job_raises_and_creates_no_run(db, monkeypatch):
    Fakes().install(monkeypatch)
    orch = Orch().install(monkeypatch, db)
    with pytest.raises(ValueError, match="not found"):
        agent.run_agent(db, 404, 1)
    with pytest.raises(ValueError, match="no match"):
        agent.run_agent(db, 5, 2)
    assert db.scalars(select(LetterRun)).all() == [] and orch.calls == []


# ---------------------------------------------------------------------------
# agent loop: gap policy
# ---------------------------------------------------------------------------
_GAP = {"R1": ("gap", [])}  # an essential headline gap


def test_a_must_have_gap_goes_through_ask_user_and_the_default_policy_leaves_it_out(db, monkeypatch):
    res, fakes, _ = _go(db, monkeypatch, fakes=Fakes(match=_GAP))
    assert res.status == "done" and res.clean
    assert res.state.requirement("R1").user_decision.choice == "leave_out"
    steps = _steps(db, res.run_id)
    assert [s.tool for s in steps][:4] == ["analyze_job", "match_profile", "ask_user", "generate_letter"]
    ask = steps[2]
    assert ask.error is None
    assert json.loads(ask.result_summary) == {"asked_about": ["R1"], "decided": {"R1": "leave_out"},
                                              "waiting_on_user": False}
    assert res.state.budget.tool_calls == 7


def test_generate_before_ask_user_is_refused_naming_ask_user(db, monkeypatch):
    orch = Orch([_t("analyze_job"), _t("match_profile"), _t("generate_letter")], autopilot=True)
    res, fakes, _ = _go(db, monkeypatch, fakes=Fakes(match=_GAP), orch=orch)
    assert res.status == "done"
    refused = _steps(db, res.run_id)[2]
    assert refused.tool == "generate_letter" and refused.error.startswith("refused: ")
    assert "ask_user" in refused.error and "R1" in refused.error
    assert "ask_user" in orch.prompts()[3] and "REFUSED" in orch.prompts()[3]
    assert fakes.calls.count("generate_letter") == 1


def test_a_policy_that_opens_a_question_leaves_the_run_waiting_on_the_user(db, monkeypatch):
    def ask(state, ctx):
        state.user_questions.append({"requirement": "R1", "status": "open"})

    res, fakes, _ = _go(db, monkeypatch, fakes=Fakes(match=_GAP), gap_policy=ask)
    assert res.status == "waiting_user"
    assert res.draft is None and res.clean is False and res.open_issues == []
    assert "generate_letter" not in fakes.calls
    row = _run_row(db, res.run_id)
    assert row.status == "waiting_user" and row.finished_at is None and row.final_draft_version is None
    assert _steps(db, res.run_id)[-1].tool == "ask_user"


# ---------------------------------------------------------------------------
# agent loop: the turn prompt
# ---------------------------------------------------------------------------
def test_turn_prompt_has_the_state_the_steps_and_the_last_result():
    state = _state()
    text = agent.turn_prompt(state, ["analyze_job", "match_profile"], "match_profile -> {}")
    assert state.summary_for_orchestrator() in text
    assert "STEPS SO FAR: analyze_job, match_profile" in text
    assert "LAST RESULT: match_profile -> {}" in text
    assert "STEPS SO FAR: none" in agent.turn_prompt(state, [], "x")


def test_each_turn_is_stateless_and_carries_the_previous_tools_summary(db, monkeypatch):
    res, _, orch = _go(db, monkeypatch)
    assert len(orch.calls) == 7
    first, second = orch.prompts()[0], orch.prompts()[1]
    assert "STEPS SO FAR: none" in first and "LAST RESULT: none yet" in first
    assert "STATE" in first and "REQUIREMENTS: not analysed yet" in first
    assert "STEPS SO FAR: analyze_job" in second
    assert f"LAST RESULT: analyze_job -> {json.dumps({'requirements': 2})}" in second
    for c in orch.calls:
        assert len(c["messages"]) == 1 and c["messages"][0]["role"] == "user"
        assert c["system"] == agent.SYSTEM_PROMPT
        assert [t.name for t in c["tools"]] == [*NON_FINISH, "finish"]
        kw = c["kw"]
        assert kw["tier"] == "mid" and kw["task"] == "orchestrate"
        assert kw["job_id"] == 5 and kw["match_id"] == 9 and kw["run_id"] == res.run_id
    assert "STEPS SO FAR: analyze_job, match_profile, generate_letter" in orch.prompts()[3]
    assert "BUDGET: tool calls 3/15" in orch.prompts()[3]


# ---------------------------------------------------------------------------
# scripts/letter_lab.py: agent helpers
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def lab():
    """Import scripts/letter_lab.py by path, then undo its env and sys.path edits."""
    saved_env = {k: os.environ.get(k) for k in ("DATABASE_URL", "APP_ENV")}
    saved_path = list(sys.path)
    try:
        spec = importlib.util.spec_from_file_location("letter_lab_agent_under_test", ROOT / "scripts" / "letter_lab.py")
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


def test_workflow_path_for_one_draft(lab):
    assert lab._workflow_path(1, False) == ["analyze_job", "match_profile", "generate_letter", *CHECK_CALLS]


def test_workflow_path_with_ask_user_and_two_revisions(lab):
    path = lab._workflow_path(3, True)
    assert path[:4] == ["analyze_job", "match_profile", "ask_user", "generate_letter"]
    assert path.count("revise_letter") == 2 and len(path) == 4 + 3 + 2 * 4
    assert path[7:] == ["revise_letter", *CHECK_CALLS, "revise_letter", *CHECK_CALLS]


def test_sorted_checks_ignores_the_order_within_a_draft_only(lab):
    base = ["generate_letter", "check_claims", "check_requirements", "style_lint"]
    shuffled = ["generate_letter", "style_lint", "check_claims", "check_requirements"]
    assert lab._sorted_checks(base) == lab._sorted_checks(shuffled)
    assert lab._sorted_checks(base) != lab._sorted_checks(base[:-1])  # a missing check
    assert lab._sorted_checks(base) != lab._sorted_checks(base + ["check_claims"])  # an extra one
    assert lab._sorted_checks(base) != lab._sorted_checks(["check_claims", "generate_letter",
                                                           "check_requirements", "style_lint"])  # across a step


def _row(title, tools, refused=(), *, orch_calls, orch_cost, cost=0.2):
    steps = [{"tool": t} for t in tools]
    for after, tool, why in refused:
        steps.insert(after, {"tool": tool, "refused": why})
    return {
        "title": title, "steps": steps, "per_draft": [{}],
        "orchestrator": {"calls": orch_calls, "cost_usd": orch_cost, "input_tokens": 1000, "output_tokens": 100},
        "cost_usd": cost, "input_tokens": 5000, "output_tokens": 800,
    }


def test_agent_section_reports_path_check_order_refusals_and_orchestrator_cost(lab):
    same = _row("Job A", [*CLEAN_CALLS, "finish"], orch_calls=7, orch_cost=0.02)
    reordered = _row(
        "Job B",
        ["analyze_job", "match_profile", "generate_letter", "style_lint", "check_claims",
         "check_requirements", "finish"],
        refused=[(5, "check_claims", "check_claims already ran on draft 1")],
        orch_calls=9, orch_cost=0.04,
    )
    text = "\n".join(lab._agent_section([same, reordered]))
    assert "| Same path as the workflow | 1/2 (+1 with only the check order changed) |" in text
    assert "| Different path | 0/2 |" in text
    assert "| Guardrail refusals | 1 in 1/2 runs |" in text
    assert "check_claims already ran on draft 1" in text
    assert "(check_claims)" in text  # the refusal is bracketed in the path
    assert "check order only" in text
    assert "| Orchestrator calls / letter | 8.0 |" in text
    assert "| Orchestrator cost / letter | $0.0300 (15% of the run cost) |" in text


def test_agent_section_flags_a_path_that_differs_from_the_workflow(lab):
    skipped = _row("Job C", ["analyze_job", "match_profile", "generate_letter", "check_claims", "finish"],
                   orch_calls=5, orch_cost=0.01)
    text = "\n".join(lab._agent_section([skipped]))
    assert "| Same path as the workflow | 0/1 (+0 " in text
    assert "| Different path | 1/1 |" in text and "**different**" in text
