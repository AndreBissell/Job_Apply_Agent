"""Tests for the Centrelink dashboard backend (docs/centrelink-dashboard-plan.md):
app/obligation.py's period maths, GET /obligation, PATCH /jobs/{id}/interview, the two
obligation preferences, and GET /jobs?ready=true.

No network and no LLM. In-memory SQLite only (both ``get_db`` dependencies are
overridden), never real.db / app.db. "Today" is pinned by monkeypatching
``obligation.local_today``.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.api.main as main
import app.api.profile_ui as profile_ui
from app import obligation
from app.db import Base
from app.llm.letter.state import JobInfo, LetterState
from app.models import CoverLetter, JobListing, LetterRun, LlmUsage, Match, Profile
from app.obligation import add_months, calendar_period_for, period_for, to_local_date
from app.preferences import obligation_settings, set_preferences

D = dt.date
UTC = dt.timezone.utc


# ---------------------------------------------------------------------------
# Period maths
# ---------------------------------------------------------------------------
class TestPeriodMaths:
    def test_anchor_on_the_7th(self):
        a = D(2026, 10, 7)
        assert period_for(D(2026, 10, 7), a) == (D(2026, 10, 7), D(2026, 11, 6))
        assert period_for(D(2026, 11, 6), a) == (D(2026, 10, 7), D(2026, 11, 6))
        assert period_for(D(2026, 11, 7), a) == (D(2026, 11, 7), D(2026, 12, 6))

    def test_dates_before_the_anchor(self):
        a = D(2026, 10, 7)
        assert period_for(D(2026, 10, 6), a) == (D(2026, 9, 7), D(2026, 10, 6))
        assert period_for(D(2026, 8, 1), a) == (D(2026, 7, 7), D(2026, 8, 6))

    def test_december_to_january(self):
        a = D(2026, 12, 15)
        assert period_for(D(2027, 1, 10), a) == (D(2026, 12, 15), D(2027, 1, 14))
        assert period_for(D(2027, 1, 20), a) == (D(2027, 1, 15), D(2027, 2, 14))
        assert add_months(D(2027, 1, 15), -1) == D(2026, 12, 15)

    def test_anchor_on_the_31st_clamps_from_the_anchor_day(self):
        a = D(2026, 1, 31)
        assert [add_months(a, k) for k in range(4)] == [D(2026, 1, 31), D(2026, 2, 28), D(2026, 3, 31), D(2026, 4, 30)]
        assert period_for(D(2026, 2, 27), a) == (D(2026, 1, 31), D(2026, 2, 27))
        assert period_for(D(2026, 2, 28), a) == (D(2026, 2, 28), D(2026, 3, 30))
        assert period_for(D(2026, 4, 29), a) == (D(2026, 3, 31), D(2026, 4, 29))
        assert add_months(D(2028, 1, 31), 1) == D(2028, 2, 29)  # leap year

    @pytest.mark.parametrize("anchor", [D(2026, 10, 1), D(2026, 10, 7), D(2026, 1, 29), D(2026, 1, 30), D(2026, 1, 31)])
    def test_periods_tile_with_no_gap_or_overlap(self, anchor):
        d, prev = D(2025, 6, 1), None
        while d < D(2028, 6, 1):
            start, end = period_for(d, anchor)
            assert start <= d <= end
            if prev is not None and prev != (start, end):
                assert start == prev[1] + dt.timedelta(days=1)
            prev = (start, end)
            d += dt.timedelta(days=1)

    def test_calendar_fallback(self):
        assert calendar_period_for(D(2026, 2, 14)) == (D(2026, 2, 1), D(2026, 2, 28))
        assert calendar_period_for(D(2026, 12, 31)) == (D(2026, 12, 1), D(2026, 12, 31))

    def test_naive_timestamps_read_as_utc(self):
        aware = dt.datetime(2026, 10, 6, 22, 0, tzinfo=UTC)
        assert to_local_date(aware.replace(tzinfo=None)) == to_local_date(aware) == aware.astimezone().date()


# ---------------------------------------------------------------------------
# API fixtures
# ---------------------------------------------------------------------------
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
def db(engine):
    s = sessionmaker(bind=engine)()
    s.add(Profile(id=1, name="Alice", email="alice@example.com", password_hash="x"))
    s.commit()
    yield s
    s.close()


@pytest.fixture()
def client(engine, db):
    Session = sessionmaker(bind=engine)

    def _db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    main.app.dependency_overrides[profile_ui.get_db] = _db
    main.app.dependency_overrides[main.get_db] = _db
    with TestClient(main.app, raise_server_exceptions=True) as c:
        yield c
    main.app.dependency_overrides.pop(profile_ui.get_db, None)
    main.app.dependency_overrides.pop(main.get_db, None)


@pytest.fixture()
def today(monkeypatch):
    """Pin "today" to 20/10/2026."""
    monkeypatch.setattr(obligation, "local_today", lambda: D(2026, 10, 20))
    return D(2026, 10, 20)


def _local_noon(d: dt.date) -> dt.datetime:
    """Noon local time on ``d``, as UTC: lands on ``d`` whatever the machine's zone."""
    return dt.datetime(d.year, d.month, d.day, 12).astimezone().astimezone(UTC)


def seed(db, job_id, *, applied=None, score=80, hidden=False, expired=False, letter=False, title=None):
    db.add(JobListing(id=job_id, source="seek", source_job_id=str(job_id), url=f"https://x/{job_id}",
                      title=title or f"Job {job_id}", company=f"Co {job_id}",
                      expired_detected_at=dt.datetime(2026, 10, 1, tzinfo=UTC) if expired else None))
    db.flush()
    m = Match(id=job_id, user_id=1, job_id=job_id, score=score,
              status="applied" if applied else "new",
              applied_at=_local_noon(applied) if type(applied) is dt.date else applied,
              hidden_at=dt.datetime(2026, 10, 2, tzinfo=UTC) if hidden else None)
    db.add(m)
    db.flush()
    if letter:
        db.add(CoverLetter(match_id=m.id, generated_content="Dear team"))
    db.commit()
    return m


def usage(db, job_id, cost):
    db.add(LlmUsage(task="match", tier="small", model="m", cost_usd=Decimal(str(cost)), job_id=job_id))
    db.commit()


# ---------------------------------------------------------------------------
# GET /obligation
# ---------------------------------------------------------------------------
class TestObligation:
    def test_counts_and_newest_first(self, client, db, today):
        set_preferences(db, 1, {"obligation_cycle_start": "2026-10-07"})
        seed(db, 1, applied=D(2026, 10, 7))
        seed(db, 2, applied=D(2026, 10, 19))
        seed(db, 3, applied=D(2026, 10, 6))   # previous period
        seed(db, 4, applied=D(2026, 8, 20))   # two back
        seed(db, 5)                           # not applied
        body = client.get("/obligation").json()

        assert body["target"] == 20
        assert body["cycle_start"] == "2026-10-07"
        assert body["current"] == {"start": "2026-10-07", "end": "2026-11-06", "applied": 2, "days_left": 18}
        assert [(p["start"], p["end"], p["applied"], p["is_current"]) for p in body["periods"]] == [
            ("2026-10-07", "2026-11-06", 2, True),
            ("2026-09-07", "2026-10-06", 1, False),
            ("2026-08-07", "2026-09-06", 1, False),
        ]
        # newest application first within a period
        assert [j["job_id"] for j in body["periods"][0]["jobs"]] == [2, 1]
        assert body["periods"][0]["jobs"][0]["company"] == "Co 2"

    def test_current_period_present_at_zero(self, client, db, today):
        set_preferences(db, 1, {"obligation_cycle_start": "2026-10-07"})
        seed(db, 1, applied=D(2026, 9, 10))
        body = client.get("/obligation").json()
        assert body["current"]["applied"] == 0
        assert body["periods"][0] == {"start": "2026-10-07", "end": "2026-11-06", "is_current": True,
                                      "applied": 0, "cost_usd": 0, "uncosted": 0, "jobs": []}
        assert len(body["periods"]) == 2

    def test_hidden_applications_still_count(self, client, db, today):
        set_preferences(db, 1, {"obligation_cycle_start": "2026-10-07"})
        seed(db, 1, applied=D(2026, 10, 8), hidden=True, expired=True)
        assert client.get("/obligation").json()["current"]["applied"] == 1

    def test_cost_is_all_usage_for_the_job_and_null_when_unlogged(self, client, db, today):
        set_preferences(db, 1, {"obligation_cycle_start": "2026-10-07"})
        seed(db, 1, applied=D(2026, 10, 8))
        seed(db, 2, applied=D(2026, 10, 9))
        seed(db, 3, applied=D(2026, 10, 10))
        usage(db, 1, 0.01)
        usage(db, 1, 0.2)
        usage(db, 2, 0.05)
        usage(db, 99, 3.0)  # another job's spend is not counted
        period = client.get("/obligation").json()["periods"][0]
        costs = {j["job_id"]: j["cost_usd"] for j in period["jobs"]}
        assert costs == {1: pytest.approx(0.21), 2: pytest.approx(0.05), 3: None}
        assert period["cost_usd"] == pytest.approx(0.26)
        assert period["uncosted"] == 1

    def test_no_start_date_groups_by_calendar_month(self, client, db, today):
        seed(db, 1, applied=D(2026, 10, 2))
        seed(db, 2, applied=D(2026, 9, 30))
        body = client.get("/obligation").json()
        assert body["cycle_start"] is None
        assert body["current"]["start"] == "2026-10-01" and body["current"]["end"] == "2026-10-31"
        assert [(p["start"], p["applied"]) for p in body["periods"]] == [("2026-10-01", 1), ("2026-09-01", 1)]

    def test_counts_on_the_local_date(self, client, db, today):
        """An application at 22:00 UTC the day before the period starts lands in the new
        period when the server is 2+ hours ahead of UTC (Australia)."""
        late = dt.datetime(2026, 10, 6, 22, 0, tzinfo=UTC)
        if late.astimezone().date() != D(2026, 10, 7):
            pytest.skip("needs a local zone at UTC+2 or more")
        set_preferences(db, 1, {"obligation_cycle_start": "2026-10-07"})
        seed(db, 1, applied=late.replace(tzinfo=None))  # SQLite stores naive UTC
        assert client.get("/obligation").json()["current"]["applied"] == 1

    def test_waiting_count(self, client, db, today):
        m = seed(db, 1)
        state = LetterState(profile_id=1, job=JobInfo(job_id=1, title="Job 1"))
        db.add(LetterRun(match_id=m.id, engine="agent", status="waiting_user", state=state.model_dump_json()))
        db.commit()
        assert client.get("/obligation").json()["waiting_count"] == 1

    def test_bad_stored_values_fall_back(self, db):
        set_preferences(db, 1, {"obligation_target": "lots", "obligation_cycle_start": "not a date"})
        assert obligation_settings(db, 1) == {"target": 20, "cycle_start": None}


# ---------------------------------------------------------------------------
# PATCH /jobs/{id}/interview
# ---------------------------------------------------------------------------
class TestInterview:
    def test_set_and_clear_keeps_status(self, client, db, today):
        seed(db, 1, applied=D(2026, 10, 8))
        res = client.patch("/jobs/1/interview", json={"interview": True})
        assert res.status_code == 200
        first = res.json()["interview_at"]
        assert first
        # a second "yes" keeps the first date
        assert client.patch("/jobs/1/interview", json={"interview": True}).json()["interview_at"] == first
        job = client.get("/obligation").json()["periods"][0]["jobs"][0]
        assert job["interview_at"] == first
        db.expire_all()
        assert db.get(Match, 1).status == "applied"

        assert client.patch("/jobs/1/interview", json={"interview": False}).json()["interview_at"] is None
        db.expire_all()
        assert db.get(Match, 1).interview_at is None

    def test_unknown_job_is_404(self, client):
        assert client.patch("/jobs/42/interview", json={"interview": True}).status_code == 404

    def test_not_applied_is_409(self, client, db):
        seed(db, 1)
        assert client.patch("/jobs/1/interview", json={"interview": True}).status_code == 409


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------
class TestPreferences:
    def test_start_date_round_trips_as_a_string(self, client, db):
        res = client.put("/profile/1/preferences", json={"obligation_cycle_start": "2026-10-07", "obligation_target": 15})
        assert res.status_code == 200
        assert res.json()["obligation_cycle_start"] == "2026-10-07"
        assert client.get("/profile/1/preferences").json()["obligation_target"] == 15
        db.expire_all()
        assert obligation_settings(db, 1) == {"target": 15, "cycle_start": D(2026, 10, 7)}

    @pytest.mark.parametrize("body", [{"obligation_target": 0}, {"obligation_target": 101},
                                      {"obligation_cycle_start": "2026-02-30"}, {"obligation_cycle_start": "soon"}])
    def test_bounds(self, client, body):
        assert client.put("/profile/1/preferences", json=body).status_code == 422


# ---------------------------------------------------------------------------
# GET /jobs?ready=true
# ---------------------------------------------------------------------------
class TestReadyJobs:
    def test_only_unapplied_jobs_with_a_letter(self, client, db):
        seed(db, 1, letter=True, score=70)
        seed(db, 2, letter=True, score=90)
        seed(db, 3, score=95)                                       # no letter
        seed(db, 4, letter=True, applied=D(2026, 10, 8))            # applied
        seed(db, 5, letter=True, expired=True)                      # expired
        seed(db, 6, letter=True, hidden=True)                       # hidden
        rows = client.get("/jobs", params={"ready": "true"}).json()
        assert [r["job_id"] for r in rows] == [2, 1]
        assert all(r["has_cover_letter"] for r in rows)

    def test_default_view_unchanged(self, client, db):
        seed(db, 1, letter=True)
        seed(db, 3, score=95)
        assert {r["job_id"] for r in client.get("/jobs").json()} == {1, 3}
