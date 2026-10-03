"""Tests for Phase 7b's HTTP surface: app/api/letters.py (letter-run questions, the
to-work-on list), the origin carry-over and auto_clear in PUT /profile-ui/data, the
extraction hook that counts sightings, and scripts/gap_report.py ``render``.

No network and no real LLM: the answer parser's ``complete_json`` is patched. In-memory
SQLite only (get_db is overridden), never real.db / app.db.
"""
from __future__ import annotations

import datetime
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import gaps
from app.api.main import app
from app.api.profile_ui import get_db
from app.db import Base
from app.llm import extract as extract_mod
from app.llm.client import DailyQuotaError
from app.llm.letter import answers
from app.llm.letter.gap_policy import ask_user_gaps
from app.llm.letter.runner import persist, start_run
from app.llm.letter.state import JobInfo, LetterState, Requirement
from app.models import (
    Experience, GapDecision, GapSighting, JobListing, JobSkill, LetterRun, Match, Profile,
    Qualification, Skill,
)
from app.retention import now_utc

ROOT = Path(__file__).resolve().parent.parent
PBI_TEXT = "Experience building dashboards with Power BI"


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
def client(engine):
    Session = sessionmaker(bind=engine)

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


@pytest.fixture()
def db(engine):
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    s.add(Profile(id=1, name="Bob", email="b@x.com", password_hash="x", summary="Grad."))
    s.flush()
    s.add_all([
        Skill(id=7, user_id=1, name="SQL"),
        JobListing(id=5, source="seek", source_job_id="1", url="u", title="Engineer", company="LogiCo",
                   raw_description="We build software."),
    ])
    s.flush()
    s.add(Match(id=9, user_id=1, job_id=5, score=80))
    s.commit()
    yield s
    s.close()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _req(rid, text, skill):
    return Requirement(id=rid, text=text, importance="essential", letter_role="headline", status="gap",
                       skill=skill, theme=f"t-{rid}")


def _waiting_run(db, match_id=9, job_id=5, profile_id=1, texts=((PBI_TEXT, "Power BI"),), title="Engineer"):
    """A letter run paused with one open question per (text, skill)."""
    state = LetterState(profile_id=profile_id, job=JobInfo(job_id=job_id, title=title, company="LogiCo"),
                        requirements=[_req(f"R{i}", t, s) for i, (t, s) in enumerate(texts, start=1)])
    ctx = start_run(db, match_id, "workflow", state)
    ask_user_gaps(state, ctx)
    ctx.run.status = "waiting_user"
    persist(ctx, state)
    return ctx.run.id


def _fake_parse(monkeypatch, rows=None, exc=None):
    payload = rows or {
        "experiences": [{
            "experience_type": "university_project", "title": "Sales dashboard", "organization": "UQ",
            "start": "2024-03", "end": "2024-06", "description": "Built Power BI dashboards.",
            "skills": ["Power BI"],
        }],
        "qualifications": [], "skills": [],
    }

    def fake(system, user, **kw):
        if exc:
            raise exc
        return payload

    monkeypatch.setattr(answers, "complete_json", fake)


def _answer(client, run_id, qid="Q1", choice="no", text=None):
    body = {"answers": [{"question_id": qid, "choice": choice, **({"text": text} if text is not None else {})}]}
    return client.post(f"/letter-runs/{run_id}/answers", json=body)


def _run_status(db, run_id):
    db.expire_all()
    return db.get(LetterRun, run_id).status


# ---------------------------------------------------------------------------
# 1. waiting / get
# ---------------------------------------------------------------------------
class TestWaiting:
    def test_lists_only_waiting_runs_of_profile(self, client, db):
        waiting = _waiting_run(db)
        # a finished run of the same profile
        state = LetterState(profile_id=1, job=JobInfo(job_id=5, title="Engineer"))
        ctx = start_run(db, 9, "agent", state)
        ctx.run.status = "done"
        db.commit()
        # another user's waiting run
        db.add_all([Profile(id=2, name="Eve", email="e@x.com", password_hash="x")])
        db.flush()
        db.add(JobListing(id=6, source="seek", source_job_id="2", url="u", title="Other", raw_description="x"))
        db.flush()
        db.add(Match(id=10, user_id=2, job_id=6, score=70))
        db.commit()
        other = _waiting_run(db, match_id=10, job_id=6, profile_id=2, title="Other")

        body = client.get("/letter-runs/waiting").json()
        assert [r["run_id"] for r in body["runs"]] == [waiting]
        run = body["runs"][0]
        assert run["status"] == "waiting_user" and run["job_title"] == "Engineer"
        assert run["company"] == "LogiCo" and run["answer_hint"]
        q = run["questions"][0]
        assert q["id"] == "Q1" and q["requirement_text"] == PBI_TEXT and q["status"] == "open"

        assert client.get(f"/letter-runs/{waiting}").status_code == 200
        assert client.get(f"/letter-runs/{other}").status_code == 404
        assert client.get(f"/letter-runs/{other}", params={"profile_id": 2}).status_code == 200
        assert client.get("/letter-runs/9999").status_code == 404

    def test_empty(self, client, db):
        assert client.get("/letter-runs/waiting").json() == {"runs": []}


# ---------------------------------------------------------------------------
# 2. POST answers
# ---------------------------------------------------------------------------
class TestAnswers:
    def test_no_answers_and_run_becomes_answered(self, client, db):
        rid = _waiting_run(db)
        r = _answer(client, rid, choice="no")
        assert r.status_code == 200
        body = r.json()
        assert body["questions"][0]["status"] == "answered" and body["questions"][0]["choice"] == "no"
        assert body["status"] == "answered"
        assert _run_status(db, rid) == "answered"
        assert db.scalars(select(GapDecision)).one().skill_key == "power bi"

    def test_no_keeps_run_waiting_with_questions_left(self, client, db):
        rid = _waiting_run(db, texts=((PBI_TEXT, "Power BI"), ("Kubernetes in production", "Kubernetes")))
        r = _answer(client, rid, "Q1", "no")
        assert r.json()["status"] == "waiting_user"
        assert _answer(client, rid, "Q2", "no").json()["status"] == "answered"

    def test_yes_without_text_422(self, client, db):
        rid = _waiting_run(db)
        assert _answer(client, rid, choice="yes").status_code == 422
        assert _answer(client, rid, choice="yes", text="   ").status_code == 422

    def test_run_not_waiting_409(self, client, db):
        rid = _waiting_run(db)
        run = db.get(LetterRun, rid)
        run.status = "done"
        db.commit()
        assert _answer(client, rid, choice="no").status_code == 409

    def test_answering_answered_question_409(self, client, db):
        rid = _waiting_run(db, texts=((PBI_TEXT, "Power BI"), ("Kubernetes", "Kubernetes")))
        assert _answer(client, rid, "Q1", "no").status_code == 200
        assert _answer(client, rid, "Q1", "no").status_code == 409

    def test_unknown_question_404(self, client, db):
        rid = _waiting_run(db)
        assert _answer(client, rid, "Q99", "no").status_code == 404

    def test_unknown_run_404(self, client, db):
        assert _answer(client, 12345, "Q1", "no").status_code == 404

    def test_empty_answers_list_422(self, client, db):
        rid = _waiting_run(db)
        assert client.post(f"/letter-runs/{rid}/answers", json={"answers": []}).status_code == 422

    def test_yes_with_text_needs_confirm(self, client, db, monkeypatch):
        rid = _waiting_run(db)
        _fake_parse(monkeypatch)
        r = _answer(client, rid, choice="yes", text="I built dashboards at uni")
        assert r.status_code == 200
        q = r.json()["questions"][0]
        assert q["status"] == "needs_confirm" and q["proposal"]["experiences"][0]["title"] == "Sales dashboard"
        assert r.json()["status"] == "waiting_user"
        assert db.scalars(select(Experience)).first() is None  # nothing saved yet

    def test_daily_quota_503(self, client, db, monkeypatch):
        rid = _waiting_run(db)
        _fake_parse(monkeypatch, exc=DailyQuotaError("out of quota"))
        assert _answer(client, rid, choice="yes", text="I did it").status_code == 503

    def test_unreadable_yes_422(self, client, db, monkeypatch):
        rid = _waiting_run(db)
        _fake_parse(monkeypatch, rows={"experiences": [], "qualifications": [], "skills": []})
        assert _answer(client, rid, choice="yes", text="meh").status_code == 422

    def test_other_users_run_404(self, client, db):
        db.add(Profile(id=2, name="Eve", email="e@x.com", password_hash="x"))
        db.flush()
        db.add(JobListing(id=6, source="seek", source_job_id="2", url="u", title="Other", raw_description="x"))
        db.flush()
        db.add(Match(id=10, user_id=2, job_id=6, score=70))
        db.commit()
        rid = _waiting_run(db, match_id=10, job_id=6, profile_id=2)
        assert _answer(client, rid, choice="no").status_code == 404


# ---------------------------------------------------------------------------
# 3. POST confirm
# ---------------------------------------------------------------------------
class TestConfirm:
    def _needs_confirm(self, client, db, monkeypatch):
        rid = _waiting_run(db)
        _fake_parse(monkeypatch)
        assert _answer(client, rid, choice="yes", text="I built dashboards at uni").status_code == 200
        return rid

    def test_accept_saves_rows_and_answers_run(self, client, db, monkeypatch):
        rid = self._needs_confirm(client, db, monkeypatch)
        r = client.post(f"/letter-runs/{rid}/questions/Q1/confirm", json={"accept": True})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "answered"
        assert body["questions"][0]["status"] == "answered" and body["questions"][0]["saved_as"]
        db.expire_all()
        exp = db.scalars(select(Experience)).one()
        assert exp.title == "Sales dashboard" and exp.origin == "ask_user"
        assert db.scalars(select(Skill).where(Skill.name == "Power BI")).one().origin == "ask_user"

    def test_accept_with_edited_rows(self, client, db, monkeypatch):
        rid = self._needs_confirm(client, db, monkeypatch)
        rows = {"experiences": [{
            "experience_type": "personal_project", "title": "Edited title", "organization": "", "start": "",
            "end": "", "description": "Mine.", "skills": ["Power BI"]}], "qualifications": [], "skills": []}
        r = client.post(f"/letter-runs/{rid}/questions/Q1/confirm", json={"accept": True, "rows": rows})
        assert r.status_code == 200
        db.expire_all()
        assert db.scalars(select(Experience)).one().title == "Edited title"

    def test_reject_reopens_question(self, client, db, monkeypatch):
        rid = self._needs_confirm(client, db, monkeypatch)
        r = client.post(f"/letter-runs/{rid}/questions/Q1/confirm", json={"accept": False})
        assert r.status_code == 200
        q = r.json()["questions"][0]
        assert q["status"] == "open" and q["proposal"] is None
        assert r.json()["status"] == "waiting_user"
        db.expire_all()
        assert db.scalars(select(Experience)).first() is None

    def test_confirm_open_question_409(self, client, db):
        rid = _waiting_run(db)
        assert client.post(f"/letter-runs/{rid}/questions/Q1/confirm", json={"accept": True}).status_code == 409

    def test_confirm_unknown_question_404(self, client, db):
        rid = _waiting_run(db)
        assert client.post(f"/letter-runs/{rid}/questions/Q9/confirm", json={"accept": True}).status_code == 404

    def test_confirm_empty_rows_422(self, client, db, monkeypatch):
        rid = self._needs_confirm(client, db, monkeypatch)
        r = client.post(f"/letter-runs/{rid}/questions/Q1/confirm",
                        json={"accept": True, "rows": {"experiences": [], "qualifications": [], "skills": []}})
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# 4. to-work-on + clear
# ---------------------------------------------------------------------------
class TestToWorkOn:
    def _seed(self, db):
        a = gaps.save_no(db, 1, label="Power BI", key="power bi")
        b = gaps.save_no(db, 1, label="Kubernetes", key="kubernetes")
        for i in range(3):
            db.add(GapSighting(gap_id=b.id, job_id=100 + i, job_title=f"K job {i}", source="scan",
                               importance="essential", seen_at=now_utc() - datetime.timedelta(days=i + 1)))
        db.add(GapSighting(gap_id=a.id, job_id=200, job_title="P job", source="scan", seen_at=now_utc()))
        db.commit()
        return a, b

    def test_ranked_items_with_fields(self, client, db):
        self._seed(db)
        body = client.get("/gaps/to-work-on").json()
        assert body["window_days"] == 90
        labels = [i["label"] for i in body["items"]]
        assert labels == ["Kubernetes", "Power BI"]
        item = body["items"][0]
        for k in ("label", "total", "recent", "essential", "recent_titles", "said_no_at"):
            assert k in item
        assert item["total"] == 3 and item["recent"] == 3 and item["essential"] == 3
        assert item["recent_titles"][0] == "K job 0"

    def test_days_param_and_range(self, client, db):
        self._seed(db)
        narrow = client.get("/gaps/to-work-on", params={"days": 1}).json()
        assert {i["label"]: i["recent"] for i in narrow["items"]}["Kubernetes"] == 0
        assert client.get("/gaps/to-work-on", params={"days": 0}).status_code == 422
        assert client.get("/gaps/to-work-on", params={"days": 4000}).status_code == 422
        assert client.get("/gaps/to-work-on", params={"days": 3650}).status_code == 200

    def test_clear_removes_item(self, client, db):
        a, _ = self._seed(db)
        r = client.post(f"/gaps/{a.id}/clear")
        assert r.status_code == 200 and r.json()["ok"] is True and r.json()["cleared_at"]
        labels = [i["label"] for i in client.get("/gaps/to-work-on").json()["items"]]
        assert labels == ["Kubernetes"]
        assert client.post(f"/gaps/{a.id}/clear").status_code == 200  # harmless twice

    def test_clear_unknown_404(self, client, db):
        assert client.post("/gaps/9999/clear").status_code == 404

    def test_clear_other_users_item_404(self, client, db):
        a, _ = self._seed(db)
        assert client.post(f"/gaps/{a.id}/clear", params={"profile_id": 2}).status_code == 404

    def test_empty(self, client, db):
        assert client.get("/gaps/to-work-on").json()["items"] == []


# ---------------------------------------------------------------------------
# 5. profile-ui origin carry-over
# ---------------------------------------------------------------------------
class TestProfileUiOrigin:
    def _seed_origin_rows(self, db):
        db.add_all([
            Experience(user_id=1, experience_type="university_project", title="Sales dashboard",
                       organization="UQ", description="Built it.", origin="ask_user"),
            Qualification(user_id=1, qualification_type="certificate", title="PL-300", institution="Microsoft",
                          origin="ask_user"),
        ])
        db.commit()

    @staticmethod
    def _body(exp_title="Sales dashboard", skills=("SQL",)):
        return {
            "profile": {"name": "Bob", "email": "b@x.com"},
            "qualifications": [{"qualification_type": "certificate", "title": "PL-300",
                                "institution": "Microsoft"}],
            "experiences": [{"experience_type": "university_project", "title": exp_title,
                             "organization": "UQ", "description": "Built it.", "skills": []}],
            "skills": list(skills),
        }

    def test_origin_survives_unchanged_resend(self, client, db):
        self._seed_origin_rows(db)
        assert client.put("/profile-ui/data", json=self._body()).status_code == 200
        db.expire_all()
        assert db.scalars(select(Experience)).one().origin == "ask_user"
        assert db.scalars(select(Qualification)).one().origin == "ask_user"

    def test_edited_title_loses_origin(self, client, db):
        self._seed_origin_rows(db)
        assert client.put("/profile-ui/data", json=self._body(exp_title="Renamed")).status_code == 200
        db.expire_all()
        assert db.scalars(select(Experience)).one().origin is None
        assert db.scalars(select(Qualification)).one().origin == "ask_user"

    def test_get_returns_origin_and_label(self, client, db):
        self._seed_origin_rows(db)
        db.add(Skill(user_id=1, name="Power BI", origin="ask_user"))
        db.commit()
        body = client.get("/profile-ui/data").json()
        label = "added while applying to a job"
        assert body["experiences"][0]["origin"] == "ask_user" and body["experiences"][0]["origin_label"] == label
        assert body["qualifications"][0]["origin"] == "ask_user"
        assert body["qualifications"][0]["origin_label"] == label
        assert body["skill_origins"] == {"Power BI": label}

    def test_plain_rows_have_no_label(self, client, db):
        db.add(Experience(user_id=1, experience_type="job", title="Dev"))
        db.commit()
        e = client.get("/profile-ui/data").json()["experiences"][0]
        assert e["origin"] is None and e["origin_label"] is None

    def test_skill_origin_kept_through_resend(self, client, db):
        db.add(Skill(user_id=1, name="Power BI", origin="ask_user"))
        db.commit()
        body = self._body(skills=("SQL", "Power BI"))
        body["experiences"] = []
        body["qualifications"] = []
        assert client.put("/profile-ui/data", json=body).status_code == 200
        db.expire_all()
        assert db.scalars(select(Skill).where(Skill.name == "Power BI")).one().origin == "ask_user"

    def test_put_adding_skill_auto_clears_no(self, client, db):
        d = gaps.save_no(db, 1, label="Power BI", key="power bi")
        other = gaps.save_no(db, 1, label="Kubernetes", key="kubernetes")
        body = self._body(skills=("SQL", "Microsoft Power BI"))
        body["experiences"] = []
        body["qualifications"] = []
        r = client.put("/profile-ui/data", json=body)
        assert r.status_code == 200 and r.json()["cleared_to_work_on"] == ["Power BI"]
        db.expire_all()
        assert db.get(GapDecision, d.id).cleared_at is not None
        assert db.get(GapDecision, other.id).cleared_at is None

    def test_put_without_matching_skill_clears_nothing(self, client, db):
        gaps.save_no(db, 1, label="Power BI", key="power bi")
        body = self._body(skills=("SQL",))
        r = client.put("/profile-ui/data", json=body)
        assert r.json()["cleared_to_work_on"] == []


# ---------------------------------------------------------------------------
# 6. extraction hook
# ---------------------------------------------------------------------------
class TestExtractionHook:
    def _extraction(self, skills):
        return {
            "employer_name": None, "hard_skills": list(skills), "soft_skills": [], "qualifications": [],
            "experience": [], "seniority": "graduate", "key_responsibilities": [], "summary": "s",
        }

    def test_extraction_adds_one_scan_sighting(self, db, monkeypatch):
        gaps.save_no(db, 1, label="Power BI", key="power bi")
        monkeypatch.setattr(extract_mod, "complete_json",
                            lambda *a, **kw: self._extraction(["Microsoft Power BI", "Python"]))
        extract_mod.extract_job(5, session=db)
        rows = list(db.scalars(select(GapSighting)))
        assert len(rows) == 1 and rows[0].source == "scan" and rows[0].job_id == 5
        extract_mod.extract_job(5, session=db, force=True)  # re-extraction counts nothing new
        assert db.scalar(select(func.count()).select_from(GapSighting)) == 1

    def test_no_matching_skill_no_sighting(self, db, monkeypatch):
        gaps.save_no(db, 1, label="Power BI", key="power bi")
        monkeypatch.setattr(extract_mod, "complete_json", lambda *a, **kw: self._extraction(["Python"]))
        extract_mod.extract_job(5, session=db)
        assert db.scalar(select(func.count()).select_from(GapSighting)) == 0

    def test_hook_failure_does_not_fail_extraction(self, db, monkeypatch):
        gaps.save_no(db, 1, label="Power BI", key="power bi")
        monkeypatch.setattr(extract_mod, "complete_json", lambda *a, **kw: self._extraction(["Power BI"]))

        def boom(*a, **kw):
            raise RuntimeError("sightings exploded")

        monkeypatch.setattr(gaps, "record_scan_sightings", boom)
        extract_mod.extract_job(5, session=db)  # must not raise
        db.expire_all()
        job = db.get(JobListing, 5)
        assert job.extracted_at is not None
        assert {s.name for s in db.scalars(select(JobSkill).where(JobSkill.job_id == 5))} == {"Power BI"}


# ---------------------------------------------------------------------------
# 7. scripts/gap_report.py render
# ---------------------------------------------------------------------------
def _load_gap_report():
    spec = importlib.util.spec_from_file_location("gap_report_under_test", ROOT / "scripts" / "gap_report.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gap_report_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestGapReport:
    TODAY = datetime.date(2026, 10, 3)

    @staticmethod
    def _item(label, recent=3, essential=1, total=5, titles=("Dev",)):
        return gaps.WorkItem(
            id=1, label=label, skill_key=label.lower(), total=total, recent=recent, essential=essential,
            recent_titles=list(titles), said_no_at=datetime.datetime(2026, 9, 1, tzinfo=datetime.timezone.utc),
            requirement_text=None,
        )

    def test_empty(self):
        text = _load_gap_report().render([], days=90, env="test", today=self.TODAY)
        assert "Nothing yet" in text
        assert "|" not in text

    def test_table_in_rank_order(self):
        text = _load_gap_report().render(
            [self._item("Kubernetes", recent=5), self._item("Power BI", recent=2)],
            days=90, env="real", today=self.TODAY)
        assert "Nothing yet" not in text
        assert "# To work on" in text and "2026-10-03" in text and "real" in text
        assert text.index("Kubernetes") < text.index("Power BI")
        rows = [ln for ln in text.splitlines() if ln.startswith("| ") and "---" not in ln]
        assert len(rows) == 3  # header + two items
        assert rows[1].startswith("| 1 | Kubernetes | 5 |") and rows[2].startswith("| 2 | Power BI | 2 |")
        assert "2026-09-01" in rows[1]

    def test_pipes_escaped(self):
        text = _load_gap_report().render(
            [self._item("A|B", titles=("Dev | Ops", "Plain"))], days=30, env="test", today=self.TODAY)
        row = [ln for ln in text.splitlines() if ln.startswith("| 1 |")][0]
        assert "A/B" in row and "Dev / Ops; Plain" in row
        assert row.count("|") == 8  # 7 cells: no stray pipes from the data

    def test_no_titles(self):
        text = _load_gap_report().render([self._item("X", titles=())], days=90, env="test", today=self.TODAY)
        assert "none yet" in text
