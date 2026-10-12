"""Safety nets for splitting app/api/main.py into routers (docs/refactor-9-10-plan.md, 9.0).

A pure move must not change what the API answers. These tests pin that down:
- the flattened route table (scripts/dump_routes.py) equals tests/fixtures/route_table.txt;
- app.openapi() equals tests/fixtures/openapi.json (paths, params, bodies, docstrings);
- no route is shadowed by an earlier one (e.g. GET /jobs/known-ids reaching
  GET /jobs/{job_id} and answering 422), checked on the table and over HTTP.

When an endpoint changes ON PURPOSE, regenerate the fixtures and review their diff:
    python scripts/dump_routes.py > tests/fixtures/route_table.txt
    python -c "import json,sys; sys.path.insert(0,'.'); from app.api.main import app; \
open('tests/fixtures/openapi.json','w',encoding='utf-8',newline='\\n').write(json.dumps(app.openapi(), sort_keys=True, indent=1) + '\\n')"

Only imports the app; the HTTP test uses an in-memory SQLite DB and no lifespan (so no
idle loop). No server, no AI call, no network.
"""

from __future__ import annotations

import difflib
import json
import re
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.api import deps, main, profile_ui  # noqa: E402
from app.api.main import app  # noqa: E402
from app.db import Base  # noqa: E402
from app.models import Profile  # noqa: E402
from scripts.dump_routes import flatten, route_lines  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"

# GET /jobs/<literal> paths that sit beside GET /jobs/{job_id} (job_id: int). Each must be
# matched before {job_id}, or it answers 422 instead of its data.
LITERAL_JOB_GETS = [
    "/jobs/known-ids",
    "/jobs/pending-count",
    "/jobs/by-source-id/no-such-id",
    "/jobs/search-performance",
    "/jobs/evidence-export",
    "/jobs/suggested-searches",
]


def _diff(expected: str, actual: str, name: str) -> str:
    return "\n".join(difflib.unified_diff(
        expected.splitlines(), actual.splitlines(),
        fromfile=f"{name} (fixture)", tofile=f"{name} (now)", lineterm="",
    ))


def test_route_table_unchanged():
    expected = (FIXTURES / "route_table.txt").read_text(encoding="utf-8").strip()
    actual = "\n".join(route_lines(app))
    assert actual == expected, "Route table changed:\n" + _diff(expected, actual, "route_table.txt")


def test_there_is_one_get_db():
    """Overriding either key must override every route (step 9.1)."""
    assert main.get_db is profile_ui.get_db is deps.get_db


def test_openapi_unchanged():
    expected = json.loads((FIXTURES / "openapi.json").read_text(encoding="utf-8"))
    actual = app.openapi()
    if actual != expected:
        pytest.fail("OpenAPI schema changed:\n" + _diff(
            json.dumps(expected, sort_keys=True, indent=1),
            json.dumps(actual, sort_keys=True, indent=1),
            "openapi.json",
        ))


def test_literal_job_paths_reach_their_own_endpoint():
    """First-match check over the whole table, the way Starlette routes a request."""
    routes = flatten(app.routes)
    assert len(routes) > 40, "flatten() found too few routes: is it still expanding routers?"
    shadowed = []
    for route in routes:
        methods = getattr(route, "methods", None)
        if not methods:
            continue  # a mount
        sample = re.sub(r"\{[^}]+\}", "1", route.path)
        for candidate in routes:
            cand_methods = getattr(candidate, "methods", None)
            if cand_methods is not None and not (cand_methods & methods):
                continue
            if candidate.path_regex.match(sample):
                if candidate is not route and candidate.path != route.path:
                    shadowed.append(f"{sorted(methods)} {route.path} is reached by {candidate.path}")
                break
    assert not shadowed, "Shadowed routes:\n" + "\n".join(shadowed)


@pytest.fixture()
def client():
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
    Session = sessionmaker(bind=eng)
    with Session() as db:
        db.add(Profile(id=1, name="Alice", email="alice@example.com", password_hash="x"))
        db.commit()

    def _db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    # Both session dependencies, so no route can fall through to the default app.db.
    overrides = {main.get_db, profile_ui.get_db}
    for dep in overrides:
        app.dependency_overrides[dep] = _db
    try:
        yield TestClient(app)  # no `with`: the lifespan (and its idle loop) never starts
    finally:
        for dep in overrides:
            app.dependency_overrides.pop(dep, None)
        Base.metadata.drop_all(eng)


@pytest.mark.parametrize("path", LITERAL_JOB_GETS)
def test_literal_job_paths_answer(client, path):
    res = client.get(path)
    assert res.status_code != 422, f"GET {path} answered 422: shadowed by /jobs/{{job_id}}?"
    expected = 404 if path.startswith("/jobs/by-source-id/") else 200
    assert res.status_code == expected, f"GET {path}: {res.status_code} {res.text[:200]}"
