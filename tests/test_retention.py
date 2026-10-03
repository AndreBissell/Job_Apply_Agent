"""Tests for the rolling retention window, post-profile-update weighting, and
screenshot expiry (app/retention.py, app/screenshots.py).

The properties that matter most, and are asserted directly rather than hoped for:
  * the sweep is structurally incapable of deleting anything the user acted on;
  * the miner reads exactly the window the sweep keeps;
  * weights of 1.0 reduce the ranking to the unweighted formula;
  * a screenshot expires but the application record it belongs to never does.

Same in-memory SQLite (StaticPool) pattern as tests/test_search_endpoints.py.
"""

from __future__ import annotations

import io
import sqlite3
import zipfile
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.api.main as main_module
from app import retention, search_suggest
from app.api.main import app, get_db
from app.db import Base
from app.llm import search_refine
from app.models import CoverLetter, JobListing, JobSkill, Match, Profile, Skill
from app.preferences import set_preferences
from app.screenshots import downscale_png, expire_screenshots

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


def days_ago(n: float) -> datetime:
    return NOW - timedelta(days=n)


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
def db(engine):
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture()
def shots(tmp_path, monkeypatch):
    directory = tmp_path / "screenshots"
    directory.mkdir()
    monkeypatch.setattr(main_module, "_screenshots_dir", directory)
    return directory


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


def _profile(db, pid=1, **prefs):
    db.merge(Profile(id=pid, name=f"User{pid}", email=f"u{pid}@example.com", password_hash="x"))
    db.commit()
    # Floor 0 by default so the window alone decides; floor tests override it.
    set_preferences(db, pid, {"retention_floor_matches": 0, **prefs})


def _gone(db, model, pk) -> bool:
    """True if the row no longer exists. ``db.get`` can't be used for this: the
    session still holds the (expired) instance and raises rather than
    returning None."""
    return db.scalar(select(func.count()).select_from(model).where(model.id == pk)) == 0


def _match(db, title="Data Analyst", score=80, age=0, *, user=1, job=None, status="new",
           applied_at=None, scored_at=None, screenshot=None, shot_age=None):
    if job is None:
        job = JobListing(
            source="seek", source_job_id=f"j{db.query(JobListing).count() + 1}",
            url="u", title=title, raw_description="x",
        )
        db.add(job)
        db.flush()
    m = Match(
        user_id=user, job_id=job.id, score=score, status=status, applied_at=applied_at,
        created_at=days_ago(age), scored_at=scored_at,
        screenshot_path=f"screenshots/{screenshot}" if screenshot else None,
        screenshot_taken_at=days_ago(shot_age) if shot_age is not None else None,
    )
    db.add(m)
    db.commit()
    return m


# --------------------------------------------------------------------------
# The window and the sweep
# --------------------------------------------------------------------------
class TestPurge:
    def test_deletes_only_matches_outside_the_window(self, db, shots):
        _profile(db)
        old_id = _match(db, age=200).id
        recent_id = _match(db, age=10).id
        purged, _jobs, _more = retention.purge_old_matches(db, 1, shots, NOW)
        assert purged == 1
        assert _gone(db, Match, old_id)
        assert not _gone(db, Match, recent_id)

    def test_never_touches_an_applied_match_however_old(self, db, shots):
        _profile(db)
        applied = _match(db, age=900, status="applied", applied_at=days_ago(899))
        retention.purge_old_matches(db, 1, shots, NOW)
        assert db.get(Match, applied.id) is not None

    def test_never_touches_a_match_the_user_acted_on(self, db, shots):
        """A whitelist on status, not 'anything but applied'."""
        _profile(db)
        kept = [
            _match(db, age=900, status=s)
            for s in ("shortlisted", "interviewing", "rejected", "withdrawn")
        ]
        # applied_at set but status drifted: still protected.
        kept.append(_match(db, age=900, status="new", applied_at=days_ago(899)))
        retention.purge_old_matches(db, 1, shots, NOW)
        assert all(db.get(Match, m.id) is not None for m in kept)

    def test_hidden_matches_are_purged_like_any_other(self, db, shots):
        """Soft delete hides from the UI; it is not an exemption from the window."""
        _profile(db)
        hidden = _match(db, age=200)
        hidden.hidden_at = days_ago(199)
        db.commit()
        hidden_id = hidden.id
        retention.purge_old_matches(db, 1, shots, NOW)
        assert _gone(db, Match, hidden_id)

    def test_recency_floor_keeps_the_newest_n_even_when_all_are_old(self, db, shots):
        _profile(db, retention_floor_matches=3)
        ids = [_match(db, age=200 + i).id for i in range(6)]  # ids[0] is newest
        retention.purge_old_matches(db, 1, shots, NOW)
        survivors = {m.id for m in db.query(Match).all()}
        assert survivors == set(ids[:3])

    def test_fewer_matches_than_the_floor_purges_nothing(self, db, shots):
        _profile(db, retention_floor_matches=50)
        _match(db, age=400)
        _match(db, age=300)
        assert retention.purge_old_matches(db, 1, shots, NOW)[0] == 0
        assert db.query(Match).count() == 2

    def test_cascades_to_cover_letter_and_orphaned_job(self, db, shots):
        _profile(db)
        m = _match(db, age=200)
        job_id = m.job_id
        db.add(CoverLetter(match_id=m.id, generated_content="hi"))
        db.add(JobSkill(job_id=job_id, name="sql"))
        db.commit()
        purged, jobs, _ = retention.purge_old_matches(db, 1, shots, NOW)
        assert (purged, jobs) == (1, 1)
        assert db.query(CoverLetter).count() == 0
        assert db.query(JobSkill).count() == 0
        assert _gone(db, JobListing, job_id)

    def test_keeps_a_job_another_profile_still_matches(self, db, shots):
        """job_listings is a shared pool: only orphaned jobs are deleted."""
        _profile(db)
        _profile(db, pid=2)
        mine = _match(db, age=200)
        mine_id, job_id = mine.id, mine.job_id
        _match(db, user=2, job=db.get(JobListing, job_id), age=5)
        retention.purge_old_matches(db, 1, shots, NOW)
        assert _gone(db, Match, mine_id)
        assert not _gone(db, JobListing, job_id)

    def test_removes_the_screenshot_file_of_a_purged_match(self, db, shots):
        _profile(db)
        (shots / "old.png").write_bytes(b"png")
        _match(db, age=200, screenshot="old.png", shot_age=199)
        retention.purge_old_matches(db, 1, shots, NOW)
        assert not (shots / "old.png").exists()

    def test_batch_cap_reports_more_pending(self, db, shots):
        _profile(db)
        for _ in range(5):
            _match(db, age=200)
        purged, _jobs, more = retention.purge_old_matches(db, 1, shots, NOW, batch=3)
        assert (purged, more) == (3, True)
        assert retention.purge_old_matches(db, 1, shots, NOW, batch=3)[2] is False

    def test_baseline_survives_a_purge_when_age_is_unrelated_to_score(self, db, shots):
        """The property that makes age-based deletion safe: unlike deleting by
        score, dropping a time slice does not move the baseline."""
        _profile(db)
        for age in (200, 220, 240, 5, 10, 15):
            for score in (40, 60, 80, 90):
                _match(db, score=score, age=age)

        def baseline():
            cutoff = retention.effective_cutoff(db, 1, NOW, {"retention_floor_matches": 0})
            scores = [float(m.score) for m in db.query(Match).all()
                      if retention.utc(m.created_at) >= cutoff]
            return sum(scores) / len(scores)

        before = baseline()
        retention.purge_old_matches(db, 1, shots, NOW)
        assert db.query(Match).count() == 12
        assert baseline() == pytest.approx(before)


class TestSweepScheduling:
    def test_runs_once_a_day(self, db, shots):
        _profile(db)
        first = retention.run_sweep_if_due(db, 1, shots, NOW)
        assert first is not None
        assert retention.run_sweep_if_due(db, 1, shots, NOW + timedelta(hours=3)) is None
        assert retention.run_sweep_if_due(db, 1, shots, NOW + timedelta(hours=25)) is not None

    def test_a_partial_batch_does_not_mark_the_sweep_done(self, db, shots, monkeypatch):
        _profile(db)
        for _ in range(3):
            _match(db, age=200)
        monkeypatch.setattr(retention, "PURGE_BATCH", 2)
        # purge_old_matches binds `batch` at definition time, so drive it directly.
        monkeypatch.setattr(
            retention, "purge_old_matches",
            lambda db_, pid, d, now=None: (2, 2, True),
        )
        retention.run_sweep_if_due(db, 1, shots, NOW)
        # Not stamped, so the very next tick continues the backlog.
        assert retention.run_sweep_if_due(db, 1, shots, NOW + timedelta(seconds=30)) is not None

    def test_invalid_tunables_fall_back_to_defaults(self):
        bad = {"retention_window_days": -5, "screenshot_ttl_days": "soon",
               "stale_profile_weight": 7, "retention_floor_matches": True}
        assert retention.window_days(bad) == retention.DEFAULT_WINDOW_DAYS
        assert retention.screenshot_ttl_days(bad) == retention.DEFAULT_SCREENSHOT_TTL_DAYS
        assert retention.stale_weight(bad) == retention.DEFAULT_STALE_WEIGHT
        assert retention.floor_matches(bad) == retention.DEFAULT_FLOOR_MATCHES


# --------------------------------------------------------------------------
# Post-profile-update weighting
# --------------------------------------------------------------------------
class TestProfileRevisionWeights:
    def test_no_revision_means_full_weight(self):
        assert retention.match_weight(None, days_ago(5), None) == 1.0

    def test_match_scored_before_the_edit_is_discounted(self):
        revised = days_ago(10)
        assert retention.match_weight(days_ago(30), days_ago(30), revised, 0.35) == 0.35
        assert retention.match_weight(days_ago(2), days_ago(30), revised, 0.35) == 1.0

    def test_rescoring_clears_staleness_even_though_created_at_is_old(self):
        """The reason scored_at exists: created_at alone would leave a
        re-scored match stale forever."""
        revised = days_ago(10)
        assert retention.match_weight(days_ago(1), days_ago(60), revised, 0.35) == 1.0

    def test_legacy_row_without_scored_at_reads_as_created_at(self):
        revised = days_ago(10)
        assert retention.match_weight(None, days_ago(60), revised, 0.35) == 0.35

    def test_revision_is_derived_from_child_table_timestamps(self, db):
        _profile(db)
        assert retention.profile_revised_at(db, 1) is None
        skill = Skill(user_id=1, name="Python")
        db.add(skill)
        db.commit()
        assert retention.profile_revised_at(db, 1) is not None

    def test_explicit_marker_covers_a_deletion(self, db):
        _profile(db)
        marker = days_ago(1)
        db.get(Profile, 1).profile_revised_at = marker
        db.commit()
        assert retention.profile_revised_at(db, 1) == marker


class TestRelativeWeights:
    def test_all_stale_means_no_discount(self):
        assert retention.relative_weights([0.35, 0.35, 0.35]) == [1.0, 1.0, 1.0]

    def test_a_fresh_match_switches_the_discount_on(self):
        assert retention.relative_weights([0.35, 1.0, 0.35]) == [0.35, 1.0, 0.35]

    def test_empty_is_fine(self):
        assert retention.relative_weights([]) == []

    def test_a_profile_edit_with_no_new_matches_leaves_suggestions_unchanged(self, client, db):
        """Regression: re-seeding a profile made every match stale, and the
        uniform discount re-ordered the ranking with no new information."""
        _profile(db)
        rows = ([("Data Analyst", 90)] * 3 + [("Software Engineer", 75)] * 8
                + [("Support Officer", 40)] * 4 + [("Clerk Assistant", 55)] * 3)
        for title, score in rows:
            _match(db, title=title, score=score, age=30)

        before = client.get("/jobs/suggested-searches?profile_id=1").json()["candidates"]
        db.add(Skill(user_id=1, name="Rust"))  # profile edited AFTER all of them were scored
        db.commit()
        after = client.get("/jobs/suggested-searches?profile_id=1").json()["candidates"]

        assert [c["phrase"] for c in after] == [c["phrase"] for c in before]
        assert [c["rank"] for c in after] == [c["rank"] for c in before]


class TestWeightedRanking:
    TITLES = [
        ("Data Analyst", 90), ("Data Analyst", 88), ("Data Analyst", 92),
        ("Support Officer", 40), ("Support Officer", 35), ("Clerk Assistant", 50),
        ("Software Engineer", 60), ("Software Engineer", 62),
    ]

    def test_unit_weights_reproduce_the_unweighted_ranking_exactly(self):
        plain = search_suggest.rank_phrases(self.TITLES)
        weighted = search_suggest.rank_phrases([(t, s, 1.0) for t, s in self.TITLES])
        assert [(p.phrase, p.rank, p.shrunk_mean) for p in plain] == \
               [(p.phrase, p.rank, p.shrunk_mean) for p in weighted]

    def test_stale_evidence_ranks_below_the_same_evidence_when_fresh(self):
        fresh = search_suggest.rank_phrases([(t, s, 1.0) for t, s in self.TITLES])
        stale = search_suggest.rank_phrases(
            [(t, s, 0.35 if t == "Data Analyst" else 1.0) for t, s in self.TITLES]
        )
        rank = lambda ranked: next(p.rank for p in ranked if p.phrase == "data analyst")
        assert rank(stale) < rank(fresh)

    def test_baseline_is_weighted_with_the_same_weights(self):
        """If only phrase means were discounted, a uniformly stale corpus would
        drift every phrase down. Discounting everything equally must not change
        the shrunk mean's relationship to the baseline."""
        uniform = search_suggest.rank_phrases([(t, s, 0.5) for t, s in self.TITLES])
        plain = search_suggest.rank_phrases(self.TITLES)
        assert [p.phrase for p in uniform] == [p.phrase for p in plain]
        # ...though halving all evidence halves its support, so ranks shrink.
        assert all(u.rank < p.rank for u, p in zip(uniform, plain))

    def test_zero_weight_rows_are_ignored(self):
        assert search_suggest.rank_phrases([("Data Analyst", 90, 0.0)]) == []


class TestEndpointUsesWindowAndWeights:
    def _seed(self, db, rows):
        for title, score, age, kw in rows:
            _match(db, title=title, score=score, age=age, **kw)

    def test_fresh_evidence_outranks_stale_evidence_after_a_profile_edit(self, client, db):
        _profile(db)
        # Same shape of evidence for two roles; only the scoring date differs.
        scored_before_edit = days_ago(40)
        scored_after_edit = days_ago(2)
        for _ in range(4):
            _match(db, title="Data Analyst", score=88, age=41, scored_at=scored_before_edit)
            _match(db, title="Software Engineer", score=88, age=3, scored_at=scored_after_edit)
        for _ in range(4):
            _match(db, title="Support Officer", score=40, age=20, scored_at=scored_after_edit)
        db.add(Skill(user_id=1, name="Rust"))  # profile edit "now"
        db.commit()
        skill = db.query(Skill).one()
        skill.updated_at = days_ago(10)
        db.commit()

        out = client.get("/jobs/suggested-searches?profile_id=1").json()
        cands = [c["phrase"] for c in out["candidates"]]
        assert cands.index("software engineer") < cands.index("data analyst")

    def test_matches_outside_the_window_do_not_feed_the_miner(self, client, db):
        """An old applied match survives the sweep but must not be mined."""
        _profile(db)
        for _ in range(6):
            _match(db, title="Rocket Scientist", score=99, age=400,
                   status="applied", applied_at=days_ago(399))
        for _ in range(4):
            _match(db, title="Data Analyst", score=85, age=10)
            _match(db, title="Support Officer", score=30, age=10)
        out = client.get("/jobs/suggested-searches?profile_id=1").json()
        assert "rocket scientist" not in [c["phrase"] for c in out["candidates"]]

    def test_everything_outside_the_window_falls_back_to_the_profile(self, client, db):
        _profile(db)
        db.get(Profile, 1).target_role = "Data Engineer"
        db.commit()
        for _ in range(5):
            _match(db, title="Data Analyst", score=90, age=300)
        out = client.get("/jobs/suggested-searches?profile_id=1").json()
        assert out["suggestions"] == ["Data Engineer"]
        assert out["candidates"] == []


class TestLlmCacheFollowsTheProfile:
    def test_shrinking_corpus_also_triggers_a_refresh(self):
        cached = {"searches": ["x"], "match_count": 100}
        assert search_refine.should_refresh(cached, 85) is True
        assert search_refine.should_refresh(cached, 95) is False


# --------------------------------------------------------------------------
# Screenshots
# --------------------------------------------------------------------------
class TestScreenshotExpiry:
    def test_expired_file_removed_but_the_application_record_survives(self, db, shots):
        _profile(db)
        (shots / "a.png").write_bytes(b"png")
        m = _match(db, status="applied", applied_at=days_ago(40), age=40,
                   screenshot="a.png", shot_age=40)
        n = expire_screenshots(db, shots, NOW - timedelta(days=30))
        assert n == 1
        assert not (shots / "a.png").exists()
        db.refresh(m)
        assert m.screenshot_path is None
        assert m.screenshot_taken_at is not None  # "captured, since expired"
        assert m.status == "applied" and m.applied_at is not None

    def test_screenshot_inside_the_ttl_is_kept(self, db, shots):
        _profile(db)
        (shots / "b.png").write_bytes(b"png")
        m = _match(db, screenshot="b.png", shot_age=10)
        assert expire_screenshots(db, shots, NOW - timedelta(days=30)) == 0
        assert (shots / "b.png").exists()
        db.refresh(m)
        assert m.screenshot_path is not None

    def test_a_missing_file_does_not_break_the_sweep(self, db, shots):
        _profile(db)
        _match(db, screenshot="ghost.png", shot_age=90)
        assert expire_screenshots(db, shots, NOW - timedelta(days=30)) == 1

    def test_sweep_expires_screenshots_using_the_configured_ttl(self, db, shots):
        _profile(db, screenshot_ttl_days=5)
        (shots / "c.png").write_bytes(b"png")
        _match(db, screenshot="c.png", shot_age=6)
        result = retention.run_sweep(db, 1, shots, NOW)
        assert result.screenshots_expired == 1


def _png(width: int, height: int = 100) -> bytes:
    buf = io.BytesIO()
    Image.linear_gradient("L").resize((width, height)).convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


class TestDownscale:
    def test_wide_image_is_resized_and_smaller(self):
        big = _png(2400, 1200)
        out = downscale_png(big)
        assert Image.open(io.BytesIO(out)).width == 1200
        assert len(out) < len(big)

    def test_small_image_is_returned_untouched(self):
        small = _png(400)
        assert downscale_png(small) == small

    def test_garbage_is_returned_untouched_rather_than_lost(self):
        assert downscale_png(b"not a png") == b"not a png"

    def test_upload_endpoint_stores_the_downscaled_image(self, client, db, shots):
        import base64
        _profile(db)
        m = _match(db)
        big = _png(2400, 1200)
        res = client.post(
            f"/jobs/{m.job_id}/screenshot?profile_id=1",
            json={"data_url": "data:image/png;base64," + base64.b64encode(big).decode()},
        )
        assert res.status_code == 200
        stored = next(shots.iterdir()).read_bytes()
        assert Image.open(io.BytesIO(stored)).width == 1200


# --------------------------------------------------------------------------
# Evidence export + expiry surfaced in /jobs
# --------------------------------------------------------------------------
class TestEvidenceExport:
    def _zip(self, client):
        res = client.get("/jobs/evidence-export?profile_id=1")
        assert res.status_code == 200
        assert res.headers["content-type"] == "application/zip"
        return zipfile.ZipFile(io.BytesIO(res.content))

    def test_zip_holds_csv_and_surviving_screenshots(self, client, db, shots):
        _profile(db)
        (shots / "keep.png").write_bytes(b"png-bytes")
        _match(db, title="Kept", status="applied", applied_at=days_ago(2), screenshot="keep.png", shot_age=2)
        _match(db, title="Gone", status="applied", applied_at=days_ago(60), age=60, shot_age=45)
        _match(db, title="Bare", status="applied", applied_at=days_ago(1))
        _match(db, title="Not applied", status="new")

        z = self._zip(client)
        assert z.read("screenshots/keep.png") == b"png-bytes"
        csv_text = z.read("applied-jobs.csv").decode()
        assert "Not applied" not in csv_text
        rows = {line.split(",")[1]: line for line in csv_text.splitlines()[1:]}
        assert ",yes," in rows["Kept"]
        assert ",expired," in rows["Gone"]
        assert ",no," in rows["Bare"]

    def test_spreadsheet_formulas_in_titles_are_neutralised(self, client, db):
        _profile(db)
        _match(db, title="=HYPERLINK(\"http://evil\")", status="applied", applied_at=days_ago(1))
        text = self._zip(client).read("applied-jobs.csv").decode()
        assert "'=HYPERLINK" in text

    def test_jobs_endpoint_reports_when_the_screenshot_expires(self, client, db, shots):
        _profile(db)
        (shots / "x.png").write_bytes(b"png")
        _match(db, status="applied", applied_at=days_ago(1), screenshot="x.png", shot_age=1)
        job = client.get("/jobs?profile_id=1&status=applied").json()[0]
        taken = retention.utc(datetime.fromisoformat(job["screenshot_taken_at"]))
        expires = retention.utc(datetime.fromisoformat(job["screenshot_expires_at"]))
        assert (expires - taken).days == 30
