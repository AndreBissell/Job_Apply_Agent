"""Remembered "no" answers and the to-work-on list (docs/cover-letter-loop-plan.md §5.9).

Every "No" to an ``ask_user`` gap question is a skill the user doesn't have yet.
It is saved once (``gap_decisions``), and every ad that asks for it afterwards is
counted once (``gap_sightings``). The ranked count is the to-work-on list: the
skills worth learning are the ones the market keeps asking for.

Code only, no LLM calls. Three hooks write sightings:

    scan        after extract.py writes a job's job_skills (record_scan_sightings)
    letter_run  after match_profile, for a requirement matching a remembered "no"
                (app/llm/letter/gap_policy.py calls record_sighting)
    seed        when a "no" is first saved, every job already in the DB (seed_sightings)

Matching is by ``prefilter.normalise_skill()``: exact, or the remembered key as a
whole phrase inside a longer name ("power bi" in "Microsoft Power BI") for keys of
3+ characters, so short keys like "r" or "go" never match by accident.
"""

from __future__ import annotations

import datetime
import json
import logging
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.llm.prefilter import normalise_skill
from app.models import GapDecision, GapSighting, JobListing, JobSkill, Skill
from app.retention import now_utc, utc

logger = logging.getLogger(__name__)

WINDOW_DAYS = 90
RECENT_TITLES = 3
MIN_CONTAINS_LEN = 3  # shorter keys match exactly only
MAX_KEY_LEN = 200  # a key built from a requirement's wording is cut to this

_IMPORTANCE_RANK = {"essential": 0, "important": 1, "nice_to_have": 2}


# ---------------------------------------------------------------------------
# Keys and matching
# ---------------------------------------------------------------------------
def skill_key(skill: str | None, text: str | None = None) -> str:
    """The remembered key for a requirement: its short ``skill`` name normalised, or
    its own wording when it names no skill (a one-off ask that will rarely recur)."""
    source = (skill or "").strip() or (text or "").strip()
    return normalise_skill(source)[:MAX_KEY_LEN] if source else ""


def skill_matches(key: str, name: str | None) -> bool:
    """Does a skill/requirement ``name`` ask for the remembered ``key``?"""
    if not key or not name:
        return False
    norm = normalise_skill(name)
    if norm == key:
        return True
    if len(key) < MIN_CONTAINS_LEN:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", norm) is not None


def _higher(a: str | None, b: str | None) -> str | None:
    """The more important of two importance values (None is the least)."""
    if a is None:
        return b
    if b is None:
        return a
    return a if _IMPORTANCE_RANK.get(a, 9) <= _IMPORTANCE_RANK.get(b, 9) else b


def checklist_skills(job: JobListing) -> list[tuple[str, str]]:
    """(skill, importance) for each requirement in the job's cached analyze_job
    checklist that names a skill. Empty when there is no (v3+) checklist."""
    if not job.requirements_checklist:
        return []
    try:
        data = json.loads(job.requirements_checklist)
    except (json.JSONDecodeError, TypeError):
        return []
    out = []
    for r in data.get("requirements", []) if isinstance(data, dict) else []:
        skill = (r.get("skill") or "").strip() if isinstance(r, dict) else ""
        if skill:
            out.append((skill, r.get("importance")))
    return out


def find_decision(decisions: list[GapDecision], skill: str | None, text: str | None = None) -> GapDecision | None:
    """The active remembered "no" a requirement asks for, if any."""
    key = skill_key(skill, text)
    for d in decisions:
        if d.skill_key == key or (skill and skill_matches(d.skill_key, skill)):
            return d
    return None


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------
def active_decisions(db: Session, user_id: int | None = None) -> list[GapDecision]:
    stmt = select(GapDecision).where(GapDecision.cleared_at.is_(None))
    if user_id is not None:
        stmt = stmt.where(GapDecision.user_id == user_id)
    return list(db.scalars(stmt.order_by(GapDecision.id)))


def save_no(
    db: Session,
    user_id: int,
    *,
    label: str,
    key: str,
    requirement_text: str | None = None,
) -> GapDecision:
    """Remember a "No". A skill already on the list keeps its one row (a cleared one
    is reopened); a new one is seeded with the jobs already in the DB. Commits."""
    decision = db.scalar(
        select(GapDecision).where(GapDecision.user_id == user_id, GapDecision.skill_key == key)
    )
    if decision is None:
        decision = GapDecision(
            user_id=user_id, skill_key=key, label=label.strip() or key,
            requirement_text=requirement_text, created_at=now_utc(),
        )
        db.add(decision)
        db.flush()
        seed_sightings(db, decision)
    elif decision.cleared_at is not None:
        decision.cleared_at = None
        decision.created_at = now_utc()
        decision.requirement_text = requirement_text or decision.requirement_text
    db.commit()
    return decision


def clear(db: Session, user_id: int, gap_id: int) -> GapDecision | None:
    """Take an item off the list (e.g. after the course). Sightings are kept. Commits."""
    decision = db.get(GapDecision, gap_id)
    if decision is None or decision.user_id != user_id:
        return None
    if decision.cleared_at is None:
        decision.cleared_at = now_utc()
        db.commit()
    return decision


def auto_clear(db: Session, user_id: int) -> list[str]:
    """Clear every active "no" the profile now has a skill for (a later "Yes", or the
    skill added in the editor). Returns the cleared labels. Commits when any cleared."""
    names = list(db.scalars(select(Skill.name).where(Skill.user_id == user_id)))
    cleared = []
    for d in active_decisions(db, user_id):
        if any(skill_matches(d.skill_key, n) for n in names):
            d.cleared_at = now_utc()
            cleared.append(d.label)
    if cleared:
        db.commit()
        logger.info("auto-cleared %d to-work-on item(s): %s", len(cleared), cleared)
    return cleared


# ---------------------------------------------------------------------------
# Sightings
# ---------------------------------------------------------------------------
def record_sighting(
    db: Session,
    decision: GapDecision,
    *,
    job_id: int,
    job_title: str | None,
    source: str,
    importance: str | None = None,
    seen_at: datetime.datetime | None = None,
) -> bool:
    """Count one ad for one remembered "no", at most once per ad. A repeat sighting
    only fills in a better importance. Returns True when a new row was added.
    Flushes, never commits: the caller owns the transaction."""
    existing = db.scalar(
        select(GapSighting).where(GapSighting.gap_id == decision.id, GapSighting.job_id == job_id)
    )
    if existing is not None:
        existing.importance = _higher(existing.importance, importance)
        return False
    try:
        with db.begin_nested():
            db.add(GapSighting(
                gap_id=decision.id, job_id=job_id, job_title=job_title, importance=importance,
                source=source, seen_at=utc(seen_at) or now_utc(),
            ))
    except IntegrityError:  # another writer counted this ad first
        return False
    return True


def _job_asks(job: JobListing, skill_names: list[str], key: str) -> tuple[bool, str | None]:
    """Does this job ask for ``key``, and with what importance (from its checklist)?"""
    importance = None
    found = False
    for skill, imp in checklist_skills(job):
        if skill_matches(key, skill):
            found, importance = True, _higher(importance, imp)
    if not found:
        found = any(skill_matches(key, n) for n in skill_names)
    return found, importance


def seed_sightings(db: Session, decision: GapDecision) -> int:
    """Count every job already in the DB that asks for a newly saved "no"."""
    names_by_job: dict[int, list[str]] = {}
    for job_id, name in db.execute(select(JobSkill.job_id, JobSkill.name)):
        names_by_job.setdefault(job_id, []).append(name)
    added = 0
    jobs = db.scalars(
        select(JobListing).where(
            (JobListing.id.in_(names_by_job.keys())) | (JobListing.requirements_checklist.is_not(None))
        )
    )
    for job in jobs:
        asks, importance = _job_asks(job, names_by_job.get(job.id, []), decision.skill_key)
        if asks and record_sighting(
            db, decision, job_id=job.id, job_title=job.title, source="seed",
            importance=importance, seen_at=job.date_scraped,
        ):
            added += 1
    return added


def record_scan_sightings(db: Session, job_id: int) -> int:
    """After extraction: count this job for every active "no" (all users) its
    job_skills ask for. Re-scanning the same job adds nothing. Commits."""
    decisions = active_decisions(db)
    if not decisions:
        return 0
    job = db.get(JobListing, job_id)
    if job is None:
        return 0
    names = list(db.scalars(select(JobSkill.name).where(JobSkill.job_id == job_id)))
    added = 0
    for d in decisions:
        asks, importance = _job_asks(job, names, d.skill_key)
        if asks and record_sighting(
            db, d, job_id=job.id, job_title=job.title, source="scan",
            importance=importance, seen_at=job.date_scraped,
        ):
            added += 1
    db.commit()
    return added


# ---------------------------------------------------------------------------
# The list
# ---------------------------------------------------------------------------
@dataclass
class WorkItem:
    id: int
    label: str
    skill_key: str
    total: int  # distinct ads ever
    recent: int  # distinct ads in the window
    essential: int  # of the recent ones, rated essential
    recent_titles: list[str]
    said_no_at: datetime.datetime | None
    requirement_text: str | None

    def as_dict(self) -> dict:
        return {
            "id": self.id, "label": self.label, "skill_key": self.skill_key,
            "total": self.total, "recent": self.recent, "essential": self.essential,
            "recent_titles": self.recent_titles,
            "said_no_at": self.said_no_at.isoformat() if self.said_no_at else None,
            "requirement_text": self.requirement_text,
        }


def to_work_on(
    db: Session,
    user_id: int,
    *,
    days: int = WINDOW_DAYS,
    now: datetime.datetime | None = None,
) -> list[WorkItem]:
    """Active "no"s ranked by distinct ads in the last ``days`` days, then by how many
    of those rated it essential, then by the all-time count."""
    cutoff = (utc(now) or now_utc()) - datetime.timedelta(days=days)
    items = []
    for d in active_decisions(db, user_id):
        sightings = sorted(
            db.scalars(select(GapSighting).where(GapSighting.gap_id == d.id)),
            key=lambda s: utc(s.seen_at), reverse=True,
        )
        recent = [s for s in sightings if utc(s.seen_at) >= cutoff]
        items.append(WorkItem(
            id=d.id, label=d.label, skill_key=d.skill_key,
            total=len(sightings), recent=len(recent),
            essential=sum(1 for s in recent if s.importance == "essential"),
            recent_titles=[s.job_title for s in sightings if s.job_title][:RECENT_TITLES],
            said_no_at=utc(d.created_at), requirement_text=d.requirement_text,
        ))
    items.sort(key=lambda i: (-i.recent, -i.essential, -i.total, i.label.lower()))
    return items
