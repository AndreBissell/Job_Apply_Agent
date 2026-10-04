"""Tests for the three per-run limits of the cover-letter pipeline, now user preferences:
max drafts, max tool calls and the per-run USD budget.

Covers: the constants and ``Budget`` defaults (app/preferences.py, app/llm/letter/state.py),
``letter_settings()["limits"]`` and its validation, the bounded fields of ``PUT
/profile/{id}/preferences``, ``outcome.open_run(limits=)``, what each limit does in the fixed
workflow and in the agent, a resumed run keeping the limits it opened with, and the limits being
forwarded by ``engines.run_letter`` and ``production._run_pipeline``.

No LLM and no network: the letter tools are scripted fakes (reused from the workflow and agent
tests), the orchestrator is a scripted/auto-pilot fake, and the engines are replaced where the
test is about forwarding. In-memory SQLite only.
"""
from __future__ import annotations

import copy
import json
import sqlite3
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.api.main as main
import app.api.profile_ui as profile_ui
from app.db import Base
from app.llm.client import ToolStep
from app.llm.letter import agent, answers, engines, guardrails, outcome, production, workflow
from app.llm.letter.gap_policy import ask_user_gaps
from app.llm.letter.outcome import RESUME_EXTRA_CALLS, open_run
from app.llm.letter.production import PIPELINE, Work
from app.llm.letter.runner import REFUSED
from app.llm.letter.state import Budget, LetterState
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
from app.preferences import (
    DEFAULT_LETTER_MAX_DRAFTS,
    DEFAULT_LETTER_MAX_TOOL_CALLS,
    DEFAULT_LLM_RUN_BUDGET_USD,
    DEFAULTS,
    LIMIT_BOUNDS,
    letter_settings,
    set_preferences,
)
from tests.test_letter_agent import Fakes
from tests.test_letter_production import FakeEngines
from tests.test_letter_workflow import Script

DEFAULT_LIMITS = {"max_drafts": 3, "max_tool_calls": 15, "max_cost_usd": 0.5}
CHECK_CALLS = ["check_claims", "check_requirements", "style_lint"]
CLEAN_CALLS = ["analyze_job", "match_profile", "generate_letter", *CHECK_CALLS]
_TOOL_OF_CHECK = {v: k for k, v in guardrails.CHECK_TOOLS.items()}


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
        JobListing(id=5, source="seek", source_job_id="1", url="u", title="Engineer",
                   company="LogiCo", raw_description="We build logistics software."),
    ])
    session.flush()
    session.add(Match(id=9, user_id=1, job_id=5, score=90))
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


def _run_row(db, run_id) -> LetterRun:
    db.expire_all()
    return db.get(LetterRun, run_id)


def _run_state(db, run_id) -> LetterState:
    return LetterState.model_validate_json(_run_row(db, run_id).state)


def _steps(db, run_id) -> list[LetterRunStep]:
    db.expire_all()
    return list(db.scalars(select(LetterRunStep).where(LetterRunStep.run_id == run_id).order_by(LetterRunStep.seq)))


# --- the agent's orchestrator -------------------------------------------------
class Pilot:
    """A fake for ``agent.complete_tools``: a scripted list of ToolSteps (or exceptions), then an
    auto-pilot that picks what the fixed workflow would. ``cost`` inserts an ``llm_usage`` row for
    the run on every call, like a real call does. It captures the run's state on open AND reopen,
    so it also drives a resumed run."""

    def __init__(self, script=None, autopilot=False, cost=None, max_calls=60):
        self.script = list(script or [])
        self.autopilot = autopilot
        self.cost = Decimal(str(cost)) if cost is not None else None
        self.max_calls = max_calls
        self.calls: list[dict] = []
        self.state: LetterState | None = None
        self.db = None

    def install(self, monkeypatch, db):
        self.db = db
        monkeypatch.setattr(agent, "complete_tools", self)
        for attr in ("open_run", "reopen_run"):
            monkeypatch.setattr(agent, attr, self._capturing(getattr(agent, attr)))
        return self

    def _capturing(self, real):
        def wrapped(*a, **kw):
            state, ctx = real(*a, **kw)
            self.state = state
            return state, ctx
        return wrapped

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
        if guardrails.can_finish(s) is None:
            return "finish"
        if len(s.drafts) < s.budget.max_drafts:
            return "revise_letter"
        return "finish"

    def __call__(self, system_prompt, messages, tools, **kw):
        self.calls.append({"messages": copy.deepcopy(messages), "kw": kw})
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


def _t(*names: str) -> list[ToolStep]:
    return [ToolStep(tool=n) for n in names]


def _go_agent(db, monkeypatch, *, fakes=None, pilot=None, **kw):
    fakes = (fakes or Fakes()).install(monkeypatch)
    pilot = (pilot or Pilot(autopilot=True)).install(monkeypatch, db)
    res = agent.run_agent(db, 5, 1, **kw)
    return res, fakes, pilot


def _always_failing(n=8, key="claims"):
    """A plan where one check fails on every draft, so the letter never gets clean."""
    return {v: {key: False} for v in range(1, n + 1)}


def _revisions(calls) -> int:
    return calls.count("revise_letter")


# ---------------------------------------------------------------------------
# 1. the constants and Budget's defaults
# ---------------------------------------------------------------------------
def test_the_constants_are_the_documented_defaults():
    assert DEFAULT_LETTER_MAX_DRAFTS == 3
    assert DEFAULT_LETTER_MAX_TOOL_CALLS == 15
    assert DEFAULT_LLM_RUN_BUDGET_USD == 0.50


def test_budget_defaults_come_from_the_preference_constants():
    b = Budget()
    assert b.max_drafts == DEFAULT_LETTER_MAX_DRAFTS
    assert b.max_tool_calls == DEFAULT_LETTER_MAX_TOOL_CALLS
    assert b.max_cost_usd == DEFAULT_LLM_RUN_BUDGET_USD
    assert (b.drafts_used, b.tool_calls, b.side_calls, b.cost_usd) == (0, 0, 0, 0)


def test_defaults_dict_has_the_three_keys_at_the_constants():
    assert DEFAULTS["letter_max_drafts"] == DEFAULT_LETTER_MAX_DRAFTS
    assert DEFAULTS["letter_max_tool_calls"] == DEFAULT_LETTER_MAX_TOOL_CALLS
    assert DEFAULTS["llm_run_budget_usd"] == DEFAULT_LLM_RUN_BUDGET_USD


def test_the_defaults_sit_inside_their_bounds():
    for key, default in (("letter_max_drafts", 3), ("letter_max_tool_calls", 15), ("llm_run_budget_usd", 0.5)):
        lo, hi = LIMIT_BOUNDS[key]
        assert lo <= default <= hi
    assert LIMIT_BOUNDS == {
        "letter_max_drafts": (1, 5), "letter_max_tool_calls": (6, 40), "llm_run_budget_usd": (0.05, 5.0),
    }


def test_a_default_full_run_fits_the_default_tool_call_cap():
    # 4N+2 letter tool calls for N drafts (analyze, match, generate, 3 checks, then revise + 3 checks each)
    assert 4 * DEFAULT_LETTER_MAX_DRAFTS + 2 <= DEFAULT_LETTER_MAX_TOOL_CALLS + 1


# ---------------------------------------------------------------------------
# 2. letter_settings()["limits"]
# ---------------------------------------------------------------------------
def test_limits_with_nothing_stored_are_the_defaults(db):
    assert letter_settings(db, 1)["limits"] == DEFAULT_LIMITS


def test_stored_in_bounds_limits_are_used(db):
    set_preferences(db, 1, {"letter_max_drafts": 2, "letter_max_tool_calls": 10, "llm_run_budget_usd": 1.25})
    assert letter_settings(db, 1)["limits"] == {"max_drafts": 2, "max_tool_calls": 10, "max_cost_usd": 1.25}


@pytest.mark.parametrize("key,lo,hi", [
    ("letter_max_drafts", 1, 5), ("letter_max_tool_calls", 6, 40), ("llm_run_budget_usd", 0.05, 5.0),
])
def test_the_bound_edges_themselves_are_accepted(db, key, lo, hi):
    out = {"letter_max_drafts": "max_drafts", "letter_max_tool_calls": "max_tool_calls",
           "llm_run_budget_usd": "max_cost_usd"}[key]
    for edge in (lo, hi):
        set_preferences(db, 1, {key: edge})
        assert letter_settings(db, 1)["limits"][out] == edge


@pytest.mark.parametrize("key,out,default,bad", [
    ("letter_max_drafts", "max_drafts", 3, [0, 6, -1, 100]),
    ("letter_max_tool_calls", "max_tool_calls", 15, [0, 5, 41, 1000]),
    ("llm_run_budget_usd", "max_cost_usd", 0.5, [0, 0.01, 0.049, 9.0, 5.01, -1.0]),
])
def test_an_out_of_bounds_value_falls_back_to_its_default(db, key, out, default, bad):
    for value in bad:
        set_preferences(db, 1, {key: value})
        assert letter_settings(db, 1)["limits"][out] == default, value


@pytest.mark.parametrize("key,out,default", [
    ("letter_max_drafts", "max_drafts", 3),
    ("letter_max_tool_calls", "max_tool_calls", 15),
    ("llm_run_budget_usd", "max_cost_usd", 0.5),
])
@pytest.mark.parametrize("bad", [True, False, "3", "", None, [2], {"n": 2}])
def test_a_bool_string_none_or_container_falls_back_to_its_default(db, key, out, default, bad):
    set_preferences(db, 1, {key: bad})
    assert letter_settings(db, 1)["limits"][out] == default


@pytest.mark.parametrize("key,out,default", [
    ("letter_max_drafts", "max_drafts", 3),
    ("letter_max_tool_calls", "max_tool_calls", 15),
])
@pytest.mark.parametrize("bad", [2.5, 7.0001, float("inf"), float("nan")])
def test_a_non_integral_float_for_an_int_key_falls_back_to_its_default(db, key, out, default, bad):
    set_preferences(db, 1, {key: bad})
    assert letter_settings(db, 1)["limits"][out] == default


def test_an_integral_float_for_an_int_key_is_accepted_as_an_int(db):
    set_preferences(db, 1, {"letter_max_drafts": 4.0, "letter_max_tool_calls": 20.0})
    limits = letter_settings(db, 1)["limits"]
    assert limits["max_drafts"] == 4 and type(limits["max_drafts"]) is int
    assert limits["max_tool_calls"] == 20 and type(limits["max_tool_calls"]) is int


def test_an_integral_float_outside_the_bounds_is_still_rejected(db):
    set_preferences(db, 1, {"letter_max_drafts": 6.0, "letter_max_tool_calls": 5.0})
    limits = letter_settings(db, 1)["limits"]
    assert limits["max_drafts"] == 3 and limits["max_tool_calls"] == 15


def test_an_int_for_the_usd_key_reads_as_a_float(db):
    set_preferences(db, 1, {"llm_run_budget_usd": 1})
    value = letter_settings(db, 1)["limits"]["max_cost_usd"]
    assert value == 1.0 and type(value) is float


def test_the_usd_key_rejects_nan_and_inf(db):
    for bad in (float("nan"), float("inf")):
        set_preferences(db, 1, {"llm_run_budget_usd": bad})
        assert letter_settings(db, 1)["limits"]["max_cost_usd"] == 0.5


def test_one_bad_limit_does_not_spoil_the_others(db):
    set_preferences(db, 1, {"letter_max_drafts": "many", "letter_max_tool_calls": 12, "llm_run_budget_usd": 2.0})
    assert letter_settings(db, 1)["limits"] == {"max_drafts": 3, "max_tool_calls": 12, "max_cost_usd": 2.0}


def test_settings_for_a_missing_profile_still_give_default_limits(db):
    assert letter_settings(db, 404)["limits"] == DEFAULT_LIMITS


# ---------------------------------------------------------------------------
# 3. PUT /profile/{id}/preferences
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("key,value", [
    ("letter_max_drafts", 1), ("letter_max_drafts", 5),
    ("letter_max_tool_calls", 6), ("letter_max_tool_calls", 40),
    ("llm_run_budget_usd", 0.05), ("llm_run_budget_usd", 5.0),
])
def test_put_accepts_each_bound_edge_and_returns_it(client, db, key, value):
    res = client.put("/profile/1/preferences", json={key: value})
    assert res.status_code == 200
    assert res.json()[key] == value
    assert client.get("/profile/1/preferences").json()[key] == value


@pytest.mark.parametrize("key,value", [
    ("letter_max_drafts", 0), ("letter_max_drafts", 6),
    ("letter_max_tool_calls", 5), ("letter_max_tool_calls", 41),
    ("llm_run_budget_usd", 0.04), ("llm_run_budget_usd", 5.01),
    ("letter_max_drafts", 2.5), ("letter_max_tool_calls", "lots"), ("llm_run_budget_usd", "free"),
])
def test_put_rejects_values_just_outside_the_bounds(client, db, key, value):
    res = client.put("/profile/1/preferences", json={key: value})
    assert res.status_code == 422
    assert client.get("/profile/1/preferences").json()[key] == DEFAULTS[key]  # nothing was stored


def test_put_with_all_three_limits_feeds_letter_settings(client, db):
    res = client.put("/profile/1/preferences", json={
        "letter_max_drafts": 4, "letter_max_tool_calls": 30, "llm_run_budget_usd": 1.5,
    })
    assert res.status_code == 200
    db.expire_all()
    assert letter_settings(db, 1)["limits"] == {"max_drafts": 4, "max_tool_calls": 30, "max_cost_usd": 1.5}


def test_a_partial_update_keeps_the_other_limits(client, db):
    client.put("/profile/1/preferences", json={
        "letter_max_drafts": 2, "letter_max_tool_calls": 10, "llm_run_budget_usd": 1.25,
    })
    res = client.put("/profile/1/preferences", json={"letter_max_tool_calls": 25})
    body = res.json()
    assert body["letter_max_drafts"] == 2 and body["letter_max_tool_calls"] == 25
    assert body["llm_run_budget_usd"] == 1.25
    res = client.put("/profile/1/preferences", json={"letter_engine": "workflow"})
    body = res.json()
    assert (body["letter_max_drafts"], body["letter_max_tool_calls"], body["llm_run_budget_usd"]) == (2, 25, 1.25)


def test_get_preferences_shows_the_defaults_of_the_three_limits(client, db):
    prefs = client.get("/profile/1/preferences").json()
    assert (prefs["letter_max_drafts"], prefs["letter_max_tool_calls"], prefs["llm_run_budget_usd"]) == (3, 15, 0.5)


# ---------------------------------------------------------------------------
# 4. open_run(limits=)
# ---------------------------------------------------------------------------
def test_open_run_with_limits_sets_the_budget_and_persists_it(db):
    state, ctx = open_run(db, 5, 1, "agent", limits={"max_drafts": 2, "max_tool_calls": 10, "max_cost_usd": 1.25})
    b = state.budget
    assert (b.max_drafts, b.max_tool_calls, b.max_cost_usd) == (2, 10, 1.25)
    saved = json.loads(_run_row(db, ctx.run.id).state)["budget"]
    assert (saved["max_drafts"], saved["max_tool_calls"], saved["max_cost_usd"]) == (2, 10, 1.25)
    assert _run_state(db, ctx.run.id).budget.max_drafts == 2


def test_open_run_without_limits_uses_the_defaults(db):
    for limits in (None, {}):
        state, ctx = open_run(db, 5, 1, "agent", limits=limits)
        assert (state.budget.max_drafts, state.budget.max_tool_calls, state.budget.max_cost_usd) == (3, 15, 0.5)
        saved = json.loads(_run_row(db, ctx.run.id).state)["budget"]
        assert (saved["max_drafts"], saved["max_tool_calls"], saved["max_cost_usd"]) == (3, 15, 0.5)


@pytest.mark.parametrize("limits,expected", [
    ({"max_drafts": 5}, (5, 15, 0.5)),
    ({"max_tool_calls": 40}, (3, 40, 0.5)),
    ({"max_cost_usd": 2.0}, (3, 15, 2.0)),
    ({"max_drafts": None, "max_tool_calls": 22, "max_cost_usd": None}, (3, 22, 0.5)),
    ({"max_drafts": 1, "max_tool_calls": None}, (1, 15, 0.5)),
])
def test_a_missing_or_none_key_keeps_its_default(db, limits, expected):
    state, ctx = open_run(db, 5, 1, "agent", limits=limits)
    assert (state.budget.max_drafts, state.budget.max_tool_calls, state.budget.max_cost_usd) == expected
    saved = _run_state(db, ctx.run.id).budget
    assert (saved.max_drafts, saved.max_tool_calls, saved.max_cost_usd) == expected


def test_open_run_ignores_keys_that_are_not_limits(db):
    state, _ = open_run(db, 5, 1, "agent", limits={"cost_usd": 99, "tool_calls": 7, "drafts_used": 2, "bogus": 1})
    b = state.budget
    assert (b.cost_usd, b.tool_calls, b.drafts_used) == (0, 0, 0)
    assert (b.max_drafts, b.max_tool_calls, b.max_cost_usd) == (3, 15, 0.5)


def test_limit_fields_are_exactly_the_three_budget_limits():
    assert outcome.LIMIT_FIELDS == ("max_drafts", "max_tool_calls", "max_cost_usd")
    for field in outcome.LIMIT_FIELDS:
        assert field in Budget.model_fields


def test_open_run_accepts_what_letter_settings_produces(db):
    set_preferences(db, 1, {"letter_max_drafts": 2, "letter_max_tool_calls": 9, "llm_run_budget_usd": 0.75})
    state, _ = open_run(db, 5, 1, "workflow", limits=letter_settings(db, 1)["limits"])
    assert (state.budget.max_drafts, state.budget.max_tool_calls, state.budget.max_cost_usd) == (2, 9, 0.75)


def test_open_run_still_raises_for_a_missing_job_with_limits(db):
    with pytest.raises(ValueError, match="not found"):
        open_run(db, 404, 1, "agent", limits=DEFAULT_LIMITS)
    assert db.scalars(select(LetterRun)).all() == []


# ---------------------------------------------------------------------------
# 5. the fixed workflow
# ---------------------------------------------------------------------------
def _wf(db, monkeypatch, plan=None, **kw):
    script = Script(plan if plan is not None else _always_failing()).install(monkeypatch)
    res = workflow.run_workflow(db, 5, 1, **kw)
    return res, script


def test_workflow_default_limits_allow_two_revisions(db, monkeypatch):
    res, script = _wf(db, monkeypatch)
    assert res.status == "budget_stopped" and "revision limit reached (2/2)" in res.stop_reason
    assert _revisions(script.calls) == 2 and len(res.state.drafts) == 3
    assert res.state.budget.max_drafts == 3


def test_workflow_with_max_drafts_1_never_revises(db, monkeypatch):
    res, script = _wf(db, monkeypatch, limits={"max_drafts": 1})
    assert res.status == "budget_stopped"
    assert "revision limit reached (0/0)" in res.stop_reason
    assert _revisions(script.calls) == 0 and len(res.state.drafts) == 1
    assert script.calls == CLEAN_CALLS
    assert res.draft.version == 1 and res.clean is False and res.open_issues
    row = _run_row(db, res.run_id)
    assert row.status == "budget_stopped" and row.final_draft_version == 1


def test_workflow_with_max_drafts_1_is_still_done_when_draft_one_is_clean(db, monkeypatch):
    res, script = _wf(db, monkeypatch, plan={}, limits={"max_drafts": 1})
    assert res.status == "done" and res.clean and len(res.state.drafts) == 1


def test_workflow_with_max_drafts_2_makes_exactly_one_revision(db, monkeypatch):
    res, script = _wf(db, monkeypatch, limits={"max_drafts": 2})
    assert res.status == "budget_stopped" and "revision limit reached (1/1)" in res.stop_reason
    assert _revisions(script.calls) == 1 and len(res.state.drafts) == 2
    assert script.calls == CLEAN_CALLS + ["revise_letter"] + CHECK_CALLS


def test_workflow_with_max_drafts_4_makes_three_revisions(db, monkeypatch):
    res, script = _wf(db, monkeypatch, limits={"max_drafts": 4, "max_tool_calls": 40})
    assert res.status == "budget_stopped" and "revision limit reached (3/3)" in res.stop_reason
    assert _revisions(script.calls) == 3 and len(res.state.drafts) == 4
    assert len(script.calls) == 4 * 4 + 2
    assert res.state.budget.tool_calls == 18 < res.state.budget.max_tool_calls == 40


def test_workflow_with_max_drafts_5_makes_four_revisions(db, monkeypatch):
    res, script = _wf(db, monkeypatch, limits={"max_drafts": 5, "max_tool_calls": 40})
    assert _revisions(script.calls) == 4 and len(res.state.drafts) == 5


def test_workflow_stops_early_once_a_revision_goes_clean(db, monkeypatch):
    res, script = _wf(db, monkeypatch, plan={1: {"claims": False}, 2: {"style": False}},
                      limits={"max_drafts": 5, "max_tool_calls": 40})
    assert res.status == "done" and res.clean and res.draft.version == 3
    assert _revisions(script.calls) == 2


def test_an_explicit_max_revisions_wins_over_max_drafts(db, monkeypatch):
    res, script = _wf(db, monkeypatch, limits={"max_drafts": 4, "max_tool_calls": 40}, max_revisions=1)
    assert res.status == "budget_stopped" and "revision limit reached (1/1)" in res.stop_reason
    assert _revisions(script.calls) == 1 and len(res.state.drafts) == 2


def test_an_explicit_max_revisions_of_zero_wins_over_a_big_draft_budget(db, monkeypatch):
    res, script = _wf(db, monkeypatch, limits={"max_drafts": 5, "max_tool_calls": 40}, max_revisions=0)
    assert _revisions(script.calls) == 0 and len(res.state.drafts) == 1


def test_the_draft_cap_still_binds_when_max_revisions_asks_for_more(db, monkeypatch):
    res, script = _wf(db, monkeypatch, limits={"max_drafts": 2}, max_revisions=5)
    assert res.status == "budget_stopped"
    assert "draft limit reached (2/2)" in res.stop_reason
    assert _revisions(script.calls) == 1 and len(res.state.drafts) == 2


def test_the_tool_call_cap_binds_before_the_draft_cap(db, monkeypatch):
    # 5 drafts would need 22 tool calls; the cap of 10 stops it first, with the best draft
    res, script = _wf(db, monkeypatch, limits={"max_drafts": 5, "max_tool_calls": 10})
    assert res.status == "budget_stopped" and "tool-call limit reached (10/10)" in res.stop_reason
    assert len(script.calls) == 10
    assert res.draft is not None and res.clean is False


def test_default_tool_call_cap_cuts_a_four_draft_run_short(db, monkeypatch):
    res, script = _wf(db, monkeypatch, limits={"max_drafts": 4})
    assert res.status == "budget_stopped" and "tool-call limit reached (15/15)" in res.stop_reason
    assert len(script.calls) == 15


def test_workflow_limits_are_persisted_on_the_run(db, monkeypatch):
    res, _ = _wf(db, monkeypatch, plan={}, limits={"max_drafts": 2, "max_tool_calls": 9, "max_cost_usd": 0.25})
    saved = _run_state(db, res.run_id).budget
    assert (saved.max_drafts, saved.max_tool_calls, saved.max_cost_usd) == (2, 9, 0.25)


def test_workflow_usd_limit_stops_the_run(db, monkeypatch):
    script = Script({}).install(monkeypatch)
    real = script.generate_letter

    def generate_and_spend(state, ctx):
        out = real(state, ctx)
        ctx.db.add(LlmUsage(task="generate", tier="strong", model="fake", cost_usd=Decimal("0.20"),
                            run_id=ctx.run.id))
        ctx.db.commit()
        return out

    monkeypatch.setattr(workflow, "generate_letter", generate_and_spend)
    res = workflow.run_workflow(db, 5, 1, limits={"max_cost_usd": 0.10})
    assert res.status == "budget_stopped" and "run budget reached" in res.stop_reason
    assert script.calls == ["analyze_job", "match_profile", "generate_letter"]
    assert res.draft.version == 1 and res.clean is False


# ---------------------------------------------------------------------------
# 6. the agent
# ---------------------------------------------------------------------------
def test_agent_default_limits_allow_three_drafts(db, monkeypatch):
    res, fakes, _ = _go_agent(db, monkeypatch, fakes=Fakes(_always_failing()))
    assert res.status == "budget_stopped" and "draft limit reached" in res.stop_reason
    assert len(res.state.drafts) == 3 and _revisions(fakes.calls) == 2
    assert res.state.budget.max_drafts == 3


def test_agent_with_max_drafts_2_stops_with_two_drafts(db, monkeypatch):
    res, fakes, _ = _go_agent(db, monkeypatch, fakes=Fakes(_always_failing()), limits={"max_drafts": 2})
    assert res.status == "budget_stopped" and "draft limit reached" in res.stop_reason
    assert len(res.state.drafts) == 2 and _revisions(fakes.calls) == 1
    assert res.clean is False and res.open_issues and res.draft is not None
    steps = _steps(db, res.run_id)
    assert steps[-1].tool == "finish" and steps[-1].error is None
    assert json.loads(steps[-1].result_summary)["out_of_drafts"] is True


def test_agent_revise_after_the_last_draft_is_refused_and_finish_is_accepted(db, monkeypatch):
    script = _t("analyze_job", "match_profile", "generate_letter", *CHECK_CALLS,
                "revise_letter", *CHECK_CALLS, "revise_letter", "finish")
    res, fakes, _ = _go_agent(db, monkeypatch, fakes=Fakes(_always_failing()),
                              pilot=Pilot(script), limits={"max_drafts": 2})
    assert len(res.state.drafts) == 2 and _revisions(fakes.calls) == 1  # the 2nd revise never ran
    steps = _steps(db, res.run_id)
    refused = [s for s in steps if (s.error or "").startswith(REFUSED)]
    assert [s.tool for s in refused] == ["revise_letter"]
    assert "draft limit reached (2/2)" in refused[0].error
    assert steps[-1].tool == "finish" and steps[-1].error is None
    assert res.status == "budget_stopped" and "draft limit reached" in res.stop_reason
    assert res.state.budget.tool_calls == 10  # the refusal cost no tool call


def test_agent_with_max_drafts_1_finishes_after_the_first_drafts_checks(db, monkeypatch):
    res, fakes, _ = _go_agent(db, monkeypatch, fakes=Fakes({1: {"claims": False}}), limits={"max_drafts": 1})
    assert res.status == "budget_stopped" and len(res.state.drafts) == 1
    assert _revisions(fakes.calls) == 0 and fakes.calls == CLEAN_CALLS


def test_agent_with_max_drafts_4_allows_three_revisions(db, monkeypatch):
    res, fakes, _ = _go_agent(db, monkeypatch, fakes=Fakes(_always_failing()),
                              limits={"max_drafts": 4, "max_tool_calls": 40})
    assert len(res.state.drafts) == 4 and _revisions(fakes.calls) == 3
    assert res.status == "budget_stopped" and "draft limit reached" in res.stop_reason


def test_agent_with_a_tool_call_cap_of_8_stops_at_the_cap(db, monkeypatch):
    res, fakes, _ = _go_agent(db, monkeypatch, fakes=Fakes(_always_failing()),
                              limits={"max_drafts": 5, "max_tool_calls": 8})
    assert res.status == "budget_stopped" and "tool-call limit reached (8/8)" in res.stop_reason
    assert len(fakes.calls) == 8  # never more than the cap
    assert res.state.budget.tool_calls == 8
    assert res.draft is not None and res.clean is False
    assert _run_row(db, res.run_id).tool_calls == 8


def test_agent_hitting_the_tool_call_cap_with_a_clean_letter_ends_done_by_code(db, monkeypatch):
    res, fakes, _ = _go_agent(db, monkeypatch, limits={"max_tool_calls": 6})
    assert res.status == "done" and res.clean and fakes.calls == CLEAN_CALLS
    last = _steps(db, res.run_id)[-1]
    summary = json.loads(last.result_summary)
    assert last.tool == "finish" and summary["by"] == "code" and "tool-call limit reached (6/6)" in summary["reason"]


def test_agent_stops_when_orchestrator_spend_reaches_a_tiny_run_budget(db, monkeypatch):
    res, fakes, pilot = _go_agent(db, monkeypatch, pilot=Pilot(autopilot=True, cost=0.02),
                                  limits={"max_cost_usd": 0.01})
    assert res.status == "budget_stopped" and "run budget reached" in res.stop_reason
    assert len(pilot.calls) == 1  # not asked a second time
    assert fakes.calls == ["analyze_job"]
    assert res.state.budget.cost_usd == pytest.approx(0.02)
    assert float(_run_row(db, res.run_id).cost_usd) == pytest.approx(0.02)
    assert res.account_limit is None


def test_agent_stops_on_run_budget_from_llm_usage_rows_of_the_run(db, monkeypatch):
    # spend logged by the tools themselves (llm_usage rows with the run id), not by the orchestrator
    fakes = Fakes()
    real = fakes.generate_letter

    def generate_and_spend(state, ctx):
        out = real(state, ctx)
        ctx.db.add(LlmUsage(task="generate", tier="strong", model="fake", cost_usd=Decimal("0.30"),
                            run_id=ctx.run.id))
        ctx.db.commit()
        return out

    fakes.generate_letter = generate_and_spend
    res, fakes, _ = _go_agent(db, monkeypatch, fakes=fakes, limits={"max_cost_usd": 0.25})
    assert res.status == "budget_stopped" and "run budget reached" in res.stop_reason
    assert fakes.calls == ["analyze_job", "match_profile", "generate_letter"]
    assert res.draft.version == 1 and res.clean is False


def test_agent_a_larger_run_budget_lets_the_same_spend_through(db, monkeypatch):
    res, fakes, pilot = _go_agent(db, monkeypatch, pilot=Pilot(autopilot=True, cost=0.02),
                                  limits={"max_cost_usd": 5.0})
    assert res.status == "done" and res.clean and fakes.calls == CLEAN_CALLS
    assert len(pilot.calls) == 7


def test_agent_limits_are_persisted_on_the_run(db, monkeypatch):
    res, _, _ = _go_agent(db, monkeypatch, limits={"max_drafts": 2, "max_tool_calls": 11, "max_cost_usd": 0.6})
    saved = _run_state(db, res.run_id).budget
    assert (saved.max_drafts, saved.max_tool_calls, saved.max_cost_usd) == (2, 11, 0.6)


def test_agent_orchestrator_prompt_shows_the_run_limits(db, monkeypatch):
    _, _, pilot = _go_agent(db, monkeypatch, limits={"max_drafts": 2, "max_tool_calls": 12})
    first = pilot.calls[0]["messages"][0]["content"]
    assert "tool calls 0/12" in first and "drafts 0/2" in first


# ---------------------------------------------------------------------------
# 7. a resumed run keeps the limits it opened with
# ---------------------------------------------------------------------------
_GAP_REQS = (
    dict(id="R1", text="Experience building dashboards with Power BI", importance="essential",
         letter_role="headline", skill="Power BI"),
    dict(id="R2", text="Build REST APIs", importance="essential", letter_role="headline"),
)
_RUN_LIMITS = {"max_drafts": 2, "max_tool_calls": 12, "max_cost_usd": 0.75}


def test_a_resumed_agent_run_keeps_its_limits(db, monkeypatch):
    fakes = Fakes(reqs=_GAP_REQS, match={"R1": ("gap", [])}).install(monkeypatch)
    Pilot(_t("analyze_job", "match_profile", "ask_user")).install(monkeypatch, db)
    first = agent.run_agent(db, 5, 1, gap_policy=ask_user_gaps, limits=_RUN_LIMITS)
    assert first.status == "waiting_user"
    paused = _run_state(db, first.run_id).budget
    assert (paused.max_drafts, paused.max_tool_calls, paused.max_cost_usd) == (2, 12, 0.75)

    answers.answer_no(db, first.run_id, "Q1")
    assert _run_row(db, first.run_id).status == "answered"

    # the preferences changing in the meantime must not touch a run already opened
    set_preferences(db, 1, {"letter_max_drafts": 5, "letter_max_tool_calls": 40, "llm_run_budget_usd": 5.0})
    pilot = Pilot(autopilot=True).install(monkeypatch, db)
    res = agent.resume_agent(db, first.run_id, gap_policy=ask_user_gaps)
    assert res.status == "done" and res.clean
    assert res.state.budget.max_drafts == 2
    assert res.state.budget.max_tool_calls == 12 + RESUME_EXTRA_CALLS
    assert res.state.budget.max_cost_usd == 0.75
    assert pilot.calls and fakes.calls.count("analyze_job") == 1
    saved = _run_state(db, first.run_id).budget
    assert (saved.max_drafts, saved.max_tool_calls) == (2, 12 + RESUME_EXTRA_CALLS)


def test_a_resumed_agent_run_still_obeys_its_draft_cap(db, monkeypatch):
    fakes = Fakes(plan=_always_failing(), reqs=_GAP_REQS, match={"R1": ("gap", [])}).install(monkeypatch)
    Pilot(_t("analyze_job", "match_profile", "ask_user")).install(monkeypatch, db)
    first = agent.run_agent(db, 5, 1, gap_policy=ask_user_gaps, limits=_RUN_LIMITS)
    answers.answer_no(db, first.run_id, "Q1")
    Pilot(autopilot=True).install(monkeypatch, db)
    res = agent.resume_agent(db, first.run_id, gap_policy=ask_user_gaps)
    assert res.status == "budget_stopped" and "draft limit reached" in res.stop_reason
    assert len(res.state.drafts) == 2 and _revisions(fakes.calls) == 1


def test_a_resumed_workflow_run_keeps_its_limits(db, monkeypatch):
    script = Script(reqs=_GAP_REQS, match={"R1": ("gap", [])}).install(monkeypatch)
    first = workflow.run_workflow(db, 5, 1, gap_policy=ask_user_gaps, limits=_RUN_LIMITS)
    assert first.status == "waiting_user"
    assert _run_state(db, first.run_id).budget.max_drafts == 2
    answers.answer_no(db, first.run_id, "Q1")

    script.plan = _always_failing()
    res = workflow.resume_workflow(db, first.run_id, gap_policy=ask_user_gaps)
    assert res.state.budget.max_drafts == 2
    assert res.state.budget.max_tool_calls == 12 + RESUME_EXTRA_CALLS
    assert res.state.budget.max_cost_usd == 0.75
    # max_drafts 2 -> one revision, whatever the preferences say now
    assert res.status == "budget_stopped" and _revisions(script.calls) == 1 and len(res.state.drafts) == 2


def test_reopen_run_adds_only_the_resume_allowance_to_the_tool_calls(db):
    state, ctx = open_run(db, 5, 1, "agent", limits=_RUN_LIMITS)
    run = _run_row(db, ctx.run.id)
    run.status = "answered"
    db.commit()
    reopened, _ = outcome.reopen_run(db, ctx.run.id)
    assert reopened.budget.max_tool_calls == 12 + RESUME_EXTRA_CALLS == 14
    assert reopened.budget.max_drafts == 2 and reopened.budget.max_cost_usd == 0.75


# ---------------------------------------------------------------------------
# 8. engines.run_letter and production._run_pipeline forward the limits
# ---------------------------------------------------------------------------
class _Recorder:
    def __init__(self):
        self.calls: list[tuple[tuple, dict]] = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return "result"


@pytest.mark.parametrize("engine_name,module", [("agent", agent), ("workflow", workflow)])
def test_run_letter_forwards_limits_to_the_engine(db, monkeypatch, engine_name, module):
    rec = _Recorder()
    monkeypatch.setattr(module, "run_agent" if module is agent else "run_workflow", rec)
    out = engines.run_letter(db, 5, 1, engine=engine_name, limits=_RUN_LIMITS)
    assert out == "result"
    ((args, kwargs),) = rec.calls
    assert args == (db, 5, 1)
    assert kwargs["limits"] == _RUN_LIMITS


@pytest.mark.parametrize("engine_name,module", [("agent", agent), ("workflow", workflow)])
def test_run_letter_limits_default_to_none(db, monkeypatch, engine_name, module):
    rec = _Recorder()
    monkeypatch.setattr(module, "run_agent" if module is agent else "run_workflow", rec)
    engines.run_letter(db, 5, 1, engine=engine_name)
    ((_, kwargs),) = rec.calls
    assert kwargs["limits"] is None


def test_run_letter_forwards_side_outputs_alongside_the_limits(db, monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(agent, "run_agent", rec)
    engines.run_letter(db, 5, 1, side_outputs=("answer_screening",), limits=_RUN_LIMITS)
    ((_, kwargs),) = rec.calls
    assert kwargs["side_outputs_enabled"] == ("answer_screening",) and kwargs["limits"] == _RUN_LIMITS


def test_run_letter_really_opens_the_run_with_the_limits(db, monkeypatch):
    # no stubbed engine: the workflow's tools are scripted, so this goes through open_run for real
    Script({}).install(monkeypatch)
    res = engines.run_letter(db, 5, 1, engine="workflow", limits={"max_drafts": 2, "max_tool_calls": 9})
    assert (res.state.budget.max_drafts, res.state.budget.max_tool_calls) == (2, 9)
    assert res.state.budget.max_cost_usd == 0.5


def test_pipeline_passes_the_default_limits(db, monkeypatch):
    fe = FakeEngines(monkeypatch, db)
    landed = production.run_work(Work(PIPELINE, 5), 1, db=db)
    assert landed.status == "done"
    assert fe.limits == DEFAULT_LIMITS


def test_pipeline_passes_stored_non_default_limits(db, monkeypatch):
    set_preferences(db, 1, {"letter_max_drafts": 2, "letter_max_tool_calls": 10, "llm_run_budget_usd": 1.25})
    fe = FakeEngines(monkeypatch, db)
    production.run_work(Work(PIPELINE, 5), 1, db=db)
    assert fe.limits == {"max_drafts": 2, "max_tool_calls": 10, "max_cost_usd": 1.25}
    assert fe.limits == letter_settings(db, 1)["limits"]


def test_pipeline_passes_defaults_for_invalid_stored_limits(db, monkeypatch):
    set_preferences(db, 1, {"letter_max_drafts": 99, "letter_max_tool_calls": "x", "llm_run_budget_usd": -3})
    fe = FakeEngines(monkeypatch, db)
    production.run_work(Work(PIPELINE, 5), 1, db=db)
    assert fe.limits == DEFAULT_LIMITS


def test_the_explicit_regenerate_passes_the_limits_too(db, monkeypatch):
    set_preferences(db, 1, {"letter_max_drafts": 4, "letter_max_tool_calls": 30})
    fe = FakeEngines(monkeypatch, db)
    landed = production.generate_for(db, 5, 1)
    assert landed is not None and landed.status == "done"
    assert fe.limits == {"max_drafts": 4, "max_tool_calls": 30, "max_cost_usd": 0.5}


def test_resuming_a_run_in_production_does_not_pass_new_limits(db, monkeypatch):
    # resume_letter takes no limits: the run keeps the ones it opened with
    fe = FakeEngines(monkeypatch, db)
    state, ctx = open_run(db, 5, 1, "agent", limits=_RUN_LIMITS)
    run = _run_row(db, ctx.run.id)
    run.status = "answered"
    db.commit()
    production.run_work(Work(production.RESUME, 5, run_id=run.id), 1, db=db)
    assert fe.resume_calls == [run.id]
    assert fe.run_calls == []
