"""Tests for Phase 7c's side outputs as the engines, preferences, API, view and lab see them
(the tools and rules themselves are in tests/test_letter_side_tools.py).

Covered here:
* the agent loop with the three side tools switched on or off (system prompt, tools offered,
  finish refused while one is due, once-only, a failing side tool, the tool-call cap, the USD
  cap, out of drafts, resuming a paused run),
* the fixed workflow running the same side outputs after the letter,
* ``engines.run_letter`` / ``production._run_pipeline`` handing the user's toggles on,
* ``preferences`` (the three toggles) and PUT/GET /profile/{id}/preferences,
* ``view.not_claimed`` / ``side_output_view`` / ``letter_info`` and GET /jobs/{id}/letter-info,
* the side-output sections of scripts/letter_lab.py.

No LLM, no network. The tools are replaced by scripted fakes that mutate the LetterState the way
the real ones do, and the orchestrator's ``complete_tools`` by ``Orch``. In-memory SQLite only.
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
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.api.main as main
import app.api.profile_ui as profile_ui
from app.db import Base
from app.llm.client import ToolStep
from app.llm.letter import agent, answers, engines, guardrails, production, registry, side_outputs, view, workflow
from app.llm.letter.gap_policy import ask_user_gaps
from app.llm.letter.runner import REFUSED, ToolError
from app.llm.letter.state import (
    SIDE_OUTPUT_TOOLS,
    Check,
    Claim,
    JobInfo,
    LetterState,
    Requirement,
    SideOutputs,
    UserDecision,
)
from app.models import (
    CoverLetter,
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
from app.preferences import DEFAULTS, SIDE_OUTPUT_TOGGLES, letter_settings, set_preferences
from tests.test_letter_production import FakeEngines

ROOT = Path(__file__).resolve().parent.parent
ALL = tuple(SIDE_OUTPUT_TOOLS)
SCREENING = ["Do you have Australian work rights?", "Describe a project you are proud of."]


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
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
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture()
def db(Session):
    session = Session()
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
        JobListing(id=5, source="seek", source_job_id="5", url="u", title="Engineer",
                   company="LogiCo", raw_description="We build logistics software."),
    ])
    session.flush()
    session.add(Match(id=9, user_id=1, job_id=5, score=80))
    session.commit()
    yield session
    session.close()


@pytest.fixture()
def client(Session, monkeypatch):
    async def _no_idle_loop():  # the real one would work on whatever SessionLocal points at
        return None

    monkeypatch.setattr(main, "_processing_idle_loop", _no_idle_loop)

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


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _run_row(db, run_id) -> LetterRun:
    db.expire_all()
    return db.get(LetterRun, run_id)


def _run_state(db, run_id) -> LetterState:
    return LetterState.model_validate_json(_run_row(db, run_id).state)


def _steps(db, run_id) -> list[LetterRunStep]:
    db.expire_all()
    return list(db.scalars(select(LetterRunStep).where(LetterRunStep.run_id == run_id).order_by(LetterRunStep.seq)))


def _names(steps) -> list[str]:
    return [s.tool for s in steps]


def _is_refused(step) -> bool:
    return (step.error or "").startswith(REFUSED)


CHECK_CALLS = ["check_claims", "check_requirements", "style_lint"]
CLEAN_CALLS = ["analyze_job", "match_profile", "generate_letter", *CHECK_CALLS]
NON_FINISH = ["analyze_job", "match_profile", "ask_user", "generate_letter",
              "check_claims", "check_requirements", "style_lint", "revise_letter"]
_TOOL_OF_CHECK = {v: k for k, v in guardrails.CHECK_TOOLS.items()}

_DEFAULT_REQS = (
    dict(id="R1", text="Build REST APIs", importance="essential", letter_role="headline"),
    dict(id="R2", text="SQL databases", importance="essential", letter_role="mention"),
)
_GAP_REQ = dict(id="R3", text="Kubernetes experience", importance="essential", letter_role="headline",
                skill="Kubernetes")


# --- scripted tool fakes ----------------------------------------------------
class Fakes:
    """Stand-ins for the letter tools, for the agent (patched on ``registry``) and for the
    workflow (patched on ``workflow``).

    ``questions``: the screening questions analyze_job puts on the job.
    ``gap``: add R3, a must-have gap the user already said no to on an earlier ad (match_profile
    leaves it out as remembered), i.e. a *confirmed* gap suggest_learning works from.
    ``plan``: draft version -> {"claims"/"requirements"/"style": True | False | Exception}.
    ``after_check``: tool name -> callable(state, ctx) run at the end of that check.
    """

    def __init__(self, plan=None, questions=(), gap=False, reqs=None, match=None, after_check=None):
        self.plan = plan or {}
        self.questions = list(questions)
        self.reqs = [dict(r) for r in (reqs if reqs is not None else _DEFAULT_REQS)]
        self.match = dict(match or {})
        if gap and reqs is None:
            self.reqs.append(dict(_GAP_REQ))
            self.match.setdefault("R3", ("gap", []))
        self.remember = {"R3"} if gap and reqs is None else set()
        self.after_check = after_check or {}
        self.calls: list[str] = []

    def analyze_job(self, state, ctx):
        self.calls.append("analyze_job")
        state.requirements = [Requirement(**r) for r in self.reqs]
        state.job.screening_questions = list(self.questions)
        return {"requirements": len(state.requirements)}

    def match_profile(self, state, ctx):
        self.calls.append("match_profile")
        for r in state.requirements:
            if r.status == "unknown":
                r.status, r.evidence = self.match.get(r.id, ("supported", ["experience:12"]))
            if r.id in self.remember and r.user_decision is None:
                r.user_decision = UserDecision(choice="leave_out", remembered=True)
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
            state.record_check(key, Check(passed=bool(result), issues=[] if result else [f"{key} broke on v{version}"]))
            if tool in self.after_check:
                self.after_check[tool](state, ctx)
            return {"passed": bool(result)}
        return fn

    def install_agent(self, monkeypatch):
        monkeypatch.setattr(registry, "analyze_job", self.analyze_job)
        monkeypatch.setattr(registry, "match_profile", self.match_profile)
        monkeypatch.setattr(registry, "generate_letter", self.generate_letter)
        monkeypatch.setattr(registry, "revise_letter", self.revise_letter)
        monkeypatch.setattr(registry, "check_claims", self._check("check_claims", "claims"))
        monkeypatch.setattr(registry, "check_requirements", self._check("check_requirements", "requirements"))
        monkeypatch.setattr(registry, "style_lint", self._check("style_lint", "style"))
        return self

    def install_workflow(self, monkeypatch):
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


class Sides:
    """Fakes for the three side tools, installed where the engines look them up at call time
    (``registry.<name>`` for the agent, ``workflow.<name>`` for the workflow).

    ``calls`` lists the executions in order; ``final_seen`` the draft version
    ``guardrails.letter_final`` pointed at when each ran; ``raises`` maps a tool to an exception.
    """

    def __init__(self, raises=None):
        self.raises = raises or {}
        self.calls: list[str] = []
        self.final_seen: dict[str, int | None] = {}

    def _fn(self, name):
        def fn(state, ctx):
            self.calls.append(name)
            final = guardrails.letter_final(state)
            self.final_seen[name] = final.version if final else None
            if name in self.raises:
                raise self.raises[name]
            so = state.side_outputs
            if name == "answer_screening":
                so.screening_answers = [{"question": q, "answer": "a", "verified": True}
                                        for q in state.job.screening_questions]
            elif name == "suggest_learning":
                so.learning_suggestions = [{"requirement_id": r.id} for r in guardrails.confirmed_gaps(state)]
            else:
                so.resume_notes = {"lead_with": [], "draft_version": final.version if final else None}
            return {"ok": name}
        return fn

    def install(self, monkeypatch):
        for name in SIDE_OUTPUT_TOOLS:
            monkeypatch.setattr(registry, name, self._fn(name))
            monkeypatch.setattr(workflow, name, self._fn(name))
        return self


class Orch:
    """A fake for ``agent.complete_tools``: a scripted list of ToolSteps (or exceptions), then
    an auto-pilot that picks what the fixed workflow would, calling each due side output just
    before it finishes. ``tweak`` is called on the run's fresh state to lower a limit."""

    def __init__(self, script=None, autopilot=False, tweak=None, max_calls=60):
        self.script = list(script or [])
        self.autopilot = autopilot
        self.tweak = tweak
        self.max_calls = max_calls
        self.calls: list[dict] = []
        self.state: LetterState | None = None

    def install(self, monkeypatch):
        monkeypatch.setattr(agent, "complete_tools", self)
        for attr in ("open_run", "reopen_run"):
            monkeypatch.setattr(agent, attr, self._capturing(getattr(agent, attr)))
        return self

    def _capturing(self, real):
        def wrapped(*a, **kw):
            state, ctx = real(*a, **kw)
            self.state = state
            if self.tweak:
                self.tweak(state)
            return state, ctx
        return wrapped

    def prompts(self) -> list[str]:
        return [c["messages"][0]["content"] for c in self.calls]

    def pick(self) -> str:
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
        if guardrails.can_finish(s) is not None and len(s.drafts) < s.budget.max_drafts:
            return "revise_letter"
        due = side_outputs.due(s)
        return due[0] if due else "finish"

    def __call__(self, system_prompt, messages, tools, **kw):
        self.calls.append({"system": system_prompt, "messages": copy.deepcopy(messages), "tools": tools, "kw": kw})
        if self.script:
            item = self.script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        assert self.autopilot, "the orchestrator was called after the loop should have stopped"
        assert len(self.calls) <= self.max_calls, "auto-pilot is looping"
        return ToolStep(tool=self.pick())


def _t(*names: str) -> list[ToolStep]:
    return [ToolStep(tool=n) for n in names]


def _full() -> Fakes:
    """A job with screening questions and a confirmed gap: every side output has work."""
    return Fakes(questions=SCREENING, gap=True)


def _go(db, monkeypatch, *, fakes=None, orch=None, sides=None, enabled=ALL, **kw):
    fakes = (fakes or _full()).install_agent(monkeypatch)
    sides = (sides or Sides()).install(monkeypatch)
    orch = (orch or Orch(autopilot=True)).install(monkeypatch)
    res = agent.run_agent(db, 5, 1, side_outputs_enabled=enabled, **kw)
    return res, fakes, orch, sides


# ---------------------------------------------------------------------------
# 16. agent: nothing enabled is the old agent
# ---------------------------------------------------------------------------
def test_agent_with_no_side_outputs_sees_the_old_system_prompt_and_nine_tools(db, monkeypatch):
    res, fakes, orch, sides = _go(db, monkeypatch, enabled=())
    assert res.status == "done" and res.clean
    assert fakes.calls == CLEAN_CALLS and sides.calls == []
    assert len(orch.calls) == 7
    for c in orch.calls:
        assert c["system"] == agent.SYSTEM_PROMPT
        assert [t.name for t in c["tools"]] == [*NON_FINISH, "finish"]
    assert not any("SIDE OUTPUTS" in p for p in orch.prompts())
    assert res.state.side_outputs.enabled == [] and res.state.budget.side_calls == 0


# ---------------------------------------------------------------------------
# 17. agent: everything enabled
# ---------------------------------------------------------------------------
def test_agent_with_all_side_outputs_runs_each_once_and_still_ends_clean(db, monkeypatch):
    res, fakes, orch, sides = _go(db, monkeypatch)
    assert res.status == "done" and res.clean
    assert sides.calls == list(ALL)  # each exactly once, in the fixed order
    assert fakes.calls == CLEAN_CALLS
    for c in orch.calls:
        assert c["system"] == agent.SYSTEM_PROMPT + "\n\n" + agent.SIDE_OUTPUTS_PROMPT
        assert [t.name for t in c["tools"]] == [*NON_FINISH, *ALL, "finish"]
    assert all("SIDE OUTPUTS" in p for p in orch.prompts())
    budget = res.state.budget
    assert budget.side_calls == 3 and budget.tool_calls == len(CLEAN_CALLS)
    assert res.state.side_outputs.ran == list(ALL) and res.state.side_outputs.errors == {}
    steps = _steps(db, res.run_id)
    assert _names(steps) == [*CLEAN_CALLS, *ALL, "finish"]
    assert all(s.error is None for s in steps)
    assert json.loads(steps[-1].result_summary) == {"accepted": True}
    assert _run_row(db, res.run_id).tool_calls == len(CLEAN_CALLS) + 3
    # the fakes' output lands in the persisted state
    persisted = _run_state(db, res.run_id).side_outputs
    assert [a["question"] for a in persisted.screening_answers] == SCREENING
    assert [s["requirement_id"] for s in persisted.learning_suggestions] == ["R3"]
    assert persisted.resume_notes["draft_version"] == 1


def test_agent_offers_only_the_enabled_side_tools(db, monkeypatch):
    _, _, orch, sides = _go(db, monkeypatch, enabled=("suggest_learning",))
    assert [t.name for t in orch.calls[0]["tools"]] == [*NON_FINISH, "suggest_learning", "finish"]
    assert sides.calls == ["suggest_learning"]


def test_the_side_output_section_lists_what_is_due_done_and_not_needed(db, monkeypatch):
    res, _, orch, _ = _go(db, monkeypatch, fakes=Fakes(questions=SCREENING))  # no confirmed gap
    prompts = orch.prompts()
    assert "answer_screening: not yet" in prompts[0] and "suggest_learning: not yet" in prompts[0]
    after_match = prompts[2]  # analysed and matched, no draft yet
    assert "answer_screening: due" in after_match and "suggest_learning: not needed" in after_match
    assert "suggest_resume_tweaks: not yet" in after_match
    last = prompts[-1]  # the finish turn: everything that could run has run
    assert "answer_screening: done" in last and "suggest_resume_tweaks: done" in last
    assert "suggest_learning: not needed" in last
    assert res.state.side_outputs.ran == ["answer_screening", "suggest_resume_tweaks"]


# ---------------------------------------------------------------------------
# 18. agent: finish waits for the due side outputs
# ---------------------------------------------------------------------------
def test_finish_is_refused_while_a_side_output_is_due_then_accepted(db, monkeypatch):
    script = _t(*CLEAN_CALLS, "finish", *ALL, "finish")
    res, _, orch, sides = _go(db, monkeypatch, orch=Orch(script))
    assert res.status == "done" and res.clean
    steps = _steps(db, res.run_id)
    assert _names(steps) == [*CLEAN_CALLS, "finish", *ALL, "finish"]
    early = steps[6]
    assert _is_refused(early)
    for tool in ALL:
        assert tool in early.error
    assert not any(s.tool in ALL for s in steps[:6])  # nothing ran for the refusal
    assert "REFUSED" in orch.prompts()[7] and "answer_screening" in orch.prompts()[7]
    assert steps[-1].error is None and json.loads(steps[-1].result_summary) == {"accepted": True}
    assert sides.calls == list(ALL)
    assert res.state.budget.tool_calls == len(CLEAN_CALLS)  # a refusal costs no tool call


def test_finish_names_only_the_side_outputs_still_due(db, monkeypatch):
    script = _t(*CLEAN_CALLS, "answer_screening", "finish", "suggest_learning", "suggest_resume_tweaks", "finish")
    res, _, _, _ = _go(db, monkeypatch, orch=Orch(script))
    refused = [s for s in _steps(db, res.run_id) if _is_refused(s)]
    assert len(refused) == 1 and refused[0].tool == "finish"
    assert "suggest_learning" in refused[0].error and "answer_screening" not in refused[0].error


# ---------------------------------------------------------------------------
# 19. agent: refused side calls
# ---------------------------------------------------------------------------
def test_a_side_tool_called_twice_is_refused_the_second_time(db, monkeypatch):
    script = _t(*CLEAN_CALLS, "answer_screening", "answer_screening")
    res, _, _, sides = _go(db, monkeypatch, orch=Orch(script, autopilot=True))
    assert res.status == "done"
    assert sides.calls.count("answer_screening") == 1
    again = [s for s in _steps(db, res.run_id) if s.tool == "answer_screening" and _is_refused(s)]
    assert len(again) == 1 and "already ran" in again[0].error
    assert res.state.budget.side_calls == 3


def test_resume_tweaks_before_the_letter_is_final_is_refused_and_does_not_run(db, monkeypatch):
    script = _t("analyze_job", "match_profile", "generate_letter", "suggest_resume_tweaks")
    res, _, _, sides = _go(db, monkeypatch, orch=Orch(script, autopilot=True))
    assert res.status == "done"
    steps = _steps(db, res.run_id)
    assert steps[3].tool == "suggest_resume_tweaks" and _is_refused(steps[3])
    assert "not final" in steps[3].error
    assert sides.calls.count("suggest_resume_tweaks") == 1  # only the real call, later
    assert sides.final_seen["suggest_resume_tweaks"] == 1   # and by then the letter was final
    real = [i for i, s in enumerate(steps) if s.tool == "suggest_resume_tweaks" and not _is_refused(s)]
    assert real and real[0] > 5


def test_a_side_tool_that_is_not_enabled_is_an_unknown_tool(db, monkeypatch):
    script = _t("analyze_job", "match_profile", "answer_screening")
    res, _, orch, sides = _go(db, monkeypatch, orch=Orch(script, autopilot=True), enabled=("suggest_learning",))
    assert res.status == "done"
    steps = _steps(db, res.run_id)
    assert _is_refused(steps[2]) and "unknown tool 'answer_screening'" in steps[2].error
    assert "answer_screening" not in sides.calls and sides.calls == ["suggest_learning"]
    assert all("answer_screening" not in [t.name for t in c["tools"]] for c in orch.calls)


# ---------------------------------------------------------------------------
# 20. agent: a failing side tool never fails the letter
# ---------------------------------------------------------------------------
def test_a_side_tool_that_raises_is_reported_and_the_run_carries_on(db, monkeypatch):
    sides = Sides(raises={"answer_screening": RuntimeError("screening exploded")})
    res, _, orch, sides = _go(db, monkeypatch, sides=sides)
    assert res.status == "done" and res.clean and res.stop_reason is None
    assert "screening exploded" in res.state.side_outputs.errors["answer_screening"]
    assert "answer_screening" in res.state.side_outputs.ran
    assert sides.calls == list(ALL)  # the failed one was not retried, the others still ran
    prompts = orch.prompts()
    next_turn = next(p for p in prompts if "answer_screening FAILED and will not be retried" in p)
    assert "screening exploded" in next_turn and "answer_screening: FAILED, not retried" in next_turn
    steps = _steps(db, res.run_id)
    failed = next(s for s in steps if s.tool == "answer_screening")
    assert "screening exploded" in failed.error and not _is_refused(failed)
    assert steps[-1].tool == "finish" and steps[-1].error is None
    assert _run_row(db, res.run_id).status == "done"


# ---------------------------------------------------------------------------
# 21. agent: side calls are not charged to the letter's cap
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("cap", [len(CLEAN_CALLS) + 1, len(CLEAN_CALLS)])
def test_the_tool_cap_is_set_by_the_letter_alone_so_the_side_outputs_still_run(db, monkeypatch, cap):
    orch = Orch(autopilot=True, tweak=lambda s: setattr(s.budget, "max_tool_calls", cap))
    res, _, _, sides = _go(db, monkeypatch, orch=orch)
    assert res.status == "done" and res.clean
    assert sides.calls == list(ALL)
    assert res.state.budget.tool_calls == len(CLEAN_CALLS) and res.state.budget.side_calls == 3


# ---------------------------------------------------------------------------
# 22. agent: when a cap ends the run first
# ---------------------------------------------------------------------------
def test_hitting_the_tool_cap_with_a_clean_letter_makes_code_run_the_due_side_outputs(db, monkeypatch):
    orch = Orch(autopilot=True, tweak=lambda s: setattr(s.budget, "max_tool_calls", len(CLEAN_CALLS)))
    res, _, orch, sides = _go(db, monkeypatch, orch=orch)
    assert res.status == "done" and res.clean
    assert len(orch.calls) == len(CLEAN_CALLS)  # the orchestrator never got to call them
    assert sides.calls == list(ALL)
    steps = _steps(db, res.run_id)
    assert _names(steps) == [*CLEAN_CALLS, *ALL, "finish"]  # side steps are logged before the code finish
    summary = json.loads(steps[-1].result_summary)
    assert summary["by"] == "code" and "tool-call limit" in summary["reason"]


def test_hitting_the_usd_cap_makes_code_skip_the_side_outputs(db, monkeypatch):
    def spend(state, ctx):
        ctx.db.add(LlmUsage(task="check_requirements", tier="mid", model="fake", cost_usd=Decimal("1"),
                            run_id=ctx.run.id))
        ctx.db.commit()

    fakes = Fakes(questions=SCREENING, gap=True, after_check={"style_lint": spend})
    res, _, _, sides = _go(db, monkeypatch, fakes=fakes)
    assert res.status == "done" and res.clean
    assert sides.calls == []
    steps = _steps(db, res.run_id)
    assert _names(steps) == [*CLEAN_CALLS, "finish"]
    summary = json.loads(steps[-1].result_summary)
    assert summary["by"] == "code" and "run budget reached" in summary["reason"]
    assert res.state.side_outputs.ran == []


# ---------------------------------------------------------------------------
# 23. agent: out of drafts
# ---------------------------------------------------------------------------
def test_out_of_drafts_still_runs_resume_tweaks_before_finish_is_accepted(db, monkeypatch):
    # v2 is the best draft (claims pass); v1 and v3 fail claims, v2 fails style.
    plan = {1: {"claims": False}, 2: {"style": False}, 3: {"claims": False}}
    script = _t(*CLEAN_CALLS, "revise_letter", *CHECK_CALLS, "revise_letter", *CHECK_CALLS,
                "finish", "suggest_resume_tweaks", "finish")
    res, _, _, sides = _go(db, monkeypatch, fakes=Fakes(plan=plan), orch=Orch(script),
                           enabled=("suggest_resume_tweaks",))
    assert res.status == "budget_stopped" and "draft limit reached" in res.stop_reason
    assert res.clean is False and res.draft.version == 2 and len(res.state.drafts) == 3
    steps = _steps(db, res.run_id)
    refused = [s for s in steps if _is_refused(s)]
    assert len(refused) == 1 and refused[0].tool == "finish" and "suggest_resume_tweaks" in refused[0].error
    assert sides.calls == ["suggest_resume_tweaks"] and sides.final_seen["suggest_resume_tweaks"] == 2
    assert steps[-1].tool == "finish" and json.loads(steps[-1].result_summary)["out_of_drafts"] is True
    assert _run_row(db, res.run_id).final_draft_version == 2


# ---------------------------------------------------------------------------
# 24. agent: a resumed run keeps the side outputs it started with
# ---------------------------------------------------------------------------
def test_resume_agent_keeps_the_enabled_side_outputs_from_the_persisted_state(db, monkeypatch):
    reqs = [
        dict(id="R1", text="Experience building dashboards with Power BI", importance="essential",
             letter_role="headline", skill="Power BI"),
        dict(id="R2", text="Build REST APIs", importance="essential", letter_role="headline"),
    ]
    fakes = Fakes(reqs=reqs, questions=SCREENING, match={"R1": ("gap", [])}).install_agent(monkeypatch)
    sides = Sides().install(monkeypatch)
    Orch(_t("analyze_job", "match_profile", "ask_user")).install(monkeypatch)
    first = agent.run_agent(db, 5, 1, gap_policy=ask_user_gaps, side_outputs_enabled=ALL)
    assert first.status == "waiting_user"
    assert _run_state(db, first.run_id).side_outputs.enabled == list(ALL)

    answers.answer_no(db, first.run_id, "Q1")
    assert _run_row(db, first.run_id).status == "answered"

    orch = Orch(autopilot=True).install(monkeypatch)
    res = agent.resume_agent(db, first.run_id, gap_policy=ask_user_gaps)  # no side_outputs argument
    assert res.status == "done" and res.clean
    assert res.state.side_outputs.enabled == list(ALL)
    assert sides.calls == list(ALL)  # the user's "no" is a confirmed gap, so learning ran too
    assert fakes.calls.count("analyze_job") == 1
    for c in orch.calls:
        assert c["system"] == agent.SYSTEM_PROMPT + "\n\n" + agent.SIDE_OUTPUTS_PROMPT
        assert [t.name for t in c["tools"]] == [*NON_FINISH, *ALL, "finish"]
        assert "SIDE OUTPUTS" in c["messages"][0]["content"]


# ---------------------------------------------------------------------------
# 25-27. workflow
# ---------------------------------------------------------------------------
def _wf(db, monkeypatch, *, fakes=None, sides=None, enabled=ALL, **kw):
    fakes = (fakes or _full()).install_workflow(monkeypatch)
    sides = (sides or Sides()).install(monkeypatch)
    res = workflow.run_workflow(db, 5, 1, side_outputs_enabled=enabled, **kw)
    return res, fakes, sides


def test_workflow_with_no_side_outputs_runs_exactly_the_old_steps(db, monkeypatch):
    res, fakes, sides = _wf(db, monkeypatch, enabled=())
    assert res.status == "done" and res.clean
    assert _names(_steps(db, res.run_id)) == CLEAN_CALLS
    assert sides.calls == [] and res.state.side_outputs.enabled == []
    assert res.state.budget.side_calls == 0 and res.state.budget.tool_calls == len(CLEAN_CALLS)


def test_workflow_runs_the_side_outputs_in_order_after_the_last_check(db, monkeypatch):
    res, _, sides = _wf(db, monkeypatch)
    assert res.status == "done" and res.clean
    steps = _steps(db, res.run_id)
    assert _names(steps) == [*CLEAN_CALLS, *ALL]
    assert all(s.error is None for s in steps)
    assert sides.calls == list(ALL)
    assert res.state.budget.side_calls == 3 and res.state.budget.tool_calls == len(CLEAN_CALLS)
    assert _run_row(db, res.run_id).tool_calls == len(CLEAN_CALLS) + 3


def test_workflow_runs_the_side_outputs_on_the_best_draft_when_the_revision_limit_is_hit(db, monkeypatch):
    plan = {1: {"claims": False}, 2: {"style": False}, 3: {"claims": False}}  # v2 is the best draft
    res, fakes, sides = _wf(db, monkeypatch, fakes=Fakes(plan=plan, questions=SCREENING, gap=True))
    assert res.status == "budget_stopped" and "revision limit reached" in res.stop_reason
    assert res.draft.version == 2 and res.clean is False
    assert sides.calls == list(ALL)
    assert sides.final_seen["suggest_resume_tweaks"] == 2
    assert fakes.calls.count("revise_letter") == 2
    assert _names(_steps(db, res.run_id))[-3:] == list(ALL)


def test_workflow_logs_a_failing_side_tool_and_still_runs_the_rest(db, monkeypatch):
    sides = Sides(raises={"suggest_learning": RuntimeError("learning exploded")})
    res, _, sides = _wf(db, monkeypatch, sides=sides)
    assert res.status == "done" and res.clean and res.stop_reason is None
    assert sides.calls == list(ALL)
    assert "learning exploded" in res.state.side_outputs.errors["suggest_learning"]
    steps = _steps(db, res.run_id)
    assert _names(steps) == [*CLEAN_CALLS, *ALL]
    failed = next(s for s in steps if s.tool == "suggest_learning")
    assert "learning exploded" in failed.error
    assert next(s for s in steps if s.tool == "suggest_resume_tweaks").error is None
    assert res.state.side_outputs.resume_notes is not None


def test_workflow_skips_side_outputs_with_nothing_to_work_on(db, monkeypatch):
    res, _, sides = _wf(db, monkeypatch, fakes=Fakes())  # no screening questions, no confirmed gap
    assert res.status == "done"
    assert sides.calls == ["suggest_resume_tweaks"]
    assert _names(_steps(db, res.run_id)) == [*CLEAN_CALLS, "suggest_resume_tweaks"]
    assert res.state.side_outputs.ran == ["suggest_resume_tweaks"]


def test_the_agent_also_skips_side_outputs_with_nothing_to_work_on(db, monkeypatch):
    res, _, orch, sides = _go(db, monkeypatch, fakes=Fakes())
    assert res.status == "done"
    assert sides.calls == ["suggest_resume_tweaks"]
    assert "answer_screening: not needed" in orch.prompts()[-1]


def test_workflow_treats_a_left_out_gap_nobody_asked_about_as_nothing_to_learn(db, monkeypatch):
    # the evals' leave_out_gaps policy decides R1 itself (assumed=True): not a confirmed gap
    fakes = Fakes(questions=SCREENING, match={"R1": ("gap", [])})
    res, _, sides = _wf(db, monkeypatch, fakes=fakes)
    assert res.state.requirement("R1").user_decision.assumed is True
    assert "suggest_learning" not in sides.calls
    assert sides.calls == ["answer_screening", "suggest_resume_tweaks"]


# ---------------------------------------------------------------------------
# 28. engines.run_letter forwards the toggles
# ---------------------------------------------------------------------------
def test_run_letter_forwards_side_outputs_to_the_agent(monkeypatch):
    seen = {}
    monkeypatch.setattr(agent, "run_agent", lambda db_, job, profile, **kw: seen.update(kw) or "A")
    assert engines.run_letter(None, 5, 1, engine="agent", side_outputs=("suggest_learning",)) == "A"
    assert seen["side_outputs_enabled"] == ("suggest_learning",)


def test_run_letter_forwards_side_outputs_to_the_workflow(monkeypatch):
    seen = {}
    monkeypatch.setattr(workflow, "run_workflow", lambda db_, job, profile, **kw: seen.update(kw) or "W")
    assert engines.run_letter(None, 5, 1, engine="workflow", side_outputs=ALL) == "W"
    assert seen["side_outputs_enabled"] == ALL


@pytest.mark.parametrize("which, module, fn", [("agent", agent, "run_agent"), ("workflow", workflow, "run_workflow")])
def test_run_letter_defaults_to_no_side_outputs(monkeypatch, which, module, fn):
    seen = {}
    monkeypatch.setattr(module, fn, lambda db_, job, profile, **kw: seen.update(kw) or "X")
    engines.run_letter(None, 5, 1, engine=which)
    assert seen["side_outputs_enabled"] == ()


# ---------------------------------------------------------------------------
# 29. production._run_pipeline passes the settings on
# ---------------------------------------------------------------------------
def test_the_pipeline_gets_all_three_side_outputs_by_default(db, monkeypatch):
    fake = FakeEngines(monkeypatch, db)
    production._run_pipeline(db, 5, 1, None)
    assert tuple(fake.side_outputs) == ALL


def test_the_pipeline_leaves_out_a_side_output_the_user_switched_off(db, monkeypatch):
    set_preferences(db, 1, {"screening_answers_enabled": False})
    fake = FakeEngines(monkeypatch, db)
    production._run_pipeline(db, 5, 1, None)
    assert tuple(fake.side_outputs) == ("suggest_learning", "suggest_resume_tweaks")


def test_the_pipeline_gets_no_side_outputs_when_all_are_off(db, monkeypatch):
    set_preferences(db, 1, {key: False for key in SIDE_OUTPUT_TOGGLES.values()})
    fake = FakeEngines(monkeypatch, db)
    production._run_pipeline(db, 5, 1, None)
    assert tuple(fake.side_outputs) == ()


# ---------------------------------------------------------------------------
# 30. preferences
# ---------------------------------------------------------------------------
def test_the_three_side_output_toggles_default_to_on():
    for key in ("resume_advice_enabled", "learning_suggestions_enabled", "screening_answers_enabled"):
        assert DEFAULTS[key] is True
    assert set(SIDE_OUTPUT_TOGGLES.values()) == {
        "resume_advice_enabled", "learning_suggestions_enabled", "screening_answers_enabled",
    }


def test_the_toggle_table_names_every_side_tool_and_is_shared_with_side_outputs():
    assert set(SIDE_OUTPUT_TOGGLES) == set(SIDE_OUTPUT_TOOLS)
    assert SIDE_OUTPUT_TOGGLES == side_outputs.TOGGLES


def test_letter_settings_side_outputs_is_a_tuple_in_the_fixed_tool_order(db):
    got = letter_settings(db, 1)["side_outputs"]
    assert isinstance(got, tuple) and got == ALL


@pytest.mark.parametrize("key, removed", [
    ("screening_answers_enabled", "answer_screening"),
    ("learning_suggestions_enabled", "suggest_learning"),
    ("resume_advice_enabled", "suggest_resume_tweaks"),
])
def test_a_stored_false_removes_just_that_tool(db, key, removed):
    set_preferences(db, 1, {key: False})
    assert letter_settings(db, 1)["side_outputs"] == tuple(t for t in ALL if t != removed)


@pytest.mark.parametrize("bad", ["nope", 0, None, [], 1])
def test_a_stored_value_that_is_not_a_bool_reads_as_on(db, bad):
    set_preferences(db, 1, {"learning_suggestions_enabled": bad})
    assert letter_settings(db, 1)["side_outputs"] == ALL


# ---------------------------------------------------------------------------
# 31. PUT / GET /profile/1/preferences
# ---------------------------------------------------------------------------
TOGGLE_KEYS = ["resume_advice_enabled", "learning_suggestions_enabled", "screening_answers_enabled"]


class TestPreferencesApi:
    def test_defaults_are_returned(self, client, db):
        prefs = client.get("/profile/1/preferences").json()
        assert all(prefs[k] is True for k in TOGGLE_KEYS)

    @pytest.mark.parametrize("key", TOGGLE_KEYS)
    @pytest.mark.parametrize("value", [True, False])
    def test_each_toggle_is_accepted_and_returned(self, client, db, key, value):
        res = client.put("/profile/1/preferences", json={key: value})
        assert res.status_code == 200 and res.json()[key] is value
        assert client.get("/profile/1/preferences").json()[key] is value

    def test_a_partial_update_keeps_the_other_toggles(self, client, db):
        client.put("/profile/1/preferences", json={"screening_answers_enabled": False})
        client.put("/profile/1/preferences", json={"resume_advice_enabled": False})
        body = client.get("/profile/1/preferences").json()
        assert body["screening_answers_enabled"] is False and body["resume_advice_enabled"] is False
        assert body["learning_suggestions_enabled"] is True
        assert letter_settings(db, 1)["side_outputs"] == ("suggest_learning",)

    @pytest.mark.parametrize("key", TOGGLE_KEYS)
    @pytest.mark.parametrize("bad", ["maybe", [1], {"x": 1}, 2])
    def test_a_value_that_is_not_a_bool_is_rejected(self, client, db, key, bad):
        assert client.put("/profile/1/preferences", json={key: bad}).status_code == 422
        assert client.get("/profile/1/preferences").json()[key] is True  # nothing was stored


# ---------------------------------------------------------------------------
# 32. view.not_claimed
# ---------------------------------------------------------------------------
def _gap(rid, text, skill="", decision=None, role="headline"):
    return Requirement(id=rid, text=text, importance="essential", letter_role=role, status="gap",
                       skill=skill, user_decision=decision, theme=f"t-{rid}")


def _vstate(requirements=(), job_id=5) -> LetterState:
    return LetterState(profile_id=1, job=JobInfo(job_id=job_id, title="Engineer"), requirements=list(requirements))


def test_a_skill_less_gap_nobody_asked_about_is_not_listed():
    state = _vstate([_gap("R1", "Attention to detail")])
    assert view.not_claimed(state) == []


def test_a_skill_less_gap_the_user_said_no_to_is_listed():
    d = UserDecision(choice="leave_out", answer="no")
    (row,) = view.not_claimed(_vstate([_gap("R1", "Attention to detail", decision=d)]))
    assert row["id"] == "R1" and row["reason"] == "You said you don't have this"


def test_a_remembered_no_says_so():
    d = UserDecision(choice="leave_out", remembered=True)
    (row,) = view.not_claimed(_vstate([_gap("R1", "Power BI", skill="Power BI", decision=d)]))
    assert row["reason"] == "You said you don't have this (remembered from an earlier ad)"


def test_an_assumed_leave_out_reads_left_out_without_asking():
    d = UserDecision(choice="leave_out", assumed=True)
    (row,) = view.not_claimed(_vstate([_gap("R1", "Attention to detail", decision=d)]))
    assert row["reason"] == "Left out without asking you"


def test_a_skill_gap_with_no_decision_says_nothing_in_the_profile_backs_it():
    (row,) = view.not_claimed(_vstate([_gap("R1", "Power BI", skill="Power BI")]))
    assert row["reason"] == "Nothing in your profile backs this"


def test_not_claimed_never_lists_eligibility_items():
    state = _vstate([_gap("R1", "Australian work rights", skill="Work rights", role="not_for_letter")])
    assert view.not_claimed(state) == []


# ---------------------------------------------------------------------------
# 33. letter_info: the run's side outputs
# ---------------------------------------------------------------------------
LETTER = "Dear LogiCo, here is my letter."


def _final_state(side: SideOutputs | None = None) -> LetterState:
    state = _vstate()
    state.add_draft(LETTER)
    for name in guardrails.REQUIRED_CHECKS:
        state.record_check(name, Check(passed=True))
    if side is not None:
        state.side_outputs = side
    return state


def _add_run(db, state, status="done", final_version=1) -> int:
    run = LetterRun(match_id=9, engine="agent", status=status, state=state.model_dump_json(),
                    final_draft_version=final_version, cost_usd=0.2)
    db.add(run)
    db.commit()
    return run.id


def _add_letter(db, text=LETTER):
    db.add(CoverLetter(match_id=9, generated_content=text, status="draft"))
    db.commit()


def test_a_run_without_side_outputs_enabled_reports_none(db):
    _add_run(db, _final_state())
    _add_letter(db)
    info = view.letter_info(db, 5, 1)
    assert info["run"] is not None and info["run"]["side_outputs"] is None


def test_a_run_with_side_outputs_carries_them_their_errors_and_what_was_skipped(db):
    side = SideOutputs(
        enabled=list(ALL), ran=["answer_screening", "suggest_learning"],
        errors={"suggest_learning": "learning exploded"},
        screening_answers=[{"question": "q?", "answer": "a", "verified": True}],
        learning_suggestions=[{"requirement_id": "R3"}],
        resume_notes=None,
    )
    _add_run(db, _final_state(side))
    _add_letter(db)
    out = view.letter_info(db, 5, 1)["run"]["side_outputs"]
    assert out["screening_answers"] == side.screening_answers
    assert out["learning_suggestions"] == side.learning_suggestions
    assert out["resume_notes"] is None
    assert out["errors"] == {"suggest_learning": "learning exploded"}
    assert out["skipped"] == ["suggest_resume_tweaks"]


def test_side_output_view_carries_resume_notes_and_skips_nothing_when_all_ran():
    side = SideOutputs(enabled=list(ALL), ran=list(ALL), resume_notes={"lead_with": ["x"], "dropped": 0})
    out = view.side_output_view(_final_state(side))
    assert out["resume_notes"] == {"lead_with": ["x"], "dropped": 0}
    assert out["skipped"] == [] and out["errors"] == {}


def test_side_output_view_is_none_when_nothing_was_enabled():
    assert view.side_output_view(_final_state()) is None


def test_side_outputs_are_not_shown_once_the_letter_has_moved_on(db):
    _add_run(db, _final_state(SideOutputs(enabled=list(ALL), ran=list(ALL))))
    _add_letter(db, "A later, different letter.")
    info = view.letter_info(db, 5, 1)
    assert info["run"] is None


# ---------------------------------------------------------------------------
# 34. GET /jobs/{id}/letter-info
# ---------------------------------------------------------------------------
def test_the_letter_info_endpoint_returns_the_runs_side_outputs(client, db):
    side = SideOutputs(
        enabled=list(ALL), ran=["answer_screening"],
        screening_answers=[{"question": "q?", "answer": "a", "verified": True}],
    )
    _add_run(db, _final_state(side))
    _add_letter(db)
    body = client.get("/jobs/5/letter-info").json()
    out = body["run"]["side_outputs"]
    assert out["screening_answers"] == side.screening_answers
    assert out["skipped"] == ["suggest_learning", "suggest_resume_tweaks"]
    assert out["errors"] == {} and out["resume_notes"] is None


def test_the_letter_info_endpoint_reports_null_side_outputs_for_a_letter_only_run(client, db):
    _add_run(db, _final_state())
    _add_letter(db)
    assert client.get("/jobs/5/letter-info").json()["run"]["side_outputs"] is None


# ---------------------------------------------------------------------------
# 35. scripts/letter_lab.py
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def lab():
    """Import scripts/letter_lab.py by path, then undo its env and sys.path edits."""
    saved_env = {k: os.environ.get(k) for k in ("DATABASE_URL", "APP_ENV")}
    saved_path = list(sys.path)
    try:
        spec = importlib.util.spec_from_file_location("letter_lab_side_under_test", ROOT / "scripts" / "letter_lab.py")
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


def _lab_row(title, tools, *, refused=(), side=None, cost=0.2):
    steps = [{"tool": t} for t in tools]
    for after, tool, why in refused:
        steps.insert(after, {"tool": tool, "refused": why})
    row = {
        "title": title, "steps": steps, "per_draft": [{}],
        "orchestrator": {"calls": 8, "cost_usd": 0.02, "input_tokens": 1000, "output_tokens": 100},
        "cost_usd": cost, "input_tokens": 5000, "output_tokens": 800,
    }
    if side is not None:
        row["side_outputs"] = side
    return row


def _side(enabled=ALL, ran=ALL, errors=None, answers_=2, verified=1, learning=1, dropped=0):
    return {
        "enabled": list(enabled), "ran": list(ran), "errors": errors or {},
        "screening_answers": answers_, "screening_verified": verified, "learning_suggestions": learning,
        "resume_items": {"lead_with": 2, "keywords_to_mirror": 3, "consider_cutting": 0, "gaps_to_address": 1},
        "resume_dropped": dropped,
    }


def test_agent_section_ignores_side_tools_when_comparing_the_path_to_the_workflow(lab):
    row = _lab_row("Job A", [*CLEAN_CALLS, *ALL, "finish"], side=_side())
    text = "\n".join(lab._agent_section([row]))
    assert "| Same path as the workflow | 1/1 (+0 with only the check order changed) |" in text
    assert "| Different path | 0/1 |" in text


def test_agent_section_still_flags_a_real_path_difference_when_side_tools_are_present(lab):
    row = _lab_row("Job B", ["analyze_job", "match_profile", "generate_letter", "check_claims",
                             "answer_screening", "finish"], side=_side(ran=["answer_screening"]))
    text = "\n".join(lab._agent_section([row]))
    assert "| Same path as the workflow | 0/1 (+0 " in text and "| Different path | 1/1 |" in text


def test_side_section_is_empty_without_side_outputs(lab):
    assert lab._side_section([]) == []
    no_side = _lab_row("Job A", [*CLEAN_CALLS, "finish"])
    switched_off = _lab_row("Job B", [*CLEAN_CALLS, "finish"], side=_side(enabled=(), ran=()))
    assert lab._side_section([no_side, switched_off]) == []


def test_side_section_counts_ran_tools_side_refusals_and_finish_refusals(lab):
    finish_why = "call answer_screening, suggest_learning first: each enabled side output runs once before finish"
    a = _lab_row(
        "Job A", [*CLEAN_CALLS, *ALL, "finish"],
        refused=[(6, "finish", finish_why), (8, "answer_screening", "answer_screening already ran in this run"),
                 (9, "finish", "claims not checked on draft 1")],
        side=_side(),
    )
    b = _lab_row("Job B", [*CLEAN_CALLS, "suggest_resume_tweaks", "finish"],
                 side=_side(enabled=ALL, ran=["suggest_resume_tweaks"], answers_=0, verified=0, learning=0))
    text = "\n".join(lab._side_section([a, b]))
    assert "## Side outputs (Phase 7c)" in text
    assert "| answer_screening ran | 1/2 |" in text
    assert "| suggest_learning ran | 1/2 |" in text
    assert "| suggest_resume_tweaks ran | 2/2 |" in text
    assert "| Side tool calls refused by a gate | 1 |" in text
    assert "| finish refused while a side output was due | 1 |" in text  # the unrelated refusal isn't counted
    assert "| Side outputs run before the final draft existed | 0 |" in text
    assert "answer_screening already ran in this run" in text and "each enabled side output runs once" in text
    assert "2 answers (1 verified)" in text


def test_side_section_reports_a_failed_side_output(lab):
    row = _lab_row("Job A", [*CLEAN_CALLS, *ALL, "finish"],
                   side=_side(errors={"suggest_learning": "boom"}))
    text = "\n".join(lab._side_section([row]))
    assert "errors ['suggest_learning']" in text


def test_side_section_notes_a_side_output_that_ran_before_the_letter_existed(lab):
    row = _lab_row("Job A", ["analyze_job", "match_profile", "answer_screening", "generate_letter",
                             *CHECK_CALLS, "finish"], side=_side(ran=["answer_screening"]))
    text = "\n".join(lab._side_section([row]))
    assert "| Side outputs run before the final draft existed | 1 |" in text
    assert "answer_screening 3/7 (d=0)" in text
