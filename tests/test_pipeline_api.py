"""Tests for the Pipeline tab's backend (docs/pipeline-tab-plan.md, Step 1):
``GET /jobs`` ``applied`` / ``limit`` / ``fresh`` / ``date_scraped``,
``POST /jobs/{job_id}/write-letter`` (``letter_bands.claim_job``) and the skills view
(``app/skill_gaps.py``, ``GET /gaps/market`` and its answers).

No network and no LLM: ``worker._bg_executor`` is a recording fake and the database is an
in-memory SQLite (both ``get_db`` dependencies are overridden), never real.db / app.db.
"""
from __future__ import annotations

import itertools
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.api.main as main
import app.api.profile_ui as profile_ui
from app import letter_bands, skill_gaps
from app.api import idle_loop, worker
from app.db import Base
from app.models import (
    CoverLetter, GapDecision, GapSighting, JobListing, JobSkill, LetterRun, Match, Profile, Skill,
)
from app.preferences import set_preferences

_ids = itertools.count(5000)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _clean_queue():
    letter_bands.queued.clear()
    yield
    letter_bands.queued.clear()


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
def Session(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def client(Session, monkeypatch):
    async def _no_idle_loop():
        return None

    monkeypatch.setattr(idle_loop, "_processing_idle_loop", _no_idle_loop)

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
def db(Session):
    s = Session()
    s.add(Profile(id=1, name="Bob", email="b@x.com", password_hash="x", summary="Grad."))
    s.add(Profile(id=2, name="Other", email="o@x.com", password_hash="x", summary="Other."))
    s.commit()
    yield s
    s.close()


class FakeExecutor:
    def __init__(self):
        self.calls: list[tuple] = []

    def submit(self, fn, *args, **kwargs):
        self.calls.append((fn, args, kwargs))


@pytest.fixture()
def fake_exec(monkeypatch):
    fake = FakeExecutor()
    monkeypatch.setattr(worker, "_bg_executor", fake)
    return fake


def seed(db, score, *, user_id=1, seen=None, extracted=True, skills=(), scored_at=None, **job_fields):
    """One JobListing (seen live now unless ``seen`` is given) with hard ``skills``, plus a Match."""
    n = next(_ids)
    job = JobListing(
        source="seek", source_job_id=str(n), url=f"u{n}", title=f"Job {n}", company="Co",
        raw_description="desc",
        extracted_at=now_utc() if extracted else None,
        last_seen_live_at=seen if seen is not None else now_utc(),
        **job_fields,
    )
    db.add(job)
    db.flush()
    for name in skills:
        db.add(JobSkill(job_id=job.id, name=name, skill_type="hard"))
    match = Match(user_id=user_id, job_id=job.id, score=score, status="new", scored_at=scored_at or now_utc())
    db.add(match)
    db.commit()
    return job, match


def jobs_of(client, **params):
    res = client.get("/jobs", params={"profile_id": 1, **params})
    assert res.status_code == 200, res.text
    return res.json()


# ---------------------------------------------------------------------------
# GET /jobs: applied filter, limit, fresh, date_scraped
# ---------------------------------------------------------------------------
class TestJobsList:
    def test_applied_false_leaves_out_applied_jobs(self, client, db):
        a, _ = seed(db, 80)
        b, mb = seed(db, 70)
        mb.applied_at = now_utc()
        db.commit()
        assert [j["job_id"] for j in jobs_of(client, applied="false")] == [a.id]
        assert [j["job_id"] for j in jobs_of(client, applied="true")] == [b.id]
        assert {j["job_id"] for j in jobs_of(client)} == {a.id, b.id}  # unchanged without it

    def test_limit_is_capped_at_500(self, client, db):
        assert client.get("/jobs", params={"limit": 501}).status_code == 422
        assert client.get("/jobs", params={"limit": 0}).status_code == 422
        assert client.get("/jobs", params={"limit": 500}).status_code == 200

    def test_limit_above_50_returns_them_all(self, client, db):
        for i in range(55):
            seed(db, 50 + i % 40)
        assert len(jobs_of(client)) == 50  # the old default stays
        assert len(jobs_of(client, limit=500, applied="false")) == 55

    def test_fresh_follows_the_availability_gate(self, client, db):
        live, _ = seed(db, 80)
        stale, _ = seed(db, 70, seen=now_utc() - timedelta(days=3))
        rows = {j["job_id"]: j for j in jobs_of(client)}
        assert rows[live.id]["fresh"] is True
        assert rows[stale.id]["fresh"] is False
        set_preferences(db, 1, {"availability_checks_enabled": False})
        rows = {j["job_id"]: j for j in jobs_of(client)}
        assert rows[stale.id]["fresh"] is True  # checks off: no wait

    def test_date_scraped_is_aware_utc(self, client, db):
        seed(db, 80)
        row = jobs_of(client)[0]
        assert row["date_scraped"] and row["date_scraped"].endswith("+00:00")


# ---------------------------------------------------------------------------
# POST /jobs/{job_id}/write-letter
# ---------------------------------------------------------------------------
def write(client, job_id, profile_id=1):
    return client.post(f"/jobs/{job_id}/write-letter", params={"profile_id": profile_id})


class TestWriteLetter:
    def test_queues_a_job_below_the_bar(self, client, db, fake_exec):
        job, _ = seed(db, 60)
        res = write(client, job.id)
        assert res.status_code == 200 and res.json() == {"queued": 1, "reason": "queued"}
        assert fake_exec.calls == [(worker._write_band_letters, ([job.id], 1), {})]
        assert job.id in letter_bands.queued_ids()

    def test_a_second_click_does_not_queue_twice(self, client, db, fake_exec):
        job, _ = seed(db, 60)
        write(client, job.id)
        assert write(client, job.id).json() == {"queued": 0, "reason": "writing"}
        assert len(fake_exec.calls) == 1

    def test_unknown_job_is_404(self, client, db, fake_exec):
        assert write(client, 999999).status_code == 404
        other, _ = seed(db, 60, user_id=2)
        assert write(client, other.id).status_code == 404  # another profile's match

    def test_has_letter(self, client, db, fake_exec):
        job, match = seed(db, 60)
        db.add(CoverLetter(match_id=match.id, generated_content="Dear", status="draft"))
        db.commit()
        assert write(client, job.id).json()["reason"] == "has_letter"

    @pytest.mark.parametrize("change", ["applied", "hidden", "expired", "unextracted"])
    def test_not_eligible(self, client, db, fake_exec, change):
        job, match = seed(db, 60, extracted=change != "unextracted")
        if change == "applied":
            match.applied_at = now_utc()
        elif change == "hidden":
            match.hidden_at = now_utc()
        elif change == "expired":
            job.expired_detected_at = now_utc()
        db.commit()
        assert write(client, job.id).json() == {"queued": 0, "reason": "not_eligible"}
        assert fake_exec.calls == []

    def test_at_or_above_the_bar_is_the_idle_loops_job(self, client, db, fake_exec):
        set_preferences(db, 1, {"auto_cover_letter_min_score": 80})
        job, _ = seed(db, 80)
        assert write(client, job.id).json()["reason"] == "at_or_above_bar"

    def test_a_live_run_counts_as_writing(self, client, db, fake_exec):
        job, match = seed(db, 60)
        db.add(LetterRun(match_id=match.id, status="running", engine="agent", state="{}"))
        db.commit()
        assert write(client, job.id).json()["reason"] == "writing"

    def test_a_stale_job_waits_for_a_check(self, client, db, fake_exec):
        job, _ = seed(db, 60, seen=now_utc() - timedelta(days=3))
        assert write(client, job.id).json() == {"queued": 0, "reason": "waiting_check"}
        set_preferences(db, 1, {"availability_checks_enabled": False})
        assert write(client, job.id).json()["reason"] == "queued"


# ---------------------------------------------------------------------------
# Skills ads ask for
# ---------------------------------------------------------------------------
def add_skills(db, *names, user_id=1):
    for n in names:
        db.add(Skill(user_id=user_id, name=n))
    db.commit()


def market(client, **params):
    res = client.get("/gaps/market", params={"profile_id": 1, **params})
    assert res.status_code == 200, res.text
    return res.json()


def labels(data):
    return [i["skill"] for i in data["items"]]


class TestCovered:
    @pytest.mark.parametrize("job_skill,profile,expected", [
        ("TypeScript", ["Typescript Programming"], True),
        ("React", ["React Front End Development"], True),
        ("Azure", ["Microsoft Azure"], True),
        ("Docker", ["Docker Software"], True),
        ("Microsoft Power BI", ["Power BI"], True),   # the other way round
        ("JavaScript", ["Java Programming"], False),
        ("Java", ["JavaScript Programming"], False),
        ("Kubernetes", ["Docker Software"], False),
        ("C++", ["C++ Programming"], True),
    ])
    def test_whole_phrase_either_way(self, job_skill, profile, expected):
        assert skill_gaps.covered(job_skill, profile) is expected


class TestMarketGaps:
    def test_seek_style_profile_names_cover_their_skills(self, client, db):
        add_skills(db, "Typescript Programming", "Docker Software", "Java Programming")
        seed(db, 70, skills=["TypeScript", "Docker", "Kubernetes", "JavaScript"])
        seed(db, 60, skills=["Kubernetes", "Angular"])
        data = market(client)
        assert data["ads"] == 2
        assert labels(data) == ["Kubernetes", "JavaScript", "Angular"]  # JavaScript's ad scored higher
        kube = data["items"][0]
        assert kube["ads"] == 2 and kube["avg_score"] == 65 and kube["key"] == "kubernetes"

    def test_sorted_by_ads_then_average_score_then_name(self, client, db):
        seed(db, 90, skills=["Zig", "Go"])
        seed(db, 40, skills=["Go", "Rust"])
        seed(db, 90, skills=["Ada"])
        assert labels(market(client)) == ["Go", "Ada", "Zig", "Rust"]

    def test_spellings_group_under_the_most_common(self, client, db):
        seed(db, 70, skills=["Next.js"])
        seed(db, 70, skills=["Next.js"])
        seed(db, 70, skills=["next-js"])
        data = market(client)
        assert labels(data) == ["Next.js"] and data["items"][0]["ads"] == 3

    def test_window(self, client, db):
        seed(db, 70, skills=["Old"], scored_at=now_utc() - timedelta(days=120))
        seed(db, 70, skills=["New"])
        assert labels(market(client)) == ["New"]
        assert set(labels(market(client, days=365))) == {"Old", "New"}

    def test_hidden_and_applied_jobs_still_count(self, client, db):
        _, m1 = seed(db, 50, skills=["Kubernetes"])
        _, m2 = seed(db, 80, skills=["Kubernetes"])
        m1.hidden_at = now_utc()
        m2.applied_at = now_utc()
        db.commit()
        assert market(client)["items"][0]["ads"] == 2

    def test_soft_skills_and_other_profiles_are_left_out(self, client, db):
        job, _ = seed(db, 70, skills=["Kubernetes"])
        db.add(JobSkill(job_id=job.id, name="Teamwork", skill_type="soft"))
        db.commit()
        seed(db, 70, user_id=2, skills=["Fortran"])
        assert labels(market(client)) == ["Kubernetes"]

    def test_a_remembered_no_is_left_out(self, client, db):
        seed(db, 70, skills=["Kubernetes", "Angular"])
        client.post("/gaps/market/not-yet", params={"profile_id": 1}, json={"skill": "Kubernetes"})
        assert labels(market(client)) == ["Angular"]

    def test_bad_days(self, client, db):
        assert client.get("/gaps/market", params={"days": 0}).status_code == 422


class TestMarketAnswers:
    def test_have_adds_the_skill_and_it_leaves_the_list(self, client, db):
        seed(db, 70, skills=["Kubernetes", "Angular"])
        res = client.post("/gaps/market/have", params={"profile_id": 1}, json={"skill": "Kubernetes"})
        assert res.status_code == 200, res.text
        skill = db.scalar(select(Skill).where(Skill.user_id == 1, Skill.name == "Kubernetes"))
        assert skill is not None and skill.origin == "ask_user"
        assert labels(market(client)) == ["Angular"]

    def test_have_clears_a_remembered_no(self, client, db):
        seed(db, 70, skills=["Kubernetes"])
        client.post("/gaps/market/not-yet", json={"skill": "Kubernetes"})
        res = client.post("/gaps/market/have", json={"skill": "Kubernetes"})
        assert res.json()["auto_cleared"] == ["Kubernetes"]

    def test_not_yet_lands_on_to_work_on_with_the_ads_counted(self, client, db):
        seed(db, 70, skills=["Kubernetes"])
        seed(db, 60, skills=["Kubernetes"])
        res = client.post("/gaps/market/not-yet", json={"skill": "Kubernetes"})
        assert res.status_code == 200 and res.json()["label"] == "Kubernetes"
        items = client.get("/gaps/to-work-on").json()["items"]
        assert [(i["label"], i["recent"]) for i in items] == [("Kubernetes", 2)]

    def test_ignore_and_unignore(self, client, db):
        seed(db, 70, skills=["APIs", "Angular"])
        client.post("/gaps/market/ignore", json={"skill": "APIs"})
        assert labels(market(client)) == ["Angular"]
        db.refresh(db.get(Profile, 1))
        assert json.loads(db.get(Profile, 1).preferences)["gap_ignored"] == ["apis"]
        client.post("/gaps/market/unignore", json={"skill": "APIs"})
        assert set(labels(market(client))) == {"APIs", "Angular"}

    def test_ignore_twice_keeps_one_key(self, client, db):
        client.post("/gaps/market/ignore", json={"skill": "APIs"})
        res = client.post("/gaps/market/ignore", json={"skill": "apis"})
        assert res.json()["ignored"] == ["apis"]

    @pytest.mark.parametrize("path", ["have", "not-yet", "ignore"])
    def test_blank_skill_is_422(self, client, db, path):
        assert client.post(f"/gaps/market/{path}", json={"skill": "   "}).status_code == 422
        assert client.post(f"/gaps/market/{path}", json={"skill": ""}).status_code == 422
