"""Tests for the Phase 3 cover-letter tools (analyze_job, match_profile) and the
runner that logs them. The LLM is replaced by a fake that returns canned JSON, so
what is under test is the code around the model: caching, post-processing, evidence
validation, step logging and cost attribution.
"""
from __future__ import annotations

import datetime
import json
import sqlite3

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.llm.client import BudgetExceededError
from app.llm.letter import runner
from app.llm.letter.runner import ToolError, execute_tool, finish_run, start_run
from app.llm.letter.state import JobInfo, LetterState
from app.llm.letter.tools import analyze_job as aj
from app.llm.letter.tools import match_profile as mp
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

AD = "We build logistics software. You will own production systems. Australian work rights required."


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
        profile = Profile(
            id=1, name="Bob", email="b@x.com", password_hash="x", visa_status="Australian citizen",
            summary="Grad developer.",
        )
        session.add(profile)
        session.flush()
        exp = Experience(
            id=12, user_id=1, experience_type="job", title="Developer", organization="Acme",
            description="Built REST APIs for 3 teams. Deployed services to AWS.",
        )
        sql = Skill(id=7, user_id=1, name="SQL")
        session.add_all([
            exp, sql, Skill(id=8, user_id=1, name="Docker"),
            Qualification(id=4, user_id=1, qualification_type="degree", title="BIT", institution="UQ"),
        ])
        session.add(JobListing(id=5, source="seek", source_job_id="1", url="u", title="Engineer",
                               company="LogiCo", raw_description=AD))
        session.flush()
        session.add(Match(id=9, user_id=1, job_id=5, score=80))
        session.commit()
        yield session
    Base.metadata.drop_all(eng)


def _analysis(requirements=None, **extra) -> dict:
    base = {
        "requirements": requirements
        or [
            {"n": 1, "text": "Build REST APIs", "importance": "essential", "letter_role": "headline",
             "theme": "APIs", "implied_by": []},
            {"n": 2, "text": "CI/CD pipelines", "importance": "important", "letter_role": "implied",
             "theme": "Operations", "implied_by": [1]},
            {"n": 3, "text": "Australian work rights", "importance": "essential",
             "letter_role": "not_for_letter", "theme": "Eligibility", "implied_by": []},
        ],
        "tone": "technical",
        "keywords": ["logistics"],
        "screening_questions": [],
        "company_facts": ["They build logistics software"],
    }
    base.update(extra)
    return base


@pytest.fixture()
def fake_llm(monkeypatch):
    """Replaces complete_json in both tools. Set .by_task[...] to the JSON to return."""
    calls: list[dict] = []
    by_task: dict[str, dict] = {"analyze_job": _analysis()}

    def fake(system, user, schema=None, **kw):
        calls.append({"task": kw.get("task"), "user": user, "kw": kw})
        return by_task[kw["task"]]

    monkeypatch.setattr(aj, "complete_json", fake)
    monkeypatch.setattr(mp, "complete_json", fake)
    monkeypatch.setattr(aj, "model_for", lambda tier: "fake-mid")

    class NS:
        pass

    ns = NS()
    ns.calls, ns.by_task = calls, by_task
    return ns


def _fresh(db) -> tuple[LetterState, runner.ToolContext]:
    state = LetterState(profile_id=1, job=JobInfo(job_id=5, title="Engineer", company="LogiCo"))
    return state, start_run(db, 9, "workflow", state)


# ---------------------------------------------------------------------------
# _postprocess
# ---------------------------------------------------------------------------
def _req(n, text, importance="important", role="mention", theme="T", implied_by=()):
    return {"n": n, "text": text, "importance": importance, "letter_role": role,
            "theme": theme, "implied_by": list(implied_by)}


def _post(reqs):
    return aj._postprocess(aj.JobAnalysis.model_validate(_analysis(reqs)))


def test_nice_to_have_can_never_be_a_headline():
    out = _post([_req(1, "Exposure to logistics", "nice_to_have", "headline")])
    assert out[0]["letter_role"] == "mention"


def test_headlines_capped_keeping_the_most_important_then_earliest():
    reqs = [_req(i, f"Thing {i}", "important", "headline") for i in range(1, 7)]
    reqs[5]["importance"] = "essential"  # last one is essential: must survive the cut
    roles = {r["id"]: r["letter_role"] for r in _post(reqs)}
    assert sum(1 for v in roles.values() if v == "headline") == aj.MAX_HEADLINES
    assert roles["R6"] == "headline"
    assert roles["R5"] == "mention"  # the latest of the equally-important ones is demoted


@pytest.mark.parametrize("text", [
    "Australian work rights", "Must hold Australian citizenship or permanent residency",
    "Current security clearance (NV1)", "Valid driver's licence", "National police check",
    "Visa status allowing full-time work",
])
def test_eligibility_items_are_forced_out_of_the_letter(text):
    out = _post([_req(1, text, "essential", "headline")])
    assert out[0]["letter_role"] == "not_for_letter"


def test_visa_the_company_is_not_eligibility():
    out = _post([_req(1, "Experience integrating Visa payment APIs", "essential", "headline")])
    assert out[0]["letter_role"] == "headline"


def test_implied_by_must_point_at_a_leading_item_and_only_on_implied_rows():
    out = _post([
        _req(1, "Build APIs", "essential", "headline"),
        _req(2, "CI/CD", "important", "implied", implied_by=[1, 2, 9]),  # self + unknown dropped
        _req(3, "Idempotency", "important", "implied", implied_by=[2]),  # points at an implied row
        _req(4, "Docs", "nice_to_have", "mention", implied_by=[1]),  # not an implied row
    ])
    by = {r["id"]: r for r in out}
    assert by["R2"]["implied_by"] == ["R1"]
    assert by["R3"]["implied_by"] == []
    assert by["R4"]["implied_by"] == []


def test_duplicates_blank_text_and_missing_theme_are_cleaned():
    out = _post([_req(1, "Build  APIs", theme=""), _req(2, "build apis"), _req(3, "   ")])
    assert [(r["id"], r["text"], r["theme"]) for r in out] == [("R1", "Build APIs", "General")]


def test_requirements_capped():
    out = _post([_req(i, f"Req {i}") for i in range(1, 30)])
    assert len(out) == aj.MAX_REQUIREMENTS


# ---------------------------------------------------------------------------
# analyze_job
# ---------------------------------------------------------------------------
def test_analyze_job_builds_state_and_caches_on_the_job_row(db, fake_llm):
    state, ctx = _fresh(db)
    result = execute_tool(ctx, state, "analyze_job", aj.analyze_job)

    assert result.ok and result.summary["source"] == "llm"
    assert result.summary["by_letter_role"] == {"headline": 1, "implied": 1, "not_for_letter": 1}
    assert [r.id for r in state.requirements] == ["R1", "R2", "R3"]
    assert state.requirements[1].implied_by == ["R1"]
    assert state.job.tone == "technical" and state.job.company_facts == ["They build logistics software"]
    assert state.eligibility_notes() == ["Australian work rights"]

    job = db.get(JobListing, 5)
    cached = json.loads(job.requirements_checklist)
    assert cached["version"] == aj.ANALYSIS_VERSION and cached["model"] == "fake-mid"
    assert job.requirements_checklist_at is not None
    # Attribution: the call carries the run id and job id, on the mid tier.
    assert fake_llm.calls[0]["kw"] | {} and fake_llm.calls[0]["kw"]["run_id"] == ctx.run.id
    assert fake_llm.calls[0]["kw"]["tier"] == "mid" and fake_llm.calls[0]["kw"]["job_id"] == 5
    assert "JOB AD:\n" + AD in fake_llm.calls[0]["user"]


def test_analyze_job_second_run_uses_the_cache_without_an_llm_call(db, fake_llm):
    s1, c1 = _fresh(db)
    execute_tool(c1, s1, "analyze_job", aj.analyze_job)
    s2, c2 = _fresh(db)
    result = execute_tool(c2, s2, "analyze_job", aj.analyze_job)
    assert result.summary["source"] == "cache"
    assert len(fake_llm.calls) == 1
    assert [r.text for r in s2.requirements] == [r.text for r in s1.requirements]


def test_changed_description_or_force_or_version_bump_redoes_the_analysis(db, fake_llm, monkeypatch):
    s, c = _fresh(db)
    execute_tool(c, s, "analyze_job", aj.analyze_job)
    execute_tool(c, s, "analyze_job", aj.analyze_job, force=True)
    assert len(fake_llm.calls) == 2

    db.get(JobListing, 5).raw_description = AD + " Updated: now also React."
    db.commit()
    execute_tool(c, s, "analyze_job", aj.analyze_job)
    assert len(fake_llm.calls) == 3

    monkeypatch.setattr(aj, "ANALYSIS_VERSION", aj.ANALYSIS_VERSION + 1)
    execute_tool(c, s, "analyze_job", aj.analyze_job)
    assert len(fake_llm.calls) == 4


def test_analyze_job_without_a_description_fails_cleanly(db, fake_llm):
    db.get(JobListing, 5).raw_description = None
    db.commit()
    state, ctx = _fresh(db)
    result = execute_tool(ctx, state, "analyze_job", aj.analyze_job)
    assert not result.ok and "no stored description" in result.error
    assert fake_llm.calls == []


def test_empty_model_answer_is_an_error_and_is_not_cached(db, fake_llm):
    fake_llm.by_task["analyze_job"] = _analysis(requirements=[_req(1, "   ")])
    state, ctx = _fresh(db)
    result = execute_tool(ctx, state, "analyze_job", aj.analyze_job)
    assert not result.ok and "no usable requirements" in result.error
    assert db.get(JobListing, 5).requirements_checklist is None


def test_corrupt_cache_is_ignored_not_fatal(db, fake_llm):
    db.get(JobListing, 5).requirements_checklist = "{not json"
    db.commit()
    state, ctx = _fresh(db)
    assert execute_tool(ctx, state, "analyze_job", aj.analyze_job).summary["source"] == "llm"


# ---------------------------------------------------------------------------
# match_profile
# ---------------------------------------------------------------------------
def _matches(*items) -> dict:
    return {"matches": [{"id": i, "status": s, "evidence": e, "note": n} for i, s, e, n in items]}


def _analysed(db, fake_llm):
    state, ctx = _fresh(db)
    execute_tool(ctx, state, "analyze_job", aj.analyze_job)
    return state, ctx


def test_match_profile_requires_analysis_first(db, fake_llm):
    state, ctx = _fresh(db)
    result = execute_tool(ctx, state, "match_profile", mp.match_profile)
    assert not result.ok and "run analyze_job first" in result.error


def test_match_profile_applies_valid_evidence(db, fake_llm):
    state, ctx = _analysed(db, fake_llm)
    fake_llm.by_task["match_profile"] = _matches(
        ("R1", "supported", ["experience:12#s1"], "Built REST APIs at Acme"),
        ("R2", "partial", ["experience:12#s2"], "Deployed to AWS"),
        ("R3", "supported", [], "citizen per profile facts"),
    )
    result = execute_tool(ctx, state, "match_profile", mp.match_profile)

    assert result.ok and result.summary["corrections"] == []
    r1, r2, r3 = state.requirements
    assert (r1.status, r1.evidence, r1.note) == ("supported", ["experience:12#s1"], "Built REST APIs at Acme")
    assert r2.status == "partial"
    assert (r3.status, r3.evidence) == ("supported", [])  # eligibility: supported from facts, no pointers
    prompt = fake_llm.calls[-1]["user"]
    assert "[experience:12#s1] Built REST APIs for 3 teams." in prompt
    assert "visa/work status: Australian citizen" in prompt
    assert "R2 [important / implied] CI/CD pipelines (follows from R1)" in prompt


def test_hallucinated_pointers_are_dropped_and_unsupported_claims_demoted(db, fake_llm):
    state, ctx = _analysed(db, fake_llm)
    fake_llm.by_task["match_profile"] = _matches(
        ("R1", "supported", ["experience:99#s1", "experience:12#s9", "made up"], "x"),
        ("R2", "supported", ["skill:8"], "Docker listed"),
        ("R3", "gap", ["experience:12#s1"], "n/a"),
    )
    result = execute_tool(ctx, state, "match_profile", mp.match_profile)
    r1, r2, r3 = state.requirements
    assert (r1.status, r1.evidence) == ("gap", [])  # nothing valid left -> gap
    assert (r2.status, r2.evidence) == ("partial", ["skill:8"])  # a bare skill can't be "supported"
    assert (r3.status, r3.evidence) == ("gap", [])  # gaps carry no evidence
    assert len(result.summary["corrections"]) == 5
    assert result.summary["pending_gaps"] == ["R1"]  # essential + headline + gap blocks drafting


def test_pointers_copied_with_brackets_or_quotes_still_resolve(db, fake_llm):
    state, ctx = _analysed(db, fake_llm)
    fake_llm.by_task["match_profile"] = _matches(
        ("R1", "supported", ["[experience:12#s1]", " `experience:12#s2` ", "'qualification:4'"], "ok"),
        ("R2", "gap", [], "none"),
        ("R3", "gap", [], "none"),
    )
    result = execute_tool(ctx, state, "match_profile", mp.match_profile)
    assert state.requirements[0].status == "supported"
    assert state.requirements[0].evidence == ["experience:12#s1", "experience:12#s2", "qualification:4"]
    assert result.summary["corrections"] == []


def test_requirement_missing_from_the_answer_becomes_a_gap(db, fake_llm):
    state, ctx = _analysed(db, fake_llm)
    fake_llm.by_task["match_profile"] = _matches(("R1", "supported", ["experience:12#s1"], "ok"))
    result = execute_tool(ctx, state, "match_profile", mp.match_profile)
    assert state.requirements[1].status == "gap" and state.requirements[1].note == "not assessed by the model"
    assert any("missing" in c for c in result.summary["corrections"])


def test_eligibility_evidence_is_never_cited_and_user_decisions_survive_a_rematch(db, fake_llm):
    state, ctx = _analysed(db, fake_llm)
    from app.llm.letter.state import UserDecision

    state.requirements[0].user_decision = UserDecision(choice="leave_out")
    fake_llm.by_task["match_profile"] = _matches(
        ("R1", "gap", [], "none"),
        ("R2", "gap", [], "none"),
        ("R3", "supported", ["experience:12#s1"], "citizen"),
    )
    execute_tool(ctx, state, "match_profile", mp.match_profile)
    assert state.requirements[2].evidence == []
    assert state.requirements[0].user_decision.choice == "leave_out"
    assert state.pending_gaps() == []  # decided, so no longer blocking


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------
def test_steps_are_numbered_logged_and_state_persisted(db, fake_llm):
    state, ctx = _analysed(db, fake_llm)
    fake_llm.by_task["match_profile"] = _matches(("R1", "supported", ["experience:12#s1"], "ok"))
    execute_tool(ctx, state, "match_profile", mp.match_profile)

    steps = db.scalars(select(LetterRunStep).where(LetterRunStep.run_id == ctx.run.id).order_by(LetterRunStep.seq)).all()
    assert [(s.seq, s.tool) for s in steps] == [(1, "analyze_job"), (2, "match_profile")]
    assert json.loads(steps[0].result_summary)["requirements"] == 3
    assert steps[0].error is None and steps[0].duration_ms >= 0

    run = db.get(LetterRun, ctx.run.id)
    assert run.tool_calls == 2
    assert LetterState.model_validate_json(run.state).requirements[0].evidence == ["experience:12#s1"]


def test_failed_step_is_logged_with_its_error_and_does_not_raise(db, fake_llm):
    state, ctx = _fresh(db)
    result = execute_tool(ctx, state, "match_profile", mp.match_profile)  # no analysis yet
    assert not result.ok
    step = db.scalars(select(LetterRunStep)).one()
    assert step.error and step.result_summary is None
    assert state.budget.tool_calls == 1  # a failed call still counts against the cap


def test_unexpected_exception_in_a_tool_is_caught_and_logged(db):
    state, ctx = _fresh(db)

    def boom(state, ctx):
        raise ValueError("kaboom")

    result = execute_tool(ctx, state, "boom", boom)
    assert not result.ok and result.error == "kaboom"
    assert db.scalars(select(LetterRunStep)).one().error == "kaboom"


def test_budget_error_is_logged_then_reraised(db):
    state, ctx = _fresh(db)

    def over(state, ctx):
        raise BudgetExceededError("Daily LLM budget reached")

    with pytest.raises(BudgetExceededError):
        execute_tool(ctx, state, "over", over)
    step = db.scalars(select(LetterRunStep)).one()
    assert "BudgetExceededError" in step.error


def test_run_cost_is_summed_from_llm_usage_by_run_id(db, fake_llm):
    state, ctx = _fresh(db)
    now = datetime.datetime.now(datetime.timezone.utc)
    db.add_all([
        LlmUsage(task="analyze_job", tier="mid", model="m", cost_usd=0.0123, run_id=ctx.run.id, created_at=now),
        LlmUsage(task="match_profile", tier="mid", model="m", cost_usd=0.0077, run_id=ctx.run.id, created_at=now),
        LlmUsage(task="other", tier="mid", model="m", cost_usd=5.0, run_id=ctx.run.id + 100, created_at=now),
    ])
    db.commit()
    execute_tool(ctx, state, "noop", lambda s, c: {})
    assert state.budget.cost_usd == pytest.approx(0.02)
    assert float(db.get(LetterRun, ctx.run.id).cost_usd) == pytest.approx(0.02)


def test_finish_run_stamps_status_and_deleting_the_match_cascades(db, fake_llm):
    state, ctx = _analysed(db, fake_llm)
    state.add_draft("a letter")
    finish_run(ctx, state, "done")
    run = db.get(LetterRun, ctx.run.id)
    assert run.status == "done" and run.finished_at is not None and run.final_draft_version == 1

    db.delete(db.get(Match, 9))
    db.commit()
    assert db.scalars(select(LetterRun)).all() == []
    assert db.scalars(select(LetterRunStep)).all() == []


def test_waiting_run_is_not_marked_finished(db, fake_llm):
    state, ctx = _fresh(db)
    finish_run(ctx, state, "waiting_user")
    assert db.get(LetterRun, ctx.run.id).finished_at is None


# ---------------------------------------------------------------------------
# API: eligibility notes
# ---------------------------------------------------------------------------
def test_api_exposes_eligibility_notes_from_the_cached_checklist(db, fake_llm):
    from fastapi.testclient import TestClient

    from app.api.main import app, get_db

    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        # Before any analysis: empty, not an error.
        assert client.get("/jobs/5").json()["eligibility_notes"] == []
        assert client.get("/jobs").json()[0]["eligibility_notes"] == []

        state, ctx = _fresh(db)
        execute_tool(ctx, state, "analyze_job", aj.analyze_job)
        assert client.get("/jobs/5").json()["eligibility_notes"] == ["Australian work rights"]
        assert client.get("/jobs").json()[0]["eligibility_notes"] == ["Australian work rights"]

        db.get(JobListing, 5).requirements_checklist = "{corrupt"
        db.commit()
        assert client.get("/jobs/5").json()["eligibility_notes"] == []
    finally:
        app.dependency_overrides.pop(get_db, None)
