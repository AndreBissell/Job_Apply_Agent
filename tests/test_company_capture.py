"""Company-name capture (bug: every Seek job had company = NULL, so letters said
"at Unknown"). Covers the two backend layers of the fix:

  * /ingest backfills company/location/work_type/salary onto an existing row, so a
    job first captured from its detail page is completed by its search card;
  * extraction recovers the employer name from the ad text, filling NULL only.

The extension layer (JSON-LD on the detail page) is JavaScript and is not covered
here; it needs a live Seek page to verify.
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.main import app, get_db
from app.db import Base
from app.llm import extract
from app.models import JobListing, Profile


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
        session.add(Profile(id=1, name="Bob", email="b@x.com", password_hash="x"))
        session.commit()
        yield session
    Base.metadata.drop_all(eng)


@pytest.fixture()
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def _ingest(client, **fields):
    listing = {"source_job_id": "77", "url": "https://au.seek.com/job/77", "title": "Engineer", **fields}
    return client.post("/ingest", json={"listings": [listing]}).json()


# ---------------------------------------------------------------------------
# /ingest
# ---------------------------------------------------------------------------
def test_detail_page_capture_now_stores_company(client, db):
    _ingest(client, raw_description="Ad text", company="Acme", location="Brisbane, QLD", work_type="Full time")
    job = db.query(JobListing).one()
    assert (job.company, job.location, job.work_type) == ("Acme", "Brisbane, QLD", "Full time")


def test_search_card_backfills_a_row_first_captured_from_its_detail_page(client, db):
    _ingest(client, raw_description="Ad text")  # old detail-page payload: no company
    assert db.query(JobListing).one().company is None

    result = _ingest(client, company="Acme", location="Perth", work_type="Contract", salary="$90k")
    job = db.query(JobListing).one()
    assert result["updated"] == 1
    assert (job.company, job.location, job.work_type, job.salary) == ("Acme", "Perth", "Contract", "$90k")
    assert job.raw_description == "Ad text"


def test_backfill_never_overwrites_a_captured_value(client, db):
    _ingest(client, company="Acme", location="Perth")
    result = _ingest(client, company="Someone Else", location="Sydney")
    job = db.query(JobListing).one()
    assert (job.company, job.location) == ("Acme", "Perth")
    assert result["updated"] == 0


# ---------------------------------------------------------------------------
# Extraction fallback
# ---------------------------------------------------------------------------
def _extraction(employer):
    return {
        "employer_name": employer, "hard_skills": ["Python"], "soft_skills": [],
        "qualifications": [], "experience": [], "seniority": "graduate",
        "key_responsibilities": ["Build things"], "summary": "A role.",
    }


def _job(db, company=None, description="Join Boeing Defence Australia as a graduate."):
    job = JobListing(source="seek", source_job_id="1", url="u", title="Grad", company=company,
                     raw_description=description)
    db.add(job)
    db.commit()
    return job


def test_extraction_fills_a_missing_company_from_the_ad(db, monkeypatch):
    monkeypatch.setattr(extract, "complete_json", lambda *a, **k: _extraction("Boeing Defence Australia"))
    job = _job(db)
    extract.extract_job(job.id, session=db)
    assert db.get(JobListing, job.id).company == "Boeing Defence Australia"


def test_extraction_never_overwrites_a_captured_company(db, monkeypatch):
    monkeypatch.setattr(extract, "complete_json", lambda *a, **k: _extraction("Recruiter Pty Ltd"))
    job = _job(db, company="Acme")
    extract.extract_job(job.id, session=db)
    assert db.get(JobListing, job.id).company == "Acme"


@pytest.mark.parametrize("name", [None, "", "   ", "x" * 200])
def test_unnamed_or_implausible_employer_leaves_company_null(db, monkeypatch, name):
    monkeypatch.setattr(extract, "complete_json", lambda *a, **k: _extraction(name))
    job = _job(db)
    extract.extract_job(job.id, session=db)
    assert db.get(JobListing, job.id).company is None


def test_a_name_not_in_the_ad_text_is_never_stored(db, monkeypatch):
    monkeypatch.setattr(extract, "complete_json", lambda *a, **k: _extraction("The Boeing Company"))
    job = _job(db)  # the ad says "Boeing Defence Australia", not "The Boeing Company"
    extract.extract_job(job.id, session=db)
    assert db.get(JobListing, job.id).company is None


def test_infer_employer_name_fills_null_rows_only(db, monkeypatch):
    calls = []

    def fake(system, user, schema=None, **kw):
        calls.append(kw)
        return {"employer_name": "  Boeing   Defence Australia "}

    monkeypatch.setattr(extract, "complete_json", fake)
    job = _job(db)
    assert extract.infer_employer_name(job.id, session=db) == "Boeing Defence Australia"
    assert calls[0]["tier"] == "small" and calls[0]["task"] == "employer_name"

    # Already filled -> no call at all.
    assert extract.infer_employer_name(job.id, session=db) is None
    assert len(calls) == 1


def test_infer_employer_name_skips_rows_without_a_description(db, monkeypatch):
    monkeypatch.setattr(extract, "complete_json", lambda *a, **k: pytest.fail("should not call the LLM"))
    job = _job(db, description=None)
    assert extract.infer_employer_name(job.id, session=db) is None
