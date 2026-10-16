"""Quick Apply evidence (docs/centrelink-ease-apply-plan.md).

- POST /jobs/{id}/quick-apply-sent: the extension saw Seek's "application sent" page.
  Records applied_at with applied_via='auto', marks apply_type='quick', creates an
  unscored match when there is none, keeps the first date on a repeat.
- PATCH /jobs/{id}/status records applied_via='manual'.
- POST /ingest carries apply_type (latest reading wins, never erased by a capture that
  couldn't read it) and replaces a stub's placeholder title when the ad is first captured.
- The read endpoints and the evidence CSV show both fields.
- extension/selectors.js detectApplyType / extractApplySentJobId, run in Node against the
  scrubbed samples in tests/fixtures/apply_flow_samples.json (skipped without Node).

In-memory SQLite (StaticPool), same pattern as tests/test_status_update.py. No AI call,
no network.
"""
from __future__ import annotations

import csv
import io
import json
import re
import shutil
import sqlite3
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import events
from app.api.main import app, get_db
from app.db import Base
from app.models import JobListing, Match, Profile

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = json.loads((ROOT / "tests" / "fixtures" / "apply_flow_samples.json").read_text(encoding="utf-8"))
APPLIED_AT = datetime(2026, 9, 1, 3, 0, tzinfo=timezone.utc)


@pytest.fixture()
def engine():
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
def broadcasts(monkeypatch):
    sent: list[tuple[str, dict]] = []
    monkeypatch.setattr(events, "broadcast_from_thread", lambda name, data: sent.append((name, data)))
    return sent


@pytest.fixture()
def client(engine, broadcasts):
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
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _job(db, *, match: dict | None = None, **fields) -> int:
    """Profile 1 and one job (id 1), optionally with a match. Returns the job id."""
    db.merge(Profile(id=1, name="Alice", email="alice@example.com", password_hash="x"))
    db.add(JobListing(id=1, source="seek", source_job_id="111", url="https://example.com/job/111",
                      title=fields.pop("title", "Test Job"), **fields))
    if match is not None:
        db.add(Match(user_id=1, job_id=1, **match))
    db.commit()
    return 1


def _match(db) -> Match | None:
    db.expire_all()
    return db.query(Match).filter_by(user_id=1, job_id=1).one_or_none()


def _job_row(db) -> JobListing:
    db.expire_all()
    return db.get(JobListing, 1)


def _sent(client, job_id: int = 1):
    return client.post(f"/jobs/{job_id}/quick-apply-sent?profile_id=1")


# --------------------------------------------------------------------------
# POST /jobs/{id}/quick-apply-sent
# --------------------------------------------------------------------------
class TestQuickApplySent:
    def test_records_applied_auto_and_marks_the_job_quick(self, client, db, broadcasts):
        _job(db, match={"score": 80, "status": "new"})
        r = _sent(client)
        assert r.status_code == 200
        body = r.json()
        assert body["applied_via"] == "auto" and body["newly_applied"] is True
        m = _match(db)
        assert m.status == "applied" and m.applied_at is not None and m.applied_via == "auto"
        assert float(m.score) == 80  # the score is left alone
        assert _job_row(db).apply_type == "quick"
        assert broadcasts == [("job_applied", {"job_id": 1, "applied_via": "auto"})]

    def test_creates_an_unscored_match_when_the_job_was_never_scored(self, client, db):
        _job(db)
        r = _sent(client)
        assert r.status_code == 200 and r.json()["newly_applied"] is True
        m = _match(db)
        assert m.score is None and m.status == "applied" and m.applied_via == "auto"

    def test_repeat_keeps_the_first_date(self, client, db):
        _job(db)
        first = _sent(client).json()
        second = _sent(client)
        assert second.status_code == 200
        assert second.json()["applied_at"] == first["applied_at"]
        assert second.json()["newly_applied"] is False

    def test_a_manual_mark_made_first_stays_manual_with_its_date(self, client, db):
        _job(db, match={"score": 70, "status": "applied", "applied_at": APPLIED_AT, "applied_via": "manual"})
        body = _sent(client).json()
        assert body["applied_via"] == "manual" and body["newly_applied"] is False
        m = _match(db)
        assert m.applied_at.replace(tzinfo=None) == APPLIED_AT.replace(tzinfo=None)
        assert _job_row(db).apply_type == "quick"  # the flow itself is the proof

    def test_overrides_an_external_reading(self, client, db):
        _job(db, apply_type="external")
        _sent(client)
        assert _job_row(db).apply_type == "quick"

    def test_lifts_an_expired_flag(self, client, db):
        _job(db, expired_detected_at=APPLIED_AT, availability_misses=2)
        _sent(client)
        job = _job_row(db)
        assert job.expired_detected_at is None and job.availability_misses == 0

    def test_unknown_job_is_404(self, client, db):
        _job(db)
        assert _sent(client, 999).status_code == 404
        assert _match(db) is None


def test_mark_applied_records_manual(client, db):
    _job(db, match={"score": 80, "status": "new"})
    r = client.patch("/jobs/1/status", json={"status": "applied"})
    assert r.status_code == 200 and r.json()["applied_via"] == "manual"
    assert _match(db).applied_via == "manual"


def test_mark_applied_after_auto_keeps_auto(client, db):
    _job(db)
    _sent(client)
    r = client.patch("/jobs/1/status", json={"status": "applied"})
    assert r.status_code == 200 and r.json()["applied_via"] == "auto"


# --------------------------------------------------------------------------
# POST /ingest
# --------------------------------------------------------------------------
def _ingest(client, **listing):
    base = {"source_job_id": "111", "url": "https://example.com/job/111", "title": "Test Job"}
    return client.post("/ingest", json={"listings": [{**base, **listing}], "profile_id": 1})


class TestIngestApplyType:
    def test_new_row_takes_the_reading(self, client, db):
        db.merge(Profile(id=1, name="Alice", email="alice@example.com", password_hash="x"))
        db.commit()
        assert _ingest(client, apply_type="quick").status_code == 200
        assert _job_row(db).apply_type == "quick"

    def test_latest_reading_wins(self, client, db):
        _job(db, apply_type="external")
        _ingest(client, apply_type="quick")
        assert _job_row(db).apply_type == "quick"

    def test_a_capture_without_a_reading_never_erases_one(self, client, db):
        _job(db, apply_type="quick")
        _ingest(client, raw_description="An ad.")
        assert _job_row(db).apply_type == "quick"

    def test_unknown_value_is_rejected(self, client, db):
        _job(db)
        assert _ingest(client, apply_type="email").status_code == 422


class TestIngestStubTitle:
    def test_the_first_description_replaces_a_stub_title(self, client, db):
        _job(db, title="Seek job 111")
        _ingest(client, title="Graduate Engineer", raw_description="The ad.")
        assert _job_row(db).title == "Graduate Engineer"

    def test_a_card_without_a_description_keeps_the_title(self, client, db):
        _job(db, title="Seek job 111")
        _ingest(client, title="Graduate Engineer")
        assert _job_row(db).title == "Seek job 111"

    def test_a_captured_ad_keeps_its_title(self, client, db):
        _job(db, title="Graduate Engineer", raw_description="The ad.")
        _ingest(client, title="Something else", raw_description="The ad, again.")
        assert _job_row(db).title == "Graduate Engineer"


# --------------------------------------------------------------------------
# What the side panel and the export read
# --------------------------------------------------------------------------
def test_job_detail_shows_apply_type_and_applied_via(client, db):
    _job(db, apply_type="quick", match={"score": 80, "status": "new"})
    _sent(client)
    body = client.get("/jobs/1?profile_id=1").json()
    assert body["apply_type"] == "quick"
    assert body["match"]["applied_via"] == "auto"


def test_job_list_shows_apply_type_and_applied_via(client, db):
    _job(db, apply_type="external", match={"score": 80, "status": "new"})
    client.patch("/jobs/1/status", json={"status": "applied"})
    rows = client.get("/jobs?profile_id=1").json()
    assert rows[0]["apply_type"] == "external" and rows[0]["applied_via"] == "manual"


def test_an_unscored_auto_application_counts_on_the_dashboard(client, db):
    _job(db)
    _sent(client)
    data = client.get("/obligation?profile_id=1").json()
    current = next(p for p in data["periods"] if p["is_current"])
    assert current["applied"] == 1
    job = current["jobs"][0]
    assert job["applied_via"] == "auto" and job["score"] is None


def test_evidence_csv_says_how_each_application_was_recorded(client, db, tmp_path, monkeypatch):
    from app.api import paths
    monkeypatch.setattr(paths, "_screenshots_dir", tmp_path)
    _job(db)
    _sent(client)
    db.add(JobListing(id=2, source="seek", source_job_id="222", url="https://example.com/job/222", title="Other"))
    db.add(Match(user_id=1, job_id=2, score=60, status="applied", applied_at=APPLIED_AT))  # before the column
    db.commit()
    res = client.get("/jobs/evidence-export?profile_id=1")
    text = zipfile.ZipFile(io.BytesIO(res.content)).read("applied-jobs.csv").decode()
    rows = list(csv.DictReader(io.StringIO(text)))
    recorded = {r["Job Title"]: r["Recorded"] for r in rows}
    assert recorded == {"Test Job": "Seek confirmation page", "Other": ""}


# --------------------------------------------------------------------------
# extension/selectors.js, run in Node against the scrubbed samples
# --------------------------------------------------------------------------
def _run_selectors_js(script: str):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    source = (ROOT / "extension" / "selectors.js").read_text(encoding="utf-8")
    runner = (
        "const vm = require('vm');"
        "let out = '';"
        "vm.runInNewContext(process.argv[1] + '\\n;' + process.argv[2], { out: (v) => { out = JSON.stringify(v); } });"
        "process.stdout.write(out);"
    )
    done = subprocess.run([node, "-e", runner, source, script], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _text_content(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html)


def test_detect_apply_type_on_the_real_button_samples():
    # A minimal stand-in for the page: querySelector finds the Apply button only when the
    # sample has that exact <a data-automation="job-detail-apply">, and gives its text.
    cases = []
    for s in SAMPLES["apply_buttons"]:
        has_button = s["html"].startswith("<a ") and 'data-automation="job-detail-apply"' in s["html"]
        cases.append({"id": s["id"], "has": has_button, "text": _text_content(s["html"]), "expected": s["expected"]})
    got = _run_selectors_js(
        f"const cases = {json.dumps(cases)};"
        "out(cases.map((c) => ({ id: c.id, expected: c.expected, got: detectApplyType({"
        "  querySelector: (sel) => (sel === SELECTORS.DETAIL_APPLY_BUTTON && c.has ? { textContent: c.text } : null),"
        "}) })));"
    )
    assert all(g["got"] == g["expected"] for g in got), got
    assert {g["id"] for g in got} == {"quick", "external", "unknown_text", "no_button"}


def test_the_apply_url_alone_is_not_the_sent_page():
    sent = SAMPLES["sent_page"]
    paths = {
        sent["success_path"]: sent["job_id"],
        sent["success_path"] + "/": sent["job_id"],
        sent["review_path"]: None,
        f"/job/{sent['job_id']}/apply": None,  # where an EXTERNAL ad's button goes too
        f"/job/{sent['job_id']}": None,
        sent["success_path"] + "/more": None,
        "/jobs/apply/success": None,
    }
    got = _run_selectors_js(
        f"const paths = {json.dumps(list(paths))};"
        "out(paths.map((p) => extractApplySentJobId(p)));"
    )
    assert dict(zip(paths, got)) == paths


def test_sent_page_sample_has_the_heading_the_extension_waits_for():
    got = _run_selectors_js("out(SELECTORS.APPLY_SENT_HEADING);")
    assert got == "h1#applicationSent"
    assert '<h1 id="applicationSent">' in SAMPLES["sent_page"]["html"]
