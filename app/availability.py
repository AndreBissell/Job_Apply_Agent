"""Is this listing still advertised? Candidate choice, verdicts and the "seen live" gate.

The backend NEVER visits Seek (Seek Access Policy in CLAUDE.md). The side panel does the
visiting, one background tab at a time (extension/sidebar_availability.js), and reports what
it saw. This module is the other half, all plain database logic:

- ``next_candidate``: which stored listing the panel should re-visit next;
- ``record_verdict``: what a visit means (live / missing / unknown) and the two-strike rule;
- ``llm_ready``: the gate the idle loop and the letter picker put in front of every LLM
  call, so a model is never paid to score or write for a listing nobody has seen live lately.

Verdicts (decided with the user, 2026-10-13):
- ``live``     the page showed the description. Stamps ``last_seen_live_at``, resets the miss
               count and un-expires the job.
- ``missing``  no description. One miss is only a strike; ``MISSES_TO_EXPIRE`` of them (on
               separate visits, at least ``RECHECK_AFTER_MISS`` apart) flag the job. Never an
               applied job (Centrelink evidence).
- ``unknown``  the visit proved nothing (challenge page, redirect, tab error, or a miss the
               panel could not corroborate). Only stamps ``availability_checked_at`` so the job
               moves to the back of the queue.

Time columns are compared with aware-UTC values, the same way app/retention.py does.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.models import CoverLetter, JobListing, Match
from app.preferences import availability_checks_enabled

# LLM work (quick screen, extraction, matching, a letter) only runs on a job seen live this
# recently. A fresh capture always passes; a backlog job has to be re-checked first.
FRESH_FOR = timedelta(hours=24)

# A job is due a re-check exactly when it stops being fresh enough for LLM work. Defined AS
# FRESH_FOR, not as its own number: when the two differed (3 days vs 24 h) a job could be too
# stale for a letter yet not due a check (docs/availability-fast-mode-plan.md, D2).
RECHECK_AFTER = FRESH_FOR
# A job with a strike waits this long: the second strike must come from a separate visit.
RECHECK_AFTER_MISS = timedelta(days=1)

MISSES_TO_EXPIRE = 2

# Page loads per rolling 24 hours, fast mode and the background trickle together, counted from
# the verdicts themselves. A policy cap (CLAUDE.md, "Availability re-checks"):
# tests/test_scan_policy.py pins it. Raised 24 -> 100 on purpose (plan D5, 2026-11-17).
DAILY_CAP = 100

# A scored job WITHOUT a letter is checked only from this score up (the long tail under it is
# folded away in the list). A job with a letter is checked at any score.
MIN_DISPLAY_SCORE = 60

VERDICTS = ("live", "missing", "unknown")

# The queue's three groups, in order (plan D1). The label goes to the panel for its log.
GROUP_LETTER = "letter"        # scored, has a cover letter: any score
GROUP_NO_LETTER = "no_letter"  # scored, no letter yet: MIN_DISPLAY_SCORE and up
GROUP_UNSCORED = "unscored"    # no match for this profile yet
_GROUP_NAMES = {1: GROUP_LETTER, 2: GROUP_NO_LETTER, 3: GROUP_UNSCORED}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# The gate in front of every LLM call
# ---------------------------------------------------------------------------
def llm_ready(enabled: bool, now: datetime | None = None):
    """SQL condition: this job may be sent to a model.

    Always: not flagged expired. With the availability checks on: also seen live within
    ``FRESH_FOR``. A job created before the checks existed was backfilled from its capture
    time, so old backlog is "stale" and gets re-checked before it costs anything.
    """
    clause = JobListing.expired_detected_at.is_(None)
    if enabled:
        clause = and_(clause, JobListing.last_seen_live_at >= (now or now_utc()) - FRESH_FOR)
    return clause


def llm_gate(db: Session, profile_id: int, now: datetime | None = None):
    """``llm_ready`` for this profile's current setting."""
    return llm_ready(availability_checks_enabled(db, profile_id), now)


# ---------------------------------------------------------------------------
# Choosing what to re-visit
# ---------------------------------------------------------------------------
def _due(now: datetime):
    """Not seen live or re-checked within ``RECHECK_AFTER`` (a job with a strike waits
    ``RECHECK_AFTER_MISS`` after its last check)."""
    checked, seen = JobListing.availability_checked_at, JobListing.last_seen_live_at
    checked_ok = or_(
        checked.is_(None),
        and_(JobListing.availability_misses == 0, checked < now - RECHECK_AFTER),
        and_(JobListing.availability_misses > 0, checked < now - RECHECK_AFTER_MISS),
    )
    return and_(checked_ok, or_(seen.is_(None), seen < now - RECHECK_AFTER))


def remaining_today(db: Session, now: datetime | None = None) -> int:
    now = now or now_utc()
    used = db.scalar(
        select(func.count()).select_from(JobListing)
        .where(JobListing.availability_checked_at >= now - timedelta(hours=24))
    ) or 0
    return max(0, DAILY_CAP - used)


def _queue(profile_id: int):
    """Every listing that may be re-checked, in the plan's D1 order, due or not, as a
    ``select(JobListing, group number)``. The ONE definition of the order: callers add the
    "due" filter, exclusions and a limit.

    Never offered: no stored description (a job we never captured has no baseline, so "no
    description" would mean nothing), flagged expired, applied (by anyone: Centrelink
    evidence), or hidden by this profile.
    1. ``letter``: scored, with a cover letter, at ANY score (a letter is the strongest sign
       the user means to apply), highest score first.
    2. ``no_letter``: scored, no letter, score >= MIN_DISPLAY_SCORE, highest score first.
       Equal scores in groups 1-2: lower job id first.
    3. ``unscored``: no match for this profile yet, newest capture first.
    """
    # Outer joins: a job with no match (group 3) must still come back. They can't repeat a
    # job: matches is unique on (user_id, job_id) and cover_letters on match_id.
    my_match = and_(Match.job_id == JobListing.id, Match.user_id == profile_id)
    scored = Match.id.isnot(None)
    has_letter = CoverLetter.id.isnot(None)
    group = case((has_letter, 1), (scored, 2), else_=3)
    # Applied by ANY user, so a subquery rather than the joined row (which is this profile's).
    applied_jobs = select(Match.job_id).where(Match.applied_at.isnot(None))
    return (
        select(JobListing, group)
        .outerjoin(Match, my_match)
        .outerjoin(CoverLetter, CoverLetter.match_id == Match.id)
        .where(JobListing.raw_description.isnot(None))
        .where(JobListing.expired_detected_at.is_(None))
        .where(JobListing.id.notin_(applied_jobs))
        .where(or_(~scored, Match.hidden_at.is_(None)))
        .where(or_(~scored, has_letter, Match.score >= MIN_DISPLAY_SCORE))
        .order_by(
            group,
            # Empty in group 3, so it doesn't reorder it. nulls_last: Postgres would otherwise
            # put an empty score FIRST in a descending sort, SQLite last.
            Match.score.desc().nulls_last(),
            case((group == 3, JobListing.date_scraped), else_=None).desc(),  # group 3 only
            JobListing.id,
        )
    )


def next_candidate(
    db: Session, profile_id: int, exclude: list[int] | None = None, now: datetime | None = None,
) -> tuple[JobListing, str] | None:
    """The first listing in the queue (``_queue``) that is due a re-check, with its group
    label (``GROUP_*``), or None. ``exclude``: job ids the panel already tried this session."""
    now = now or now_utc()
    stmt = _queue(profile_id).where(_due(now))
    if exclude:
        stmt = stmt.where(JobListing.id.notin_(exclude))
    row = db.execute(stmt.limit(1)).first()
    return (row[0], _GROUP_NAMES[row[1]]) if row is not None else None


# ---------------------------------------------------------------------------
# Recording what a visit found
# ---------------------------------------------------------------------------
def is_applied(db: Session, job_id: int) -> bool:
    """True when anyone's match for this job is applied. Applied jobs are Centrelink
    evidence: they are never flagged expired and never re-visited."""
    return db.scalar(
        select(Match.id).where(Match.job_id == job_id, Match.applied_at.isnot(None)).limit(1)
    ) is not None


def mark_live(job: JobListing, now: datetime | None = None) -> None:
    """The listing was seen live right now: un-expire it and clear its strikes.
    The caller commits."""
    job.last_seen_live_at = now or now_utc()
    job.availability_misses = 0
    job.expired_detected_at = None


def flag_expired(db: Session, job: JobListing, now: datetime | None = None) -> bool:
    """Flag a job expired unless it is applied. True if it is now flagged. The caller commits."""
    if is_applied(db, job.id):
        return False
    if job.expired_detected_at is None:
        job.expired_detected_at = now or now_utc()
    return True


def record_verdict(db: Session, job_id: int, verdict: str, now: datetime | None = None) -> dict:
    """Apply one re-check verdict. Raises LookupError for an unknown job, ValueError for an
    unknown verdict. Returns what happened, for the panel's log."""
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}")
    job = db.get(JobListing, job_id)
    if job is None:
        raise LookupError(f"Job {job_id} not found")
    now = now or now_utc()
    job.availability_checked_at = now
    note = None

    if verdict == "live":
        mark_live(job, now)
    elif verdict == "missing":
        if job.raw_description is None:
            note = "never_captured"  # no baseline: a missing description proves nothing
        elif is_applied(db, job_id):
            note = "applied"  # evidence: never flagged
        else:
            job.availability_misses = (job.availability_misses or 0) + 1
            if job.availability_misses >= MISSES_TO_EXPIRE and job.expired_detected_at is None:
                job.expired_detected_at = now
    db.commit()
    return {
        "job_id": job_id,
        "verdict": verdict,
        "misses": job.availability_misses,
        "expired": job.expired_detected_at is not None,
        "note": note,
    }
