"""Tests for the Phase 5 cover-letter draft and check tools: the guardrails that gate
them, generate_letter, revise_letter, check_claims and check_requirements. The LLM is
replaced by a fake that returns canned JSON, so what is under test is the code around
the model: gating, prompt contents, post-processing of the writer's output, the code
half of claim checking, and how checks attach to drafts.
"""
from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.llm.letter import guardrails, runner
from app.llm.letter.runner import execute_tool, start_run
from app.llm.letter.state import Check, Claim, JobInfo, LetterState, Requirement, UserDecision
from app.llm.letter.tools import check_claims as cc
from app.llm.letter.tools import check_requirements as cr
from app.llm.letter.tools import generate as gen
from app.llm.letter.tools import revise as rev
from app.llm.letter.tools import style_lint as sl
from app.models import (
    Experience,
    JobListing,
    LetterRunStep,
    Match,
    Profile,
    Qualification,
    Skill,
)

AD = (
    "We build logistics software. You will own production systems and build REST APIs. "
    "Australian work rights required."
)
SENT1 = "Built REST APIs for 3 teams."


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
                       company="LogiCo", raw_description=AD),
        ])
        session.flush()
        session.add(Match(id=9, user_id=1, job_id=5, score=80))
        session.commit()
        yield session
    Base.metadata.drop_all(eng)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _req(rid, text, importance="essential", role="headline", status="supported", evidence=(),
         decision=None, note=None):
    return Requirement(
        id=rid, text=text, importance=importance, letter_role=role, status=status,
        evidence=list(evidence), user_decision=decision, note=note, theme=f"theme-{rid}",
    )


def _plan_requirements() -> list[Requirement]:
    return [
        _req("R1", "Build REST APIs", evidence=["experience:12#s1"]),  # must
        _req("R2", "SQL databases", role="mention", status="partial", evidence=["skill:7"]),  # must (essential mention)
        _req("R3", "Cloud deployment", "important", "mention", evidence=["experience:12#s2"]),  # may
        _req("R4", "Kubernetes in production", "important", "headline", "gap"),  # do not claim (not essential: not pending)
        _req("R5", "Degree in IT", "essential", "headline", evidence=["qualification:4"],
             decision=UserDecision(choice="leave_out")),  # left out
        _req("R6", "Version control", "important", "implied", evidence=["experience:12"]),  # may
        _req("R7", "Valid driver licence", "essential", "not_for_letter", "gap"),  # eligibility
        _req("R8", "Exposure to logistics", "nice_to_have", "mention", evidence=["profile:summary"]),  # may
    ]


def _ids(reqs) -> list[str]:
    return [r.id for r in reqs]


def _fresh(db, requirements=None, **profile_fields) -> tuple[LetterState, runner.ToolContext]:
    if profile_fields:
        profile = db.get(Profile, 1)
        for k, v in profile_fields.items():
            setattr(profile, k, v)
        db.commit()
    state = LetterState(
        profile_id=1,
        job=JobInfo(job_id=5, title="Engineer", company="LogiCo", tone="technical",
                    keywords=["logistics"], company_facts=["They build logistics software"]),
        requirements=requirements if requirements is not None else _plan_requirements(),
    )
    return state, start_run(db, 9, "workflow", state)


def _letter_json(letter="Dear Hiring Manager,\n\nI built REST APIs.\n\nSincerely,\nBob", claims=None):
    return {
        "letter": letter,
        "claims": claims if claims is not None else [{"quote": "I built REST APIs", "source": "experience:12#s1"}],
    }


@pytest.fixture()
def fake(monkeypatch):
    """Replaces complete_json in the writer and both checkers. responses[task] is the JSON
    (or a callable returning it) for that task; every call is recorded."""
    calls: list[dict] = []
    responses: dict = {}

    def fake_complete(system, user, schema=None, **kw):
        calls.append({"task": kw.get("task"), "system": system, "user": user, "kw": kw})
        r = responses[kw["task"]]
        return r() if callable(r) else r

    monkeypatch.setattr(gen, "complete_json", fake_complete)
    monkeypatch.setattr(cc, "complete_json", fake_complete)
    monkeypatch.setattr(cr, "complete_json", fake_complete)

    class NS:
        pass

    ns = NS()
    ns.calls, ns.responses = calls, responses
    return ns


def _add_draft(state, text="Dear Hiring Manager,\n\nI built REST APIs.\n\nSincerely,\nBob", claims=None):
    return state.add_draft(
        text, claims if claims is not None else [Claim(text="I built REST APIs", source="experience:12#s1")]
    )


def _checks(state, claims=True, requirements=True, style=True):
    """Record passing (True) / failing (False) / absent (None) checks on the latest draft."""
    for name, val in (("claims", claims), ("requirements", requirements), ("style", style)):
        if val is None:
            continue
        state.record_check(name, Check(passed=bool(val), issues=[] if val else [f"{name} broke"]))


def _judged(*claims) -> dict:
    claims = claims or ({},)
    return {"claims": [
        {"quote": "q", "about": "candidate", "declared": 1, "verdict": "supported",
         "support": ["experience:12#s1"], "reason": "ok", **c}
        for c in claims
    ]}


def _state(requirements) -> LetterState:
    return LetterState(profile_id=1, job=JobInfo(job_id=5, title="E"), requirements=requirements)


# ---------------------------------------------------------------------------
# guardrails: the letter plan
# ---------------------------------------------------------------------------
def test_must_cover_is_supported_headlines_plus_essential_mentions():
    assert _ids(guardrails.must_cover(_state(_plan_requirements()))) == ["R1", "R2"]


def test_may_use_is_the_other_covered_mentions_and_implied_items():
    assert _ids(guardrails.may_use(_state(_plan_requirements()))) == ["R3", "R6", "R8"]


def test_do_not_claim_is_gaps_and_left_out_but_not_eligibility():
    state = _state(_plan_requirements())
    assert _ids(guardrails.do_not_claim(state)) == ["R4", "R5"]
    assert _ids(guardrails.eligibility(state)) == ["R7"]


def test_leave_out_removes_a_supported_item_from_must_cover_and_may_use():
    state = _state(_plan_requirements())
    assert "R5" not in _ids(guardrails.must_cover(state))
    assert "R5" not in _ids(guardrails.may_use(state))


# ---------------------------------------------------------------------------
# guardrails: gates
# ---------------------------------------------------------------------------
def test_drafting_blocked_without_requirements():
    assert "analyze_job" in guardrails.drafting_blocked(_state([]))


def test_drafting_blocked_by_unmatched_requirement():
    msg = guardrails.drafting_blocked(_state([_req("R1", "x", status="unknown")]))
    assert "R1" in msg and "match_profile" in msg


def test_drafting_blocked_by_pending_gap_until_user_decides():
    gap = _req("R2", "Kubernetes", status="gap")
    reqs = [_req("R1", "x", evidence=["skill:7"]), gap]
    msg = guardrails.drafting_blocked(_state(reqs))
    assert "R2" in msg and "ask_user" in msg
    gap.user_decision = UserDecision(choice="leave_out")
    assert guardrails.drafting_blocked(_state(reqs)) is None


def test_can_generate_refuses_once_a_draft_exists():
    state = _state(_plan_requirements())
    assert guardrails.can_generate(state) is None
    _add_draft(state)
    assert "revise_letter" in guardrails.can_generate(state)


def test_can_revise_needs_a_draft():
    assert "no draft" in guardrails.can_revise(_state(_plan_requirements()))


def test_can_revise_names_the_checks_not_yet_run():
    state = _state(_plan_requirements())
    _add_draft(state)
    msg = guardrails.can_revise(state)
    assert "check_claims" in msg and "check_requirements" in msg and "style_lint" in msg
    _checks(state, claims=False, requirements=True, style=None)
    msg = guardrails.can_revise(state)
    assert "style_lint" in msg and "check_claims" not in msg and "check_requirements" not in msg


def test_can_revise_refuses_when_everything_passed():
    state = _state(_plan_requirements())
    _add_draft(state)
    _checks(state)
    assert "nothing to revise" in guardrails.can_revise(state)


def test_can_revise_refuses_at_the_draft_cap():
    state = _state(_plan_requirements())
    state.budget.max_drafts = 2
    _add_draft(state)
    _add_draft(state)
    _checks(state, claims=False)
    assert "draft limit" in guardrails.can_revise(state)


def test_can_revise_allowed_when_all_ran_and_one_failed():
    state = _state(_plan_requirements())
    _add_draft(state)
    _checks(state, claims=False)
    assert guardrails.can_revise(state) is None
    assert guardrails.failed_checks(state) == ["claims"]


def test_can_finish_requires_passing_checks_on_the_latest_draft_not_an_earlier_one():
    state = _state(_plan_requirements())
    assert "no draft" in guardrails.can_finish(state)
    _add_draft(state)
    _checks(state)
    assert guardrails.can_finish(state) is None
    _add_draft(state)  # draft 2 has no checks: draft 1's pass says nothing about it
    msg = guardrails.can_finish(state)
    assert "not checked on draft 2" in msg
    for name in ("claims", "requirements", "style"):
        assert name in msg
    _checks(state, claims=True, requirements=False, style=True)
    assert "requirements failed on draft 2" in guardrails.can_finish(state)


# ---------------------------------------------------------------------------
# generate_letter
# ---------------------------------------------------------------------------
def test_refused_gate_returns_error_and_makes_no_llm_call(db, fake):
    state, ctx = _fresh(db, requirements=[_req("R1", "Kubernetes", status="gap")])
    res = execute_tool(ctx, state, "generate_letter", gen.generate_letter)
    assert not res.ok
    assert "R1" in res.error and "ask_user" in res.error
    assert fake.calls == []
    assert state.drafts == []


def test_generate_happy_path_adds_draft_with_cleaned_claims(db, fake):
    fake.responses["generate_letter"] = _letter_json(claims=[
        {"quote": "I  built\nREST APIs", "source": "[experience:12#s1]"},
        {"quote": "I know SQL", "source": " `skill:7` "},
        {"quote": "I studied", "source": "'qualification:4'"},
        {"quote": "no source given", "source": ""},
        {"quote": "   ", "source": "skill:7"},  # empty quote: dropped
    ])
    state, ctx = _fresh(db)
    res = execute_tool(ctx, state, "generate_letter", gen.generate_letter)
    assert res.ok, res.error
    d = state.latest_draft
    assert d.version == 1 and d.checks == {}
    assert [(c.text, c.source) for c in d.claims] == [
        ("I built REST APIs", "experience:12#s1"),
        ("I know SQL", "skill:7"),
        ("I studied", "qualification:4"),
        ("no source given", None),
    ]
    assert res.summary["draft"] == 1 and res.summary["claims"] == 4 and res.summary["unsourced_claims"] == 1
    assert res.summary["drafts_used"] == "1/3"


def test_generate_uses_strong_tier_task_and_ids(db, fake):
    fake.responses["generate_letter"] = _letter_json()
    state, ctx = _fresh(db)
    execute_tool(ctx, state, "generate_letter", gen.generate_letter)
    (call,) = fake.calls
    assert call["kw"]["tier"] == "strong"
    assert call["kw"]["task"] == "generate_letter"
    assert call["kw"]["run_id"] == ctx.run.id
    assert call["kw"]["match_id"] == 9
    assert call["kw"]["job_id"] == 5


def _plan_prompt(db, fake, requirements=None):
    fake.responses["generate_letter"] = _letter_json()
    state, ctx = _fresh(db, requirements=requirements)
    execute_tool(ctx, state, "generate_letter", gen.generate_letter)
    return fake.calls[0]["user"]


def test_generate_prompt_has_ad_name_and_resolved_evidence(db, fake):
    user = _plan_prompt(db, fake)
    assert AD in user
    assert "Name (sign off with this): Bob" in user
    must = user.split("MUST ADDRESS")[1].split("MAY USE")[0]
    assert "R1" in must and f"[experience:12#s1] {SENT1}" in must  # pointer resolved to profile text
    assert "[skill:7] SQL" in must


def test_generate_prompt_marks_partial_items(db, fake):
    user = _plan_prompt(db, fake)
    must = user.split("MUST ADDRESS")[1].split("MAY USE")[0]
    assert "R2" in must and "PARTIAL" in must
    assert "PARTIAL" not in must.split("R2")[0]  # R1 is fully supported


def test_generate_prompt_puts_gaps_under_do_not_claim_only(db, fake):
    user = _plan_prompt(db, fake)
    must = user.split("MUST ADDRESS")[1].split("DO NOT CLAIM")[0]
    assert "Kubernetes in production" not in must
    dont = user.split("DO NOT CLAIM")[1].split("NEVER IN THE LETTER")[0]
    assert "Kubernetes in production" in dont and "Degree in IT" in dont


def test_generate_prompt_lists_eligibility_under_never_in_the_letter(db, fake):
    user = _plan_prompt(db, fake)
    never = user.split("NEVER IN THE LETTER")[1].split("=== TASK ===")[0]
    assert "Valid driver licence" in never
    assert "Valid driver licence" not in user.split("NEVER IN THE LETTER")[0]


def test_generate_system_prompt_carries_voice_and_no_copy_line(db, fake):
    fake.responses["generate_letter"] = _letter_json()
    state, ctx = _fresh(db, writing_sample="I like making things that actually work in practice.")
    execute_tool(ctx, state, "generate_letter", gen.generate_letter)
    system = fake.calls[0]["system"]
    assert "VOICE REFERENCE" in system
    assert "I like making things that actually work in practice." in system
    assert gen._NO_COPY in system


def test_generate_system_prompt_has_no_no_copy_line_without_voice_material(db, fake, monkeypatch):
    monkeypatch.setattr(gen, "voice_prompt", lambda profile: "")
    fake.responses["generate_letter"] = _letter_json()
    state, ctx = _fresh(db)
    execute_tool(ctx, state, "generate_letter", gen.generate_letter)
    system = fake.calls[0]["system"]
    assert gen._NO_COPY not in system and "VOICE REFERENCE" not in system


def test_generate_empty_letter_is_a_tool_error_and_adds_no_draft(db, fake):
    fake.responses["generate_letter"] = _letter_json(letter="   \n ")
    state, ctx = _fresh(db)
    res = execute_tool(ctx, state, "generate_letter", gen.generate_letter)
    assert not res.ok and "empty letter" in res.error
    assert state.drafts == []


def test_generate_step_is_logged(db, fake):
    fake.responses["generate_letter"] = _letter_json()
    state, ctx = _fresh(db)
    execute_tool(ctx, state, "generate_letter", gen.generate_letter)
    step = db.query(LetterRunStep).filter_by(run_id=ctx.run.id).one()
    assert step.tool == "generate_letter" and step.error is None


def test_revise_reuses_the_generate_prefix_for_caching(db, fake):
    fake.responses["generate_letter"] = _letter_json()
    fake.responses["revise_letter"] = _letter_json(letter="Dear Hiring Manager,\n\nRevised.\n\nSincerely,\nBob")
    state, ctx = _fresh(db, writing_sample="Plain words, short and direct.")
    execute_tool(ctx, state, "generate_letter", gen.generate_letter)
    _checks(state, claims=False)
    res = execute_tool(ctx, state, "revise_letter", rev.revise_letter)
    assert res.ok, res.error
    g, r = fake.calls
    assert g["system"] == r["system"]
    g_ctx, g_task = g["user"].split("=== TASK ===")
    r_ctx, r_task = r["user"].split("=== TASK ===")
    assert g_ctx == r_ctx
    assert g_task != r_task


# ---------------------------------------------------------------------------
# revise_letter
# ---------------------------------------------------------------------------
def _failed_state(db, fake, revised="Dear Hiring Manager,\n\nI built REST APIs for three teams.\n\nSincerely,\nBob"):
    fake.responses["revise_letter"] = _letter_json(letter=revised)
    state, ctx = _fresh(db)
    _add_draft(state, "Dear Hiring Manager,\n\nI led REST API work.\n\nSincerely,\nBob")
    state.record_check("claims", Check(
        passed=False, issues=["overstated: 'I led REST API work'"], warnings=["undeclared: 'x'"]))
    state.record_check("requirements", Check(passed=True, warnings=["thin: R2"]))
    state.record_check("style", Check(passed=True))
    return state, ctx


def test_revise_task_block_lists_failed_issues_warnings_and_current_draft(db, fake):
    state, ctx = _failed_state(db, fake)
    res = execute_tool(ctx, state, "revise_letter", rev.revise_letter)
    assert res.ok, res.error
    task = fake.calls[0]["user"].split("=== TASK ===")[1]
    must_fix = task.split("MUST FIX:")[1].split("FIX ONLY IF")[0]
    assert "overstated: 'I led REST API work'" in must_fix
    assert "(requirements) thin: R2" not in must_fix
    optional = task.split("FIX ONLY IF IT IS EASY")[1]
    assert "(claims) undeclared: 'x'" in optional and "(requirements) thin: R2" in optional
    assert "I led REST API work." in task  # the current draft is included
    assert "CURRENT DRAFT 1:" in task
    # only the failed check's how-to-fix hint appears
    assert rev._HOW_TO_FIX["claims"] in task
    assert rev._HOW_TO_FIX["requirements"] not in task and rev._HOW_TO_FIX["style"] not in task


def test_revise_omits_the_optional_heading_without_warnings(db, fake):
    fake.responses["revise_letter"] = _letter_json()
    state, ctx = _fresh(db)
    _add_draft(state)
    state.record_check("claims", Check(passed=False, issues=["bad"]))
    state.record_check("requirements", Check(passed=True))
    state.record_check("style", Check(passed=True))
    execute_tool(ctx, state, "revise_letter", rev.revise_letter)
    assert "FIX ONLY IF" not in fake.calls[0]["user"]


def test_revise_creates_v2_with_summary(db, fake):
    state, ctx = _failed_state(db, fake)
    res = execute_tool(ctx, state, "revise_letter", rev.revise_letter)
    assert [d.version for d in state.drafts] == [1, 2]
    assert state.latest_draft.checks == {}  # a pass on v1 says nothing about v2
    s = res.summary
    assert s["draft"] == 2 and s["revised_from"] == 1 and s["fixing"] == ["claims"]
    assert 0 < s["changed_pct"] <= 100
    call = fake.calls[0]
    assert call["kw"]["tier"] == "strong" and call["kw"]["task"] == "revise_letter"


def test_revise_fixing_lists_every_failed_check_in_order(db, fake):
    fake.responses["revise_letter"] = _letter_json()
    state, ctx = _fresh(db)
    _add_draft(state)
    _checks(state, claims=True, requirements=False, style=False)
    res = execute_tool(ctx, state, "revise_letter", rev.revise_letter)
    assert res.summary["fixing"] == ["requirements", "style"]


def test_changed_pct_bounds():
    text = "I built REST APIs for three teams at Acme."
    assert rev.changed_pct(text, text) == 0
    assert rev.changed_pct("alpha beta gamma delta", "one two three four") == 100
    assert 0 < rev.changed_pct(text, text.replace("three", "four")) < 50


def test_revise_refuses_without_all_checks_and_makes_no_llm_call(db, fake):
    state, ctx = _fresh(db)
    _add_draft(state)
    _checks(state, claims=False, requirements=None, style=None)
    res = execute_tool(ctx, state, "revise_letter", rev.revise_letter)
    assert not res.ok and "check_requirements" in res.error and "style_lint" in res.error
    assert fake.calls == [] and len(state.drafts) == 1


def test_revise_refuses_at_the_draft_cap_and_makes_no_llm_call(db, fake):
    state, ctx = _fresh(db)
    state.budget.max_drafts = 1
    _add_draft(state)
    _checks(state, claims=False)
    res = execute_tool(ctx, state, "revise_letter", rev.revise_letter)
    assert not res.ok and "draft limit" in res.error
    assert fake.calls == [] and len(state.drafts) == 1


# ---------------------------------------------------------------------------
# check_claims
# ---------------------------------------------------------------------------
def _claims_state(db, claims, text="Dear Hiring Manager,\n\nI built REST APIs.\n\nSincerely,\nBob"):
    state, ctx = _fresh(db)
    _add_draft(state, text, claims)
    return state, ctx


def _run_claims(ctx, state, **kw):
    res = execute_tool(ctx, state, "check_claims", cc.check_claims, **kw)
    assert res.ok, res.error
    return res.summary


def test_claims_not_a_claim_is_ignored_but_listed_for_audit(db, fake):
    # "I have not used Power Apps" is an admission; without this outlet a forced
    # verdict put it under "unsupported" and failed an honest letter.
    fake.responses["check_claims"] = _judged(
        {"quote": "I have not used Power Apps", "declared": 0, "verdict": "not_a_claim",
         "support": [], "reason": "an admission"},
    )
    state, ctx = _claims_state(db, [Claim(text="I built REST APIs", source="experience:12#s1")])
    s = _run_claims(ctx, state)
    assert s["passed"] and not s["issues"] and not s["warnings"]
    assert s["not_claims"] == ["'I have not used Power Apps': an admission"]


def test_claims_judge_reasons_before_its_verdict():
    # JSON-schema property order is generation order: the verdict must come after
    # the reason, or the model commits first and argues the opposite afterwards.
    fields = list(cc.JudgedClaim.model_json_schema()["properties"])
    assert fields.index("support") < fields.index("reason") < fields.index("verdict")


def test_claims_bad_source_pointer_blocks_in_stage_one(db, fake):
    fake.responses["check_claims"] = {"claims": []}
    state, ctx = _claims_state(db, [Claim(text="I ran a team", source="experience:999")])
    s = _run_claims(ctx, state)
    assert s["passed"] is False
    assert len(s["issues"]) == 1 and s["issues"][0].startswith("bad_source")
    assert state.latest_draft.checks["claims"].passed is False


def test_claims_declared_claim_without_source_is_not_a_stage_one_issue(db, fake):
    fake.responses["check_claims"] = {"claims": []}
    state, ctx = _claims_state(db, [Claim(text="I built REST APIs", source=None)])
    assert cc._stage1(state, ctx) == []
    assert _run_claims(ctx, state)["passed"] is True


def test_claims_overstated_blocks(db, fake):
    fake.responses["check_claims"] = _judged({"verdict": "overstated", "reason": "only contributed"})
    state, ctx = _claims_state(db, [Claim(text="I built REST APIs", source="experience:12#s1")])
    s = _run_claims(ctx, state)
    assert s["passed"] is False and s["issues"][0].startswith("overstated")
    assert "only contributed" in s["issues"][0]


def test_claims_unsupported_blocks(db, fake):
    fake.responses["check_claims"] = _judged({"verdict": "unsupported", "support": [], "reason": "nothing says this"})
    state, ctx = _claims_state(db, [Claim(text="I built REST APIs", source="experience:12#s1")])
    s = _run_claims(ctx, state)
    assert s["passed"] is False and s["issues"][0].startswith("unsupported")


def test_claims_supported_passes_and_records_on_latest_draft(db, fake):
    fake.responses["check_claims"] = _judged({})
    state, ctx = _claims_state(db, [Claim(text="I built REST APIs", source="experience:12#s1")])
    s = _run_claims(ctx, state)
    assert s["passed"] is True and s["issues"] == [] and s["warnings"] == []
    assert state.latest_draft.checks["claims"].passed is True
    assert s["verdicts"] == {"supported": 1}


def test_claims_supported_with_no_resolvable_support_counts_as_unsupported(db, fake):
    fake.responses["check_claims"] = _judged({"support": ["experience:999", "nonsense"]})
    state, ctx = _claims_state(db, [Claim(text="I built REST APIs", source=None)])
    s = _run_claims(ctx, state)
    assert s["passed"] is False and s["issues"][0].startswith("unsupported")
    assert s["verdicts"] == {"unsupported": 1}


def test_claims_supported_falls_back_to_the_declared_source(db, fake):
    fake.responses["check_claims"] = _judged({"support": []})
    state, ctx = _claims_state(db, [Claim(text="I built REST APIs", source="experience:12#s1")])
    s = _run_claims(ctx, state)
    assert s["passed"] is True and s["warnings"] == []


def test_claims_judge_pointers_are_cleaned_before_resolving(db, fake):
    fake.responses["check_claims"] = _judged({"support": ["[experience:12#s1]"]})
    state, ctx = _claims_state(db, [Claim(text="I built REST APIs", source=None)])
    assert _run_claims(ctx, state)["passed"] is True


def test_claims_undeclared_supported_claim_is_a_warning_not_blocking(db, fake):
    fake.responses["check_claims"] = _judged(
        {"declared": 0, "quote": "I deployed to AWS", "support": ["experience:12#s2"]})
    state, ctx = _claims_state(db, [Claim(text="I built REST APIs", source="experience:12#s1")])
    s = _run_claims(ctx, state)
    assert s["passed"] is True and s["issues"] == []
    assert len(s["warnings"]) == 1 and s["warnings"][0].startswith("undeclared")
    assert "experience:12#s2" in s["warnings"][0]
    assert state.latest_draft.checks["claims"].warnings == s["warnings"]


def test_claims_employer_claim_not_in_ad_blocks(db, fake):
    fake.responses["check_claims"] = _judged(
        {"about": "employer", "declared": 0, "verdict": "unsupported", "support": [],
         "quote": "your 500 staff", "reason": "ad silent"}
    )
    state, ctx = _claims_state(db, [])
    s = _run_claims(ctx, state)
    assert s["passed"] is False and s["issues"][0].startswith("employer_claim_not_in_ad")
    assert s["verdicts"] == {"employer_unsupported": 1}


def test_claims_supported_employer_claim_is_fine(db, fake):
    fake.responses["check_claims"] = _judged(
        {"about": "employer", "declared": 0, "support": [], "quote": "you build logistics software"}
    )
    state, ctx = _claims_state(db, [])
    s = _run_claims(ctx, state)
    assert s["passed"] is True and s["issues"] == [] and s["warnings"] == []


def test_claims_default_tier_mid_and_tier_override_passes_through(db, fake):
    # mid since 2026-10-03: small passed real overclaims mid caught (plan Decision log)
    fake.responses["check_claims"] = _judged({})
    state, ctx = _claims_state(db, [Claim(text="I built REST APIs", source="experience:12#s1")])
    _run_claims(ctx, state)
    _run_claims(ctx, state, tier="small")
    assert [c["kw"]["tier"] for c in fake.calls] == ["mid", "small"]
    assert all(c["kw"]["task"] == "check_claims" and c["kw"]["run_id"] == ctx.run.id for c in fake.calls)


def test_claims_prompt_contains_letter_ad_and_declared_claims(db, fake):
    fake.responses["check_claims"] = _judged({})
    state, ctx = _claims_state(db, [Claim(text="I built REST APIs", source="experience:12#s1")])
    _run_claims(ctx, state)
    user = fake.calls[0]["user"]
    assert AD in user and "I built REST APIs." in user
    assert f"1. 'I built REST APIs'  -- cited: [experience:12#s1] {SENT1}" in user


def test_claims_duplicate_issues_are_deduplicated(db, fake):
    fake.responses["check_claims"] = _judged(
        {"verdict": "overstated", "reason": "r", "quote": "same"},
        {"verdict": "overstated", "reason": "r", "quote": "same"},
    )
    state, ctx = _claims_state(db, [Claim(text="I built REST APIs", source="experience:12#s1")])
    s = _run_claims(ctx, state)
    assert len(s["issues"]) == 1
    assert len(state.latest_draft.checks["claims"].issues) == 1


def test_claims_without_a_draft_is_a_tool_error(db, fake):
    state, ctx = _fresh(db)
    res = execute_tool(ctx, state, "check_claims", cc.check_claims)
    assert not res.ok and "needs a draft" in res.error
    assert fake.calls == []


# ---------------------------------------------------------------------------
# check_requirements
# ---------------------------------------------------------------------------
LETTER = (
    "Dear Hiring Manager,\n\nAt Acme I built REST APIs for three teams. "
    "I also worked with SQL databases and deployed services to AWS.\n\nSincerely,\nBob"
)


def _item(rid, addressed=True, quote=""):
    return {"id": rid, "addressed": addressed, "quote": quote}


def _req_state(db, requirements=None):
    state, ctx = _fresh(db, requirements=requirements)
    _add_draft(state, LETTER)
    return state, ctx


def _run_reqs(ctx, state):
    res = execute_tool(ctx, state, "check_requirements", cr.check_requirements)
    assert res.ok, res.error
    return res.summary


@pytest.mark.parametrize("quote, expected", [
    ("built REST APIs for three teams", True),  # exact substring
    ("Built rest APIs, for THREE teams!", True),  # case and punctuation ignored
    ("built REST APIs for three teams today", True),  # 6 of 7 words present (86%)
    ("built REST APIs for four teams today", False),  # 5 of 7 words present (71%)
    ("I invented a quantum compiler at Google", False),
    ("", False),
    ("   ...  ", False),
])
def test_quote_in_letter(quote, expected):
    assert cr.quote_in_letter(quote, LETTER) is expected


def test_requirements_must_cover_with_real_quote_passes(db, fake):
    fake.responses["check_requirements"] = {"items": [
        _item("R1", True, "built REST APIs for three teams"),
        _item("R2", True, "worked with SQL databases"),
    ]}
    state, ctx = _req_state(db)
    s = _run_reqs(ctx, state)
    assert s["passed"] is True and s["must_cover"] == 2 and s["uncovered"] == []
    assert state.latest_draft.checks["requirements"].passed is True


def test_requirements_invented_quote_counts_as_uncovered(db, fake):
    fake.responses["check_requirements"] = {"items": [
        _item("R1", True, "built REST APIs for three teams"),
        _item("R2", True, "I led a quantum computing lab at Google"),
    ]}
    state, ctx = _req_state(db)
    s = _run_reqs(ctx, state)
    assert s["passed"] is False and s["uncovered"] == ["R2"]
    assert s["unverified_quotes"] == ["R2"]
    check = state.latest_draft.checks["requirements"]
    assert check.passed is False and check.issues == ["R2 not addressed: 'SQL databases'"]


def test_requirements_reported_not_addressed_is_uncovered_but_not_unverified(db, fake):
    fake.responses["check_requirements"] = {"items": [
        _item("R1", True, "built REST APIs for three teams"), _item("R2", False)]}
    state, ctx = _req_state(db)
    s = _run_reqs(ctx, state)
    assert s["uncovered"] == ["R2"] and s["unverified_quotes"] == []


def test_requirements_missing_item_in_report_is_uncovered(db, fake):
    fake.responses["check_requirements"] = {"items": [_item("R1", True, "built REST APIs for three teams")]}
    state, ctx = _req_state(db)
    assert _run_reqs(ctx, state)["uncovered"] == ["R2"]


def test_requirements_unaddressed_may_use_item_does_not_fail(db, fake):
    fake.responses["check_requirements"] = {"items": [
        _item("R1", True, "built REST APIs for three teams"),
        _item("R2", True, "worked with SQL databases"),
        _item("R3", True, "deployed services to AWS"),  # may_use, addressed
        _item("R6", False),  # may_use, not addressed
        _item("R8", False),
    ]}
    state, ctx = _req_state(db)
    s = _run_reqs(ctx, state)
    assert s["passed"] is True
    assert s["optional_covered"] == ["R3"]


def test_requirements_asks_the_model_about_must_and_may_items_only(db, fake):
    fake.responses["check_requirements"] = {"items": []}
    state, ctx = _req_state(db)
    _run_reqs(ctx, state)
    user = fake.calls[0]["user"]
    for rid in ("R1", "R3", "R6", "R8"):
        assert f"{rid}: " in user
    for rid in ("R4", "R5", "R7"):
        assert f"{rid}: " not in user and f"{rid} (PARTIAL)" not in user
    assert fake.calls[0]["kw"]["tier"] == "small" and fake.calls[0]["kw"]["task"] == "check_requirements"


def test_requirements_marks_partial_items_so_honest_framing_counts(db, fake):
    # A partial item can only be met by related experience; unmarked, the checker
    # expects the full claim and pushes the reviser towards overclaiming.
    fake.responses["check_requirements"] = {"items": []}
    state, ctx = _req_state(db)
    _run_reqs(ctx, state)
    assert "R2 (PARTIAL): SQL databases" in fake.calls[0]["user"]
    assert "R1: Build REST APIs" in fake.calls[0]["user"]
    assert "PARTIAL" in fake.calls[0]["system"]


def test_requirements_nothing_to_check_passes_without_llm(db, fake):
    state, ctx = _req_state(db, requirements=[
        _req("R1", "Kubernetes", status="gap"),
        _req("R2", "Valid licence", role="not_for_letter", status="gap"),
    ])
    s = _run_reqs(ctx, state)
    assert s["passed"] is True and s["must_cover"] == 0
    assert fake.calls == []
    assert state.latest_draft.checks["requirements"].passed is True


def test_requirements_without_a_draft_is_a_tool_error(db, fake):
    state, ctx = _fresh(db)
    res = execute_tool(ctx, state, "check_requirements", cr.check_requirements)
    assert not res.ok and "needs a draft" in res.error and fake.calls == []


def test_requirements_check_lands_on_the_latest_draft_only(db, fake):
    fake.responses["check_requirements"] = {"items": [
        _item("R1", True, "built REST APIs for three teams"),
        _item("R2", True, "worked with SQL databases"),
    ]}
    state, ctx = _req_state(db)
    _add_draft(state, LETTER)  # draft 2
    _run_reqs(ctx, state)
    assert state.drafts[0].checks == {}
    assert "requirements" in state.drafts[1].checks


# ---------------------------------------------------------------------------
# integration: generate -> three checks -> finish gate, and the revise loop
# ---------------------------------------------------------------------------
_P1 = (
    "I am writing to apply for the Engineer role at LogiCo. Your ad describes production systems "
    "that carry real freight, and that is the work I want to do. At Acme I built REST APIs for "
    "three teams, so I know what it takes to keep an interface steady while several groups depend "
    "on it. I also deployed services to AWS and learned to watch them closely after each release."
)
_P2 = (
    "My studies gave me the base for this. I completed a Bachelor of Information Technology at the "
    "University of Queensland. Along the way I worked with SQL databases, writing queries that "
    "answered real questions for the people who asked them. Short, plain code is what I aim for, "
    "and I test it before I hand it over, because the next person who reads it will not have me "
    "beside them to explain what I meant."
)
_P3 = (
    "LogiCo builds logistics software, and I would like to help build it. I learn quickly, I ask "
    "early when I am stuck, and I finish what I start. I would welcome a chance to talk through "
    "how my work at Acme could help your team keep its production systems steady as they grow, "
    "and I can start the conversation whenever suits you."
)
GOOD_LETTER = f"Dear Hiring Manager,\n\n{_P1}\n\n{_P2}\n\n{_P3}\n\nSincerely,\nBob"
GOOD_CLAIMS = [
    {"quote": "I built REST APIs for three teams", "source": "experience:12#s1"},
    {"quote": "I also deployed services to AWS", "source": "experience:12#s2"},
    {"quote": "I worked with SQL databases", "source": "skill:7"},
]


def test_good_letter_passes_real_style_lint():
    result = sl.lint(GOOD_LETTER)
    assert result["issues"] == [], result


def _run_all_checks(ctx, state):
    for name, fn in (("check_claims", cc.check_claims), ("check_requirements", cr.check_requirements),
                     ("style_lint", sl.style_lint)):
        assert execute_tool(ctx, state, name, fn).ok


def test_integration_generate_check_finish(db, fake):
    fake.responses["generate_letter"] = _letter_json(letter=GOOD_LETTER, claims=GOOD_CLAIMS)
    fake.responses["check_claims"] = _judged(
        {"declared": 1}, {"declared": 2, "support": ["experience:12#s2"]}, {"declared": 3, "support": ["skill:7"]}
    )
    fake.responses["check_requirements"] = {"items": [
        _item("R1", True, "I built REST APIs for three teams"),
        _item("R2", True, "I worked with SQL databases"),
        _item("R3", True, "deployed services to AWS"),
    ]}
    state, ctx = _fresh(db)
    assert execute_tool(ctx, state, "generate_letter", gen.generate_letter).ok
    assert guardrails.can_finish(state) is not None
    _run_all_checks(ctx, state)
    assert all(state.latest_draft.checks[n].passed for n in ("claims", "requirements", "style"))
    assert guardrails.can_finish(state) is None
    assert guardrails.can_revise(state) is not None  # nothing to fix


def test_integration_failed_claims_revise_then_checks_missing_on_new_draft(db, fake):
    fake.responses["generate_letter"] = _letter_json(letter=GOOD_LETTER, claims=GOOD_CLAIMS)
    fake.responses["revise_letter"] = _letter_json(
        letter=GOOD_LETTER.replace("three teams", "two teams"), claims=GOOD_CLAIMS)
    fake.responses["check_claims"] = _judged(
        {"declared": 1, "verdict": "overstated", "reason": "profile says 3 teams"},
        {"declared": 2, "support": ["experience:12#s2"]},
        {"declared": 3, "support": ["skill:7"]},
    )
    fake.responses["check_requirements"] = {"items": [
        _item("R1", True, "I built REST APIs for three teams"),
        _item("R2", True, "I worked with SQL databases"),
    ]}
    state, ctx = _fresh(db)
    execute_tool(ctx, state, "generate_letter", gen.generate_letter)
    _run_all_checks(ctx, state)
    assert guardrails.failed_checks(state) == ["claims"]
    assert "claims failed on draft 1" in guardrails.can_finish(state)

    res = execute_tool(ctx, state, "revise_letter", rev.revise_letter)
    assert res.ok, res.error
    assert res.summary["fixing"] == ["claims"] and res.summary["revised_from"] == 1
    assert state.latest_draft.version == 2 and state.latest_draft.checks == {}
    msg = guardrails.can_finish(state)
    for name in ("claims", "requirements", "style"):
        assert name in msg
    assert "not checked on draft 2" in msg
    # the revise prompt carried the claim issue
    revise_call = [c for c in fake.calls if c["task"] == "revise_letter"][0]
    assert "profile says 3 teams" in revise_call["user"]
