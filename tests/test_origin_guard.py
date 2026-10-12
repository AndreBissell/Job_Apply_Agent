"""The local server sends no CORS headers, so other websites can't read it.

No AI is involved. A /health request is enough to see the response headers; the lifespan
(idle loop) is not started because TestClient is used without ``with``.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.main import app

EVIL = "https://example.com"


def test_no_cors_header_for_another_website():
    r = TestClient(app).get("/health", headers={"Origin": EVIL})
    assert r.status_code == 200
    assert "access-control-allow-origin" not in r.headers


def test_preflight_from_another_website_is_not_allowed():
    r = TestClient(app).options(
        "/jobs/1/status",
        headers={
            "Origin": EVIL,
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert "access-control-allow-origin" not in r.headers
    assert "access-control-allow-methods" not in r.headers
    assert r.status_code >= 400


# ---------------------------------------------------------------------------
# Writes from other websites (the Origin guard)
# ---------------------------------------------------------------------------
import sqlite3  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import app.api.main as api  # noqa: E402
from app.api.origin_guard import origin_allowed  # noqa: E402
from app.db import Base  # noqa: E402
from app.models import JobListing, Match, Profile  # noqa: E402


class _RecordingExecutor:
    def __init__(self) -> None:
        self.submitted: list[tuple] = []

    def submit(self, *args, **kwargs):
        self.submitted.append((args, kwargs))


@pytest.fixture()
def guarded(monkeypatch):
    """A client on http://localhost:8001 with one job; the worker records, never runs."""
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)

    @event.listens_for(eng, "connect")
    def _fk_on(conn, _):
        if isinstance(conn, sqlite3.Connection):
            conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng)
    with Session() as s:
        s.add(Profile(id=1, name="Bob", email="b@x.com", password_hash="x"))
        s.add(JobListing(id=1, source="seek", source_job_id="1", url="u", title="T",
                         raw_description="d"))
        s.add(Match(user_id=1, job_id=1, score=50, status="new"))
        s.commit()

    def _db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    executor = _RecordingExecutor()
    monkeypatch.setattr(api, "_bg_executor", executor)  # nothing runs, no AI call
    api.app.dependency_overrides[api.get_db] = _db
    yield TestClient(api.app, base_url="http://localhost:8001"), executor
    api.app.dependency_overrides.pop(api.get_db, None)
    Base.metadata.drop_all(eng)


def test_cross_site_post_is_refused_and_queues_nothing(guarded):
    client, executor = guarded
    r = client.post("/jobs/1/regenerate", headers={"Origin": EVIL})
    assert r.status_code == 403
    assert executor.submitted == []


@pytest.mark.parametrize("origin", ["null", "http://localhost:9999", "http://127.0.0.1:8001"])
def test_other_origins_are_refused_too(guarded, origin):
    client, executor = guarded
    assert client.post("/jobs/1/regenerate", headers={"Origin": origin}).status_code == 403
    assert executor.submitted == []


@pytest.mark.parametrize("headers", [
    {},                                          # scripts, tests: no Origin
    {"Origin": "chrome-extension://abcdef"},     # side panel, popup, background worker
    {"Origin": "http://localhost:8001"},         # the profile editor, same origin
])
def test_extension_own_origin_and_no_origin_are_accepted(guarded, headers):
    client, executor = guarded
    r = client.post("/jobs/1/regenerate", headers=headers)
    assert r.status_code == 200
    assert len(executor.submitted) == 1


def test_patch_from_own_origin_is_accepted(guarded):
    client, _ = guarded
    r = client.patch("/jobs/1/status", json={"status": "shortlisted"},
                     headers={"Origin": "http://localhost:8001"})
    assert r.status_code == 200


def test_reads_are_not_guarded(guarded):
    client, _ = guarded
    assert client.get("/health", headers={"Origin": EVIL}).status_code == 200


def test_rebinding_host_does_not_pass_as_own_origin():
    assert not origin_allowed("http://evil.example:8001", "evil.example:8001")
    assert origin_allowed("http://localhost:8001", "localhost:8001")
