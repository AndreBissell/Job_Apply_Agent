"""Tests for the suggestion + search-performance endpoints and query attribution.

Covers Layers 2-4 of the pipeline: that /ingest records and backfills
``discovered_query``, that /jobs/search-performance computes yield from it, that
a proven-bad query demotes a phrase, and that the LLM layer stays off unless
asked for.

Same in-memory SQLite (StaticPool) pattern as tests/test_preferences.py.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.main import app, get_db
from app.db import Base
from app.models import JobListing, Match, Profile


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
def client(engine):
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
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _profile(db):
    db.merge(Profile(id=1, name="Alice", email="alice@example.com", password_hash="x"))
    db.commit()


def _scored(db, rows, query=None):
    """Seed (title, score) pairs, optionally all attributed to one query."""
    for i, (title, score) in enumerate(rows, start=1):
        job_id = db.query(JobListing).count() + 1
        db.add(
            JobListing(
                id=job_id,
                source="seek",
                source_job_id=f"j{job_id}",
                url=f"u{job_id}",
                title=title,
                discovered_query=query,
            )
        )
        db.add(Match(user_id=1, job_id=job_id, score=score, status="new"))
    db.commit()


class TestQueryAttribution:
    def test_ingest_records_the_discovering_query(self, client, db):
        _profile(db)
        res = client.post("/ingest", json={
            "listings": [{
                "source_job_id": "1",
                "url": "https://au.seek.com/job/1",
                "title": "Data Analyst",
                "discovered_query": "data analyst",
            }],
            "profile_id": 1,
        })
        assert res.status_code == 200
        assert db.get(JobListing, res.json()["job_ids"][0]).discovered_query == "data analyst"

    def test_detail_ingest_backfills_taxonomy_without_clobbering_the_query(self, client, db):
        """The card arrives first (query, no description), the detail page
        second (description + taxonomy, no query). The row must end up with
        all of it."""
        _profile(db)
        base = {"source_job_id": "1", "url": "https://au.seek.com/job/1", "title": "Data Analyst"}
        client.post("/ingest", json={
            "listings": [{**base, "discovered_query": "data analyst"}], "profile_id": 1,
        })
        res = client.post("/ingest", json={
            "listings": [{
                **base,
                "raw_description": "Full description",
                "classification": "Information & Communication Technology",
                "subclassification": "Developers/Programmers",
            }],
            "profile_id": 1,
        })
        assert res.json()["updated"] == 1

        job = db.get(JobListing, res.json()["job_ids"][0])
        db.refresh(job)
        assert job.discovered_query == "data analyst"
        assert job.raw_description == "Full description"
        assert job.subclassification == "Developers/Programmers"

    def test_existing_values_are_never_overwritten(self, client, db):
        _profile(db)
        base = {"source_job_id": "1", "url": "u", "title": "T"}
        client.post("/ingest", json={
            "listings": [{**base, "discovered_query": "first"}], "profile_id": 1,
        })
        res = client.post("/ingest", json={
            "listings": [{**base, "discovered_query": "second"}], "profile_id": 1,
        })
        job = db.get(JobListing, res.json()["job_ids"][0])
        db.refresh(job)
        assert job.discovered_query == "first"


class TestSearchPerformance:
    def test_yield_per_query(self, client, db):
        _profile(db)
        _scored(db, [("Data Analyst", 90), ("Data Analyst", 80), ("Data Clerk", 20)], query="data")
        _scored(db, [("Warehouse Operator", 10), ("Forklift Operator", 15)], query="warehouse")

        rows = {r["query"]: r for r in client.get("/jobs/search-performance").json()["performance"]}
        assert rows["data"] == {"query": "data", "volume": 3, "hits": 2, "yield": pytest.approx(0.667, abs=0.001)}
        assert rows["warehouse"]["hits"] == 0

    def test_unattributed_jobs_are_excluded(self, client, db):
        _profile(db)
        _scored(db, [("Data Analyst", 90)], query=None)
        assert client.get("/jobs/search-performance").json()["performance"] == []

    def test_flags_high_volume_low_yield_queries(self, client, db):
        _profile(db)
        # 6 jobs, none good — past the 5-job minimum volume, under 15% yield.
        _scored(db, [("Warehouse Operator", 10)] * 6, query="warehouse operator")
        # 2 jobs, none good — too little volume to judge.
        _scored(db, [("Night Packer", 10)] * 2, query="night packer")

        data = client.get("/jobs/search-performance").json()
        assert data["underperforming"] == ["warehouse operator"]

    def test_empty_history(self, client, db):
        _profile(db)
        assert client.get("/jobs/search-performance").json()["performance"] == []


class TestSuggestedSearches:
    def test_ranks_by_score_not_frequency(self, client, db):
        _profile(db)
        _scored(db, [("Software Engineer", 50)] * 5 + [("Data Analyst", 92)] * 2)
        suggestions = client.get("/jobs/suggested-searches").json()["suggestions"]
        assert suggestions[0] == "Data Analyst"

    def test_no_seniority_fragments(self, client, db):
        _profile(db)
        _scored(db, [
            ("Junior-Intermediate Software Engineer", 88),
            ("Graduate / Intermediate .NET Developer", 85),
            ("Warehouse Operator", 20),
        ])
        suggestions = client.get("/jobs/suggested-searches").json()["suggestions"]
        assert not any("Intermediate" in s for s in suggestions)

    def test_proven_bad_query_demotes_its_phrase(self, client, db):
        """Two equally-strong phrases, mined from jobs found some other way.
        One of them has *also* been run as a literal Seek search that returned
        nothing worth having — so it should no longer be the top suggestion."""
        _profile(db)
        _scored(db, [("Data Analyst", 86)] * 3)
        _scored(db, [("Systems Officer", 84)] * 3)
        before = client.get("/jobs/suggested-searches").json()["suggestions"]
        assert before[0] == "Data Analyst"

        # Searching "data analyst" on Seek surfaced 6 jobs, none any good.
        _scored(db, [("Data Entry Clerk", 10)] * 6, query="data analyst")
        assert client.get("/jobs/search-performance").json()["underperforming"] == ["data analyst"]

        after = client.get("/jobs/suggested-searches").json()["suggestions"]
        assert after[0] == "Systems Officer"
        assert "Data Analyst" in after  # demoted, not deleted

    def test_falls_back_to_target_role_with_no_history(self, client, db):
        _profile(db)
        profile = db.get(Profile, 1)
        profile.target_role = "Software Engineer"
        db.commit()
        assert client.get("/jobs/suggested-searches").json()["suggestions"] == ["Software Engineer"]

    def test_no_data_returns_empty(self, client, db):
        _profile(db)
        assert client.get("/jobs/suggested-searches").json()["suggestions"] == []


class TestBulkHide:
    """DELETE /jobs is a soft delete: gone from the UI, still in the stats."""

    def test_hides_from_the_jobs_list(self, client, db):
        _profile(db)
        _scored(db, [("Data Analyst", 90), ("Warehouse Operator", 20)])
        assert len(client.get("/jobs").json()) == 2

        assert client.request("DELETE", "/jobs", params={"below_score": 70}).json()["hidden"] == 1
        titles = [j["title"] for j in client.get("/jobs").json()]
        assert titles == ["Data Analyst"]

    def test_hidden_match_survives_and_still_sets_the_baseline(self, client, db):
        """The regression this whole change exists for: hiding the weak jobs
        must not move the baseline the suggestion ranking is measured against."""
        _profile(db)
        _scored(db, [("Data Analyst", 90)] * 2 + [("Warehouse Operator", 20)] * 2)
        before = client.get("/jobs/suggested-searches").json()["candidates"]

        client.request("DELETE", "/jobs", params={"below_score": 70})

        after = client.get("/jobs/suggested-searches").json()["candidates"]
        assert after == before, "hiding low scorers must not change the ranking"
        assert db.query(Match).count() == 4, "rows kept, not deleted"

    def test_hidden_jobs_still_count_toward_search_performance(self, client, db):
        _profile(db)
        _scored(db, [("Data Analyst", 90)] + [("Data Entry Clerk", 20)] * 5, query="data")
        client.request("DELETE", "/jobs", params={"below_score": 70})

        row = client.get("/jobs/search-performance").json()["performance"][0]
        assert row == {"query": "data", "volume": 6, "hits": 1, "yield": pytest.approx(0.167, abs=0.001)}

    def test_reclaims_the_description_but_keeps_the_score(self, client, db):
        _profile(db)
        db.add(JobListing(
            id=1, source="seek", source_job_id="j1", url="u", title="Warehouse Operator",
            raw_description="a very long job description",
        ))
        db.add(Match(user_id=1, job_id=1, score=20, status="new"))
        db.commit()

        client.request("DELETE", "/jobs", params={"below_score": 70})

        job, match = db.get(JobListing, 1), db.query(Match).one()
        db.refresh(job); db.refresh(match)
        assert job.raw_description is None
        assert float(match.score) == 20
        assert match.hidden_at is not None

    def test_is_idempotent(self, client, db):
        _profile(db)
        _scored(db, [("Warehouse Operator", 20)])
        assert client.request("DELETE", "/jobs", params={"below_score": 70}).json()["hidden"] == 1
        assert client.request("DELETE", "/jobs", params={"below_score": 70}).json()["hidden"] == 0

    def test_hidden_jobs_stay_out_of_the_applied_tab(self, client, db):
        _profile(db)
        _scored(db, [("Warehouse Operator", 20)])
        db.query(Match).update({"status": "applied"})
        db.commit()
        client.request("DELETE", "/jobs", params={"below_score": 70})
        assert client.get("/jobs", params={"status": "applied"}).json() == []


class TestSearchLocation:
    """The location a clicked suggestion scopes its Seek search to."""

    def test_prefers_target_location_over_current_location(self, client, db):
        _profile(db)
        profile = db.get(Profile, 1)
        profile.location = "Brisbane"        # where they live
        profile.target_location = "Melbourne"  # where they want to work
        db.commit()
        assert client.get("/jobs/suggested-searches").json()["location"] == "Melbourne"

    def test_falls_back_to_location_when_no_target_set(self, client, db):
        _profile(db)
        profile = db.get(Profile, 1)
        profile.location = "Brisbane"
        db.commit()
        assert client.get("/jobs/suggested-searches").json()["location"] == "Brisbane"

    def test_blank_target_falls_through_rather_than_winning(self, client, db):
        _profile(db)
        profile = db.get(Profile, 1)
        profile.location = "Brisbane"
        profile.target_location = "   "
        db.commit()
        assert client.get("/jobs/suggested-searches").json()["location"] == "Brisbane"

    def test_null_when_neither_is_set(self, client, db):
        _profile(db)
        assert client.get("/jobs/suggested-searches").json()["location"] is None


class TestLlmLayerIsOptIn:
    def test_llm_not_called_by_default(self, client, db):
        _profile(db)
        _scored(db, [("Data Analyst", 92)] * 3 + [("Warehouse Operator", 20)])
        with patch("app.llm.search_refine.complete_json") as mock:
            data = client.get("/jobs/suggested-searches").json()
        mock.assert_not_called()
        assert data["llm_used"] is False
        assert data["candidates"], "mined candidates are returned regardless"

    def test_use_llm_flag_calls_it_and_replaces_suggestions(self, client, db):
        _profile(db)
        _scored(db, [("Data Analyst", 92)] * 3 + [("Warehouse Operator", 20)])
        with patch(
            "app.llm.search_refine.complete_json",
            return_value={"searches": ["Business Analyst", "Reporting Analyst"]},
        ) as mock:
            data = client.get("/jobs/suggested-searches?use_llm=true").json()
        mock.assert_called_once()
        assert data["llm_used"] is True
        assert data["suggestions"] == ["Business Analyst", "Reporting Analyst"]

    def test_preference_enables_it(self, client, db):
        _profile(db)
        _scored(db, [("Data Analyst", 92)] * 3 + [("Warehouse Operator", 20)])
        client.put("/profile/1/preferences", json={"llm_search_suggestions": True})
        with patch(
            "app.llm.search_refine.complete_json",
            return_value={"searches": ["Business Analyst"]},
        ) as mock:
            data = client.get("/jobs/suggested-searches").json()
        mock.assert_called_once()
        assert data["suggestions"] == ["Business Analyst"]

    def test_result_is_cached_so_a_second_call_is_free(self, client, db):
        _profile(db)
        _scored(db, [("Data Analyst", 92)] * 3 + [("Warehouse Operator", 20)])
        client.put("/profile/1/preferences", json={"llm_search_suggestions": True})
        with patch(
            "app.llm.search_refine.complete_json",
            return_value={"searches": ["Business Analyst"]},
        ) as mock:
            client.get("/jobs/suggested-searches")
            second = client.get("/jobs/suggested-searches").json()
        mock.assert_called_once()
        assert second["suggestions"] == ["Business Analyst"]

    def test_llm_failure_falls_back_to_mined_phrases(self, client, db):
        _profile(db)
        _scored(db, [("Data Analyst", 92)] * 3 + [("Warehouse Operator", 20)])
        with patch("app.llm.search_refine.complete_json", side_effect=RuntimeError("boom")):
            data = client.get("/jobs/suggested-searches?use_llm=true").json()
        assert data["llm_used"] is False
        assert data["suggestions"] == ["Data Analyst"]
