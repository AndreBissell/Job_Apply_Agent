"""Tests for POST /jobs/{job_id}/screenshot — Centrelink evidence persistence.

Uses an in-memory SQLite database (StaticPool), same pattern as
tests/test_profile_ui.py, and monkeypatches app.api.main._screenshots_dir to a
pytest tmp_path so tests never touch the real repo's screenshots/ directory.
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.api.main as main_module
from app.api.main import app, get_db
from app.db import Base
from app.models import JobListing, Match, Profile

# A minimal valid 1x1 transparent PNG.
_TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY"
    "42YAAAAASUVORK5CYII="
)
_DATA_URL = f"data:image/png;base64,{_TINY_PNG_B64}"


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
def client(engine, tmp_path, monkeypatch):
    Session = sessionmaker(bind=engine)

    def _db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    screenshots_dir = tmp_path / "screenshots"
    screenshots_dir.mkdir()
    monkeypatch.setattr(main_module, "_screenshots_dir", screenshots_dir)

    app.dependency_overrides[get_db] = _db
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def db(engine):
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _seed_match(db, job_id: int = 1, user_id: int = 1) -> Match:
    db.add(Profile(id=user_id, name="Alice", email="alice@example.com", password_hash="x"))
    db.add(
        JobListing(
            id=job_id, source="test", source_job_id=f"t-{job_id}",
            url="https://example.com", title="Test Job",
        )
    )
    match = Match(user_id=user_id, job_id=job_id, score=80, status="new")
    db.add(match)
    db.commit()
    db.refresh(match)
    return match


class TestScreenshotUpload:
    def test_upload_persists_file_and_match_columns(self, client, db, tmp_path):
        match = _seed_match(db)

        res = client.post(
            f"/jobs/{match.job_id}/screenshot?profile_id=1",
            json={"data_url": _DATA_URL},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["screenshot_path"].startswith("screenshots/")
        assert body["screenshot_taken_at"]

        db.refresh(match)
        assert match.screenshot_path is not None
        assert match.screenshot_taken_at is not None

        saved_dir = main_module._screenshots_dir
        files = list(saved_dir.glob("*.png"))
        assert len(files) == 1

    def test_repeat_upload_replaces_old_file(self, client, db):
        # Two captures within the same second can share a filename (the naming
        # scheme is second-resolution) — the real invariant under test is disk
        # usage: repeat captures never accumulate old files, latest always wins.
        match = _seed_match(db)

        client.post(f"/jobs/{match.job_id}/screenshot?profile_id=1", json={"data_url": _DATA_URL})
        res = client.post(f"/jobs/{match.job_id}/screenshot?profile_id=1", json={"data_url": _DATA_URL})
        assert res.status_code == 200

        db.refresh(match)
        assert match.screenshot_path is not None

        saved_dir = main_module._screenshots_dir
        files = list(saved_dir.glob("*.png"))
        assert len(files) == 1  # old file was deleted, not accumulated

    def test_malformed_data_url_returns_400(self, client, db):
        match = _seed_match(db)
        res = client.post(
            f"/jobs/{match.job_id}/screenshot?profile_id=1",
            json={"data_url": "not-a-data-url"},
        )
        assert res.status_code == 400

    def test_nonexistent_match_returns_404(self, client, db):
        db.add(Profile(id=1, name="Alice", email="alice@example.com", password_hash="x"))
        db.commit()
        res = client.post(
            "/jobs/999/screenshot?profile_id=1",
            json={"data_url": _DATA_URL},
        )
        assert res.status_code == 404
