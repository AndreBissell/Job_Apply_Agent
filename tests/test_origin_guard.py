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
