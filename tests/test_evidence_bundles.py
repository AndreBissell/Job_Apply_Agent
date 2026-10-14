"""Centrelink evidence bundles (docs/centrelink-ease-apply-plan.md, part 2).

Quick Apply screenshots are stored once under evidence/<YYYY-MM>/; "Collate evidence" turns
every unreported one into an upload-ready PDF in centrelink_ready/ (under Centrelink's 3 MB
limit), and Undo puts them back. No LLM anywhere: the tests make every LLM entry point raise.

In-memory SQLite, a temporary screenshots folder, no server, no network.
"""

from __future__ import annotations

import base64
import io
import re
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import evidence_pdf
from app.api import paths
from app.api.main import app, get_db
from app.db import Base
from app.llm import client as llm_client
from app.models import EvidenceBundle, EvidenceBundleItem, JobListing, Match, Profile
from app.preferences import set_preferences
from app.screenshots import evidence_relpath, safe_name_part, screenshot_file, screenshot_relpath

NOW = datetime.now(timezone.utc)


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _fk_on(conn, _):
        if isinstance(conn, sqlite3.Connection):
            conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture()
def db(engine):
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture()
def shots(tmp_path, monkeypatch):
    directory = tmp_path / "screenshots_real"
    directory.mkdir()
    monkeypatch.setattr(paths, "_screenshots_dir", directory)
    return directory


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    """The whole evidence path must work without an AI call."""
    def boom(*a, **k):
        raise AssertionError("the evidence path made an LLM call")
    for name in ("complete_json", "complete_text", "complete_tools"):
        monkeypatch.setattr(llm_client, name, boom)


@pytest.fixture()
def client(engine, shots):
    Session = sessionmaker(bind=engine)

    def _db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _db
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


def _profile(db, pid=1):
    db.merge(Profile(id=pid, name=f"User{pid}", email=f"u{pid}@example.com", password_hash="x"))
    db.commit()
    set_preferences(db, pid, {})


def _png(width=600, height=400, seed=0) -> bytes:
    img = Image.effect_noise((width, height), 40 + seed).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _applied(db, shots, title="Graduate Analyst", company="Acme Pty Ltd", *, user=1,
             hours_ago=1, shot=True, on_disk=True, png=None):
    """An applied match, with a screenshot in evidence/<month>/ unless ``shot`` is False."""
    n = db.scalar(select(func.count()).select_from(JobListing)) + 1
    job = JobListing(source="seek", source_job_id=str(9000 + n),
                     url=f"https://au.seek.com/job/{9000 + n}", title=title, company=company)
    db.add(job)
    db.flush()
    when = NOW - timedelta(hours=hours_ago)
    rel = None
    if shot:
        rel = evidence_relpath(when.astimezone(), company, job.id)
        rel = rel[:-4] + f"_{job.id}.png"  # unique per test job
        if on_disk:
            path = shots.joinpath(*rel.split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(png or _png(seed=n))
    m = Match(user_id=user, job_id=job.id, status="applied", applied_at=when, applied_via="auto",
              screenshot_path=f"screenshots/{rel}" if rel else None,
              screenshot_taken_at=when if shot else None)
    db.add(m)
    db.commit()
    return m


def _pages(pdf: bytes) -> int:
    return len(re.findall(rb"/Type\s*/Page\b(?!s)", pdf))


# --------------------------------------------------------------------------
# File naming and paths
# --------------------------------------------------------------------------
class TestNaming:
    def test_screenshot_goes_in_its_month_folder_named_time_date_company(self):
        when = datetime(2026, 10, 14, 13, 12)
        assert evidence_relpath(when, "Acme Pty. Ltd.", 5) == "evidence/2026-10/1312_14-10-2026_Acme-Pty-Ltd.png"

    def test_no_company_falls_back_to_the_job_number(self):
        assert evidence_relpath(datetime(2026, 1, 2, 9, 5), None, 77).endswith("/0905_02-01-2026_job-77.png")
        assert evidence_relpath(datetime(2026, 1, 2, 9, 5), "&&&", 77).endswith("_job-77.png")

    def test_names_hold_only_safe_characters(self):
        assert safe_name_part('Bob\'s "Café" / Co: <Ltd>?') == "Bob-s-Caf-Co-Ltd"
        assert len(safe_name_part("x" * 200)) == 40

    def test_old_and_new_paths_resolve_inside_the_folder(self, tmp_path):
        assert screenshot_relpath("screenshots/54-1-20261014T025203Z.png") == "54-1-20261014T025203Z.png"
        assert screenshot_relpath("screenshots/evidence/2026-10/a.png") == "evidence/2026-10/a.png"
        assert screenshot_file(tmp_path, "screenshots/evidence/2026-10/a.png") == tmp_path / "evidence" / "2026-10" / "a.png"

    def test_a_path_that_tries_to_leave_the_folder_is_refused(self, tmp_path):
        for bad in ("screenshots/../real.db", "../x.png", "screenshots/C:/x.png", "", None):
            assert screenshot_relpath(bad) is None
            assert screenshot_file(tmp_path, bad) is None


class TestUpload:
    def test_upload_saves_into_the_month_folder_with_the_company_name(self, client, db, shots):
        _profile(db)
        job = JobListing(source="seek", source_job_id="555", url="u", title="T", company="Acme Pty Ltd")
        db.add(job)
        db.flush()
        db.add(Match(user_id=1, job_id=job.id, status="applied", applied_at=NOW))
        db.commit()
        res = client.post(f"/jobs/{job.id}/screenshot",
                          json={"data_url": "data:image/png;base64," + base64.b64encode(_png()).decode()})
        assert res.status_code == 200
        path = res.json()["screenshot_path"]
        assert re.fullmatch(r"screenshots/evidence/\d{4}-\d{2}/\d{4}_\d{2}-\d{2}-\d{4}_Acme-Pty-Ltd\.png", path)
        assert screenshot_file(shots, path).is_file()

    def test_retake_replaces_the_file(self, client, db, shots):
        _profile(db)
        m = _applied(db, shots)
        old = screenshot_file(shots, m.screenshot_path)
        res = client.post(f"/jobs/{m.job_id}/screenshot",
                          json={"data_url": "data:image/png;base64," + base64.b64encode(_png(seed=9)).decode()})
        assert res.status_code == 200
        assert not old.exists()
        assert len(list(shots.rglob("*.png"))) == 1

    def test_same_company_same_minute_does_not_overwrite(self, client, db, shots):
        _profile(db)
        ids = []
        for n in range(2):
            job = JobListing(source="seek", source_job_id=f"7{n}", url="u", title="T", company="Acme")
            db.add(job)
            db.flush()
            db.add(Match(user_id=1, job_id=job.id, status="applied", applied_at=NOW))
            db.commit()
            ids.append(job.id)
        data = {"data_url": "data:image/png;base64," + base64.b64encode(_png()).decode()}
        paths_ = [client.post(f"/jobs/{i}/screenshot", json=data).json()["screenshot_path"] for i in ids]
        assert paths_[0] != paths_[1]
        assert len(list(shots.rglob("*.png"))) == 2


# --------------------------------------------------------------------------
# Status, collate, download, undo
# --------------------------------------------------------------------------
class TestBundles:
    def test_status_counts_only_quick_apply_screenshots_not_yet_reported(self, client, db, shots):
        _profile(db)
        _applied(db, shots, "Quick one")
        _applied(db, shots, "Quick two")
        _applied(db, shots, "External, marked by hand", shot=False)
        _applied(db, shots, "File deleted by hand", on_disk=False)
        status = client.get("/evidence/status").json()
        assert status["unreported"] == 2
        assert status["missing_files"] == 1
        assert [j["title"] for j in status["jobs"]] == ["Quick one", "Quick two"]
        assert status["bundles"] == []
        assert status["folder"] == "screenshots_real/centrelink_ready"

    def test_collate_makes_one_pdf_named_time_date_count(self, client, db, shots):
        _profile(db)
        for i in range(5):
            _applied(db, shots, f"Job {i}", hours_ago=10 - i)
        res = client.post("/evidence/bundles")
        assert res.status_code == 200
        bundles = res.json()["bundles"]
        assert len(bundles) == 1
        b = bundles[0]
        assert re.fullmatch(r"\d{4}_\d{2}-\d{2}-\d{4}_5_jobs\.pdf", b["filename"])
        assert b["job_count"] == 5 and b["on_disk"]
        pdf = (shots / "centrelink_ready" / b["filename"]).read_bytes()
        assert pdf.startswith(b"%PDF")
        assert _pages(pdf) == 5
        assert len(pdf) < evidence_pdf.LIMIT_BYTES == 3_000_000
        assert b["size_bytes"] == len(pdf)

    def test_collated_jobs_are_reported_and_their_screenshots_stay(self, client, db, shots):
        _profile(db)
        m = _applied(db, shots)
        client.post("/evidence/bundles")
        status = client.get("/evidence/status").json()
        assert status["unreported"] == 0 and len(status["bundles"]) == 1
        assert screenshot_file(shots, m.screenshot_path).is_file()  # still in evidence/<month>/
        # The Applied tab's period rows now carry an expiry date.
        row = client.get("/obligation").json()["periods"][0]["jobs"][0]
        assert row["screenshot_reported"] is True and row["screenshot_expires_at"]

    def test_only_new_applications_go_into_the_next_bundle(self, client, db, shots):
        _profile(db)
        _applied(db, shots, "First")
        client.post("/evidence/bundles")
        _applied(db, shots, "Second")
        _applied(db, shots, "Third")
        b = client.post("/evidence/bundles").json()["bundles"][0]
        assert b["job_count"] == 2
        assert db.scalar(select(func.count()).select_from(EvidenceBundleItem)) == 3

    def test_one_job_reads_singular(self, client, db, shots):
        _profile(db)
        _applied(db, shots)
        assert client.post("/evidence/bundles").json()["bundles"][0]["filename"].endswith("_1_job.pdf")

    def test_nothing_to_collate_is_a_409(self, client, db, shots):
        _profile(db)
        _applied(db, shots, shot=False)
        res = client.post("/evidence/bundles")
        assert res.status_code == 409
        assert not (shots / "centrelink_ready").exists() or not any((shots / "centrelink_ready").iterdir())

    def test_download_returns_the_pdf_as_an_attachment(self, client, db, shots):
        _profile(db)
        _applied(db, shots)
        b = client.post("/evidence/bundles").json()["bundles"][0]
        res = client.get(b["download_url"])
        assert res.status_code == 200
        assert res.headers["content-type"] == "application/pdf"
        assert b["filename"] in res.headers["content-disposition"]
        assert res.content.startswith(b"%PDF")

    def test_undo_deletes_the_pdf_and_puts_the_jobs_back(self, client, db, shots):
        _profile(db)
        m1, m2 = _applied(db, shots), _applied(db, shots)
        b = client.post("/evidence/bundles").json()["bundles"][0]
        res = client.delete(f"/evidence/bundles/{b['id']}")
        assert res.status_code == 200 and res.json()["job_count"] == 2
        assert not (shots / "centrelink_ready" / b["filename"]).exists()
        status = client.get("/evidence/status").json()
        assert status["unreported"] == 2 and status["bundles"] == []
        for m in (m1, m2):  # the screenshots were never touched
            assert screenshot_file(shots, m.screenshot_path).is_file()
        assert client.post("/evidence/bundles").json()["bundles"][0]["job_count"] == 2

    def test_another_profiles_bundle_is_not_found(self, client, db, shots):
        _profile(db)
        _profile(db, 2)
        _applied(db, shots, user=2)
        b = client.post("/evidence/bundles?profile_id=2").json()["bundles"][0]
        assert client.get(b["download_url"]).status_code == 404
        assert client.delete(f"/evidence/bundles/{b['id']}").status_code == 404
        assert client.get(f"{b['download_url']}?profile_id=2").status_code == 200

    def test_bundles_are_kept_when_the_application_is_deleted(self, client, db, shots):
        _profile(db)
        m = _applied(db, shots)
        b = client.post("/evidence/bundles").json()["bundles"][0]
        assert client.delete(f"/jobs/{m.job_id}?allow_applied=true").status_code == 200
        assert (shots / "centrelink_ready" / b["filename"]).is_file()
        assert db.scalar(select(func.count()).select_from(EvidenceBundle)) == 1

    def test_too_big_for_one_upload_is_split_into_several_pdfs(self, client, db, shots, monkeypatch):
        _profile(db)
        for i in range(6):
            _applied(db, shots, f"Job {i}", hours_ago=10 - i)
        one_page = len(evidence_pdf._pdf_bytes([Image.new("RGB", (600, 500), "white")]))
        monkeypatch.setattr(evidence_pdf, "TARGET_BYTES", 1)  # every page its own group
        monkeypatch.setattr(evidence_pdf, "LIMIT_BYTES", max(one_page, 10**6))
        bundles = client.post("/evidence/bundles").json()["bundles"]
        assert [b["job_count"] for b in bundles] == [1] * 6
        names = [b["filename"] for b in bundles]
        assert len(set(names)) == 6  # the clash suffix _2, _3, ...
        assert all(n.endswith(".pdf") and "_1_job" in n for n in names)


class TestPdf:
    def test_each_page_has_a_caption_band_above_the_screenshot(self, tmp_path):
        src = tmp_path / "s.png"
        src.write_bytes(_png(800, 300))
        page = evidence_pdf.render_page(evidence_pdf.EvidencePage(src, ("Applied 14/10/2026 at 1:12 pm", "Role — Co", "url")))
        assert page.width == 800 and page.height > 300
        assert page.getpixel((page.width - 2, 2)) == (255, 255, 255)  # the band is white

    def test_wide_screenshots_are_scaled_to_the_page_width(self, tmp_path):
        src = tmp_path / "w.png"
        src.write_bytes(_png(2400, 600))
        page = evidence_pdf.render_page(evidence_pdf.EvidencePage(src, ("a",)))
        assert page.width == evidence_pdf.PAGE_WIDTH

    def test_a_single_page_over_the_limit_is_still_emitted_alone(self, tmp_path, monkeypatch):
        monkeypatch.setattr(evidence_pdf, "LIMIT_BYTES", 10)
        src = tmp_path / "s.png"
        src.write_bytes(_png())
        pages = [evidence_pdf.EvidencePage(src, ("a",)), evidence_pdf.EvidencePage(src, ("b",))]
        out = evidence_pdf.build_bundles(pages)
        assert [len(group) for group, _ in out] == [1, 1]

    def test_real_sized_seek_pages_fit_twenty_in_one_upload(self, tmp_path):
        """A month's target (20) of ~1200 px pages stays in ONE PDF under 3 MB. (A real
        Seek "application sent" capture measured 90 KB a page: 20 = 1.8 MB.) This page is
        about as text-dense as Seek's; a solid wall of text splits into more PDFs instead."""
        src = tmp_path / "seek.png"
        img = Image.new("RGB", (1200, 900), "white")
        from PIL import ImageDraw
        d = ImageDraw.Draw(img)
        for y in range(0, 900, 54):
            d.text((20, y), "Your application has been sent to Example Employer " * 3, fill=(30, 30, 30))
        img.save(src)
        pages = [evidence_pdf.EvidencePage(src, (f"Applied {i}", "Role — Co", "url")) for i in range(20)]
        out = evidence_pdf.build_bundles(pages)
        assert len(out) == 1 and len(out[0][1]) < evidence_pdf.LIMIT_BYTES
