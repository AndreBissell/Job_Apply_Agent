"""Rewrite letter + letter date (docs/letter-rewrite-plan.md).

``cover_letters.generated_at`` is stamped by both write paths and never by saving edits;
GET /jobs and GET /jobs/{id} say whether a letter predates the last profile change; and
/regenerate (the Rewrite button) claims the job as queued, refuses applied jobs and, with
``discard_edits``, replaces the user's edits only once a new letter has landed.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.api.main as main
import app.api.profile_ui as profile_ui
from app import letter_bands
from app.api import events, worker
from app.db import Base
from app.llm import cover_letter as one_shot
from app.llm.letter import production
from app.models import CoverLetter, Experience, JobListing, LlmUsage, Match, Profile
from app.preferences import set_preferences

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


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
def client(Session):
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


@pytest.fixture(autouse=True)
def _empty_queue():
    letter_bands.queued.clear()
    yield
    letter_bands.queued.clear()


@pytest.fixture()
def db(Session):
    """Job 5 has a letter written at T0; job 6 is applied, with a letter; job 7 has none."""
    s = Session()
    s.add(Profile(id=1, name="Bob", email="b@x.com", password_hash="x"))
    s.flush()
    s.add_all([
        JobListing(id=i, source="seek", source_job_id=str(i), url=f"u{i}", title=f"Job {i}", raw_description="Ad.")
        for i in (5, 6, 7)
    ])
    s.flush()
    s.add_all([
        Match(id=9, user_id=1, job_id=5, score=90),
        Match(id=10, user_id=1, job_id=6, score=88, status="applied", applied_at=T0),
        Match(id=11, user_id=1, job_id=7, score=86),
    ])
    s.flush()
    s.add_all([
        CoverLetter(match_id=9, generated_content="Old letter.", generated_at=T0),
        CoverLetter(match_id=10, generated_content="Sent letter.", generated_at=T0),
    ])
    s.commit()
    yield s
    s.close()


def _profile_changed(db, when):
    """A profile row last updated at ``when`` (retention.profile_revised_at reads updated_at)."""
    db.add(Experience(user_id=1, experience_type="project", title="New project", updated_at=when))
    db.commit()


def _letter(s, match_id) -> CoverLetter:
    return s.scalar(select(CoverLetter).where(CoverLetter.match_id == match_id))


def _by_job(rows):
    return {r["job_id"]: r for r in rows}


class FakeExecutor:
    def __init__(self, fail=False):
        self.calls: list[tuple] = []
        self.fail = fail

    def submit(self, fn, *args, **kwargs):
        if self.fail:
            raise RuntimeError("executor down")
        self.calls.append((fn, args, kwargs))


# ---------------------------------------------------------------------------
# generated_at: both write paths stamp it, saving edits never does
# ---------------------------------------------------------------------------
class TestGeneratedAt:
    def test_land_letter_stamps_it_on_a_new_and_a_replaced_letter(self, db):
        new = production.land_letter(db, 11, "First draft.")
        assert new.generated_at is not None
        replaced = production.land_letter(db, 9, "New letter.")
        assert replaced.generated_at.replace(tzinfo=timezone.utc) > T0

    def test_the_one_shot_stamps_it(self, db, Session, monkeypatch):
        monkeypatch.setattr(one_shot, "SessionLocal", Session)
        monkeypatch.setattr(one_shot, "complete_text", lambda *a, **k: "One-shot letter.")
        cl = one_shot.generate_cover_letter(7, 1, force=True)
        assert cl is not None and cl.generated_content == "One-shot letter."
        assert cl.generated_at is not None

    def test_saving_edits_does_not_move_it(self, client, db):
        res = client.patch("/jobs/5/cover-letter", json={"edited_content": "My edit."})
        assert res.status_code == 200
        db.expire_all()
        cl = _letter(db, 9)
        assert cl.edited_content == "My edit."
        assert cl.generated_at.replace(tzinfo=timezone.utc) == T0


# ---------------------------------------------------------------------------
# GET /jobs and GET /jobs/{id}: written date and outdated flag
# ---------------------------------------------------------------------------
class TestOutdated:
    def test_a_letter_written_before_the_profile_changed_is_outdated(self, client, db):
        _profile_changed(db, T0 + timedelta(days=1))
        rows = _by_job(client.get("/jobs").json())
        assert rows[5]["letter_written_at"].startswith("2026-10-01")
        assert rows[5]["letter_outdated"] is True
        assert rows[7]["letter_written_at"] is None and rows[7]["letter_outdated"] is False
        detail = client.get("/jobs/5").json()["cover_letter"]
        assert detail["generated_at"].startswith("2026-10-01") and detail["outdated"] is True

    def test_a_letter_written_after_it_is_not(self, client, db):
        _profile_changed(db, T0 - timedelta(days=1))
        assert _by_job(client.get("/jobs").json())[5]["letter_outdated"] is False
        assert client.get("/jobs/5").json()["cover_letter"]["outdated"] is False

    def test_the_detail_says_when_a_rewrite_is_queued(self, client, db):
        assert client.get("/jobs/5").json()["cover_letter"]["queued"] is False
        letter_bands.claim_rewrite(5)
        assert client.get("/jobs/5").json()["cover_letter"]["queued"] is True


# ---------------------------------------------------------------------------
# POST /jobs/{id}/regenerate: the Rewrite button
# ---------------------------------------------------------------------------
class TestRewriteEndpoint:
    def test_it_claims_the_job_so_cards_show_writing(self, client, db, monkeypatch):
        fake = FakeExecutor()
        monkeypatch.setattr(worker, "_bg_executor", fake)
        res = client.post("/jobs/5/regenerate?discard_edits=true")
        assert res.json() == {"status": "queued"}
        ((fn, args, _),) = fake.calls
        assert fn is worker._rewrite_letter and args == (5, 1, True, True)
        assert _by_job(client.get("/jobs").json())[5]["letter_queued"] is True

    def test_a_second_click_while_queued_does_nothing(self, client, db, monkeypatch):
        fake = FakeExecutor()
        monkeypatch.setattr(worker, "_bg_executor", fake)
        client.post("/jobs/5/regenerate")
        assert client.post("/jobs/5/regenerate").json() == {"status": "writing"}
        assert len(fake.calls) == 1

    def test_an_applied_job_is_refused(self, client, db, monkeypatch):
        fake = FakeExecutor()
        monkeypatch.setattr(worker, "_bg_executor", fake)
        assert client.post("/jobs/6/regenerate").status_code == 409
        assert fake.calls == [] and 6 not in letter_bands.queued

    def test_a_failed_submit_releases_the_claim(self, client, db, monkeypatch):
        monkeypatch.setattr(worker, "_bg_executor", FakeExecutor(fail=True))
        with pytest.raises(RuntimeError):
            client.post("/jobs/5/regenerate")
        assert 5 not in letter_bands.queued


# ---------------------------------------------------------------------------
# worker._rewrite_letter: edits replaced only once a new letter landed; always released
# ---------------------------------------------------------------------------
class TestRewriteWorker:
    @pytest.fixture()
    def wired(self, Session, db, monkeypatch):
        calls = {"events": [], "lands": True}
        monkeypatch.setattr(worker, "SessionLocal", Session)
        monkeypatch.setattr(events, "broadcast_from_thread", lambda name, data: calls["events"].append((name, data)))

        def fake_process(job_id, profile_id, has_description, **kwargs):
            calls["kwargs"] = kwargs
            if calls["lands"]:
                with Session() as s:
                    production.land_letter(s, 9, "Rewritten letter.")

        monkeypatch.setattr(worker, "_process_listing", fake_process)
        with Session() as s:
            row = _letter(s, 9)
            row.edited_content, row.status = "My edits.", "edited"
            s.commit()
        letter_bands.claim_rewrite(5)
        return calls

    def _letter(self, Session):
        with Session() as s:
            return _letter(s, 9)

    def test_it_runs_the_regenerate_work(self, wired, Session):
        worker._rewrite_letter(5, 1, True)
        assert wired["kwargs"] == {"with_cover_letter": True, "bypass_threshold": True, "force": True}

    def test_discard_edits_replaces_them_once_the_new_letter_landed(self, wired, Session):
        worker._rewrite_letter(5, 1, True, discard_edits=True)
        cl = self._letter(Session)
        assert cl.generated_content == "Rewritten letter."
        assert cl.edited_content is None and cl.status == "draft"

    def test_without_discard_edits_they_are_kept(self, wired, Session):
        worker._rewrite_letter(5, 1, True)
        cl = self._letter(Session)
        assert cl.generated_content == "Rewritten letter." and cl.edited_content == "My edits."

    def test_a_rewrite_that_landed_nothing_keeps_the_edits(self, wired, Session):
        wired["lands"] = False
        worker._rewrite_letter(5, 1, True, discard_edits=True)
        cl = self._letter(Session)
        assert cl.generated_content == "Old letter." and cl.edited_content == "My edits."

    def test_it_always_releases_the_claim_and_tells_the_sidebar(self, wired, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("worker crash")

        monkeypatch.setattr(worker, "_process_listing", boom)
        with pytest.raises(RuntimeError):
            worker._rewrite_letter(5, 1, True)
        assert 5 not in letter_bands.queued
        assert ("letter_rewrite_done", {"job_id": 5}) in wired["events"]


# ---------------------------------------------------------------------------
# The confirm click's cost estimate and "replaces your edits"
# ---------------------------------------------------------------------------
class TestRewriteEstimate:
    def _usage(self, db, run_id, *costs):
        db.add_all([
            LlmUsage(task="generate_letter", tier="strong", model="m", cost_usd=Decimal(str(c)), run_id=run_id)
            for c in costs
        ])
        db.commit()

    def test_it_is_the_default_before_enough_pipeline_runs(self, db):
        self._usage(db, 1, 0.20)
        assert letter_bands.rewrite_cost_estimate(db, 1) == letter_bands.REWRITE_DEFAULT_USD

    def test_it_is_the_mean_cost_per_pipeline_run(self, db):
        self._usage(db, 1, 0.10, 0.10)  # run 1 costs 0.20 in two calls
        self._usage(db, 2, 0.30)
        self._usage(db, 3, 0.25)
        assert letter_bands.rewrite_cost_estimate(db, 1) == 0.25

    def test_with_the_loop_off_it_is_the_one_pass_estimate(self, db):
        set_preferences(db, 1, {"letter_loop_enabled": False})
        db.commit()
        assert letter_bands.rewrite_cost_estimate(db, 1) == letter_bands.letter_cost_estimate(db)[0]

    def test_the_api_says_whether_there_are_edits_to_replace(self, client, db):
        assert _by_job(client.get("/jobs").json())[5]["letter_edited"] is False
        client.patch("/jobs/5/cover-letter", json={"edited_content": "Mine."})
        assert _by_job(client.get("/jobs").json())[5]["letter_edited"] is True
        detail = client.get("/jobs/5").json()["cover_letter"]
        assert detail["edited"] is True and detail["rewrite_est_usd"] == letter_bands.REWRITE_DEFAULT_USD
