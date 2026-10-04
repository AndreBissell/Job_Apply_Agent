"""Tests for Phase 9b: help with Seek Quick Apply questions.

Covers app/screening/assist.py (gating, strategies, matching helpers), app/screening/
classify.py (layer 5), the run-less gap functions at the bottom of app/llm/letter/answers.py,
the new endpoints in app/api/screening.py, and the ``stub`` provider branch of
app/llm/client.py::complete_json.

No network and no real LLM call: every model call is monkeypatched (or goes to the stub
provider's canned files). In-memory SQLite only, never real.db / app.db.
"""
from __future__ import annotations

import datetime
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.llm.client as llm_client
from app.api.main import app
from app.api.profile_ui import get_db
from app.db import Base
from app.llm.client import LLMError
from app.llm.letter.state import JobInfo, LetterState, ProfileIndex, Requirement
from app.models import (
    CoverLetter, Experience, GapDecision, GapSighting, JobListing, LetterRun, Match, Profile,
    ScreeningQuestion, Skill,
)
from app.screening import assist, bank, classify
from app.screening.bank import CapturedOption, CapturedQuestion

FIXTURE = Path(__file__).parent / "fixtures" / "quick_apply_samples.json"
SAMPLES = json.loads(FIXTURE.read_text(encoding="utf-8"))["samples"]
SAMPLE_BY_ID = {s["id"]: s for s in SAMPLES}

TODAY = datetime.date(2026, 10, 4)
SEEK_LABELS = ["No experience", "Less than 1 year", "1 year", "2 years", "3 years", "4 years",
               "5 years", "More than 5 years"]
LETTER = "Dear team, this is the cover letter."
S5_UNKNOWN = "How many years of experience you have with building application on React Native"
S5_CLASSIFIED = {"kind": "assisted", "strategy": "years_skill_text", "role": "",
                 "skill": "React Native with Expo"}


# --- fixtures ----------------------------------------------------------------------
@pytest.fixture()
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)

    @event.listens_for(eng, "connect")
    def _fk_on(conn, _):
        if isinstance(conn, sqlite3.Connection):
            conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture()
def db(engine):
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    yield s
    s.close()


@pytest.fixture()
def client(engine):
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    def _db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


class FakeModel:
    """Stands in for ``complete_json`` inside app.screening.classify."""

    def __init__(self):
        self.calls: list[dict] = []
        self.response: dict = dict(S5_CLASSIFIED)
        self.exc: Exception | None = None

    def __call__(self, system_prompt, user_content, **kw):
        self.calls.append({"user_content": user_content, **kw})
        if self.exc is not None:
            raise self.exc
        return dict(self.response)


@pytest.fixture(autouse=True)
def model(monkeypatch):
    """The layer-5 model: recorded, never real. The cooldown dict is cleared so row ids
    reused by the next in-memory DB don't inherit a failure."""
    classify._failed_at.clear()
    fake = FakeModel()
    monkeypatch.setattr("app.screening.classify.complete_json", fake)
    yield fake
    classify._failed_at.clear()


# --- builders ----------------------------------------------------------------------
def D(y: int, m: int, d: int = 1) -> datetime.date:
    return datetime.date(y, m, d)


def exp_spec(title, type="job", start=None, end=None, desc=None, skills=()):
    return {"title": title, "type": type, "start": start, "end": end, "desc": desc,
            "skills": list(skills)}


def add_profile(db, experiences=(), skills=(), **kw):
    """Profile 1 with experiences (specs), extra listed skills and the skills each
    experience links. Returns {"profile", "exps": [Experience], "skills": {name: Skill}}."""
    profile = Profile(id=1, name="Bob", email="bob@example.test", password_hash="x", **kw)
    db.add(profile)
    db.flush()
    rows: dict[str, Skill] = {}

    def skill(name):
        if name not in rows:
            rows[name] = Skill(user_id=1, name=name)
            db.add(rows[name])
            db.flush()
        return rows[name]

    for n in skills:
        skill(n)
    exps = []
    for spec in experiences:
        e = Experience(user_id=1, experience_type=spec["type"], title=spec["title"],
                       start_date=spec["start"], end_date=spec["end"], description=spec["desc"])
        db.add(e)
        db.flush()
        for n in spec["skills"]:
            e.skills.append(skill(n))
        exps.append(e)
    db.commit()
    return {"profile": profile, "exps": exps, "skills": rows}


def add_job(db, job_id: int, title: str = "Full Stack Developer") -> int:
    db.add(JobListing(id=job_id, source="seek", source_job_id=f"j{job_id}",
                      url=f"https://example.test/job/{job_id}", title=title, company="Barr Co",
                      raw_description="desc"))
    db.commit()
    return job_id


def req(id, text, skill="", importance="essential", role="headline", status="supported",
        evidence=()):
    return Requirement(id=id, text=text, importance=importance, letter_role=role, skill=skill,
                       status=status, evidence=list(evidence))


def make_state(reqs, job_id: int, text: str = LETTER) -> LetterState:
    state = LetterState(profile_id=1, job=JobInfo(job_id=job_id, title="Job"), requirements=list(reqs))
    state.add_draft(text)
    return state


def add_letter(db, job_id: int, reqs=None, *, engine="workflow", status="done", letter=LETTER,
               run_text=None, with_letter=True, with_run=True, final=1) -> int:
    """A match for the job plus (optionally) its cover letter and a pipeline run. The run's
    handed-back draft text is ``run_text`` (default: the letter's text)."""
    if reqs is None:
        reqs = [req("R0", "Teamwork", importance="nice_to_have", role="mention")]
    match = Match(id=job_id, user_id=1, job_id=job_id, score=90)
    db.add(match)
    db.flush()
    if with_run:
        state = make_state(reqs, job_id, run_text if run_text is not None else (letter or LETTER))
        db.add(LetterRun(match_id=match.id, engine=engine, status=status,
                         state=state.model_dump_json(), final_draft_version=final))
    if with_letter:
        db.add(CoverLetter(match_id=match.id, generated_content=letter, status="draft"))
    db.commit()
    return match.id


def sample_q(sample_id: str, fragment: str) -> dict:
    return next(q for q in SAMPLE_BY_ID[sample_id]["questions"] if fragment in q["text"])


def cap(q: dict) -> CapturedQuestion:
    return CapturedQuestion(q["seek_question_id"], q["field_name"], q["text"], q["input_type"],
                            [CapturedOption(o["value"], o["label"]) for o in q["options"]])


def capture(db, job_id: int, *qs: dict) -> list[dict]:
    return bank.record_job_questions(db, job_id, [cap(q) for q in qs])


def payload(*qs: dict) -> dict:
    return {"questions": [{k: q[k] for k in ("seek_question_id", "field_name", "text",
                                             "input_type", "options")} for q in qs]}


def by_text(out: dict, fragment: str) -> dict:
    return next(q for q in out["questions"] if fragment in q["text"])


Q_YEARS = lambda: sample_q("S2", "full stack developer")  # noqa: E731
Q_CSHARP = lambda: sample_q("S2", "C# development")  # noqa: E731
Q_LANGS = lambda: sample_q("S2", "programming languages")  # noqa: E731
Q_S5 = lambda: sample_q("S5", "React Native with Expo")  # noqa: E731
Q_S4 = lambda: sample_q("S4", "Front End React Developer")  # noqa: E731


def assist_for(db, job_id: int, *qs: dict) -> dict:
    capture(db, job_id, *qs)
    return assist.assist_job(db, job_id, 1, today=TODAY)


# =====================================================================================
# 1. Years
# =====================================================================================
class TestMergedMonths:
    def test_finished_role_counts_its_end_month(self):
        assert assist.merged_months([(D(2023, 1), D(2023, 12))], TODAY) == 12

    def test_overlapping_spans_merge(self):
        spans = [(D(2023, 1), D(2023, 6)), (D(2023, 4), D(2023, 9))]
        assert assist.merged_months(spans, TODAY) == 9

    def test_disjoint_spans_add(self):
        spans = [(D(2020, 1), D(2020, 6)), (D(2022, 1), D(2022, 3))]
        assert assist.merged_months(spans, TODAY) == 6 + 3

    def test_ongoing_role_counts_completed_months_only(self):
        assert assist.merged_months([(D(2026, 3), None)], TODAY) == 7

    def test_future_end_date_is_capped_at_today(self):
        # Mar..Dec 2026 is 10 months, but only Mar..Oct (8 months incl. the current one) is real.
        assert assist.merged_months([(D(2026, 3), D(2026, 12))], TODAY) == 8

    def test_end_before_start_contributes_zero(self):
        assert assist.merged_months([(D(2023, 6), D(2023, 1))], TODAY) == 0

    def test_end_before_start_does_not_disturb_other_spans(self):
        spans = [(D(2023, 6), D(2023, 1)), (D(2022, 1), D(2022, 12))]
        assert assist.merged_months(spans, TODAY) == 12

    def test_no_spans(self):
        assert assist.merged_months([], TODAY) == 0


class TestBracketFor:
    @pytest.mark.parametrize("months, expected", [
        (0, "No experience"),
        (5, "Less than 1 year"),
        (11, "Less than 1 year"),
        (12, "1 year"),
        (23, "1 year"),       # rounds DOWN
        (24, "2 years"),
        (71, "5 years"),      # 5 years 11 months is not "More than 5 years"
        (72, "More than 5 years"),
        (200, "More than 5 years"),
    ])
    def test_seek_labels(self, months, expected):
        assert assist.bracket_for(months, SEEK_LABELS) == expected

    @pytest.mark.parametrize("months, expected", [
        (12, "1-2 years"), (35, "1-2 years"), (36, "3-5 years"), (71, "3-5 years"),
    ])
    def test_range_labels(self, months, expected):
        assert assist.bracket_for(months, ["1-2 years", "3-5 years"]) == expected

    @pytest.mark.parametrize("months, expected", [(36, "3+ years"), (100, "3+ years")])
    def test_plus_labels(self, months, expected):
        assert assist.bracket_for(months, ["1 year", "2 years", "3+ years"]) == expected

    def test_below_plus_label_falls_to_exact(self):
        assert assist.bracket_for(24, ["1 year", "2 years", "3+ years"]) == "2 years"

    def test_none_when_nothing_fits(self):
        assert assist.bracket_for(30, ["1 year", "3 years"]) is None
        assert assist.bracket_for(30, []) is None
        assert assist.bracket_for(30, ["Prefer not to say"]) is None


# =====================================================================================
# 2. Role matching
# =====================================================================================
def _exp(title, type="job", skills=()):
    e = Experience(user_id=1, experience_type=type, title=title)
    e.skills = [Skill(user_id=1, name=n) for n in skills]
    return e


class TestRoleTerms:
    def test_front_end_react_developer(self):
        family, distinctive = assist.role_terms("Front End React Developer")
        assert family == frozenset({"developer", "engineer", "programmer", "dev"})
        assert distinctive == ["frontend", "react"]

    def test_full_stack_developer(self):
        family, distinctive = assist.role_terms("full stack developer")
        assert "developer" in family and "engineer" in family
        assert distinctive == ["fullstack"]


class TestExperienceMatchesRole:
    def test_full_stack_engineer_matches_full_stack_developer(self):
        assert assist.experience_matches_role(_exp("Full-Stack Software Engineer"), "full stack developer")

    def test_frontend_developer_is_not_full_stack(self):
        assert not assist.experience_matches_role(_exp("Frontend Developer"), "full stack developer")

    def test_frontend_job_with_react_skill_matches_front_end_react_developer(self):
        e = _exp("Frontend Developer", skills=["React"])
        assert assist.experience_matches_role(e, "Front End React Developer")

    def test_frontend_job_without_react_skill_does_not_match(self):
        assert not assist.experience_matches_role(_exp("Frontend Developer"), "Front End React Developer")

    @pytest.mark.parametrize("type", ["university_project", "personal_project"])
    def test_projects_are_not_work_roles(self, type):
        assert not assist.experience_matches_role(_exp("Full Stack Developer", type=type), "full stack developer")

    @pytest.mark.parametrize("type", ["internship", "volunteer"])
    def test_internship_and_volunteer_count(self, type):
        assert assist.experience_matches_role(_exp("Full Stack Developer", type=type), "full stack developer")


# =====================================================================================
# 3. Specific beats general
# =====================================================================================
class TestSpecificBeatsGeneral:
    def test_skill_parts(self):
        assert assist.skill_parts("React Native with Expo") == ["react native", "expo"]

    def test_name_covers(self):
        assert assist.name_covers("React.js", "react")
        assert not assist.name_covers("React Native", "react")
        assert not assist.name_covers("React", "react native")
        assert assist.name_covers("Microsoft Excel", "excel")

    def test_find_in(self):
        assert not assist.find_in("c", "c# and c++")
        assert not assist.find_in("react", "apps in react native")
        assert assist.find_in("react", "apps in react and node")
        assert assist.find_in("c#", "wrote c# services")

    def test_react_js_only_profile_has_no_react_native_evidence(self, db, model):
        add_profile(db, [exp_spec("Frontend Developer", start=D(2023, 1), end=D(2024, 1),
                                  desc="Built UIs.", skills=["React.js"])])
        add_job(db, 5)
        add_letter(db, 5, [req("R1", "React Native with Expo", skill="React Native")])
        out = assist_for(db, 5, Q_S5())
        a = by_text(out, "React Native with Expo")["assist"]
        assert out["help"] == "full"
        assert a["have"]["evidence"] == []
        assert {g["skill"] for g in a["gaps"]} == {"React Native", "Expo"}
        assert a["open_ended"] is True

    def test_react_native_and_expo_profile_is_backed_with_years(self, db, model):
        add_profile(db, [exp_spec("Mobile Developer", start=D(2024, 1), end=D(2024, 6),
                                  skills=["React Native", "Expo"])])
        add_job(db, 5)
        add_letter(db, 5, [req("R1", "React Native with Expo", skill="React Native")])
        a = by_text(assist_for(db, 5, Q_S5()), "React Native with Expo")["assist"]
        assert a["have"]["evidence"]
        assert a["have"]["years"]["work_months"] == 6
        assert a["gaps"] == []


# =====================================================================================
# 4. skill_in_role_yes_no (S2 C#)
# =====================================================================================
class TestSkillInRole:
    REQS = [req("R1", "Strong C# development", skill="C#")]

    def _assist(self, db, experiences=(), skills=()):
        add_profile(db, list(experiences), skills)
        add_job(db, 5)
        add_letter(db, 5, self.REQS)
        a = by_text(assist_for(db, 5, Q_CSHARP()), "C# development")["assist"]
        assert a["strategy"] == "skill_in_role_yes_no"
        return a

    def test_csharp_in_a_work_role_is_had(self, db):
        a = self._assist(db, [exp_spec("Developer", start=D(2023, 1), end=D(2023, 12), skills=["C#"])])
        assert a["have"]["evidence"]
        assert a["have"]["evidence"][0]["basis"] == "work"
        assert a["have"]["summary"].startswith("Used in a work role")
        assert a["gaps"] == []
        assert a["open_ended"] is False

    def test_csharp_only_listed_does_not_count(self, db):
        a = self._assist(db, skills=["C#"])
        assert a["have"]["evidence"] == []
        assert any(i["basis"] == "listed" for i in a["have"]["not_counted"])
        assert a["gaps"] == []  # the profile has a trace, so no Yes/No card
        assert a["open_ended"] is False

    def test_csharp_in_university_project_does_not_count(self, db):
        a = self._assist(db, [exp_spec("Capstone", type="university_project", skills=["C#"])])
        assert a["have"]["evidence"] == []
        assert any(i["basis"] == "study" for i in a["have"]["not_counted"])
        assert a["gaps"] == []

    def test_csharp_in_personal_project_does_not_count(self, db):
        a = self._assist(db, [exp_spec("Side app", type="personal_project", skills=["C#"])])
        assert a["have"]["evidence"] == []
        assert any(i["basis"] == "project" for i in a["have"]["not_counted"])

    def test_csharp_in_internship_counts(self, db):
        a = self._assist(db, [exp_spec("Intern", type="internship", skills=["C#"])])
        assert a["have"]["evidence"][0]["basis"] == "work"

    def test_no_csharp_anywhere_is_a_gap(self, db):
        a = self._assist(db, [exp_spec("Developer", skills=["Python"])], skills=["Java"])
        assert a["have"]["evidence"] == []
        assert a["have"]["not_counted"] == []
        assert [g["skill"] for g in a["gaps"]] == ["C#"]
        assert a["gaps"][0]["remembered"] is False
        assert a["open_ended"] is True
        assert a["prompt"] == assist.OPEN_PROMPT


# =====================================================================================
# 5. skill_multi_select (S2 languages)
# =====================================================================================
class TestMultiSelect:
    REQS = [
        req("R1", "JavaScript development", skill="JavaScript", importance="essential"),
        req("R2", "Java experience", skill="Java", importance="important"),
        req("R3", "C# and .NET services", skill="C#", importance="essential", status="gap"),
    ]

    @pytest.fixture()
    def out(self, db):
        add_profile(
            db,
            [exp_spec("Developer", start=D(2023, 1), end=D(2023, 12), skills=["JavaScript"]),
             exp_spec("Side site", type="personal_project", skills=["HTML"]),
             exp_spec("Capstone", type="university_project", skills=["Python"])],
            skills=["Java"],
        )
        add_job(db, 5)
        add_letter(db, 5, self.REQS)
        return assist_for(db, 5, Q_LANGS())

    def _opts(self, out):
        a = by_text(out, "programming languages")["assist"]
        assert a["strategy"] == "skill_multi_select"
        return a, {o["label"]: o for o in a["options"]}

    def test_tags(self, out):
        _, o = self._opts(out)
        assert o["JavaScript"]["tag"] == "wanted_have"
        assert o["Java"]["tag"] == "wanted_have"
        assert o["C#"]["tag"] == "wanted_missing"
        assert o["HTML"]["tag"] == "have"
        assert o["Python"]["tag"] == "have"
        assert o[".NET"]["tag"] == "wanted_missing"  # R3's wording names it
        for label in ("C", "C++", "Objective-C"):
            assert o[label]["tag"] is None, label

    def test_have_basis(self, out):
        _, o = self._opts(out)
        assert o["JavaScript"]["have_basis"] == "work"
        assert o["HTML"]["have_basis"] == "project"
        assert o["Python"]["have_basis"] == "study"
        assert o["Java"]["have_basis"] == "listed"
        assert o["C"]["have_basis"] is None

    def test_wanted_flags_and_requirements(self, out):
        _, o = self._opts(out)
        assert o["JavaScript"]["wanted"] and o["JavaScript"]["wanted_by"][0]["requirement_id"] == "R1"
        assert o["C#"]["wanted"]
        assert not o["HTML"]["wanted"] and o["HTML"]["wanted_by"] == []

    def test_gap_card_only_for_wanted_without_trace(self, out):
        a, _ = self._opts(out)
        # Wanted without a trace: C# and .NET. Neither-wanted-nor-had options (C, C++, ...) add none.
        assert sorted(g["skill"] for g in a["gaps"]) == [".NET", "C#"]

    def test_every_have_option_has_a_resolving_pointer(self, out, db):
        a, o = self._opts(out)
        profile = db.get(Profile, 1)
        index = ProfileIndex(profile)
        have = [x for x in a["options"] if x["have"]]
        assert have
        for opt in have:
            assert opt["evidence"], opt["label"]
            assert any(index.resolve(i["pointer"]) is not None for i in opt["evidence"]), opt["label"]
        for opt in a["options"]:
            if not opt["have"]:
                assert opt["evidence"] == []

    def test_requirement_evidence_for_a_deleted_row_is_not_a_have_label(self, db):
        add_profile(db, [exp_spec("Developer", skills=["JavaScript"])])
        add_job(db, 5)
        # The run's state claims Python is backed by experience:999, a row that doesn't exist.
        add_letter(db, 5, [req("R1", "Python scripting", skill="Python", status="supported",
                               evidence=["experience:999"])])
        a = by_text(assist_for(db, 5, Q_LANGS()), "programming languages")["assist"]
        python = next(o for o in a["options"] if o["label"] == "Python")
        assert python["have"] is False
        assert python["tag"] == "wanted_missing"
        assert python["evidence"] == []
        assert "Python" in [g["skill"] for g in a["gaps"]]


# =====================================================================================
# 6. years_role_bracket (S2 full stack)
# =====================================================================================
class TestYearsRole:
    REQS = [req("R1", "3+ years' experience as a full stack developer"),
            req("R2", "Strong C#", skill="C#")]

    def _assist(self, db, experiences):
        add_profile(db, experiences)
        add_job(db, 5)
        add_letter(db, 5, self.REQS)
        a = by_text(assist_for(db, 5, Q_YEARS()), "full stack developer")["assist"]
        assert a["strategy"] == "years_role_bracket"
        return a

    def test_bracket_is_rounded_down_from_merged_dates(self, db):
        a = self._assist(db, [
            exp_spec("Full Stack Developer", start=D(2020, 1), end=D(2021, 6)),            # 18
            exp_spec("Full-Stack Software Engineer", start=D(2022, 1), end=D(2023, 5)),    # 17
            exp_spec("Full Stack Developer", type="university_project", start=D(2015, 1), end=D(2018, 12)),
        ])
        assert a["have"]["years"]["work_months"] == 35
        assert a["profile_option"] == "2 years"  # 2 years 11 months rounds down
        assert a["open_ended"] is False
        assert len(a["have"]["evidence"]) == 2
        assert all(i["basis"] == "work" for i in a["have"]["evidence"])

    def test_wanted_carries_the_ads_figure(self, db):
        a = self._assist(db, [exp_spec("Full Stack Developer", start=D(2021, 1), end=D(2023, 12))])
        assert [w["requirement_id"] for w in a["wanted"]] == ["R1"]
        assert a["wanted"][0]["text"] == "3+ years' experience as a full stack developer"
        assert a["wanted_figure"] == "3+ years"
        assert a["profile_option"] == "3 years"

    def test_matching_roles_without_dates_are_open_ended(self, db):
        a = self._assist(db, [exp_spec("Full Stack Developer")])
        assert a["open_ended"] is True
        assert a["profile_option"] is None
        assert a["have"]["evidence"]  # the role matched, only the years can't be counted
        assert a["have"]["years"]["undated"] == ["Full Stack Developer"]

    def test_no_matching_role_is_open_ended_with_the_prompt(self, db):
        a = self._assist(db, [exp_spec("Frontend Developer", start=D(2021, 1), end=D(2023, 12))])
        assert a["open_ended"] is True
        assert a["prompt"] == "Do you have any experience with these?"
        assert a["profile_option"] is None
        assert a["have"]["evidence"] == []

    def test_s4_front_end_react_developer_uses_skills_for_react(self, db):
        add_profile(db, [exp_spec("Frontend Developer", start=D(2022, 1), end=D(2023, 12), skills=["React"])])
        add_job(db, 5)
        add_letter(db, 5, self.REQS)
        a = by_text(assist_for(db, 5, Q_S4()), "Front End React Developer")["assist"]
        assert a["strategy"] == "years_role_bracket"
        assert a["profile_option"] == "2 years"


# =====================================================================================
# 7. Gating
# =====================================================================================
class TestGating:
    def _get(self, client, job_id):
        r = client.get(f"/jobs/{job_id}/screening-assist")
        assert r.status_code == 200, r.text
        return r.json()

    def _all_unassisted(self, out):
        assert out["questions"]
        assert all(q["assist"] is None for q in out["questions"])

    def _captured_via_api(self, client, job_id, *qs):
        r = client.post(f"/jobs/{job_id}/screening-questions", json=payload(*qs))
        assert r.status_code == 200, r.text
        return r.json()

    def test_no_match(self, db, client, model):
        add_profile(db)
        add_job(db, 5)
        self._captured_via_api(client, 5, Q_S5(), Q_YEARS())
        out = self._get(client, 5)
        assert out["help"] == "no_letter"
        assert out["action"] == "regenerate"
        assert out["message"] == assist.MESSAGES[assist.NO_LETTER]
        self._all_unassisted(out)
        assert model.calls == []

    def test_match_without_cover_letter(self, db, client, model):
        add_profile(db)
        add_job(db, 5)
        add_letter(db, 5, with_letter=False, with_run=False)
        self._captured_via_api(client, 5, Q_S5())
        out = self._get(client, 5)
        assert out["help"] == "no_letter" and out["action"] == "regenerate"
        self._all_unassisted(out)
        assert model.calls == []

    def test_one_shot_letter_has_no_run(self, db, client, model):
        add_profile(db)
        add_job(db, 5)
        add_letter(db, 5, with_run=False)
        self._captured_via_api(client, 5, Q_S5(), Q_YEARS(), Q_CSHARP(), Q_LANGS())
        out = self._get(client, 5)
        assert out["help"] == "one_shot"
        assert out["message"] == assist.MESSAGES[assist.ONE_SHOT]
        assert out["action"] is None
        self._all_unassisted(out)
        assert model.calls == []
        # Capture still worked: the unknown S5 question is stored (kind unknown), not sorted.
        assert by_text(out, "React Native with Expo")["kind"] == "unknown"

    def test_later_one_shot_overwrote_the_pipeline_letter(self, db, client, model):
        add_profile(db)
        add_job(db, 5)
        add_letter(db, 5, letter="One-shot text written later.", run_text="Pipeline draft text.")
        self._captured_via_api(client, 5, Q_S5())
        out = self._get(client, 5)
        assert out["help"] == "one_shot"
        self._all_unassisted(out)
        assert model.calls == []

    def test_engine_other_than_pipeline_is_one_shot(self, db, model):
        add_profile(db)
        add_job(db, 5)
        add_letter(db, 5, engine="tools")  # an eval engine, not agent/workflow
        capture(db, 5, Q_S5())
        assert assist.assist_job(db, 5, 1, today=TODAY)["help"] == "one_shot"
        assert model.calls == []

    def test_run_without_evidence_is_one_shot(self, db, model):
        add_profile(db)
        add_job(db, 5)
        add_letter(db, 5, [req("R1", "Teamwork", status="unknown")])
        capture(db, 5, Q_S5())
        assert assist.assist_job(db, 5, 1, today=TODAY)["help"] == "one_shot"
        assert model.calls == []

    @pytest.mark.parametrize("status", ["running", "waiting_user", "answered"])
    def test_live_run_without_letter_is_pending(self, db, client, model, status):
        add_profile(db)
        add_job(db, 5)
        add_letter(db, 5, status=status, with_letter=False)
        self._captured_via_api(client, 5, Q_S5())
        out = self._get(client, 5)
        assert out["help"] == "letter_pending"
        assert out["message"] == assist.MESSAGES[assist.PENDING]
        assert out["action"] is None
        self._all_unassisted(out)
        assert model.calls == []

    def test_live_run_with_older_one_shot_letter_is_pending(self, db, model):
        add_profile(db)
        add_job(db, 5)
        add_letter(db, 5, status="running")  # letter text equals the run's draft, but run is live
        capture(db, 5, Q_S5())
        assert assist.assist_job(db, 5, 1, today=TODAY)["help"] == "letter_pending"
        assert model.calls == []

    def test_failed_run_without_letter_is_no_letter(self, db, model):
        add_profile(db)
        add_job(db, 5)
        add_letter(db, 5, status="failed", with_letter=False)
        assert assist.help_for_job(db, 5, 1) == ("no_letter", None)

    def test_full_pipeline(self, db, client, model):
        add_profile(db)
        add_job(db, 5)
        add_letter(db, 5)
        self._captured_via_api(client, 5, Q_YEARS())
        out = self._get(client, 5)
        assert out["help"] == "full"
        assert out["message"] is None and out["action"] is None
        assert by_text(out, "full stack developer")["assist"] is not None

    @pytest.mark.parametrize("run_engine", ["agent", "workflow"])
    @pytest.mark.parametrize("status", ["done", "budget_stopped", "failed"])
    def test_both_engines_and_finished_statuses_count(self, db, run_engine, status):
        add_profile(db)
        add_job(db, 5)
        add_letter(db, 5, engine=run_engine, status=status)
        assert assist.help_for_job(db, 5, 1)[0] == "full"

    def test_user_questions_get_no_assist_even_for_full(self, db):
        add_profile(db)
        add_job(db, 5)
        add_letter(db, 5)
        out = assist_for(db, 5, sample_q("S2", "salary"), sample_q("S2", "notice"))
        assert [q["kind"] for q in out["questions"]] == ["user", "user"]
        assert all(q["assist"] is None for q in out["questions"])


# =====================================================================================
# 8. Layer 5 (classify)
# =====================================================================================
def _row(db, fragment="React Native with Expo") -> ScreeningQuestion:
    return db.scalar(select(ScreeningQuestion).where(ScreeningQuestion.text.contains(fragment)))


class TestLayer5:
    def _full_job(self, db, job_id=5, reqs=None):
        add_job(db, job_id)
        add_letter(db, job_id, reqs or [req("R1", "React Native with Expo", skill="React Native")])

    def test_unknown_question_is_classified_once_and_stored(self, db, model):
        add_profile(db)
        self._full_job(db)
        out = assist_for(db, 5, Q_S5())
        assert len(model.calls) == 1
        assert model.calls[0]["task"] == "screening_classify"
        assert model.calls[0]["tier"] == "small"
        row = _row(db)
        db.refresh(row)
        assert row.classified_by == "model"
        assert row.status == "new"
        assert row.kind == "assisted"
        assert row.strategy == "years_skill_text"
        assert json.loads(row.parameters) == {"skill": "React Native with Expo"}
        q = by_text(out, "React Native with Expo")
        assert q["kind"] == "assisted" and q["classified_by"] == "model"
        assert q["assist"] is not None and q["assist"]["strategy"] == "years_skill_text"

    def test_bank_hit_on_second_view_and_second_job(self, db, model):
        add_profile(db)
        self._full_job(db, 5)
        self._full_job(db, 6)
        assist_for(db, 5, Q_S5())
        assert len(model.calls) == 1
        assist.assist_job(db, 5, 1, today=TODAY)
        assert len(model.calls) == 1
        out6 = assist_for(db, 6, Q_S5())
        assert len(model.calls) == 1
        assert by_text(out6, "React Native with Expo")["assist"] is not None

    def test_user_content_names_the_question(self, db, model):
        add_profile(db)
        self._full_job(db)
        assist_for(db, 5, Q_S5())
        assert S5_UNKNOWN in model.calls[0]["user_content"]
        assert "ANSWER TYPE: text" in model.calls[0]["user_content"]

    def test_skill_not_in_question_is_dropped_and_not_retried(self, db, model):
        add_profile(db)
        self._full_job(db)
        model.response = {**S5_CLASSIFIED, "skill": "Flutter"}
        out = assist_for(db, 5, Q_S5())
        assert len(model.calls) == 1
        q = by_text(out, "React Native with Expo")
        assert q["kind"] == "unknown" and q["assist"] is None
        row = _row(db)
        db.refresh(row)
        assert row.kind == "unknown" and row.classified_by != "model"
        again = assist.assist_job(db, 5, 1, today=TODAY)
        assert len(model.calls) == 1  # inside the cooldown
        assert by_text(again, "React Native with Expo")["assist"] is None

    def test_model_failure_leaves_row_unknown_and_never_raises(self, db, model):
        add_profile(db)
        self._full_job(db)
        model.exc = LLMError("boom")
        out = assist_for(db, 5, Q_S5())
        q = by_text(out, "React Native with Expo")
        assert q["kind"] == "unknown" and q["assist"] is None
        assist.assist_job(db, 5, 1, today=TODAY)
        assert len(model.calls) == 1  # and not retried straight away

    def test_value_error_from_a_malformed_answer_is_handled(self, db, model):
        add_profile(db)
        self._full_job(db)
        model.response = {"kind": "assisted", "strategy": "nonsense", "role": "", "skill": ""}
        out = assist_for(db, 5, Q_S5())
        assert by_text(out, "React Native with Expo")["kind"] == "unknown"

    def test_user_questions_never_reach_the_model(self, db, model):
        add_profile(db)
        self._full_job(db)
        seen, questions = set(), []
        for s in SAMPLES:
            for q in s["questions"]:
                if q["seek_question_id"] not in seen:
                    seen.add(q["seek_question_id"])
                    questions.append(q)
        out = assist_for(db, 5, *questions)
        assert out["help"] == "full"
        sent = [c["user_content"] for c in model.calls]
        assert len(sent) == 1
        assert S5_UNKNOWN in sent[0]
        user_texts = [q["text"] for q in out["questions"] if q["kind"] == "user"]
        assert len(user_texts) >= 10
        for text in user_texts:
            assert not any(text in content for content in sent), text

    def test_keyword_filter_sorts_a_forced_unknown_row_without_the_model(self, db, model):
        add_profile(db)
        self._full_job(db)
        capture(db, 5, Q_S5())
        row = _row(db)
        row.kind, row.strategy, row.parameters, row.classified_by = "unknown", None, None, "keyword"
        row.text = "What are your salary expectations?"
        db.commit()
        assert classify.classify(db, row, job_id=5) is True
        assert model.calls == []
        db.refresh(row)
        assert row.kind == "user" and row.strategy == "user" and row.classified_by == "keyword"

    def test_user_corrected_row_is_never_sent(self, db, model):
        add_profile(db)
        self._full_job(db)
        capture(db, 5, Q_S5())
        row = _row(db)
        row.classified_by = "user"
        db.commit()
        assert classify.classify(db, row, job_id=5) is False
        assert model.calls == []

    def test_non_unknown_row_is_never_sent(self, db, model):
        add_profile(db)
        self._full_job(db)
        capture(db, 5, sample_q("S2", "salary"))
        row = _row(db, "salary")
        assert row.kind == "user"
        assert classify.classify(db, row, job_id=5) is False
        assert model.calls == []

    def test_model_may_answer_user(self, db, model):
        add_profile(db)
        self._full_job(db)
        model.response = {"kind": "user", "strategy": "user", "role": "", "skill": ""}
        out = assist_for(db, 5, Q_S5())
        row = _row(db)
        db.refresh(row)
        assert row.kind == "user" and row.strategy == "user" and row.classified_by == "model"
        q = by_text(out, "React Native with Expo")
        assert q["kind"] == "user" and q["assist"] is None

    def test_model_assisted_free_text_without_a_subject_is_rejected_for_years(self, db, model):
        add_profile(db)
        self._full_job(db)
        model.response = {**S5_CLASSIFIED, "skill": ""}
        out = assist_for(db, 5, Q_S5())
        assert by_text(out, "React Native with Expo")["kind"] == "unknown"


class TestWordsFromQuestion:
    TEXT = "How many years of experience you have with building application on React Native with Expo, Fresh?"

    @pytest.mark.parametrize("phrase", [
        "React Native with Expo", "react native", "REACT NATIVE", "Expo React Native", "Expo",
        "the Expo", "years of experience",
    ])
    def test_true(self, phrase):
        assert classify.words_from_question(phrase, self.TEXT)

    @pytest.mark.parametrize("phrase", [
        "Flutter", "React Native Expo Go", "", "   ", "the", "Kotlin with Expo",
    ])
    def test_false(self, phrase):
        assert not classify.words_from_question(phrase, self.TEXT)

    def test_symbols_and_trailing_dot(self):
        assert classify.words_from_question("C#", "Have you used C# at work?")
        assert classify.words_from_question("Node.js", "Do you know Node.js?")
        assert classify.words_from_question("react", "Do you know React.")


class TestValidate:
    def _row(self, text, input_type="text", options=None):
        return ScreeningQuestion(text=text, input_type=input_type,
                                 options=json.dumps(options) if options else None)

    def test_multi_select_needs_options(self):
        r = classify.ModelSorting(kind="assisted", strategy="skill_multi_select", role="", skill="")
        assert classify.validate(r, self._row("Which languages?", "text")) is None
        assert classify.validate(r, self._row("Which languages?", "multi", ["A", "B"])) == (
            "assisted", "skill_multi_select", {})

    def test_option_strategies_reject_free_text(self):
        r = classify.ModelSorting(kind="assisted", strategy="skill_in_role_yes_no", role="", skill="C#")
        assert classify.validate(r, self._row("Used C#?", "text")) is None
        assert classify.validate(r, self._row("Used C#?", "single", ["Yes", "No"])) == (
            "assisted", "skill_in_role_yes_no", {"skill": "C#"})

    def test_kind_assisted_with_strategy_user_is_rejected(self):
        r = classify.ModelSorting(kind="assisted", strategy="user", role="", skill="")
        assert classify.validate(r, self._row("Anything")) is None


# =====================================================================================
# 9. Run-less gap path
# =====================================================================================
def _gap_setup(db, *, reqs=None, profile_kw=None):
    add_profile(db, [exp_spec("Developer", start=D(2023, 1), end=D(2023, 12), skills=["JavaScript"])])
    add_job(db, 5, "Mobile Developer")
    add_letter(db, 5, reqs or [
        req("R1", "JavaScript", skill="JavaScript"),
        req("R2", "Experience with Objective-C", skill="Objective-C", importance="nice_to_have",
            role="mention", status="gap"),
    ])
    capture(db, 5, Q_LANGS())


def _snapshot(db):
    db.expire_all()
    runs = [(r.id, r.status, r.state, r.final_draft_version, r.tool_calls, r.engine)
            for r in db.scalars(select(LetterRun).order_by(LetterRun.id))]
    letters = [(c.id, c.status, c.generated_content, c.edited_content)
               for c in db.scalars(select(CoverLetter).order_by(CoverLetter.id))]
    return runs, letters


def _sightings(db, job_id=None):
    db.expire_all()
    stmt = select(GapSighting)
    if job_id is not None:
        stmt = stmt.where(GapSighting.job_id == job_id)
    return list(db.scalars(stmt))


def _objc_option(out):
    a = by_text(out, "programming languages")["assist"]
    return a, next(o for o in a["options"] if o["label"] == "Objective-C")


@pytest.fixture()
def answers_model(monkeypatch):
    calls: list[dict] = []
    box = {"response": {"experiences": [], "qualifications": [], "skills": []}}

    def fake(system_prompt, user_content, **kw):
        calls.append({"user_content": user_content, **kw})
        return box["response"]

    monkeypatch.setattr("app.llm.letter.answers.complete_json", fake)
    fake.calls, fake.box = calls, box
    return fake


class TestRunlessGaps:
    def _post(self, client, **body):
        return client.post("/jobs/5/screening-gaps", json=body)

    def test_option_is_a_wanted_missing_gap_before_any_answer(self, db, client, answers_model):
        _gap_setup(db)
        a, objc = _objc_option(client.get("/jobs/5/screening-assist").json())
        assert objc["tag"] == "wanted_missing" and objc["have"] is False
        card = next(g for g in a["gaps"] if g["skill"] == "Objective-C")
        assert card["remembered"] is False and card["gap_id"] is None
        assert card["skill_key"] == "objective c"
        assert card["importance"] == "nice_to_have"
        assert card["requirement_text"] == "Experience with Objective-C"

    def test_no_creates_decision_and_quick_apply_sighting(self, db, client, answers_model):
        _gap_setup(db)
        r = self._post(client, skill="Objective-C", choice="no", importance="nice_to_have",
                       requirement_text="Experience with Objective-C")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["choice"] == "no" and body["label"] == "Objective-C"
        db.expire_all()
        decisions = list(db.scalars(select(GapDecision)))
        assert len(decisions) == 1
        d = decisions[0]
        assert d.skill_key == "objective c" and d.cleared_at is None and d.user_id == 1
        assert body["gap_id"] == d.id
        sightings = _sightings(db)
        assert len(sightings) == 1
        s = sightings[0]
        assert (s.gap_id, s.job_id, s.source, s.importance) == (d.id, 5, "quick_apply", "nice_to_have")
        assert answers_model.calls == []

    def test_reloading_shows_the_remembered_card_without_a_second_sighting(self, db, client, answers_model):
        _gap_setup(db)
        self._post(client, skill="Objective-C", choice="no", importance="nice_to_have",
                   requirement_text="Experience with Objective-C")
        for _ in range(2):
            a, _ = _objc_option(client.get("/jobs/5/screening-assist").json())
            card = next(g for g in a["gaps"] if g["skill"] == "Objective-C")
            assert card["remembered"] is True
            assert card["gap_id"] is not None
        assert len(_sightings(db, 5)) == 1

    def test_answering_no_twice_adds_no_second_decision_or_sighting(self, db, client, answers_model):
        _gap_setup(db)
        for _ in range(2):
            assert self._post(client, skill="Objective-C", choice="no").status_code == 200
        db.expire_all()
        assert db.scalar(select(func.count()).select_from(GapDecision)) == 1
        assert len(_sightings(db, 5)) == 1

    def test_remembered_no_is_counted_for_a_new_job_that_asks_for_it(self, db, client, answers_model):
        from app import gaps
        _gap_setup(db)
        gaps.save_no(db, 1, label="Objective-C", key="objective c")
        assert _sightings(db) == []  # nothing yet: no job has been seen asking for it
        a, objc = _objc_option(client.get("/jobs/5/screening-assist").json())
        assert next(g for g in a["gaps"] if g["skill"] == "Objective-C")["remembered"] is True
        s = _sightings(db, 5)
        assert len(s) == 1 and s[0].source == "quick_apply"
        assert s[0].importance == "nice_to_have"
        client.get("/jobs/5/screening-assist")
        assert len(_sightings(db, 5)) == 1  # once per ad

    def test_yes_without_text_proposes_the_skill_alone_and_saves_nothing(self, db, client, answers_model):
        _gap_setup(db)
        skills_before = db.scalar(select(func.count()).select_from(Skill))
        r = self._post(client, skill="Objective-C", choice="yes")
        assert r.status_code == 200, r.text
        assert r.json()["choice"] == "yes"
        assert r.json()["proposal"] == {"experiences": [], "qualifications": [], "skills": ["Objective-C"]}
        assert answers_model.calls == []
        db.expire_all()
        assert db.scalar(select(func.count()).select_from(Skill)) == skills_before
        assert db.get(Profile, 1).profile_revised_at is None

    def test_yes_with_whitespace_text_is_treated_as_no_text(self, db, client, answers_model):
        _gap_setup(db)
        r = self._post(client, skill="Objective-C", choice="yes", text="   ")
        assert r.status_code == 200
        assert r.json()["proposal"]["skills"] == ["Objective-C"]
        assert answers_model.calls == []

    def test_confirm_saves_skill_clears_the_no_and_shows_have(self, db, client, answers_model):
        _gap_setup(db)
        self._post(client, skill="Objective-C", choice="no")
        proposal = self._post(client, skill="Objective-C", choice="yes").json()["proposal"]
        r = client.post("/jobs/5/screening-gaps/confirm", json={"skill": "Objective-C", "rows": proposal})
        assert r.status_code == 200, r.text
        assert r.json()["auto_cleared"] == ["Objective-C"]
        assert any(p.startswith("skill:") for p in r.json()["saved_as"])
        db.expire_all()
        skill = db.scalar(select(Skill).where(Skill.name == "Objective-C"))
        assert skill is not None and skill.origin == "ask_user"
        assert db.get(Profile, 1).profile_revised_at is not None
        decision = db.scalar(select(GapDecision))
        assert decision.cleared_at is not None
        a, objc = _objc_option(client.get("/jobs/5/screening-assist").json())
        assert objc["have"] is True and objc["have_basis"] == "listed"
        assert objc["tag"] == "wanted_have"
        assert all(g["skill"] != "Objective-C" for g in a["gaps"])
        assert answers_model.calls == []

    def test_yes_with_text_calls_the_model_once_for_a_parse(self, db, client, answers_model):
        _gap_setup(db)
        answers_model.box["response"] = {
            "experiences": [{
                "experience_type": "personal_project", "title": "Notes app", "organization": "",
                "start": "2024-02", "end": "2024-05",
                "description": "Built an iOS notes app in Objective-C.", "skills": ["Objective-C"],
            }],
            "qualifications": [], "skills": [],
        }
        r = self._post(client, skill="Objective-C", choice="yes", text="I built an iOS notes app in 2024.",
                       requirement_text="Experience with Objective-C")
        assert r.status_code == 200, r.text
        assert len(answers_model.calls) == 1
        assert answers_model.calls[0]["task"] == "quick_apply_gap_parse"
        assert answers_model.calls[0]["job_id"] == 5
        assert "I built an iOS notes app" in answers_model.calls[0]["user_content"]
        proposal = r.json()["proposal"]
        assert proposal["experiences"][0]["title"] == "Notes app"
        db.expire_all()
        assert db.scalar(select(Skill).where(Skill.name == "Objective-C")) is None  # not saved yet

    def test_yes_with_text_that_parses_to_nothing_is_422(self, db, client, answers_model):
        _gap_setup(db)
        r = self._post(client, skill="Objective-C", choice="yes", text="not sure really")
        assert r.status_code == 422

    def test_yes_and_confirm_never_touch_runs_or_letters(self, db, client, answers_model):
        _gap_setup(db)
        self._post(client, skill="Objective-C", choice="no")
        before = _snapshot(db)
        proposal = self._post(client, skill="Objective-C", choice="yes").json()["proposal"]
        assert _snapshot(db) == before
        client.post("/jobs/5/screening-gaps/confirm", json={"skill": "Objective-C", "rows": proposal})
        assert _snapshot(db) == before
        client.get("/jobs/5/screening-assist")
        assert _snapshot(db) == before

    def test_yes_with_text_and_confirm_never_touch_runs_or_letters(self, db, client, answers_model):
        _gap_setup(db)
        answers_model.box["response"] = {
            "experiences": [], "qualifications": [], "skills": ["Objective-C"]}
        before = _snapshot(db)
        proposal = self._post(client, skill="Objective-C", choice="yes", text="Used it at uni").json()["proposal"]
        client.post("/jobs/5/screening-gaps/confirm", json={"skill": "Objective-C", "rows": proposal})
        assert _snapshot(db) == before
        assert db.scalar(select(func.count()).select_from(LetterRun)) == 1

    def test_empty_skill_is_422(self, db, client, answers_model):
        _gap_setup(db)
        assert self._post(client, skill="", choice="no").status_code == 422
        assert self._post(client, skill="", choice="yes").status_code == 422
        assert self._post(client, skill="   ", choice="yes").status_code == 422
        assert self._post(client, skill="   ", choice="no").status_code == 422
        assert client.post("/jobs/5/screening-gaps/confirm",
                           json={"skill": "", "rows": {"experiences": [], "qualifications": [],
                                                       "skills": ["X"]}}).status_code == 422
        db.expire_all()
        assert db.scalar(select(func.count()).select_from(GapDecision)) == 0

    def test_confirm_with_nothing_to_save_is_422(self, db, client, answers_model):
        _gap_setup(db)
        r = client.post("/jobs/5/screening-gaps/confirm", json={
            "skill": "Objective-C", "rows": {"experiences": [], "qualifications": [], "skills": []}})
        assert r.status_code == 422

    def test_unknown_job_is_404(self, db, client, answers_model):
        _gap_setup(db)
        assert client.post("/jobs/999/screening-gaps", json={"skill": "X", "choice": "no"}).status_code == 404
        assert client.get("/jobs/999/screening-assist").status_code == 404

    def test_bad_choice_is_422(self, db, client, answers_model):
        _gap_setup(db)
        assert self._post(client, skill="Objective-C", choice="maybe").status_code == 422


# =====================================================================================
# 10. Stub provider
# =====================================================================================
class TestStubProvider:
    @pytest.fixture()
    def stub(self, monkeypatch, tmp_path):
        responses = tmp_path / "responses.json"
        log = tmp_path / "log.jsonl"
        responses.write_text(json.dumps({"canned_task": {"answer": 42}}), encoding="utf-8")
        monkeypatch.setattr(llm_client, "LLM_PROVIDER", "stub")
        monkeypatch.setenv("LLM_STUB_RESPONSES", str(responses))
        monkeypatch.setenv("LLM_STUB_LOG", str(log))
        usage_calls = []
        monkeypatch.setattr(llm_client, "_record_usage", lambda *a, **k: usage_calls.append((a, k)))
        return {"responses": responses, "log": log, "usage": usage_calls}

    def _log(self, stub):
        if not stub["log"].exists():
            return []
        return [json.loads(line) for line in stub["log"].read_text(encoding="utf-8").splitlines()]

    def test_returns_canned_response_and_logs_the_call(self, stub):
        out = llm_client.complete_json("system", "the user content", schema=dict, tier="small",
                                       task="canned_task", job_id=7)
        assert out == {"answer": 42}
        log = self._log(stub)
        assert len(log) == 1
        assert log[0]["task"] == "canned_task"
        assert log[0]["user_content"] == "the user content"
        assert log[0]["tier"] == "small" and log[0]["job_id"] == 7

    def test_log_appends_one_line_per_call(self, stub):
        for text in ("one", "two", "three"):
            llm_client.complete_json("s", text, schema=dict, task="canned_task")
        assert [e["user_content"] for e in self._log(stub)] == ["one", "two", "three"]

    def test_unknown_task_raises_llm_error_but_is_still_logged(self, stub):
        with pytest.raises(LLMError):
            llm_client.complete_json("s", "content", schema=dict, task="no_such_task")
        assert [e["task"] for e in self._log(stub)] == ["no_such_task"]

    def test_no_responses_file_raises_llm_error(self, stub, monkeypatch):
        monkeypatch.delenv("LLM_STUB_RESPONSES")
        with pytest.raises(LLMError):
            llm_client.complete_json("s", "content", schema=dict, task="canned_task")

    def test_writes_no_usage_row(self, stub):
        llm_client.complete_json("s", "content", schema=dict, task="canned_task")
        with pytest.raises(LLMError):
            llm_client.complete_json("s", "content", schema=dict, task="other")
        assert stub["usage"] == []

    def test_stub_never_reaches_a_provider_sdk(self, stub, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("a provider was called")
        monkeypatch.setattr(llm_client, "_logged_call", boom)
        assert llm_client.complete_json("s", "x", schema=dict, task="canned_task") == {"answer": 42}

    def test_classify_goes_through_the_stub_end_to_end(self, db, stub, monkeypatch):
        # Undo the autouse fake so layer 5 uses the real complete_json -> stub branch.
        monkeypatch.undo()
        monkeypatch.setattr(llm_client, "LLM_PROVIDER", "stub")
        monkeypatch.setenv("LLM_STUB_RESPONSES", str(stub["responses"]))
        monkeypatch.setenv("LLM_STUB_LOG", str(stub["log"]))
        stub["responses"].write_text(json.dumps({"screening_classify": S5_CLASSIFIED}), encoding="utf-8")
        add_profile(db)
        add_job(db, 5)
        add_letter(db, 5, [req("R1", "React Native with Expo", skill="React Native")])
        out = assist_for(db, 5, Q_S5())
        assert by_text(out, "React Native with Expo")["kind"] == "assisted"
        log = self._log(stub)
        assert [e["task"] for e in log] == ["screening_classify"]
        assert S5_UNKNOWN in log[0]["user_content"]


class TestNoBroadAliasOverclaim:
    """prefilter.normalise_skill folds HTML = CSS and MySQL = PostgreSQL for scoring; a
    "you have it" label must not (assist.norm_skill folds spellings only)."""

    def test_html_does_not_back_css(self):
        assert not assist.name_covers("HTML", assist.norm_skill("CSS"))
        assert not assist.name_covers("CSS", assist.norm_skill("HTML"))

    def test_mysql_does_not_back_postgresql(self):
        assert not assist.name_covers("MySQL", assist.norm_skill("PostgreSQL"))

    def test_nodejs_does_not_back_javascript(self):
        assert not assist.name_covers("Node.js", assist.norm_skill("JavaScript"))

    def test_spelling_variants_still_fold(self):
        assert assist.name_covers("PowerBI", assist.norm_skill("Power BI"))
        assert assist.name_covers("JS", assist.norm_skill("JavaScript"))
        assert assist.name_covers("Python 3", assist.norm_skill("Python"))


class TestConcurrentClassify:
    """The overlay (or the overlay and the sidebar) can ask for the same job's help at
    once. One model call, and BOTH answers show the question sorted: the second request
    waits for the first instead of returning it as unknown (seen in the e2e)."""

    def test_two_requests_one_call_both_sorted(self, tmp_path, model, monkeypatch):
        import threading
        import time as _time

        eng = create_engine(f"sqlite:///{tmp_path / 'race.db'}",
                            connect_args={"check_same_thread": False, "timeout": 30})
        Base.metadata.create_all(eng)
        Session = sessionmaker(bind=eng, expire_on_commit=False)
        with Session() as s:
            add_profile(s)
            add_job(s, 5)
            add_letter(s, 5)
            capture(s, 5, Q_S5())

        real = model
        started = threading.Event()

        def slow(system_prompt, user_content, **kw):
            started.set()
            _time.sleep(0.5)
            return real(system_prompt, user_content, **kw)

        monkeypatch.setattr("app.screening.classify.complete_json", slow)

        results: list[dict] = []

        def worker():
            with Session() as s:
                results.append(assist.assist_job(s, 5, 1, today=TODAY))

        t1 = threading.Thread(target=worker)
        t1.start()
        started.wait(5)
        t2 = threading.Thread(target=worker)
        t2.start()
        t1.join(30)
        t2.join(30)

        assert len(model.calls) == 1
        assert len(results) == 2
        for out in results:
            q = by_text(out, "React Native with Expo")
            assert q["kind"] == "assisted" and q["assist"] is not None
        eng.dispose()
