"""Tests for app/api/profile_ui.py — profile editor endpoints.

Uses an in-memory SQLite database (StaticPool) so every test runs in
complete isolation without touching the project's app.db file.  The
FastAPI get_db dependency is overridden per-test so all DB operations
flow through the same in-memory connection that the assertion helpers
also use.
"""
from __future__ import annotations

import datetime
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.main import app
from app.api.profile_ui import _fmt_month, _parse_month, get_db
from app.db import Base
from app.models import Experience, Profile, Qualification, Skill


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def engine():
    """Fresh in-memory SQLite per test — no shared state between tests."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _fk_on(conn, _):
        if isinstance(conn, sqlite3.Connection):
            conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture()
def client(engine):
    """TestClient wired to the per-test in-memory DB via dependency override."""
    Session = sessionmaker(bind=engine)

    def _db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _db
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def db(engine):
    """Direct SQLAlchemy session for asserting DB state post-request."""
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _body(**overrides) -> dict:
    """Minimal valid PUT /profile-ui/data payload; keyword args override top-level keys."""
    base: dict = {
        "profile": {"name": "Alice", "email": "alice@example.com"},
        "qualifications": [],
        "experiences": [],
        "skills": [],
    }
    base.update(overrides)
    return base


def _qual(**kwargs) -> dict:
    """Minimal qualification dict; keyword args override defaults."""
    defaults = {"qualification_type": "degree", "title": "BSc"}
    defaults.update(kwargs)
    return defaults


def _exp(**kwargs) -> dict:
    """Minimal experience dict; keyword args override defaults."""
    defaults = {"experience_type": "job", "title": "Dev", "skills": []}
    defaults.update(kwargs)
    return defaults


# ---------------------------------------------------------------------------
# Unit: _parse_month / _fmt_month
# ---------------------------------------------------------------------------

class TestMonthHelpers:
    def test_parse_valid(self):
        assert _parse_month("2024-03") == datetime.date(2024, 3, 1)

    def test_parse_single_digit_month(self):
        assert _parse_month("2021-11") == datetime.date(2021, 11, 1)

    def test_parse_none_returns_none(self):
        assert _parse_month(None) is None

    def test_parse_empty_string_returns_none(self):
        assert _parse_month("") is None

    def test_parse_non_date_string_returns_none(self):
        assert _parse_month("not-a-date") is None

    def test_parse_invalid_month_13_returns_none(self):
        assert _parse_month("2024-13") is None

    def test_parse_invalid_month_00_returns_none(self):
        assert _parse_month("2024-00") is None

    def test_parse_year_only_returns_none(self):
        assert _parse_month("2024") is None

    def test_parse_year_month_day_takes_year_and_month(self):
        # Extra token: still parses year/month correctly (splits on "-", takes [0] and [1])
        assert _parse_month("2024-03-15") == datetime.date(2024, 3, 1)

    def test_fmt_single_digit_month_zero_padded(self):
        assert _fmt_month(datetime.date(2024, 3, 1)) == "2024-03"

    def test_fmt_double_digit_month(self):
        assert _fmt_month(datetime.date(2021, 11, 1)) == "2021-11"

    def test_fmt_none_returns_none(self):
        assert _fmt_month(None) is None

    def test_round_trip(self):
        s = "2023-07"
        assert _fmt_month(_parse_month(s)) == s


# ---------------------------------------------------------------------------
# GET /profile-ui
# ---------------------------------------------------------------------------

class TestServeUi:
    def test_returns_200(self, client):
        r = client.get("/profile-ui")
        assert r.status_code == 200

    def test_content_type_is_html(self, client):
        r = client.get("/profile-ui")
        assert "text/html" in r.headers["content-type"]

    def test_response_contains_html_doctype(self, client):
        r = client.get("/profile-ui")
        assert "<!doctype html" in r.text.lower()


# ---------------------------------------------------------------------------
# GET /profile-ui/data
# ---------------------------------------------------------------------------

class TestGetProfileData:
    def test_404_when_no_profile(self, client):
        r = client.get("/profile-ui/data")
        assert r.status_code == 404

    def test_200_after_put(self, client):
        client.put("/profile-ui/data", json=_body())
        r = client.get("/profile-ui/data")
        assert r.status_code == 200

    def test_profile_fields_returned(self, client):
        client.put("/profile-ui/data", json=_body())
        p = client.get("/profile-ui/data").json()["profile"]
        assert p["name"] == "Alice"
        assert p["email"] == "alice@example.com"

    def test_optional_fields_returned(self, client):
        body = _body()
        body["profile"].update({
            "phone": "0400 000 000",
            "location": "Brisbane, QLD",
            "summary": "Great dev.",
            "target_role": "SWE",
            "target_location": "Remote",
        })
        client.put("/profile-ui/data", json=body)
        p = client.get("/profile-ui/data").json()["profile"]
        assert p["phone"] == "0400 000 000"
        assert p["location"] == "Brisbane, QLD"
        assert p["summary"] == "Great dev."
        assert p["target_role"] == "SWE"
        assert p["target_location"] == "Remote"

    def test_missing_optional_fields_are_null(self, client):
        client.put("/profile-ui/data", json=_body())
        p = client.get("/profile-ui/data").json()["profile"]
        assert p["phone"] is None
        assert p["location"] is None
        assert p["summary"] is None
        assert p["target_role"] is None
        assert p["target_location"] is None

    def test_qualifications_returned(self, client):
        body = _body(qualifications=[_qual(
            qualification_type="degree",
            title="BSc Computer Science",
            institution="UQ",
            start_date="2021-02",
            end_date="2024-11",
            status="completed",
        )])
        client.put("/profile-ui/data", json=body)
        quals = client.get("/profile-ui/data").json()["qualifications"]
        assert len(quals) == 1
        q = quals[0]
        assert q["title"] == "BSc Computer Science"
        assert q["institution"] == "UQ"
        assert q["start_date"] == "2021-02"
        assert q["end_date"] == "2024-11"
        assert q["status"] == "completed"
        assert q["qualification_type"] == "degree"
        assert "id" in q

    def test_experiences_returned(self, client):
        body = _body(experiences=[_exp(
            title="Dev",
            organization="Acme",
            start_date="2023-01",
            end_date="2023-12",
            is_current=False,
            description="Did stuff.",
        )])
        client.put("/profile-ui/data", json=body)
        exps = client.get("/profile-ui/data").json()["experiences"]
        assert len(exps) == 1
        e = exps[0]
        assert e["title"] == "Dev"
        assert e["organization"] == "Acme"
        assert e["start_date"] == "2023-01"
        assert e["end_date"] == "2023-12"
        assert e["is_current"] is False
        assert e["description"] == "Did stuff."
        assert "id" in e

    def test_skills_returned(self, client):
        body = _body(skills=["Python", "SQL"])
        client.put("/profile-ui/data", json=body)
        skills = client.get("/profile-ui/data").json()["skills"]
        assert set(skills) == {"Python", "SQL"}

    def test_experience_skills_returned(self, client):
        body = _body(
            skills=["Python"],
            experiences=[_exp(title="Dev", skills=["Python", "Git"])],
        )
        client.put("/profile-ui/data", json=body)
        exps = client.get("/profile-ui/data").json()["experiences"]
        assert set(exps[0]["skills"]) == {"Python", "Git"}


# ---------------------------------------------------------------------------
# PUT /profile-ui/data
# ---------------------------------------------------------------------------

class TestPutProfileData:
    # --- Basic creation ---

    def test_returns_ok_and_profile_id(self, client):
        r = client.put("/profile-ui/data", json=_body())
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert isinstance(data["profile_id"], int)

    def test_profile_row_created(self, client, db):
        client.put("/profile-ui/data", json=_body())
        profiles = db.scalars(select(Profile)).all()
        assert len(profiles) == 1
        assert profiles[0].name == "Alice"
        assert profiles[0].email == "alice@example.com"

    def test_password_hash_placeholder_set(self, client, db):
        client.put("/profile-ui/data", json=_body())
        p = db.scalars(select(Profile)).first()
        assert p.password_hash == "not-set"

    # --- Optional profile fields ---

    def test_optional_fields_persisted(self, client, db):
        body = _body()
        body["profile"].update({
            "phone": "0411 222 333",
            "location": "Sydney",
            "summary": "Summary text",
            "target_role": "Engineer",
            "target_location": "Melbourne",
        })
        client.put("/profile-ui/data", json=body)
        p = db.scalars(select(Profile)).first()
        assert p.phone == "0411 222 333"
        assert p.location == "Sydney"
        assert p.summary == "Summary text"
        assert p.target_role == "Engineer"
        assert p.target_location == "Melbourne"

    def test_empty_string_optional_fields_stored_as_none(self, client, db):
        body = _body()
        body["profile"]["phone"] = ""
        body["profile"]["location"] = ""
        body["profile"]["summary"] = ""
        client.put("/profile-ui/data", json=body)
        p = db.scalars(select(Profile)).first()
        assert p.phone is None
        assert p.location is None
        assert p.summary is None

    # --- Idempotent update ---

    def test_second_put_returns_same_profile_id(self, client):
        r1 = client.put("/profile-ui/data", json=_body())
        r2 = client.put("/profile-ui/data", json=_body())
        assert r1.json()["profile_id"] == r2.json()["profile_id"]

    def test_second_put_updates_fields(self, client, db):
        client.put("/profile-ui/data", json=_body())
        body2 = _body()
        body2["profile"]["name"] = "Alice Updated"
        client.put("/profile-ui/data", json=body2)
        profiles = db.scalars(select(Profile)).all()
        assert len(profiles) == 1
        assert profiles[0].name == "Alice Updated"

    # --- Qualifications ---

    def test_qualification_persisted(self, client, db):
        body = _body(qualifications=[_qual(
            qualification_type="certificate",
            title="AWS CCP",
            institution="AWS",
            start_date=None,
            end_date="2024-08",
            status="completed",
        )])
        client.put("/profile-ui/data", json=body)
        quals = db.scalars(select(Qualification)).all()
        assert len(quals) == 1
        q = quals[0]
        assert q.title == "AWS CCP"
        assert q.qualification_type == "certificate"
        assert q.institution == "AWS"
        assert q.end_date == datetime.date(2024, 8, 1)
        assert q.start_date is None

    def test_blank_title_qual_skipped(self, client, db):
        body = _body(qualifications=[
            _qual(title="Real Qual"),
            _qual(title="   "),
            _qual(title=""),
        ])
        client.put("/profile-ui/data", json=body)
        quals = db.scalars(select(Qualification)).all()
        assert len(quals) == 1
        assert quals[0].title == "Real Qual"

    def test_multiple_qualifications_stored(self, client, db):
        body = _body(qualifications=[
            _qual(title="Degree"),
            _qual(title="Certificate", qualification_type="certificate"),
        ])
        client.put("/profile-ui/data", json=body)
        assert len(db.scalars(select(Qualification)).all()) == 2

    def test_second_put_replaces_qualifications(self, client, db):
        client.put("/profile-ui/data", json=_body(qualifications=[_qual(title="Old")]))
        client.put("/profile-ui/data", json=_body(qualifications=[_qual(title="New")]))
        quals = db.scalars(select(Qualification)).all()
        assert len(quals) == 1
        assert quals[0].title == "New"

    # --- Experiences ---

    def test_experience_persisted(self, client, db):
        body = _body(experiences=[_exp(
            experience_type="internship",
            title="Intern",
            organization="BigCo",
            start_date="2024-01",
            end_date="2024-06",
            is_current=False,
            description="Did things.",
        )])
        client.put("/profile-ui/data", json=body)
        exps = db.scalars(select(Experience)).all()
        assert len(exps) == 1
        e = exps[0]
        assert e.title == "Intern"
        assert e.organization == "BigCo"
        assert e.experience_type == "internship"
        assert e.start_date == datetime.date(2024, 1, 1)
        assert e.end_date == datetime.date(2024, 6, 1)
        assert e.description == "Did things."

    def test_is_current_true_sets_end_date_null(self, client, db):
        body = _body(experiences=[_exp(
            title="Current Job",
            start_date="2024-01",
            end_date="2024-12",
            is_current=True,
        )])
        client.put("/profile-ui/data", json=body)
        exp = db.scalars(select(Experience)).first()
        assert exp.end_date is None

    def test_is_current_false_preserves_end_date(self, client, db):
        body = _body(experiences=[_exp(
            title="Past Job",
            start_date="2022-01",
            end_date="2023-06",
            is_current=False,
        )])
        client.put("/profile-ui/data", json=body)
        exp = db.scalars(select(Experience)).first()
        assert exp.end_date == datetime.date(2023, 6, 1)

    def test_blank_title_exp_skipped(self, client, db):
        body = _body(experiences=[
            _exp(title="Real Job"),
            _exp(title=""),
            _exp(title="  "),
        ])
        client.put("/profile-ui/data", json=body)
        exps = db.scalars(select(Experience)).all()
        assert len(exps) == 1

    def test_second_put_replaces_experiences(self, client, db):
        client.put("/profile-ui/data", json=_body(experiences=[_exp(title="Old Job")]))
        client.put("/profile-ui/data", json=_body(experiences=[_exp(title="New Job")]))
        exps = db.scalars(select(Experience)).all()
        assert len(exps) == 1
        assert exps[0].title == "New Job"

    # --- Skills ---

    def test_skills_created_from_master_list(self, client, db):
        body = _body(skills=["Python", "SQL", "Docker"])
        client.put("/profile-ui/data", json=body)
        skills = db.scalars(select(Skill)).all()
        assert {s.name for s in skills} == {"Python", "SQL", "Docker"}

    def test_exp_only_skills_also_created(self, client, db):
        body = _body(
            skills=[],
            experiences=[_exp(skills=["TypeScript"])],
        )
        client.put("/profile-ui/data", json=body)
        skills = db.scalars(select(Skill)).all()
        assert any(s.name == "TypeScript" for s in skills)

    def test_skills_deduplicated_across_master_and_exp(self, client, db):
        body = _body(
            skills=["Python"],
            experiences=[_exp(skills=["Python"])],
        )
        client.put("/profile-ui/data", json=body)
        python_rows = [
            s for s in db.scalars(select(Skill)).all() if s.name == "Python"
        ]
        assert len(python_rows) == 1

    def test_whitespace_only_skill_names_ignored(self, client, db):
        body = _body(skills=["  ", "Python", ""])
        client.put("/profile-ui/data", json=body)
        skills = db.scalars(select(Skill)).all()
        assert {s.name for s in skills} == {"Python"}

    def test_stale_skills_pruned_on_second_put(self, client, db):
        client.put("/profile-ui/data", json=_body(skills=["Python", "Cobol"]))
        client.put("/profile-ui/data", json=_body(skills=["Python"]))
        skills = db.scalars(select(Skill)).all()
        assert {s.name for s in skills} == {"Python"}

    def test_skill_removed_from_all_sources_is_deleted(self, client, db):
        client.put("/profile-ui/data", json=_body(
            skills=["Python", "Rust"],
            experiences=[_exp(skills=["Rust"])],
        ))
        # Second PUT: Rust gone from both master list and all experiences
        client.put("/profile-ui/data", json=_body(skills=["Python"]))
        skills = db.scalars(select(Skill)).all()
        assert not any(s.name == "Rust" for s in skills)

    def test_skill_referenced_only_in_exp_survives_update(self, client, db):
        client.put("/profile-ui/data", json=_body(
            skills=[],
            experiences=[_exp(skills=["Go"])],
        ))
        # Second PUT keeps the same experience referencing Go
        client.put("/profile-ui/data", json=_body(
            skills=[],
            experiences=[_exp(skills=["Go"])],
        ))
        skills = db.scalars(select(Skill)).all()
        assert any(s.name == "Go" for s in skills)

    # --- Experience-skill links ---

    def test_experience_skills_linked(self, client, db):
        body = _body(
            skills=["Python"],
            experiences=[_exp(title="Dev", skills=["Python", "SQL"])],
        )
        client.put("/profile-ui/data", json=body)
        exp = db.scalars(select(Experience)).first()
        linked = {s.name for s in exp.skills}
        assert "Python" in linked
        assert "SQL" in linked

    def test_master_only_skill_not_linked_to_exp(self, client, db):
        body = _body(
            skills=["Python", "Docker"],
            experiences=[_exp(title="Dev", skills=["Python"])],
        )
        client.put("/profile-ui/data", json=body)
        exp = db.scalars(select(Experience)).first()
        linked = {s.name for s in exp.skills}
        assert "Docker" not in linked
        assert "Python" in linked


# ---------------------------------------------------------------------------
# DELETE /profile-ui/data
# ---------------------------------------------------------------------------

class TestDeleteProfileData:
    def test_returns_ok(self, client):
        client.put("/profile-ui/data", json=_body())
        r = client.delete("/profile-ui/data")
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_profile_gone_after_delete(self, client, db):
        client.put("/profile-ui/data", json=_body())
        client.delete("/profile-ui/data")
        assert db.scalars(select(Profile)).all() == []

    def test_get_returns_404_after_delete(self, client):
        client.put("/profile-ui/data", json=_body())
        client.delete("/profile-ui/data")
        assert client.get("/profile-ui/data").status_code == 404

    def test_delete_on_empty_db_returns_ok(self, client):
        r = client.delete("/profile-ui/data")
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_children_cascade_on_delete(self, client, db):
        body = _body(
            skills=["Python"],
            qualifications=[_qual(title="Degree")],
            experiences=[_exp(title="Job", skills=["Python"])],
        )
        client.put("/profile-ui/data", json=body)
        client.delete("/profile-ui/data")
        assert db.scalars(select(Qualification)).all() == []
        assert db.scalars(select(Experience)).all() == []
        assert db.scalars(select(Skill)).all() == []

    def test_can_create_new_profile_after_delete(self, client):
        client.put("/profile-ui/data", json=_body())
        client.delete("/profile-ui/data")
        body2 = _body()
        body2["profile"]["name"] = "Bob"
        body2["profile"]["email"] = "bob@example.com"
        r = client.put("/profile-ui/data", json=body2)
        assert r.status_code == 200
        p = client.get("/profile-ui/data").json()["profile"]
        assert p["name"] == "Bob"


# ---------------------------------------------------------------------------
# Date round-trips
# ---------------------------------------------------------------------------

class TestDateRoundTrip:
    def test_qual_dates_round_trip(self, client):
        body = _body(qualifications=[_qual(start_date="2021-02", end_date="2024-11")])
        client.put("/profile-ui/data", json=body)
        q = client.get("/profile-ui/data").json()["qualifications"][0]
        assert q["start_date"] == "2021-02"
        assert q["end_date"] == "2024-11"

    def test_exp_dates_round_trip(self, client):
        body = _body(experiences=[_exp(
            start_date="2022-07", end_date="2023-12", is_current=False
        )])
        client.put("/profile-ui/data", json=body)
        e = client.get("/profile-ui/data").json()["experiences"][0]
        assert e["start_date"] == "2022-07"
        assert e["end_date"] == "2023-12"

    def test_is_current_true_returns_null_end_date(self, client):
        body = _body(experiences=[_exp(
            start_date="2024-01", end_date="2024-12", is_current=True
        )])
        client.put("/profile-ui/data", json=body)
        e = client.get("/profile-ui/data").json()["experiences"][0]
        assert e["is_current"] is True
        assert e["end_date"] is None

    def test_null_start_date_stays_null(self, client):
        body = _body(qualifications=[_qual(
            qualification_type="certificate",
            title="CCP",
            start_date=None,
            end_date="2024-08",
        )])
        client.put("/profile-ui/data", json=body)
        q = client.get("/profile-ui/data").json()["qualifications"][0]
        assert q["start_date"] is None
        assert q["end_date"] == "2024-08"

    def test_no_dates_stored_as_null(self, client):
        body = _body(qualifications=[_qual(title="No Dates")])
        client.put("/profile-ui/data", json=body)
        q = client.get("/profile-ui/data").json()["qualifications"][0]
        assert q["start_date"] is None
        assert q["end_date"] is None


# ---------------------------------------------------------------------------
# Full round-trip using data/test_profile.json
# ---------------------------------------------------------------------------

_TEST_PROFILE_PATH = Path(__file__).parent.parent / "data" / "test_profile.json"


class TestFullProfileRoundTrip:
    def test_put_returns_ok(self, client):
        body = json.loads(_TEST_PROFILE_PATH.read_text(encoding="utf-8"))
        r = client.put("/profile-ui/data", json=body)
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_profile_fields(self, client):
        body = json.loads(_TEST_PROFILE_PATH.read_text(encoding="utf-8"))
        client.put("/profile-ui/data", json=body)
        p = client.get("/profile-ui/data").json()["profile"]
        assert p["name"] == "Liam Chen"
        assert p["email"] == "liam.chen.dev@example.com"
        assert p["phone"] == "0412 345 678"
        assert p["location"] == "Brisbane, QLD"
        assert p["target_role"] == "Software Engineer"
        assert p["target_location"] == "Brisbane, QLD"

    def test_two_qualifications_loaded(self, client):
        body = json.loads(_TEST_PROFILE_PATH.read_text(encoding="utf-8"))
        client.put("/profile-ui/data", json=body)
        quals = client.get("/profile-ui/data").json()["qualifications"]
        assert len(quals) == 2
        titles = {q["title"] for q in quals}
        assert "Bachelor of Engineering (Honours) — Software Engineering" in titles
        assert "AWS Certified Cloud Practitioner" in titles

    def test_three_experiences_loaded(self, client):
        body = json.loads(_TEST_PROFILE_PATH.read_text(encoding="utf-8"))
        client.put("/profile-ui/data", json=body)
        exps = client.get("/profile-ui/data").json()["experiences"]
        assert len(exps) == 3
        orgs = {e["organization"] for e in exps}
        assert "Atlassian" in orgs
        assert "Queensland Health" in orgs
        assert "University of Queensland" in orgs

    def test_18_master_skills_loaded(self, client):
        body = json.loads(_TEST_PROFILE_PATH.read_text(encoding="utf-8"))
        client.put("/profile-ui/data", json=body)
        skills = client.get("/profile-ui/data").json()["skills"]
        assert len(skills) == 18

    def test_atlassian_experience_skills_linked(self, client):
        body = json.loads(_TEST_PROFILE_PATH.read_text(encoding="utf-8"))
        client.put("/profile-ui/data", json=body)
        exps = client.get("/profile-ui/data").json()["experiences"]
        atlassian = next(e for e in exps if e["organization"] == "Atlassian")
        assert set(atlassian["skills"]) == {"Python", "AWS S3", "CI/CD", "Git", "Agile", "Code Review"}

    def test_capstone_experience_skills_linked(self, client):
        body = json.loads(_TEST_PROFILE_PATH.read_text(encoding="utf-8"))
        client.put("/profile-ui/data", json=body)
        exps = client.get("/profile-ui/data").json()["experiences"]
        capstone = next(e for e in exps if "Capstone" in e["title"])
        assert set(capstone["skills"]) == {
            "Python", "FastAPI", "SQLAlchemy", "React", "TypeScript",
            "Docker", "PostgreSQL", "Git", "Agile",
        }

    def test_degree_dates_correct(self, client):
        body = json.loads(_TEST_PROFILE_PATH.read_text(encoding="utf-8"))
        client.put("/profile-ui/data", json=body)
        quals = client.get("/profile-ui/data").json()["qualifications"]
        degree = next(q for q in quals if q["qualification_type"] == "degree")
        assert degree["start_date"] == "2021-02"
        assert degree["end_date"] == "2024-11"

    def test_certificate_no_start_date(self, client):
        body = json.loads(_TEST_PROFILE_PATH.read_text(encoding="utf-8"))
        client.put("/profile-ui/data", json=body)
        quals = client.get("/profile-ui/data").json()["qualifications"]
        cert = next(q for q in quals if q["qualification_type"] == "certificate")
        assert cert["start_date"] is None
        assert cert["end_date"] == "2024-08"

    def test_reset_and_reload_works(self, client):
        body = json.loads(_TEST_PROFILE_PATH.read_text(encoding="utf-8"))
        client.put("/profile-ui/data", json=body)
        client.delete("/profile-ui/data")
        r = client.put("/profile-ui/data", json=body)
        assert r.status_code == 200
        p = client.get("/profile-ui/data").json()["profile"]
        assert p["name"] == "Liam Chen"

    def test_idempotent_double_put(self, client):
        body = json.loads(_TEST_PROFILE_PATH.read_text(encoding="utf-8"))
        r1 = client.put("/profile-ui/data", json=body)
        r2 = client.put("/profile-ui/data", json=body)
        assert r1.json()["profile_id"] == r2.json()["profile_id"]
        exps = client.get("/profile-ui/data").json()["experiences"]
        assert len(exps) == 3
