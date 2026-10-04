"""Tests for Phase 7c, part A: the side-output tools and the rules around them.

Covers the new state fields, the guardrail helpers (confirmed_gaps, letter_final), the runner's
side-call accounting, app/llm/letter/side_outputs.py (readiness, gate, due, status, run_due),
the registry and open_run changes, and the three tools themselves (answer_screening,
suggest_learning, suggest_resume_tweaks).

No LLM and no network: ``complete_json`` is replaced in each tool module by a fake that returns
canned JSON and records what it was asked. So what is under test is the code around the model:
grounding checks, ordering, link stripping, dropping what the model invented, and what is logged.
"""
from __future__ import annotations

import importlib
import json
import sqlite3
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import gaps
from app.db import Base
from app.llm.letter import guardrails, registry, side_outputs
from app.llm.letter.outcome import leave_out_gaps, open_run
from app.llm.letter.runner import ToolError, execute_tool, start_run
from app.llm.letter.state import (
    SIDE_OUTPUT_TOOLS,
    Budget,
    Check,
    Claim,
    JobInfo,
    LetterState,
    Requirement,
    UserDecision,
)
from app.models import (
    Experience,
    JobListing,
    LetterRun,
    LlmUsage,
    Match,
    Profile,
    Qualification,
    Skill,
    UserCv,
)

as_mod = importlib.import_module("app.llm.letter.tools.answer_screening")
sl_mod = importlib.import_module("app.llm.letter.tools.suggest_learning")
rt_mod = importlib.import_module("app.llm.letter.tools.suggest_resume_tweaks")

AD = "We build logistics software using Python and REST APIs. Tell us about your experience."


# ---------------------------------------------------------------------------
# fixtures and helpers
# ---------------------------------------------------------------------------
@pytest.fixture()
def db():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)

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
                       company="LogiCo", raw_description=AD),
        ])
        session.flush()
        session.add(Match(id=9, user_id=1, job_id=5, score=80))
        session.commit()
        yield session
    Base.metadata.drop_all(eng)


def _req(rid, text="x", importance="essential", role="headline", status="supported", evidence=(),
         decision=None, skill=""):
    return Requirement(
        id=rid, text=text, importance=importance, letter_role=role, status=status,
        evidence=list(evidence), user_decision=decision, theme=f"theme-{rid}", skill=skill,
    )


NO = UserDecision(choice="leave_out", answer="No")


def _state(requirements=None, questions=(), enabled=(), keywords=()) -> LetterState:
    reqs = requirements if requirements is not None else [
        _req("R1", "Build REST APIs", evidence=["experience:12#s1"]),
    ]
    st = LetterState(
        profile_id=1,
        job=JobInfo(job_id=5, title="Engineer", screening_questions=list(questions), keywords=list(keywords)),
        requirements=reqs,
    )
    st.side_outputs.enabled = list(enabled)
    return st


def _draft(state, claims=True, requirements=True, style=True, text=None):
    """Add a draft and record its checks (True/False, or None = never ran)."""
    d = state.add_draft(text or f"draft {len(state.drafts) + 1}", [Claim(text="c", source="skill:7")])
    for key, ok in (("claims", claims), ("requirements", requirements), ("style", style)):
        if ok is not None:
            state.record_check(key, Check(passed=bool(ok), issues=[] if ok else [f"{key} broke"]))
    return d


def _ctx(db, state):
    return start_run(db, 9, "workflow", state)


def _run_row(db, run_id) -> LetterRun:
    db.expire_all()
    return db.get(LetterRun, run_id)


class FakeJson:
    """Stands in for ``complete_json``: returns ``response`` and records every call."""

    def __init__(self, response):
        self.response = response
        self.calls: list[dict] = []

    def __call__(self, system, user, schema=None, **kw):
        self.calls.append({"system": system, "user": user, "schema": schema, "kw": kw})
        return self.response


def _install(monkeypatch, mod, response) -> FakeJson:
    fake = FakeJson(response)
    monkeypatch.setattr(mod, "complete_json", fake)
    return fake


# ===========================================================================
# 1-2. State
# ===========================================================================
def test_a_state_saved_before_7c_still_loads_with_empty_defaults():
    old = _state([_req("R1", decision=NO)]).model_dump()
    del old["side_outputs"]
    del old["budget"]["side_calls"]
    del old["requirements"][0]["user_decision"]["assumed"]
    state = LetterState.model_validate_json(json.dumps(old))
    so = state.side_outputs
    assert (so.enabled, so.ran, so.errors) == ([], [], {})
    assert (so.screening_answers, so.learning_suggestions, so.resume_notes) == ([], [], None)
    assert state.budget.side_calls == 0
    assert state.requirements[0].user_decision.assumed is False


def test_over_cost_is_true_at_and_above_the_run_cap_only():
    b = Budget(max_cost_usd=0.5)
    b.cost_usd = 0.49
    assert not b.over_cost() and b.exceeded() is None
    b.cost_usd = 0.5
    assert b.over_cost() and "run budget reached" in b.exceeded()
    b.cost_usd = 0.9
    assert b.over_cost()


def test_tool_call_limit_still_reported_by_exceeded():
    b = Budget(tool_calls=15, max_tool_calls=15)
    assert "tool-call limit" in b.exceeded()


def test_side_calls_never_trigger_the_tool_call_limit():
    b = Budget(tool_calls=14, max_tool_calls=15, side_calls=3)
    assert b.exceeded() is None


# ===========================================================================
# 3. confirmed_gaps
# ===========================================================================
def test_confirmed_gaps_are_the_users_nos_and_remembered_nos():
    state = _state([
        _req("R1", status="gap", decision=UserDecision(choice="leave_out", answer="No")),
        _req("R2", status="gap", decision=UserDecision(choice="leave_out", remembered=True)),
    ])
    assert [r.id for r in guardrails.confirmed_gaps(state)] == ["R1", "R2"]


def test_confirmed_gaps_leave_out_what_nobody_confirmed():
    state = _state([
        _req("R1", status="gap", decision=UserDecision(choice="leave_out", assumed=True)),
        _req("R2", status="gap", role="not_for_letter", decision=NO),
        _req("R3", status="gap"),  # no decision at all
        _req("R4", status="gap", decision=UserDecision(choice="have_it", answer="yes I do")),
    ])
    assert guardrails.confirmed_gaps(state) == []


# ===========================================================================
# 4. letter_final
# ===========================================================================
def test_letter_final_is_none_without_a_draft():
    assert guardrails.letter_final(_state()) is None


def test_letter_final_is_none_while_a_check_has_not_run():
    state = _state()
    _draft(state, style=None)
    assert guardrails.letter_final(state) is None


def test_letter_final_is_none_while_a_check_failed_and_drafts_are_left():
    state = _state()
    _draft(state, claims=False)
    assert guardrails.letter_final(state) is None


def test_letter_final_is_the_latest_draft_when_every_check_passed():
    state = _state()
    _draft(state, claims=False)
    d2 = _draft(state)
    assert guardrails.letter_final(state) is d2


def test_letter_final_is_the_best_draft_when_out_of_drafts():
    state = _state()
    d1 = _draft(state, claims=True, requirements=True, style=False)  # best: claims passed
    _draft(state, claims=False)
    d3 = _draft(state, claims=False)
    assert state.latest_draft is d3
    final = guardrails.letter_final(state)
    assert final is d1 and final is not d3


# ===========================================================================
# 5. execute_tool and side calls
# ===========================================================================
def _ok(state, ctx):
    return {"fine": True}


def _boom(state, ctx):
    raise ToolError("boom")


def test_a_side_tool_counts_as_a_side_call_not_a_tool_call(db):
    state = _state()
    ctx = _ctx(db, state)
    res = execute_tool(ctx, state, "answer_screening", _ok)
    assert res.ok
    assert (state.budget.side_calls, state.budget.tool_calls) == (1, 0)
    assert state.side_outputs.ran == ["answer_screening"]
    assert _run_row(db, ctx.run.id).tool_calls == 1


def test_the_run_row_counts_letter_and_side_calls_together(db):
    state = _state()
    ctx = _ctx(db, state)
    execute_tool(ctx, state, "check_claims", _ok)
    execute_tool(ctx, state, "suggest_learning", _ok)
    execute_tool(ctx, state, "suggest_resume_tweaks", _ok)
    assert (state.budget.tool_calls, state.budget.side_calls) == (1, 2)
    assert _run_row(db, ctx.run.id).tool_calls == 3


def test_a_letter_tool_still_counts_in_tool_calls(db):
    state = _state()
    ctx = _ctx(db, state)
    execute_tool(ctx, state, "style_lint", _ok)
    assert (state.budget.tool_calls, state.budget.side_calls) == (1, 0)
    assert state.side_outputs.ran == []


def test_a_failing_side_tool_is_recorded_and_still_marked_as_run(db):
    state = _state()
    ctx = _ctx(db, state)
    res = execute_tool(ctx, state, "suggest_learning", _boom)
    assert not res.ok and res.error == "boom"
    assert state.side_outputs.errors == {"suggest_learning": "boom"}
    assert state.side_outputs.ran == ["suggest_learning"]


# ===========================================================================
# 6. not_ready
# ===========================================================================
def _confirmed_gap_reqs():
    return [_req("R1", evidence=["experience:12#s1"]), _req("R2", status="gap", decision=NO, skill="Power BI")]


def test_screening_is_later_with_no_requirements():
    state = _state([], questions=["Why us?"])
    assert side_outputs.not_ready(state, "answer_screening")[0] == side_outputs.LATER


def test_screening_is_not_needed_when_the_ad_has_no_questions():
    kind, reason = side_outputs.not_ready(_state(), "answer_screening")
    assert kind == side_outputs.NOT_NEEDED and "no screening questions" in reason


def test_screening_waits_for_an_unmatched_requirement():
    state = _state([_req("R1", status="unknown")], questions=["Why us?"])
    kind, reason = side_outputs.not_ready(state, "answer_screening")
    assert kind == side_outputs.LATER and "R1" in reason


def test_screening_waits_for_a_pending_must_have_gap():
    state = _state([_req("R1", status="gap")], questions=["Why us?"])
    assert side_outputs.not_ready(state, "answer_screening")[0] == side_outputs.LATER


def test_screening_is_ready_once_everything_is_settled():
    assert side_outputs.not_ready(_state(questions=["Why us?"]), "answer_screening") is None


def test_learning_is_not_needed_without_a_confirmed_gap():
    kind, _ = side_outputs.not_ready(_state(), "suggest_learning")
    assert kind == side_outputs.NOT_NEEDED
    assumed = _state([_req("R1", status="gap", decision=UserDecision(choice="leave_out", assumed=True))])
    assert side_outputs.not_ready(assumed, "suggest_learning")[0] == side_outputs.NOT_NEEDED


def test_learning_is_ready_with_a_confirmed_gap():
    assert side_outputs.not_ready(_state(_confirmed_gap_reqs()), "suggest_learning") is None


def test_learning_is_later_while_drafting_is_blocked():
    reqs = [*_confirmed_gap_reqs(), _req("R3", status="unknown")]
    assert side_outputs.not_ready(_state(reqs), "suggest_learning")[0] == side_outputs.LATER


def test_resume_tweaks_is_later_until_the_letter_is_final():
    state = _state()
    assert side_outputs.not_ready(state, "suggest_resume_tweaks")[0] == side_outputs.LATER
    _draft(state, claims=False)
    assert side_outputs.not_ready(state, "suggest_resume_tweaks")[0] == side_outputs.LATER
    _draft(state)
    assert side_outputs.not_ready(state, "suggest_resume_tweaks") is None


# ===========================================================================
# 7-9. gate, due, finish_blocked, status_lines
# ===========================================================================
def test_gate_refuses_a_tool_that_is_switched_off():
    state = _state(questions=["Why us?"], enabled=["suggest_learning"])
    assert "switched off" in side_outputs.gate("answer_screening")(state)


def test_gate_refuses_a_tool_that_already_ran():
    state = _state(questions=["Why us?"], enabled=["answer_screening"])
    state.side_outputs.ran.append("answer_screening")
    assert "already ran" in side_outputs.gate("answer_screening")(state)


def test_gate_gives_the_not_ready_reason():
    state = _state([_req("R1", status="unknown")], questions=["Why us?"], enabled=["answer_screening"])
    assert "R1" in side_outputs.gate("answer_screening")(state)


def test_gate_allows_a_tool_that_is_ready():
    state = _state(questions=["Why us?"], enabled=["answer_screening"])
    assert side_outputs.gate("answer_screening")(state) is None


def _all_due_state() -> LetterState:
    state = _state(_confirmed_gap_reqs(), questions=["Why us?"], enabled=SIDE_OUTPUT_TOOLS)
    _draft(state)
    return state


def test_due_lists_enabled_ready_unrun_tools_in_the_fixed_order():
    state = _all_due_state()
    # enabled in a different order must not matter
    state.side_outputs.enabled = ["suggest_resume_tweaks", "answer_screening", "suggest_learning"]
    assert side_outputs.due(state) == list(SIDE_OUTPUT_TOOLS)


def test_due_skips_tools_that_ran_are_off_or_not_ready():
    state = _all_due_state()
    state.side_outputs.ran.append("answer_screening")
    assert side_outputs.due(state) == ["suggest_learning", "suggest_resume_tweaks"]
    state.side_outputs.enabled.remove("suggest_learning")
    assert side_outputs.due(state) == ["suggest_resume_tweaks"]
    no_letter = _state(_confirmed_gap_reqs(), questions=["Why us?"], enabled=SIDE_OUTPUT_TOOLS)
    assert side_outputs.due(no_letter) == ["answer_screening", "suggest_learning"]


def test_finish_is_not_blocked_when_nothing_is_due():
    assert side_outputs.finish_blocked(_state()) is None
    state = _all_due_state()
    state.side_outputs.ran.extend(SIDE_OUTPUT_TOOLS)
    assert side_outputs.finish_blocked(state) is None


def test_finish_blocked_names_the_due_tools():
    reason = side_outputs.finish_blocked(_all_due_state())
    assert reason
    for tool in SIDE_OUTPUT_TOOLS:
        assert tool in reason


def test_status_lines_are_empty_when_no_side_output_is_enabled():
    assert side_outputs.status_lines(_state()) == []


def test_status_lines_show_due_done_failed_and_not_yet():
    state = _all_due_state()
    state.side_outputs.ran = ["answer_screening", "suggest_learning"]
    state.side_outputs.errors = {"suggest_learning": "boom"}
    state.drafts.clear()  # the letter is not final, so résumé notes are "not yet"
    lines = side_outputs.status_lines(state)
    assert len(lines) == 1 + 3
    assert lines[0].startswith("SIDE OUTPUTS")
    assert "answer_screening: done" in lines[1]
    assert "suggest_learning: FAILED, not retried" in lines[2]
    assert "suggest_resume_tweaks: not yet (" in lines[3]


def test_status_lines_show_due_and_not_needed():
    state = _state(questions=[], enabled=["answer_screening", "suggest_resume_tweaks"])
    _draft(state)
    lines = side_outputs.status_lines(state)
    assert len(lines) == 3
    assert "answer_screening: not needed (" in lines[1]
    assert "suggest_resume_tweaks: due" in lines[2]


# ===========================================================================
# 10. run_due
# ===========================================================================
class Recorder:
    """Side-tool fakes that record the order they ran in."""

    def __init__(self, fail=(), also=None):
        self.order: list[str] = []
        self.fail = set(fail)
        self.also = also or {}

    def fns(self):
        def make(name):
            def fn(state, ctx):
                self.order.append(name)
                if name in self.also:
                    self.also[name](state, ctx)
                if name in self.fail:
                    raise ToolError(f"{name} broke")
                return {"name": name}
            return fn
        return {name: make(name) for name in SIDE_OUTPUT_TOOLS}


def test_run_due_runs_the_due_tools_in_order_and_returns_their_names(db):
    state = _all_due_state()
    rec = Recorder()
    ran = side_outputs.run_due(_ctx(db, state), state, rec.fns())
    assert ran == rec.order == list(SIDE_OUTPUT_TOOLS)
    assert state.side_outputs.ran == list(SIDE_OUTPUT_TOOLS)
    assert state.budget.side_calls == 3 and state.budget.tool_calls == 0


def test_run_due_keeps_going_after_a_failing_tool(db):
    state = _all_due_state()
    rec = Recorder(fail={"answer_screening"})
    ran = side_outputs.run_due(_ctx(db, state), state, rec.fns())
    assert ran == list(SIDE_OUTPUT_TOOLS)
    assert state.side_outputs.errors == {"answer_screening": "answer_screening broke"}


def test_run_due_stops_once_the_run_budget_is_reached(db):
    state = _all_due_state()
    ctx = _ctx(db, state)

    def spend(state_, ctx_):
        ctx_.db.add(LlmUsage(task="x", tier="mid", model="m", cost_usd=Decimal("9"), run_id=ctx_.run.id))
        ctx_.db.commit()

    rec = Recorder(also={"answer_screening": spend})
    ran = side_outputs.run_due(ctx, state, rec.fns())
    assert ran == ["answer_screening"]
    assert state.budget.over_cost()


def test_run_due_never_runs_a_disabled_or_not_needed_tool(db):
    state = _all_due_state()
    state.job.screening_questions = []  # screening: not needed
    state.side_outputs.enabled.remove("suggest_learning")  # learning: switched off
    rec = Recorder()
    ran = side_outputs.run_due(_ctx(db, state), state, rec.fns())
    assert ran == rec.order == ["suggest_resume_tweaks"]


# ===========================================================================
# 11. open_run, leave_out_gaps
# ===========================================================================
def test_open_run_keeps_only_known_side_outputs_in_the_fixed_order(db):
    state, ctx = open_run(db, 5, 1, "agent", side_outputs=["suggest_resume_tweaks", "bogus", "answer_screening"])
    assert state.side_outputs.enabled == ["answer_screening", "suggest_resume_tweaks"]
    assert '"answer_screening","suggest_resume_tweaks"' in _run_row(db, ctx.run.id).state.replace(" ", "")


def test_open_run_enables_no_side_output_by_default(db):
    state, _ = open_run(db, 5, 1, "agent")
    assert state.side_outputs.enabled == []


def test_leave_out_gaps_marks_its_decisions_as_assumed(db):
    state = _state([_req("R1", status="gap"), _req("R2", status="gap")])
    _, ctx = open_run(db, 5, 1, "workflow")
    leave_out_gaps(state, ctx)
    for r in state.requirements:
        assert r.user_decision.choice == "leave_out" and r.user_decision.assumed is True
    assert guardrails.confirmed_gaps(state) == []


# ===========================================================================
# 12. registry
# ===========================================================================
LETTER_TOOLS = ["analyze_job", "match_profile", "ask_user", "generate_letter", "check_claims",
                "check_requirements", "style_lint", "revise_letter"]


def test_registry_without_side_outputs_is_exactly_the_nine_old_tools():
    assert list(registry.build_registry(leave_out_gaps)) == [*LETTER_TOOLS, "finish"]


def test_registry_with_all_side_outputs_puts_them_between_revise_and_finish():
    reg = registry.build_registry(leave_out_gaps, SIDE_OUTPUT_TOOLS)
    assert list(reg) == [*LETTER_TOOLS, *SIDE_OUTPUT_TOOLS, "finish"]


def test_registry_offers_only_the_enabled_side_output():
    reg = registry.build_registry(leave_out_gaps, ["suggest_learning", "not_a_tool"])
    assert list(reg) == [*LETTER_TOOLS, "suggest_learning", "finish"]


@pytest.mark.parametrize("name", SIDE_OUTPUT_TOOLS)
def test_each_side_tool_says_when_not_to_use_it_and_has_a_matching_spec(name):
    tool = registry.build_registry(leave_out_gaps, SIDE_OUTPUT_TOOLS)[name]
    d = tool.spec.description.lower()
    assert "do not" in d and "never" in d
    assert tool.spec.name == name and tool.name == name and tool.fn is not None


@pytest.mark.parametrize("name", SIDE_OUTPUT_TOOLS)
def test_each_side_gate_refuses_a_tool_that_already_ran(name):
    gate = registry.build_registry(leave_out_gaps, SIDE_OUTPUT_TOOLS)[name].gate
    state = _all_due_state()
    assert gate(state) is None
    state.side_outputs.ran.append(name)
    assert "already ran" in gate(state)


def test_side_output_fns_has_the_three_functions():
    assert set(registry.side_output_fns()) == set(SIDE_OUTPUT_TOOLS)


# ===========================================================================
# 13. answer_screening
# ===========================================================================
def _ans(number, answer, covered="yes", claims=(), note=""):
    return {"number": number, "covered": covered, "answer": answer, "note": note,
            "claims": [{"quote": q, "source": s} for q, s in claims]}


GOOD = _ans(1, "I built REST APIs for three teams at Acme.",
            claims=[("built REST APIs for three teams", "experience:12#s1")])


def _screen(db, monkeypatch, answers, questions=("Describe your API experience.",)):
    state = _state(questions=questions)
    ctx = _ctx(db, state)
    fake = _install(monkeypatch, as_mod, {"answers": answers})
    summary = as_mod.answer_screening(state, ctx)
    return state, ctx, fake, summary


def test_screening_refuses_when_the_ad_has_no_questions(db, monkeypatch):
    state = _state()
    ctx = _ctx(db, state)
    _install(monkeypatch, as_mod, {"answers": []})
    with pytest.raises(ToolError, match="no screening questions"):
        as_mod.answer_screening(state, ctx)


def test_screening_a_well_grounded_answer_is_verified_with_its_evidence(db, monkeypatch):
    state, _, _, summary = _screen(db, monkeypatch, [GOOD])
    [a] = state.side_outputs.screening_answers
    assert a["verified"] is True and a["issues"] == []
    assert a["evidence"] == ["experience:12#s1"]
    assert a["answer"].startswith("I built REST APIs")
    assert summary == {"questions": 1, "answered": 1, "verified": 1, "flagged": 0, "for_the_user": 0}


def test_screening_a_claim_with_a_bad_source_is_flagged_not_verified(db, monkeypatch):
    bad = _ans(1, "I built REST APIs for three teams at Acme.",
               claims=[("built REST APIs for three teams", "experience:99#s1")])
    state, *_ = _screen(db, monkeypatch, [bad])
    [a] = state.side_outputs.screening_answers
    assert a["verified"] is False and a["evidence"] == []
    assert any("not in your profile" in i for i in a["issues"])


def test_screening_a_claim_quote_missing_from_the_answer_is_flagged(db, monkeypatch):
    bad = _ans(1, "I built REST APIs.", claims=[("led a team of ten engineers", "experience:12#s1")])
    state, *_ = _screen(db, monkeypatch, [bad])
    [a] = state.side_outputs.screening_answers
    assert a["verified"] is False
    assert any("isn't in the answer" in i for i in a["issues"])


def test_screening_an_invented_link_is_flagged(db, monkeypatch):
    bad = _ans(1, "I built REST APIs for three teams, see github.com/bob/x for more.",
               claims=[("built REST APIs for three teams", "experience:12#s1")])
    state, *_ = _screen(db, monkeypatch, [bad])
    [a] = state.side_outputs.screening_answers
    assert a["verified"] is False
    assert any("github.com/bob/x" in i for i in a["issues"])


def test_screening_work_rights_resolve_to_the_visa_status_as_evidence(db, monkeypatch):
    ans = _ans(1, "Yes, I am an Australian citizen.", claims=[("Australian citizen", "fact:work_rights")])
    state, *_ = _screen(db, monkeypatch, [ans], questions=("Do you have the right to work in Australia?",))
    [a] = state.side_outputs.screening_answers
    assert a["verified"] is True and a["evidence"] == ["fact:work_rights"]


def test_screening_a_not_covered_answer_is_blanked_even_if_the_model_wrote_text(db, monkeypatch):
    ans = _ans(1, "I probably have some of this.", covered="no", note="Answer this yourself.")
    state, *_ = _screen(db, monkeypatch, [ans])
    [a] = state.side_outputs.screening_answers
    assert (a["answer"], a["issues"], a["verified"], a["covered"]) == ("", [], False, "no")
    assert a["note"] == "Answer this yourself."


def test_screening_a_question_the_model_skipped_comes_back_as_not_drafted(db, monkeypatch):
    state, *_ = _screen(db, monkeypatch, [GOOD], questions=("Q one?", "Q two?"))
    first, second = state.side_outputs.screening_answers
    assert first["verified"] is True
    assert second["question"] == "Q two?" and second["answer"] == "" and second["covered"] == "no"
    assert "Not drafted" in second["note"]


def test_screening_an_answer_with_no_claims_is_flagged(db, monkeypatch):
    ans = _ans(1, "I have plenty of relevant experience.", covered="yes", claims=[])
    state, *_ = _screen(db, monkeypatch, [ans])
    [a] = state.side_outputs.screening_answers
    assert a["verified"] is False
    assert any("no statement" in i and "tied to your profile" in i for i in a["issues"])


def test_screening_output_follows_the_question_order_whatever_order_the_model_used(db, monkeypatch):
    qs = ("First?", "Second?", "Third?")
    answers = [
        _ans(3, "I built REST APIs for three teams.", claims=[("built REST APIs", "experience:12#s1")]),
        _ans(1, "I deployed services to AWS.", claims=[("deployed services to AWS", "experience:12#s2")]),
        _ans(2, "I know SQL.", claims=[("know SQL", "skill:7")]),
    ]
    state, *_ = _screen(db, monkeypatch, answers, questions=qs)
    out = state.side_outputs.screening_answers
    assert [a["question"] for a in out] == list(qs)
    assert [a["answer"] for a in out] == [
        "I deployed services to AWS.", "I know SQL.", "I built REST APIs for three teams."]


def test_screening_summary_counts_answered_verified_flagged_and_for_the_user(db, monkeypatch):
    answers = [
        GOOD,
        _ans(2, "I built REST APIs.", claims=[("built REST APIs", "experience:99")]),
        _ans(3, "", covered="no", note="Tell them your notice period."),
    ]
    _, _, _, summary = _screen(db, monkeypatch, answers, questions=("a?", "b?", "c?"))
    assert summary == {"questions": 3, "answered": 2, "verified": 1, "flagged": 1, "for_the_user": 1}


def test_screening_calls_the_mid_tier_labelled_with_the_run(db, monkeypatch):
    _, ctx, fake, _ = _screen(db, monkeypatch, [GOOD])
    [call] = fake.calls
    assert call["kw"]["tier"] == "mid" and call["kw"]["task"] == "answer_screening"
    assert (call["kw"]["job_id"], call["kw"]["match_id"], call["kw"]["run_id"]) == (5, 9, ctx.run.id)
    assert "1. Describe your API experience." in call["user"]
    assert "[fact:work_rights] Australian citizen" in call["user"]
    assert AD in call["user"]


# ===========================================================================
# 14. suggest_learning
# ===========================================================================
def _tip(rid, text="Take a short course on it.", kind="course", effort="2 weeks"):
    return {"id": rid, "kind": kind, "suggestion": text, "effort": effort}


def _seen(db, decision, n, importance=None):
    for i in range(n):
        gaps.record_sighting(db, decision, job_id=1000 + decision.id * 100 + i, job_title=f"Job {i}",
                             source="scan", importance=importance)
    db.commit()


def _learning_state(db):
    """Four confirmed gaps. A (Power BI, essential) has 1 recent sighting, B (Tableau, important)
    has 3; Terraform (important) and Kubernetes (essential) are not on the to-work-on list."""
    a = gaps.save_no(db, 1, label="Power BI", key=gaps.skill_key("Power BI"))
    b = gaps.save_no(db, 1, label="Tableau", key=gaps.skill_key("Tableau"))
    _seen(db, a, 1, "essential")
    _seen(db, b, 3, "important")
    return _state([
        _req("R1", "Power BI dashboards", importance="essential", status="gap", decision=NO, skill="Power BI"),
        _req("R2", "Tableau reporting", importance="important", role="mention", status="gap",
             decision=UserDecision(choice="leave_out", remembered=True), skill="Tableau"),
        _req("R3", "Terraform", importance="important", role="mention", status="gap", decision=NO, skill="Terraform"),
        _req("R4", "Kubernetes", importance="essential", status="gap", decision=NO, skill="Kubernetes"),
    ])


ALL_TIPS = {"suggestions": [_tip("R1"), _tip("R2"), _tip("R3"), _tip("R4")]}


def test_learning_refuses_when_no_gap_was_confirmed_by_the_user(db, monkeypatch):
    state = _state([_req("R1", status="gap", decision=UserDecision(choice="leave_out", assumed=True))])
    ctx = _ctx(db, state)
    _install(monkeypatch, sl_mod, ALL_TIPS)
    with pytest.raises(ToolError, match="no gap the user confirmed"):
        sl_mod.suggest_learning(state, ctx)


def test_learning_leads_with_the_skill_the_most_recent_ads_ask_for(db, monkeypatch):
    state = _learning_state(db)
    ctx = _ctx(db, state)
    _install(monkeypatch, sl_mod, ALL_TIPS)
    summary = sl_mod.suggest_learning(state, ctx)
    out = state.side_outputs.learning_suggestions
    # B (Tableau, important, 3 ads) before A (Power BI, essential, 1 ad), then the unranked
    # ones: essential Kubernetes before important Terraform.
    assert [s["requirement_id"] for s in out] == ["R2", "R1", "R4", "R3"]
    assert (out[0]["rank"], out[0]["recent_ads"]) == (1, 3)
    assert (out[1]["rank"], out[1]["recent_ads"]) == (2, 1)
    assert (out[2]["rank"], out[2]["recent_ads"]) == (None, None)
    assert summary["leads_with"] == "Tableau" and summary["gaps"] == 4 and summary["suggestions"] == 4


def test_learning_strips_links_from_the_suggestion_and_effort(db, monkeypatch):
    state = _learning_state(db)
    ctx = _ctx(db, state)
    tips = {"suggestions": [_tip("R1", "Take the course at https://example.com/pbi today.",
                                 effort="a weekend, see www.example.org/x")]}
    _install(monkeypatch, sl_mod, tips)
    sl_mod.suggest_learning(state, ctx)
    [s] = state.side_outputs.learning_suggestions
    assert "http" not in s["suggestion"] and "example.com" not in s["suggestion"]
    assert s["suggestion"].startswith("Take the course at")
    assert "example.org" not in s["effort"]


def test_learning_drops_an_id_the_model_invented_and_reports_it(db, monkeypatch):
    state = _learning_state(db)
    ctx = _ctx(db, state)
    _install(monkeypatch, sl_mod, {"suggestions": [*ALL_TIPS["suggestions"], _tip("R99")]})
    summary = sl_mod.suggest_learning(state, ctx)
    assert "R99" not in [s["requirement_id"] for s in state.side_outputs.learning_suggestions]
    assert summary["ignored_ids"] == ["R99"]


def test_learning_omits_a_gap_the_model_returned_nothing_for(db, monkeypatch):
    state = _learning_state(db)
    ctx = _ctx(db, state)
    _install(monkeypatch, sl_mod, {"suggestions": [_tip("R1"), _tip("R2"), _tip("R3")]})
    summary = sl_mod.suggest_learning(state, ctx)
    assert [s["requirement_id"] for s in state.side_outputs.learning_suggestions] == ["R2", "R1", "R3"]
    assert summary["gaps"] == 4 and summary["suggestions"] == 3


def test_learning_calls_the_small_tier_and_tells_the_model_how_often_ads_ask(db, monkeypatch):
    state = _learning_state(db)
    ctx = _ctx(db, state)
    fake = _install(monkeypatch, sl_mod, ALL_TIPS)
    sl_mod.suggest_learning(state, ctx)
    [call] = fake.calls
    assert call["kw"]["tier"] == "small" and call["kw"]["task"] == "suggest_learning"
    assert (call["kw"]["job_id"], call["kw"]["match_id"], call["kw"]["run_id"]) == (5, 9, ctx.run.id)
    assert "asked for in 3 recent ads" in call["user"]


# ===========================================================================
# 15. suggest_resume_tweaks
# ===========================================================================
def _resume_state(**kw):
    reqs = [
        _req("R1", "Build REST APIs", evidence=["experience:12#s1"]),  # supported
        _req("R2", "Cloud deployment", role="mention", status="partial", evidence=["experience:12#s2"]),
        _req("R3", "Power BI", status="gap"),
        _req("R4", "Australian work rights", role="not_for_letter", status="gap"),
        _req("R5", "Tableau", role="mention", importance="important", status="supported",
             decision=NO),  # left out by the user, whatever the status says
    ]
    state = _state(reqs, **kw)
    _draft(state, text="FINAL LETTER TEXT")
    return state


def _advice(lead_with=(), keywords=(), cutting=(), gaps_=()):
    return {"lead_with": list(lead_with), "keywords_to_mirror": list(keywords),
            "consider_cutting": list(cutting), "gaps_to_address": list(gaps_)}


def _tweak(db, monkeypatch, response, state=None):
    state = state or _resume_state()
    ctx = _ctx(db, state)
    fake = _install(monkeypatch, rt_mod, response)
    summary = rt_mod.suggest_resume_tweaks(state, ctx)
    return state, ctx, fake, summary


def test_resume_tweaks_refuse_while_the_letter_is_not_final(db, monkeypatch):
    state = _state()
    _draft(state, claims=False)  # a failed check with drafts left: the letter can still change
    ctx = _ctx(db, state)
    fake = _install(monkeypatch, rt_mod, _advice())
    with pytest.raises(ToolError, match="not final"):
        rt_mod.suggest_resume_tweaks(state, ctx)
    assert fake.calls == []


def test_resume_tweaks_without_a_cv_are_based_on_the_profile(db, monkeypatch):
    state, _, fake, summary = _tweak(db, monkeypatch, _advice())
    assert state.side_outputs.resume_notes["based_on"] == "profile"
    assert summary["based_on"] == "profile"
    assert "no CV stored" in fake.calls[0]["user"]


def test_resume_tweaks_drop_a_lead_with_item_whose_only_pointer_is_bad(db, monkeypatch):
    lead = [
        {"point": "Lead with API work", "evidence": ["experience:99"], "why": "R1"},
        {"point": "Lead with the AWS work", "evidence": ["[experience:12#s2]", "experience:99"], "why": "R2"},
    ]
    state, _, _, summary = _tweak(db, monkeypatch, _advice(lead_with=lead))
    [item] = state.side_outputs.resume_notes["lead_with"]
    assert item["point"] == "Lead with the AWS work" and item["evidence"] == ["experience:12#s2"]
    assert summary["dropped"] == 1 and summary["lead_with"] == 1


def test_resume_tweaks_keep_only_keywords_that_are_in_the_ad_and_backed_once(db, monkeypatch):
    kws = [
        {"keyword": "REST APIs", "evidence": ["experience:12#s1"]},
        {"keyword": "Kubernetes", "evidence": ["experience:12#s1"]},  # not in the ad
        {"keyword": "rest apis", "evidence": ["experience:12#s1"]},  # duplicate, any case
        {"keyword": "python", "evidence": ["skill:99"]},  # in the ad but no resolvable evidence
    ]
    state, *_ = _tweak(db, monkeypatch, _advice(keywords=kws))
    notes = state.side_outputs.resume_notes
    assert notes["keywords_to_mirror"] == [{"keyword": "REST APIs", "evidence": ["experience:12#s1"]}]
    assert notes["dropped"] == 3


def test_resume_tweaks_keep_a_keyword_the_analysis_listed_even_if_not_in_the_ad_text(db, monkeypatch):
    state = _resume_state(keywords=["Logistics"])
    kws = [{"keyword": "logistics", "evidence": ["experience:12#s1"]}]
    state, *_ = _tweak(db, monkeypatch, _advice(keywords=kws), state)
    assert [k["keyword"] for k in state.side_outputs.resume_notes["keywords_to_mirror"]] == ["logistics"]


def test_resume_tweaks_drop_a_cv_cut_when_there_is_no_cv_and_keep_a_valid_pointer(db, monkeypatch):
    cuts = [
        {"item": "Retail job at Kmart", "source": "cv", "why": "not relevant"},
        {"item": "Deployed services to AWS", "source": "experience:12#s2", "why": "less relevant here"},
        {"item": "Something else", "source": "experience:99", "why": "x"},
    ]
    state, *_ = _tweak(db, monkeypatch, _advice(cutting=cuts))
    notes = state.side_outputs.resume_notes
    assert [c["source"] for c in notes["consider_cutting"]] == ["experience:12#s2"]
    assert notes["dropped"] == 2


def test_resume_tweaks_keep_gap_advice_only_for_gap_partial_or_left_out_requirements(db, monkeypatch):
    items = [
        {"id": "R1", "advice": "supported, so not a gap"},
        {"id": "R2", "advice": "Frame the AWS work as the nearest thing."},
        {"id": "R3", "advice": "Leave Power BI off; see https://x.com/guide for context."},
        {"id": "R4", "advice": "eligibility, never for the letter"},
        {"id": "R5", "advice": "The candidate said they lack it."},
        {"id": "R99", "advice": "no such requirement"},
        {"id": "R3", "advice": "a second note on the same gap"},
    ]
    state, *_ = _tweak(db, monkeypatch, _advice(gaps_=items))
    notes = state.side_outputs.resume_notes
    assert [g["requirement_id"] for g in notes["gaps_to_address"]] == ["R2", "R3", "R5"]
    assert all("http" not in g["advice"] and "x.com" not in g["advice"] for g in notes["gaps_to_address"])
    assert notes["dropped"] == 4  # R1, R4, R99, duplicate R3


def test_resume_tweaks_strip_links_from_lead_with_and_cuts(db, monkeypatch):
    lead = [{"point": "Lead with APIs, see https://x.com/p", "evidence": ["experience:12#s1"],
             "why": "R1 www.x.com/y"}]
    cuts = [{"item": "AWS work http://x.com/z", "source": "experience:12#s2", "why": "ok"}]
    state, *_ = _tweak(db, monkeypatch, _advice(lead_with=lead, cutting=cuts))
    notes = state.side_outputs.resume_notes
    blob = str(notes["lead_with"]) + str(notes["consider_cutting"])
    assert "x.com" not in blob and "http" not in blob


def test_resume_tweaks_dropped_counts_every_dropped_item(db, monkeypatch):
    advice = _advice(
        lead_with=[{"point": "p", "evidence": ["nope"], "why": "w"}],
        keywords=[{"keyword": "Kubernetes", "evidence": ["skill:7"]}],
        cutting=[{"item": "x", "source": "cv", "why": "w"}],
        gaps_=[{"id": "R1", "advice": "a"}],
    )
    state, _, _, summary = _tweak(db, monkeypatch, advice)
    assert summary["dropped"] == state.side_outputs.resume_notes["dropped"] == 4
    assert [summary[k] for k in rt_mod.MAX_ITEMS] == [0, 0, 0, 0]


def test_resume_tweaks_use_the_default_cv_and_accept_a_cut_that_quotes_it(db, monkeypatch):
    db.add(UserCv(id=1, user_id=1, label="main", content="Default CV: Barista at Cafe Nero. Developer at Acme.",
                  is_default=True))
    db.add(UserCv(id=2, user_id=1, label="old", content="OLDER CV: Cashier at Shop.", is_default=False))
    db.commit()
    cuts = [
        {"item": "Barista at Cafe Nero", "source": "cv", "why": "not relevant to the ad"},
        {"item": "Pilot at an airline", "source": "cv", "why": "made up, not in the CV"},
    ]
    state, _, fake, _ = _tweak(db, monkeypatch, _advice(cutting=cuts))
    notes = state.side_outputs.resume_notes
    assert notes["based_on"] == "cv"
    assert "Default CV: Barista at Cafe Nero" in fake.calls[0]["user"]
    assert "OLDER CV" not in fake.calls[0]["user"]
    assert [c["item"] for c in notes["consider_cutting"]] == ["Barista at Cafe Nero"]
    assert notes["consider_cutting"][0]["source"] == "cv"
    assert notes["dropped"] == 1


def test_resume_tweaks_read_the_best_draft_when_out_of_drafts(db, monkeypatch):
    state = _state()
    _draft(state, claims=True, requirements=True, style=False, text="BEST DRAFT TEXT")  # v1: best
    _draft(state, claims=False, text="MIDDLE DRAFT TEXT")
    _draft(state, claims=False, text="LATEST DRAFT TEXT")
    state, _, fake, _ = _tweak(db, monkeypatch, _advice(), state)
    user = fake.calls[0]["user"]
    assert "BEST DRAFT TEXT" in user and "LATEST DRAFT TEXT" not in user
    assert state.side_outputs.resume_notes["draft_version"] == 1


def test_resume_tweaks_call_the_mid_tier_labelled_with_the_run(db, monkeypatch):
    _, ctx, fake, _ = _tweak(db, monkeypatch, _advice())
    [call] = fake.calls
    assert call["kw"]["tier"] == "mid" and call["kw"]["task"] == "suggest_resume_tweaks"
    assert (call["kw"]["job_id"], call["kw"]["match_id"], call["kw"]["run_id"]) == (5, 9, ctx.run.id)
    assert "FINAL LETTER TEXT" in call["user"]
    # the eligibility item is not shown to the model
    assert "Australian work rights" not in call["user"].split("=== PROFILE")[0]
