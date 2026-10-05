"""Tests for Phase 7b's ask_user pipeline: remembered "no"s (gap_policy.py), the user's
answers (answers.py), resuming a paused run (outcome.reopen_run, workflow.resume_workflow,
agent.resume_agent, engines.py), the partial re-match in match_profile and the ``skill``
field in analyze_job.

No LLM, no network. The tools the engines call are replaced by scripted fakes that mutate
the LetterState the way the real ones do; ``complete_json`` / ``complete_tools`` are patched
in the module that imports them.
"""
from __future__ import annotations

import json
import sqlite3

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import gaps
from app.db import Base
from app.llm.client import ToolStep
from app.llm.letter import agent, answers, engines, guardrails, outcome, registry, workflow
from app.llm.letter.gap_policy import apply_remembered, ask_user_gaps, ask_user_tool
from app.llm.letter.outcome import RESUME_EXTRA_CALLS, reopen_run
from app.llm.letter.runner import load_profile, start_run
from app.llm.letter.state import (
    Check, Claim, JobInfo, LetterState, ProfileIndex, Requirement, UserDecision,
)
from app.llm.letter.tools import analyze_job as analyze_mod
from app.llm.letter.tools import match_profile as match_mod
from app.models import (
    Experience, GapDecision, GapSighting, JobListing, LetterRun, LetterRunStep, Match, Profile,
    Qualification, Skill,
)

PBI_TEXT = "Experience building dashboards with Power BI"


@pytest.fixture()
def db():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(eng, "connect")
    def _fk_on(conn, _):
        if isinstance(conn, sqlite3.Connection):
            conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(eng)
    with sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)() as session:
        session.add(Profile(id=1, name="Bob", email="b@x.com", password_hash="x",
                            visa_status="Australian citizen", summary="Grad developer."))
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
def _req(rid, text="x", importance="essential", role="headline", status="gap", skill="", evidence=(),
         decision=None):
    return Requirement(id=rid, text=text, importance=importance, letter_role=role, status=status,
                       skill=skill, evidence=list(evidence), user_decision=decision, theme=f"t-{rid}")


def _state(reqs) -> LetterState:
    return LetterState(profile_id=1, job=JobInfo(job_id=5, title="Engineer", company="LogiCo"),
                       requirements=reqs)


def _ctx(db, state, engine="workflow"):
    return start_run(db, 9, engine, state)


def _count(db, model) -> int:
    return db.scalar(select(func.count()).select_from(model))


def _run(db, run_id) -> LetterRun:
    db.expire_all()
    return db.get(LetterRun, run_id)


def _run_state(db, run_id) -> LetterState:
    return LetterState.model_validate_json(_run(db, run_id).state)


def _steps(db, run_id) -> list[str]:
    db.expire_all()
    return list(db.scalars(select(LetterRunStep.tool).where(LetterRunStep.run_id == run_id)
                           .order_by(LetterRunStep.seq)))


def _sightings(db) -> list[GapSighting]:
    db.expire_all()
    return list(db.scalars(select(GapSighting).order_by(GapSighting.id)))


# --- scripted tool fakes (workflow + agent) ---------------------------------
class Fakes:
    """Stand-ins for the tools. R1 is an essential headline gap on "Power BI"; R2 is
    supported. ``match_status`` maps id -> (status, evidence) for requirements still unknown."""

    def __init__(self, reqs=None):
        self.reqs = reqs or [
            dict(id="R1", text=PBI_TEXT, importance="essential", letter_role="headline", skill="Power BI"),
            dict(id="R2", text="Build REST APIs", importance="essential", letter_role="headline"),
        ]
        self.match_status = {"R1": ("gap", []), "R2": ("supported", ["experience:12#s1"])}
        self.calls: list[str] = []

    def analyze_job(self, state, ctx):
        self.calls.append("analyze_job")
        state.requirements = [Requirement(**r) for r in self.reqs]
        return {"requirements": len(state.requirements)}

    def match_profile(self, state, ctx):
        self.calls.append("match_profile")
        for r in state.requirements:
            if r.status == "unknown":
                r.status, r.evidence = self.match_status.get(r.id, ("supported", ["experience:12"]))
        remembered = apply_remembered(state, ctx)  # the real tool does this at its end
        return {"matched": len(state.requirements), "remembered_no": remembered}

    def generate_letter(self, state, ctx):
        self.calls.append("generate_letter")
        state.add_draft("draft 1", [Claim(text="c", source="skill:7")])
        return {"draft": 1}

    def revise_letter(self, state, ctx):  # pragma: no cover - checks always pass
        self.calls.append("revise_letter")
        raise AssertionError("not expected")

    def _check(self, tool, key):
        def fn(state, ctx):
            self.calls.append(tool)
            state.record_check(key, Check(passed=True))
            return {"passed": True}
        return fn

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

    def install_agent(self, monkeypatch):
        monkeypatch.setattr(registry, "analyze_job", self.analyze_job)
        monkeypatch.setattr(registry, "match_profile", self.match_profile)
        monkeypatch.setattr(registry, "generate_letter", self.generate_letter)
        monkeypatch.setattr(registry, "revise_letter", self.revise_letter)
        monkeypatch.setattr(registry, "check_claims", self._check("check_claims", "claims"))
        monkeypatch.setattr(registry, "check_requirements", self._check("check_requirements", "requirements"))
        monkeypatch.setattr(registry, "style_lint", self._check("style_lint", "style"))
        return self


class Orch:
    """Fake for ``agent.complete_tools``: a scripted list of tool names."""

    def __init__(self, names):
        self.names = list(names)
        self.prompts: list[str] = []

    def __call__(self, system_prompt, messages, tools, **kw):
        self.prompts.append(messages[0]["content"])
        assert self.names, "orchestrator called after the script ran out"
        return ToolStep(tool=self.names.pop(0))


def _wf_waiting(db, monkeypatch, fakes=None):
    """A workflow run paused on R1's question."""
    fakes = (fakes or Fakes()).install_workflow(monkeypatch)
    res = workflow.run_workflow(db, 5, 1, gap_policy=ask_user_gaps)
    assert res.status == "waiting_user"
    return res, fakes


YES_ROWS = {
    "experiences": [{
        "experience_type": "university_project", "title": "Sales dashboard", "organization": "UQ",
        "start": "2024-03", "end": "2024-06", "description": "Built Power BI dashboards.",
        "skills": ["Power BI", "SQL"],
    }],
    "qualifications": [],
    "skills": [],
}


def _fake_parse(rows=None):
    payload = rows if rows is not None else YES_ROWS
    calls = []

    def fake(system, user, **kw):
        calls.append({"user": user, **kw})
        return payload

    fake.calls = calls
    return fake


# ---------------------------------------------------------------------------
# 1. apply_remembered
# ---------------------------------------------------------------------------
class TestApplyRemembered:
    def test_leaves_out_matching_gap_and_counts_sighting(self, db):
        gaps.save_no(db, 1, label="Power BI", key="power bi")
        state = _state([_req("R1", PBI_TEXT, skill="Power BI"), _req("R2", "Other", skill="Rust")])
        ctx = _ctx(db, state)
        assert apply_remembered(state, ctx) == ["R1"]
        d = state.requirement("R1").user_decision
        assert d.choice == "leave_out" and d.remembered is True
        assert state.requirement("R2").user_decision is None
        s = _sightings(db)
        assert len(s) == 1
        assert s[0].source == "letter_run" and s[0].job_id == 5 and s[0].importance == "essential"
        assert s[0].job_title == "Engineer"

    def test_importance_recorded_from_requirement(self, db):
        gaps.save_no(db, 1, label="Power BI", key="power bi")
        state = _state([_req("R1", PBI_TEXT, importance="nice_to_have", role="mention", skill="Power BI")])
        apply_remembered(state, _ctx(db, state))
        assert _sightings(db)[0].importance == "nice_to_have"

    def test_not_for_letter_never_matched_or_counted(self, db):
        gaps.save_no(db, 1, label="Power BI", key="power bi")
        state = _state([_req("R1", "Power BI licence", role="not_for_letter", skill="Power BI")])
        assert apply_remembered(state, _ctx(db, state)) == []
        assert state.requirement("R1").user_decision is None
        assert _sightings(db) == []

    def test_supported_requirement_counted_decision_untouched(self, db):
        gaps.save_no(db, 1, label="Power BI", key="power bi")
        state = _state([_req("R1", PBI_TEXT, status="supported", skill="Power BI", evidence=["experience:12"])])
        assert apply_remembered(state, _ctx(db, state)) == []
        assert state.requirement("R1").user_decision is None
        assert len(_sightings(db)) == 1

    def test_no_decisions_returns_empty_and_writes_nothing(self, db):
        state = _state([_req("R1", PBI_TEXT, skill="Power BI")])
        assert apply_remembered(state, _ctx(db, state)) == []
        assert state.requirement("R1").user_decision is None
        assert _sightings(db) == []

    def test_cleared_decision_ignored(self, db):
        d = gaps.save_no(db, 1, label="Power BI", key="power bi")
        gaps.clear(db, 1, d.id)
        state = _state([_req("R1", PBI_TEXT, skill="Power BI")])
        assert apply_remembered(state, _ctx(db, state)) == []

    def test_remembered_via_requirement_wording_when_no_skill(self, db):
        text = "Strong stakeholder management"
        gaps.save_no(db, 1, label=text, key=gaps.skill_key("", text))
        state = _state([_req("R1", text, skill="")])
        assert apply_remembered(state, _ctx(db, state)) == ["R1"]


# ---------------------------------------------------------------------------
# 2. match_profile (fake complete_json)
# ---------------------------------------------------------------------------
class TestMatchProfile:
    def _install(self, monkeypatch, responses):
        calls = []
        queue = list(responses)

        def fake(system, user, **kw):
            calls.append(user)
            return queue.pop(0)

        monkeypatch.setattr(match_mod, "complete_json", fake)
        return calls

    @staticmethod
    def _m(rid, status, evidence=(), note="n"):
        return {"id": rid, "status": status, "evidence": list(evidence), "note": note}

    def test_first_pass_judges_all_then_partial_rematch(self, db, monkeypatch):
        state = _state([
            _req("R1", PBI_TEXT, status="unknown", skill="Power BI"),
            _req("R2", "Build REST APIs", status="unknown"),
        ])
        ctx = _ctx(db, state)
        calls = self._install(monkeypatch, [
            {"matches": [self._m("R1", "gap"), self._m("R2", "supported", ["experience:12#s1"])]},
            {"matches": [self._m("R1", "supported", ["experience:12#s2"])]},
        ])
        s1 = match_mod.match_profile(state, ctx)
        assert "R1 [" in calls[0] and "R2 [" in calls[0]
        assert "rematched" not in s1
        assert state.requirement("R2").evidence == ["experience:12#s1"]

        state.requirement("R1").status = "unknown"
        state.requirement("R1").evidence = []
        s2 = match_mod.match_profile(state, ctx)
        assert "R1 [" in calls[1] and "R2 [" not in calls[1]
        assert s2["rematched"] == ["R1"]
        assert state.requirement("R1").status == "supported"
        assert state.requirement("R2").status == "supported"
        assert state.requirement("R2").evidence == ["experience:12#s1"]  # untouched

    def test_remembered_no_left_out_and_not_pending(self, db, monkeypatch):
        gaps.save_no(db, 1, label="Power BI", key="power bi")
        state = _state([_req("R1", PBI_TEXT, status="unknown", skill="Power BI")])
        ctx = _ctx(db, state)
        self._install(monkeypatch, [{"matches": [self._m("R1", "gap")]}])
        summary = match_mod.match_profile(state, ctx)
        assert summary["remembered_no"] == ["R1"]
        assert "R1" not in summary["pending_gaps"]
        assert state.requirement("R1").user_decision.remembered is True
        assert state.pending_gaps() == []

    def test_unremembered_gap_is_pending(self, db, monkeypatch):
        state = _state([_req("R1", PBI_TEXT, status="unknown", skill="Power BI")])
        self._install(monkeypatch, [{"matches": [self._m("R1", "gap")]}])
        summary = match_mod.match_profile(state, _ctx(db, state))
        assert summary["pending_gaps"] == ["R1"] and "remembered_no" not in summary


# ---------------------------------------------------------------------------
# analyze_job: skill field, ANALYSIS_VERSION 3
# ---------------------------------------------------------------------------
class TestAnalyzeJobSkill:
    @staticmethod
    def _analysis():
        base = dict(importance="essential", letter_role="headline", theme="T", implied_by=[])
        return {
            "requirements": [
                {"n": 1, "text": "Experience with Power BI", "skill": "Power BI", **base},
                {"n": 2, "text": "Deliver high quality software", "skill": "x" * 60, **base},
            ],
            "tone": "technical", "keywords": ["bi"], "screening_questions": [],
            "application_instructions": [], "company_facts": [],
        }

    def test_version_is_3(self):
        assert analyze_mod.ANALYSIS_VERSION == 3

    def test_skill_kept_and_overlong_dropped_and_cached_v3(self, db, monkeypatch):
        calls = []

        def fake(*a, **kw):
            calls.append(1)
            return self._analysis()

        monkeypatch.setattr(analyze_mod, "complete_json", fake)
        state = LetterState(profile_id=1, job=JobInfo(job_id=5, title="Engineer"))
        ctx = _ctx(db, state)
        analyze_mod.analyze_job(state, ctx)
        assert state.requirements[0].skill == "Power BI"
        assert state.requirements[1].skill == ""
        db.expire_all()
        assert json.loads(db.get(JobListing, 5).requirements_checklist)["version"] == 3

        # second call uses the cache; a stale v2 cache is redone
        state2 = LetterState(profile_id=1, job=JobInfo(job_id=5, title="Engineer"))
        analyze_mod.analyze_job(state2, ctx)
        assert len(calls) == 1 and state2.requirements[0].skill == "Power BI"
        job = db.get(JobListing, 5)
        data = json.loads(job.requirements_checklist)
        data["version"] = 2
        job.requirements_checklist = json.dumps(data)
        db.commit()
        analyze_mod.analyze_job(LetterState(profile_id=1, job=JobInfo(job_id=5, title="Engineer")), ctx)
        assert len(calls) == 2


# ---------------------------------------------------------------------------
# 3. ask_user_gaps
# ---------------------------------------------------------------------------
class TestAskUserGaps:
    def _two_gaps(self):
        return _state([
            _req("R1", PBI_TEXT, skill="Power BI"),
            _req("R2", "Knowledge of Kubernetes in production", skill=""),
            _req("R3", "Fine", status="supported", evidence=["experience:12"]),
        ])

    def test_one_question_per_pending_gap(self, db):
        state = self._two_gaps()
        ask_user_gaps(state, _ctx(db, state))
        assert [q.id for q in state.user_questions] == ["Q1", "Q2"]
        q1, q2 = state.user_questions
        assert q1.requirement_id == "R1" and q2.requirement_id == "R2"
        assert PBI_TEXT in q1.prompt and '"' in q1.prompt
        assert q1.skill_key == gaps.skill_key("Power BI", PBI_TEXT) == "power bi"
        assert q2.skill_key == gaps.skill_key("", "Knowledge of Kubernetes in production")
        assert q1.status == "open"

    def test_twice_does_not_duplicate(self, db):
        state = self._two_gaps()
        ctx = _ctx(db, state)
        ask_user_gaps(state, ctx)
        ask_user_gaps(state, ctx)
        assert len(state.user_questions) == 2

    def test_waiting_and_summary(self, db):
        state = self._two_gaps()
        assert not state.waiting_on_user()
        ask_user_gaps(state, _ctx(db, state))
        assert state.waiting_on_user()
        assert "WAITING" in state.summary_for_orchestrator()

    def test_remembered_gap_not_asked(self, db):
        gaps.save_no(db, 1, label="Power BI", key="power bi")
        state = self._two_gaps()
        ask_user_gaps(state, _ctx(db, state))
        assert [q.requirement_id for q in state.user_questions] == ["R2"]

    def test_no_pending_gaps_no_questions(self, db):
        state = _state([_req("R1", "Fine", status="supported", evidence=["experience:12"])])
        ask_user_gaps(state, _ctx(db, state))
        assert state.user_questions == [] and not state.waiting_on_user()

    def test_ask_user_tool_summary(self, db):
        state = self._two_gaps()
        out = ask_user_tool(ask_user_gaps)(state, _ctx(db, state))
        assert out["asked_about"] == ["R1", "R2"]
        assert out["questions"] == ["Q1", "Q2"] and out["waiting_on_user"] is True

    def test_ask_user_tool_reports_remembered(self, db):
        gaps.save_no(db, 1, label="Power BI", key="power bi")
        state = self._two_gaps()
        # remembered gap is still pending here (match_profile not run): policy decides it
        out = ask_user_tool(ask_user_gaps)(state, _ctx(db, state))
        assert out["decided"] == {"R1": "leave_out (remembered)"}
        assert out["questions"] == ["Q1"]


# ---------------------------------------------------------------------------
# 4. engines pause on ask_user
# ---------------------------------------------------------------------------
class TestPause:
    def test_workflow_waits(self, db, monkeypatch):
        fakes = Fakes().install_workflow(monkeypatch)
        res = workflow.run_workflow(db, 5, 1, gap_policy=ask_user_gaps)
        assert res.status == "waiting_user"
        assert res.draft is None
        assert "generate_letter" not in fakes.calls
        run = _run(db, res.run_id)
        assert run.status == "waiting_user" and run.finished_at is None
        assert "ask_user" in _steps(db, res.run_id)
        assert _run_state(db, res.run_id).waiting_on_user()

    def test_agent_waits(self, db, monkeypatch):
        fakes = Fakes().install_agent(monkeypatch)
        orch = Orch(["analyze_job", "match_profile", "ask_user"])
        monkeypatch.setattr(agent, "complete_tools", orch)
        res = agent.run_agent(db, 5, 1, gap_policy=ask_user_gaps)
        assert res.status == "waiting_user" and res.draft is None
        assert "generate_letter" not in fakes.calls
        run = _run(db, res.run_id)
        assert run.status == "waiting_user" and run.finished_at is None
        assert _steps(db, res.run_id) == ["analyze_job", "match_profile", "ask_user"]
        assert orch.names == []

    def test_agent_ask_user_refused_without_gap(self, db, monkeypatch):
        fakes = Fakes(reqs=[dict(id="R1", text="Build REST APIs", importance="essential",
                                 letter_role="headline")])
        fakes.install_agent(monkeypatch)
        fakes.match_status = {"R1": ("supported", ["experience:12#s1"])}
        orch = Orch(["analyze_job", "match_profile", "ask_user", "generate_letter", "check_claims",
                     "check_requirements", "style_lint", "finish"])
        monkeypatch.setattr(agent, "complete_tools", orch)
        res = agent.run_agent(db, 5, 1, gap_policy=ask_user_gaps)
        assert res.status == "done"
        steps = list(db.scalars(select(LetterRunStep).where(LetterRunStep.run_id == res.run_id)))
        ask = [s for s in steps if s.tool == "ask_user"]
        assert len(ask) == 1 and (ask[0].error or "").startswith("refused")


# ---------------------------------------------------------------------------
# 5. answer_no
# ---------------------------------------------------------------------------
class TestAnswerNo:
    def test_single_question(self, db, monkeypatch):
        res, _ = _wf_waiting(db, monkeypatch)
        calls_before = _run(db, res.run_id).tool_calls
        state = answers.answer_no(db, res.run_id, "Q1")
        q = state.question("Q1")
        assert q.status == "answered" and q.choice == "no"
        req = state.requirement("R1")
        assert req.user_decision.choice == "leave_out" and req.user_decision.remembered is False
        d = db.scalars(select(GapDecision)).one()
        assert d.label == "Power BI" and d.skill_key == "power bi" and d.user_id == 1
        s = _sightings(db)
        assert len(s) == 1 and s[0].job_id == 5 and s[0].source == "letter_run"
        run = _run(db, res.run_id)
        assert run.status == "answered"
        assert "user_answer" in _steps(db, res.run_id)
        assert run.tool_calls == calls_before
        assert _run_state(db, res.run_id).question("Q1").status == "answered"

    def test_label_cut_to_80_when_no_skill(self, db, monkeypatch):
        long_text = "Demonstrated ability to manage competing stakeholder priorities " * 3
        fakes = Fakes(reqs=[dict(id="R1", text=long_text, importance="essential", letter_role="headline",
                                 skill="")])
        res, _ = _wf_waiting(db, monkeypatch, fakes)
        answers.answer_no(db, res.run_id, "Q1")
        d = db.scalars(select(GapDecision)).one()
        assert d.label == long_text[:80]

    def test_run_stays_waiting_until_last_question(self, db, monkeypatch):
        fakes = Fakes(reqs=[
            dict(id="R1", text=PBI_TEXT, importance="essential", letter_role="headline", skill="Power BI"),
            dict(id="R2", text="Kubernetes", importance="essential", letter_role="headline", skill="Kubernetes"),
        ])
        fakes.match_status = {"R1": ("gap", []), "R2": ("gap", [])}
        res, _ = _wf_waiting(db, monkeypatch, fakes)
        answers.answer_no(db, res.run_id, "Q1")
        assert _run(db, res.run_id).status == "waiting_user"
        answers.answer_no(db, res.run_id, "Q2")
        assert _run(db, res.run_id).status == "answered"
        assert _count(db, GapDecision) == 2


# ---------------------------------------------------------------------------
# 6. answer_yes
# ---------------------------------------------------------------------------
class TestAnswerYes:
    def _counts(self, db):
        return (_count(db, Experience), _count(db, Skill), _count(db, Qualification))

    def test_proposal_and_nothing_written(self, db, monkeypatch):
        res, _ = _wf_waiting(db, monkeypatch)
        before = self._counts(db)
        fake = _fake_parse()
        monkeypatch.setattr(answers, "complete_json", fake)
        state = answers.answer_yes(db, res.run_id, "Q1", "I built Power BI dashboards at uni")
        q = state.question("Q1")
        assert q.status == "needs_confirm" and q.choice == "yes"
        assert q.proposal["experiences"][0]["title"] == "Sales dashboard"
        assert self._counts(db) == before
        assert _run(db, res.run_id).status == "waiting_user"
        assert _run_state(db, res.run_id).question("Q1").status == "needs_confirm"
        assert "Power BI" in fake.calls[0]["user"] and fake.calls[0]["run_id"] == res.run_id
        assert fake.calls[0]["tier"] == "small"

    def test_empty_text(self, db, monkeypatch):
        res, _ = _wf_waiting(db, monkeypatch)
        monkeypatch.setattr(answers, "complete_json", _fake_parse())
        for text in ("", "   ", None):
            with pytest.raises(answers.AnswerError):
                answers.answer_yes(db, res.run_id, "Q1", text)

    def test_model_returns_nothing(self, db, monkeypatch):
        res, _ = _wf_waiting(db, monkeypatch)
        monkeypatch.setattr(answers, "complete_json",
                            _fake_parse({"experiences": [], "qualifications": [], "skills": []}))
        with pytest.raises(answers.AnswerError):
            answers.answer_yes(db, res.run_id, "Q1", "something vague")
        q = _run_state(db, res.run_id).question("Q1")
        assert q.status == "open" and q.proposal is None and q.choice is None


# ---------------------------------------------------------------------------
# 7. confirm
# ---------------------------------------------------------------------------
class TestConfirm:
    def _yes(self, db, monkeypatch):
        res, fakes = _wf_waiting(db, monkeypatch)
        monkeypatch.setattr(answers, "complete_json", _fake_parse())
        answers.answer_yes(db, res.run_id, "Q1", "I built Power BI dashboards at uni")
        return res, fakes

    def test_accept_writes_rows_and_updates_state(self, db, monkeypatch):
        res, _ = self._yes(db, monkeypatch)
        gaps.save_no(db, 1, label="Power BI", key="power bi")  # a No from another ad
        skills_before = _count(db, Skill)
        state = answers.confirm(db, res.run_id, "Q1", accept=True)

        db.expire_all()
        exp = db.scalars(select(Experience).where(Experience.title == "Sales dashboard")).one()
        assert exp.origin == "ask_user" and exp.on_cv is False
        assert {s.name for s in exp.skills} == {"Power BI", "SQL"}
        # "SQL" already existed (id 7): linked, not duplicated
        assert db.scalars(select(Skill).where(Skill.name == "SQL")).one().id == 7
        assert _count(db, Skill) == skills_before + 1
        assert db.scalars(select(Skill).where(Skill.name == "Power BI")).one().origin == "ask_user"

        req = state.requirement("R1")
        assert req.user_decision.choice == "have_it" and req.user_decision.saved_as
        index = ProfileIndex(load_profile(db, 1))
        assert all(index.resolve(p) for p in req.user_decision.saved_as)
        assert f"experience:{exp.id}" in req.user_decision.saved_as
        assert req.status == "unknown" and req.evidence == []

        assert db.get(Profile, 1).profile_revised_at is not None
        assert db.scalars(select(GapDecision)).one().cleared_at is not None
        assert _run(db, res.run_id).status == "answered"
        q = state.question("Q1")
        assert q.status == "answered" and q.saved_as == req.user_decision.saved_as
        assert "user_confirm" in _steps(db, res.run_id)

    def test_accept_with_edited_rows(self, db, monkeypatch):
        res, _ = self._yes(db, monkeypatch)
        edited = answers.ProposedRows(
            experiences=[answers.ProposedExperience(
                experience_type="personal_project", title="My own dashboard", organization="", start="",
                end="", description="Edited text.", skills=["Power BI"])],
            qualifications=[], skills=[])
        answers.confirm(db, res.run_id, "Q1", accept=True, rows=edited)
        db.expire_all()
        titles = {e.title for e in db.scalars(select(Experience))}
        assert "My own dashboard" in titles and "Sales dashboard" not in titles

    def test_reject_reopens_and_saves_nothing(self, db, monkeypatch):
        res, _ = self._yes(db, monkeypatch)
        before = (_count(db, Experience), _count(db, Skill))
        state = answers.confirm(db, res.run_id, "Q1", accept=False)
        q = state.question("Q1")
        assert q.status == "open" and q.proposal is None and q.choice is None
        assert (_count(db, Experience), _count(db, Skill)) == before
        assert _run(db, res.run_id).status == "waiting_user"

    def test_confirm_empty_edited_rows(self, db, monkeypatch):
        res, _ = self._yes(db, monkeypatch)
        with pytest.raises(answers.AnswerError):
            answers.confirm(db, res.run_id, "Q1", accept=True,
                            rows=answers.ProposedRows(experiences=[], qualifications=[], skills=[]))

    def test_existing_normalised_skill_linked(self, db, monkeypatch):
        db.add(Skill(user_id=1, name="PowerBI"))
        db.commit()
        res, _ = self._yes(db, monkeypatch)
        before = _count(db, Skill)
        answers.confirm(db, res.run_id, "Q1", accept=True)
        db.expire_all()
        assert _count(db, Skill) == before  # "Power BI" folded onto "PowerBI"; SQL existed
        exp = db.scalars(select(Experience).where(Experience.title == "Sales dashboard")).one()
        assert "PowerBI" in {s.name for s in exp.skills}

    def test_qualification_row_written_with_origin(self, db, monkeypatch):
        res, _ = _wf_waiting(db, monkeypatch)
        rows = {"experiences": [], "skills": [], "qualifications": [{
            "qualification_type": "certificate", "title": "Power BI Data Analyst", "institution": "Microsoft",
            "status": "completed"}]}
        monkeypatch.setattr(answers, "complete_json", _fake_parse(rows))
        answers.answer_yes(db, res.run_id, "Q1", "I hold the PL-300")
        answers.confirm(db, res.run_id, "Q1", accept=True)
        db.expire_all()
        q = db.scalars(select(Qualification).where(Qualification.title == "Power BI Data Analyst")).one()
        assert q.origin == "ask_user"


# ---------------------------------------------------------------------------
# 8. state conflicts
# ---------------------------------------------------------------------------
class TestConflicts:
    def test_run_not_waiting(self, db, monkeypatch):
        res, _ = _wf_waiting(db, monkeypatch)
        run = _run(db, res.run_id)
        run.status = "done"
        db.commit()
        with pytest.raises(answers.StateConflict):
            answers.answer_no(db, res.run_id, "Q1")
        with pytest.raises(answers.StateConflict):
            answers.confirm(db, res.run_id, "Q1", accept=False)

    def test_confirm_question_not_needs_confirm(self, db, monkeypatch):
        res, _ = _wf_waiting(db, monkeypatch)
        with pytest.raises(answers.StateConflict):
            answers.confirm(db, res.run_id, "Q1", accept=True)

    def test_answering_answered_question(self, db, monkeypatch):
        fakes = Fakes(reqs=[
            dict(id="R1", text=PBI_TEXT, importance="essential", letter_role="headline", skill="Power BI"),
            dict(id="R2", text="Kubernetes", importance="essential", letter_role="headline", skill="Kubernetes"),
        ])
        fakes.match_status = {"R1": ("gap", []), "R2": ("gap", [])}
        res, _ = _wf_waiting(db, monkeypatch, fakes)
        answers.answer_no(db, res.run_id, "Q1")
        with pytest.raises(answers.StateConflict):
            answers.answer_no(db, res.run_id, "Q1")
        monkeypatch.setattr(answers, "complete_json", _fake_parse())
        with pytest.raises(answers.StateConflict):
            answers.answer_yes(db, res.run_id, "Q1", "text")

    def test_unknown_question(self, db, monkeypatch):
        res, _ = _wf_waiting(db, monkeypatch)
        with pytest.raises(LookupError):
            answers.answer_no(db, res.run_id, "Q99")
        with pytest.raises(LookupError):
            answers.confirm(db, res.run_id, "Q99", accept=False)

    def test_unknown_run(self, db):
        with pytest.raises(LookupError):
            answers.answer_no(db, 999, "Q1")

    def test_state_conflict_is_an_answer_error(self):
        assert issubclass(answers.StateConflict, answers.AnswerError)


# ---------------------------------------------------------------------------
# 9. clean_rows
# ---------------------------------------------------------------------------
def _pe(**kw):
    base = dict(experience_type="job", title="T", organization="", start="", end="", description="d",
                skills=[])
    base.update(kw)
    return answers.ProposedExperience(**base)


class TestCleanRows:
    def _rows(self, **kw):
        return answers.ProposedRows(experiences=kw.get("experiences", []),
                                    qualifications=kw.get("qualifications", []), skills=kw.get("skills", []))

    def test_blank_titles_dropped(self):
        rows = answers.clean_rows(self._rows(
            experiences=[_pe(title="   "), _pe(title="Real")],
            qualifications=[answers.ProposedQualification(
                qualification_type="degree", title="  ", institution="", status="completed")]))
        assert [e.title for e in rows.experiences] == ["Real"]
        assert rows.qualifications == []

    def test_bad_dates_blanked(self):
        rows = answers.clean_rows(self._rows(experiences=[
            _pe(start="2024-13", end="last year"), _pe(title="Ok", start="2024-03", end="2024-06"),
        ]))
        assert (rows.experiences[0].start, rows.experiences[0].end) == ("", "")
        assert (rows.experiences[1].start, rows.experiences[1].end) == ("2024-03", "2024-06")

    def test_lone_experience_without_description_gets_answer(self):
        rows = answers.clean_rows(self._rows(experiences=[_pe(description="")]), answer="  I did X.  ")
        assert rows.experiences[0].description == "I did X."

    def test_not_applied_with_two_experiences_or_a_description(self):
        two = answers.clean_rows(self._rows(experiences=[_pe(title="A", description=""),
                                                         _pe(title="B", description="")]), answer="ans")
        assert [e.description for e in two.experiences] == ["", ""]
        has = answers.clean_rows(self._rows(experiences=[_pe(description="mine")]), answer="ans")
        assert has.experiences[0].description == "mine"

    def test_standalone_skill_already_in_experience_dropped(self):
        rows = answers.clean_rows(self._rows(
            experiences=[_pe(skills=["Power BI"])], skills=["PowerBI", "Tableau", "Tableau"]))
        assert rows.skills == ["Tableau"]


# ---------------------------------------------------------------------------
# 10. resume
# ---------------------------------------------------------------------------
class TestReopen:
    def test_refuses_run_not_answered(self, db, monkeypatch):
        res, _ = _wf_waiting(db, monkeypatch)
        with pytest.raises(ValueError):
            reopen_run(db, res.run_id)
        with pytest.raises(ValueError):
            reopen_run(db, 12345)

    def test_answered_run_reopens_with_extra_calls(self, db, monkeypatch):
        res, _ = _wf_waiting(db, monkeypatch)
        answers.answer_no(db, res.run_id, "Q1")
        before = _run_state(db, res.run_id).budget.max_tool_calls
        state, ctx = reopen_run(db, res.run_id)
        assert _run(db, res.run_id).status == "running"
        assert state.budget.max_tool_calls == before + RESUME_EXTRA_CALLS
        assert ctx.run.id == res.run_id


class TestResume:
    def _after_yes(self, db, monkeypatch, engine):
        fakes = Fakes()
        if engine == "workflow":
            fakes.install_workflow(monkeypatch)
            res = workflow.run_workflow(db, 5, 1, gap_policy=ask_user_gaps)
        else:
            fakes.install_agent(monkeypatch)
            monkeypatch.setattr(agent, "complete_tools", Orch(["analyze_job", "match_profile", "ask_user"]))
            res = agent.run_agent(db, 5, 1, gap_policy=ask_user_gaps)
        assert res.status == "waiting_user"
        monkeypatch.setattr(answers, "complete_json", _fake_parse())
        answers.answer_yes(db, res.run_id, "Q1", "I built Power BI dashboards at uni")
        answers.confirm(db, res.run_id, "Q1", accept=True)
        assert _run(db, res.run_id).status == "answered"
        # the re-match now finds evidence in the new experience
        pointer = next(p for p in _run_state(db, res.run_id).question("Q1").saved_as
                       if p.startswith("experience:"))
        fakes.match_status["R1"] = ("supported", [pointer])
        return res, fakes

    def test_resume_workflow_rematches_then_finishes(self, db, monkeypatch):
        res, fakes = self._after_yes(db, monkeypatch, "workflow")
        out = workflow.resume_workflow(db, res.run_id, gap_policy=ask_user_gaps)
        assert out.status == "done" and out.clean
        assert fakes.calls.count("analyze_job") == 1
        assert fakes.calls.count("match_profile") == 2  # first pass + one re-match
        assert fakes.calls[2:] == ["match_profile", "generate_letter",
                                   "check_claims", "check_requirements", "style_lint"]
        assert out.state.requirement("R1").status == "supported"
        assert _run(db, res.run_id).finished_at is not None

    def test_resume_agent_prompt_and_finish(self, db, monkeypatch):
        res, fakes = self._after_yes(db, monkeypatch, "agent")
        orch = Orch(["match_profile", "generate_letter", "check_claims", "check_requirements",
                     "style_lint", "finish"])
        monkeypatch.setattr(agent, "complete_tools", orch)
        out = agent.resume_agent(db, res.run_id, gap_policy=ask_user_gaps)
        first = orch.prompts[0]
        assert "STEPS SO FAR: analyze_job, match_profile, ask_user" in first
        assert "user_answer" not in first and "user_confirm" not in first
        last = first.split("LAST RESULT:")[1]
        assert "resumed" in last and "match_profile" in last
        assert out.status == "done" and out.clean
        assert fakes.calls.count("analyze_job") == 1

    def test_resume_note_lists_user_decisions(self, db, monkeypatch):
        res, _ = _wf_waiting(db, monkeypatch)
        answers.answer_no(db, res.run_id, "Q1")
        state, _ctx_ = reopen_run(db, res.run_id)
        assert "R1 leave_out" in agent._resume_note(state)


class TestEngines:
    def _run_row(self, db, engine, status):
        ctx = start_run(db, 9, engine, _state([_req("R1")]))
        ctx.run.status = status
        db.commit()
        return ctx.run.id

    def test_dispatch_by_engine(self, db, monkeypatch):
        seen = []
        monkeypatch.setattr(agent, "resume_agent", lambda db_, rid, **kw: seen.append(("agent", rid)) or "A")
        monkeypatch.setattr(workflow, "resume_workflow",
                            lambda db_, rid, **kw: seen.append(("workflow", rid)) or "W")
        a = self._run_row(db, "agent", "answered")
        w = self._run_row(db, "workflow", "answered")
        assert engines.resume_letter(db, a) == "A" and engines.resume_letter(db, w) == "W"
        assert seen == [("agent", a), ("workflow", w)]

    def test_unknown_engine_and_run(self, db):
        rid = self._run_row(db, "bogus", "answered")
        with pytest.raises(ValueError):
            engines.resume_letter(db, rid)
        with pytest.raises(ValueError):
            engines.resume_letter(db, 99999)

    def test_run_letter_unknown_engine(self, db):
        with pytest.raises(ValueError):
            engines.run_letter(db, 5, 1, engine="nope")

    def test_answered_runs_oldest_first(self, db):
        r1 = self._run_row(db, "agent", "answered")
        self._run_row(db, "agent", "waiting_user")
        r3 = self._run_row(db, "workflow", "answered")
        self._run_row(db, "agent", "done")
        assert engines.answered_runs(db) == [r1, r3]

    def test_default_engine_is_agent(self):
        assert engines.DEFAULT_ENGINE == "agent"


# ---------------------------------------------------------------------------
# 11. full round trip
# ---------------------------------------------------------------------------
def test_round_trip_workflow_no_then_resume(db, monkeypatch):
    fakes = Fakes().install_workflow(monkeypatch)
    first = workflow.run_workflow(db, 5, 1, gap_policy=ask_user_gaps)
    assert first.status == "waiting_user"
    answers.answer_no(db, first.run_id, "Q1")
    assert _run(db, first.run_id).status == "answered"
    assert engines.answered_runs(db) == [first.run_id]

    res = engines.resume_letter(db, first.run_id, gap_policy=ask_user_gaps)
    assert res.status == "done" and res.clean
    assert fakes.calls.count("match_profile") == 1  # nothing was reset: no re-match
    assert "R1" in [r.id for r in guardrails.do_not_claim(res.state)]
    assert "R1" not in [r.id for r in guardrails.must_cover(res.state)]
    assert [i.label for i in gaps.to_work_on(db, 1)] == ["Power BI"]
    assert _run(db, first.run_id).final_draft_version == 1


def test_round_trip_agent_no_then_resume(db, monkeypatch):
    fakes = Fakes().install_agent(monkeypatch)
    monkeypatch.setattr(agent, "complete_tools", Orch(["analyze_job", "match_profile", "ask_user"]))
    first = agent.run_agent(db, 5, 1, gap_policy=ask_user_gaps)
    assert first.status == "waiting_user"
    answers.answer_no(db, first.run_id, "Q1")
    monkeypatch.setattr(agent, "complete_tools", Orch(
        ["generate_letter", "check_claims", "check_requirements", "style_lint", "finish"]))
    res = engines.resume_letter(db, first.run_id, gap_policy=ask_user_gaps)
    assert res.status == "done"
    assert "R1" in [r.id for r in guardrails.do_not_claim(res.state)]
    assert fakes.calls.count("match_profile") == 1
