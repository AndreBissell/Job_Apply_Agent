"""Tests for app/gaps.py (Phase 7b): remembered "no"s, sightings and the to-work-on list.

Code only: no LLM, no network. In-memory SQLite.
"""
from __future__ import annotations

import datetime
import json
import sqlite3

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import gaps
from app.db import Base
from app.models import GapDecision, GapSighting, JobListing, JobSkill, Profile, Skill
from app.retention import now_utc, utc


@pytest.fixture()
def db():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(eng, "connect")
    def _fk_on(conn, _):
        if isinstance(conn, sqlite3.Connection):
            conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(eng)
    with sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)() as session:
        session.add(Profile(id=1, name="Bob", email="b@x.com", password_hash="x"))
        session.add(Profile(id=2, name="Eve", email="e@x.com", password_hash="x"))
        session.commit()
        yield session
    Base.metadata.drop_all(eng)


_n = 0


def _job(db, title="Engineer", skills=(), checklist=None, scraped=None) -> JobListing:
    global _n
    _n += 1
    job = JobListing(
        source="seek", source_job_id=f"j{_n}", url="u", title=title, raw_description="x",
        date_scraped=scraped or now_utc(),
        requirements_checklist=json.dumps(checklist) if checklist is not None else None,
    )
    db.add(job)
    db.flush()
    for s in skills:
        db.add(JobSkill(job_id=job.id, name=s, skill_type="hard"))
    db.commit()
    return job


def _no(db, label="Power BI", key=None, user=1) -> GapDecision:
    return gaps.save_no(db, user, label=label, key=key or gaps.skill_key(label), requirement_text=f"asks {label}")


def _count(db, model=GapSighting) -> int:
    return db.scalar(select(func.count()).select_from(model))


# ---------------------------------------------------------------------------
# 1. skill_key
# ---------------------------------------------------------------------------
class TestSkillKey:
    def test_prefers_skill(self):
        assert gaps.skill_key("Power BI", "Experience building dashboards") == "power bi"

    def test_falls_back_to_wording(self):
        assert gaps.skill_key("", "Strong Communication Skills") == "strong communication skills"
        assert gaps.skill_key(None, "Some Wording") == "some wording"

    def test_normalises(self):
        assert gaps.skill_key("PowerBI") == "power bi"

    def test_empty(self):
        assert gaps.skill_key("", "") == ""
        assert gaps.skill_key(None, None) == ""
        assert gaps.skill_key("  ", "   ") == ""

    def test_long_wording_cut(self):
        key = gaps.skill_key("", "word " * 200)
        assert len(key) == gaps.MAX_KEY_LEN


# ---------------------------------------------------------------------------
# 2. skill_matches
# ---------------------------------------------------------------------------
class TestSkillMatches:
    def test_exact_after_normalisation(self):
        assert gaps.skill_matches("power bi", "PowerBI")
        assert gaps.skill_matches("python", "Python")

    def test_whole_phrase_containment(self):
        assert gaps.skill_matches("power bi", "Microsoft Power BI")
        assert gaps.skill_matches("power bi", "Power BI dashboards and reports")

    def test_not_substring_of_word(self):
        assert not gaps.skill_matches("java", "JavaScript")
        assert not gaps.skill_matches("sql", "MySQL databases")

    def test_short_keys_match_exactly_only(self):
        assert gaps.skill_matches("r", "R")
        assert not gaps.skill_matches("r", "R and Python")
        assert gaps.skill_matches("go", "Go")
        assert not gaps.skill_matches("go", "Google")
        assert not gaps.skill_matches("go", "Go programming")

    def test_empty_inputs(self):
        assert not gaps.skill_matches("", "Python")
        assert not gaps.skill_matches("python", None)
        assert not gaps.skill_matches("python", "")


# ---------------------------------------------------------------------------
# 3. save_no
# ---------------------------------------------------------------------------
class TestSaveNo:
    def test_first_no_creates_one_row(self, db):
        d = _no(db)
        assert d.id is not None and d.cleared_at is None
        assert _count(db, GapDecision) == 1
        assert d.label == "Power BI" and d.skill_key == "power bi"

    def test_second_no_adds_no_row(self, db):
        a = _no(db)
        b = _no(db)
        assert a.id == b.id
        assert _count(db, GapDecision) == 1

    def test_no_to_cleared_key_reopens_same_row(self, db):
        d = _no(db)
        gaps.clear(db, 1, d.id)
        old_created = d.created_at
        d2 = _no(db)
        assert d2.id == d.id
        assert d2.cleared_at is None
        assert utc(d2.created_at) >= utc(old_created)
        assert _count(db, GapDecision) == 1

    def test_unique_user_key(self, db):
        _no(db)
        db.add(GapDecision(user_id=1, skill_key="power bi", label="dup", created_at=now_utc()))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

    def test_same_key_other_user_ok(self, db):
        _no(db, user=1)
        _no(db, user=2)
        assert _count(db, GapDecision) == 2

    def test_blank_label_falls_back_to_key(self, db):
        d = gaps.save_no(db, 1, label="  ", key="power bi")
        assert d.label == "power bi"


# ---------------------------------------------------------------------------
# 4. seeding
# ---------------------------------------------------------------------------
class TestSeeding:
    def test_counts_existing_jobs_once_each(self, db):
        a = _job(db, "A", skills=["Power BI", "Microsoft Power BI"])  # two skill rows, one job
        b = _job(db, "B", skills=["Python"])
        d = _no(db)
        rows = list(db.scalars(select(GapSighting).where(GapSighting.gap_id == d.id)))
        assert [r.job_id for r in rows] == [a.id]
        assert rows[0].source == "seed"
        assert b.id not in {r.job_id for r in rows}

    def test_seen_at_is_job_date_scraped(self, db):
        old = now_utc() - datetime.timedelta(days=200)
        job = _job(db, "Old", skills=["Power BI"], scraped=old)
        d = _no(db)
        s = db.scalar(select(GapSighting).where(GapSighting.gap_id == d.id))
        assert abs((utc(s.seen_at) - old).total_seconds()) < 2
        item = gaps.to_work_on(db, 1)[0]
        assert item.total == 1 and item.recent == 0  # outside the 90-day window
        assert job.id == s.job_id

    def test_checklist_skill_counted_with_importance(self, db):
        cl = {"version": 3, "requirements": [{"skill": "Power BI", "importance": "essential"}]}
        job = _job(db, "Checklist", checklist=cl)  # no job_skills at all
        d = _no(db)
        s = db.scalar(select(GapSighting).where(GapSighting.gap_id == d.id))
        assert s.job_id == job.id and s.importance == "essential"

    def test_checklist_best_importance_once(self, db):
        cl = {"version": 3, "requirements": [{"skill": "Power BI", "importance": "nice_to_have"},
                                             {"skill": "PowerBI", "importance": "important"}]}
        _job(db, "Both", skills=["Power BI"], checklist=cl)
        d = _no(db)
        rows = list(db.scalars(select(GapSighting).where(GapSighting.gap_id == d.id)))
        assert len(rows) == 1 and rows[0].importance == "important"

    def test_reopening_does_not_duplicate(self, db):
        _job(db, "A", skills=["Power BI"])
        d = _no(db)
        gaps.clear(db, 1, d.id)
        _no(db)
        assert _count(db) == 1


# ---------------------------------------------------------------------------
# 5. scan hook
# ---------------------------------------------------------------------------
class TestScan:
    def test_adds_exactly_one_then_none(self, db):
        d = _no(db)
        job = _job(db, "Analyst", skills=["Power BI", "SQL"])
        assert gaps.record_scan_sightings(db, job.id) == 1
        assert gaps.record_scan_sightings(db, job.id) == 0
        rows = list(db.scalars(select(GapSighting).where(GapSighting.gap_id == d.id)))
        assert len(rows) == 1 and rows[0].source == "scan" and rows[0].job_title == "Analyst"

    def test_job_not_listing_skill(self, db):
        _no(db)
        job = _job(db, "Dev", skills=["Python"])
        assert gaps.record_scan_sightings(db, job.id) == 0
        assert _count(db) == 0

    def test_cleared_decision_not_counted(self, db):
        d = _no(db)
        gaps.clear(db, 1, d.id)
        job = _job(db, "Analyst", skills=["Power BI"])
        assert gaps.record_scan_sightings(db, job.id) == 0
        assert _count(db) == 0

    def test_no_decisions(self, db):
        job = _job(db, "Analyst", skills=["Power BI"])
        assert gaps.record_scan_sightings(db, job.id) == 0

    def test_unknown_job(self, db):
        _no(db)
        assert gaps.record_scan_sightings(db, 99999) == 0

    def test_counts_for_every_users_decision(self, db):
        _no(db, user=1)
        _no(db, user=2)
        job = _job(db, "Analyst", skills=["Power BI"])
        assert gaps.record_scan_sightings(db, job.id) == 2


# ---------------------------------------------------------------------------
# 6. record_sighting
# ---------------------------------------------------------------------------
class TestRecordSighting:
    def _rec(self, db, d, job_id=1, importance=None):
        return gaps.record_sighting(db, d, job_id=job_id, job_title="T", source="scan", importance=importance)

    def test_repeat_returns_false_and_only_upgrades(self, db):
        d = _no(db)
        assert self._rec(db, d, importance=None) is True
        assert self._rec(db, d, importance="important") is False
        s = db.scalar(select(GapSighting))
        assert s.importance == "important"
        assert self._rec(db, d, importance="essential") is False
        assert s.importance == "essential"
        assert self._rec(db, d, importance="important") is False
        assert s.importance == "essential"
        assert self._rec(db, d, importance=None) is False
        assert s.importance == "essential"
        assert _count(db) == 1

    def test_never_downgrades_nice_to_have(self, db):
        d = _no(db)
        self._rec(db, d, importance="important")
        self._rec(db, d, importance="nice_to_have")
        assert db.scalar(select(GapSighting)).importance == "important"

    def test_different_jobs_both_counted(self, db):
        d = _no(db)
        assert self._rec(db, d, job_id=1) is True
        assert self._rec(db, d, job_id=2) is True
        assert _count(db) == 2


# ---------------------------------------------------------------------------
# 7. to_work_on
# ---------------------------------------------------------------------------
def _sight(db, d, n, *, days_ago=1, importance=None, base=0):
    for i in range(n):
        db.add(GapSighting(
            gap_id=d.id, job_id=base + i + 1, source="scan", job_title=f"{d.label} job {base + i}",
            importance=importance, seen_at=now_utc() - datetime.timedelta(days=days_ago, minutes=i),
        ))
    db.commit()


class TestToWorkOn:
    def test_more_ads_ranks_higher(self, db):
        a = _no(db, "Alpha")
        b = _no(db, "Bravo")
        _sight(db, a, 2)
        _sight(db, b, 5)
        assert [i.label for i in gaps.to_work_on(db, 1)] == ["Bravo", "Alpha"]

    def test_tie_breaks_on_essential(self, db):
        a = _no(db, "Alpha")
        b = _no(db, "Bravo")
        _sight(db, a, 3, importance="nice_to_have")
        _sight(db, b, 3, importance="essential")
        items = gaps.to_work_on(db, 1)
        assert [i.label for i in items] == ["Bravo", "Alpha"]
        assert items[0].essential == 3 and items[1].essential == 0

    def test_old_sightings_in_total_not_recent(self, db):
        a = _no(db, "Alpha")
        _sight(db, a, 2, days_ago=1)
        _sight(db, a, 4, days_ago=200, base=100)
        item = gaps.to_work_on(db, 1)[0]
        assert item.total == 6 and item.recent == 2

    def test_old_sightings_do_not_count_for_essential(self, db):
        a = _no(db, "Alpha")
        _sight(db, a, 3, days_ago=200, importance="essential")
        assert gaps.to_work_on(db, 1)[0].essential == 0

    def test_days_param(self, db):
        a = _no(db, "Alpha")
        _sight(db, a, 2, days_ago=10)
        assert gaps.to_work_on(db, 1, days=5)[0].recent == 0
        assert gaps.to_work_on(db, 1, days=30)[0].recent == 2

    def test_recent_titles_newest_first_three(self, db):
        a = _no(db, "Alpha")
        for i, t in enumerate(["oldest", "old", "mid", "new", "newest"]):
            db.add(GapSighting(gap_id=a.id, job_id=i + 1, job_title=t, source="scan",
                               seen_at=now_utc() - datetime.timedelta(days=5 - i)))
        db.commit()
        assert gaps.to_work_on(db, 1)[0].recent_titles == ["newest", "new", "mid"]

    def test_cleared_excluded_but_sightings_stay(self, db):
        a = _no(db, "Alpha")
        _sight(db, a, 3)
        gaps.clear(db, 1, a.id)
        assert gaps.to_work_on(db, 1) == []
        assert _count(db) == 3

    def test_other_users_excluded(self, db):
        _no(db, "Alpha", user=2)
        assert gaps.to_work_on(db, 1) == []

    def test_as_dict_fields(self, db):
        a = _no(db, "Alpha")
        _sight(db, a, 1)
        d = gaps.to_work_on(db, 1)[0].as_dict()
        for k in ("id", "label", "total", "recent", "essential", "recent_titles", "said_no_at"):
            assert k in d
        assert isinstance(d["said_no_at"], str)


# ---------------------------------------------------------------------------
# 8. clear
# ---------------------------------------------------------------------------
class TestClear:
    def test_wrong_user(self, db):
        d = _no(db, user=1)
        assert gaps.clear(db, 2, d.id) is None
        db.refresh(d)
        assert d.cleared_at is None

    def test_unknown_id(self, db):
        assert gaps.clear(db, 1, 12345) is None

    def test_clear_twice_harmless(self, db):
        d = _no(db)
        first = gaps.clear(db, 1, d.id)
        stamp = first.cleared_at
        again = gaps.clear(db, 1, d.id)
        assert again is not None and again.cleared_at == stamp


# ---------------------------------------------------------------------------
# 9. auto_clear
# ---------------------------------------------------------------------------
class TestAutoClear:
    def test_adding_matching_skill_clears(self, db):
        d = _no(db, "Power BI")
        db.add(Skill(user_id=1, name="Microsoft Power BI"))
        db.commit()
        assert gaps.auto_clear(db, 1) == ["Power BI"]
        db.refresh(d)
        assert d.cleared_at is not None

    def test_unrelated_skill_clears_nothing(self, db):
        d = _no(db, "Power BI")
        db.add(Skill(user_id=1, name="Python"))
        db.commit()
        assert gaps.auto_clear(db, 1) == []
        db.refresh(d)
        assert d.cleared_at is None

    def test_other_users_skill_ignored(self, db):
        _no(db, "Power BI", user=1)
        db.add(Skill(user_id=2, name="Power BI"))
        db.commit()
        assert gaps.auto_clear(db, 1) == []

    def test_no_skills(self, db):
        _no(db)
        assert gaps.auto_clear(db, 1) == []


# ---------------------------------------------------------------------------
# 10. checklist_skills
# ---------------------------------------------------------------------------
class TestChecklistSkills:
    def _j(self, raw):
        return JobListing(source="s", source_job_id="x", url="u", title="t", requirements_checklist=raw)

    def test_corrupt_json(self):
        assert gaps.checklist_skills(self._j("{not json")) == []

    def test_missing(self):
        assert gaps.checklist_skills(self._j(None)) == []
        assert gaps.checklist_skills(self._j("")) == []

    def test_requirements_without_skill(self):
        raw = json.dumps({"version": 2, "requirements": [{"text": "a"}, {"skill": "", "text": "b"}]})
        assert gaps.checklist_skills(self._j(raw)) == []

    def test_non_dict_payload(self):
        assert gaps.checklist_skills(self._j("[1,2]")) == []

    def test_returns_skill_and_importance(self):
        raw = json.dumps({"requirements": [{"skill": " Power BI ", "importance": "essential"}, {"skill": ""}]})
        assert gaps.checklist_skills(self._j(raw)) == [("Power BI", "essential")]
