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
from app.preferences import availability_checks_enabled, letter_settings

# LLM work (quick screen, extraction, matching, a letter) only runs on a job seen live this
# recently. A fresh capture always passes; a backlog job has to be re-checked first.
FRESH_FOR = timedelta(hours=24)

# How long before a job is re-checked: a job nobody is waiting on, a job the LLM pipeline is
# waiting on (it got no verdict yet), and a job with a strike against it (the second strike
# must come from a separate visit, a day later).
RECHECK_AFTER = timedelta(days=3)
RETRY_BLOCKING_AFTER = timedelta(hours=6)
RECHECK_AFTER_MISS = timedelta(days=1)

MISSES_TO_EXPIRE = 2

# Page loads per rolling 24 hours, counted from the verdicts themselves. A policy cap
# (CLAUDE.md, "Availability re-checks"): tests/test_scan_policy.py pins it.
DAILY_CAP = 24

# Only jobs the user can see in the main list (the long tail under this is folded away) are
# re-checked for their own sake. Jobs the LLM pipeline is waiting on are checked at any score.
MIN_DISPLAY_SCORE = 60

VERDICTS = ("live", "missing", "unknown")
TIER_BLOCKING = "blocking"  # the LLM pipeline is waiting on a sighting
TIER_LISTED = "listed"      # on the user's list; keep it honest


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
def _due(gap: timedelta, now: datetime):
    """Not seen or re-checked within ``gap`` (a job with a strike waits ``RECHECK_AFTER_MISS``)."""
    checked, seen = JobListing.availability_checked_at, JobListing.last_seen_live_at
    checked_ok = or_(
        checked.is_(None),
        and_(JobListing.availability_misses == 0, checked < now - gap),
        and_(JobListing.availability_misses > 0, checked < now - RECHECK_AFTER_MISS),
    )
    return and_(checked_ok, or_(seen.is_(None), seen < now - gap))


def remaining_today(db: Session, now: datetime | None = None) -> int:
    now = now or now_utc()
    used = db.scalar(
        select(func.count()).select_from(JobListing)
        .where(JobListing.availability_checked_at >= now - timedelta(hours=24))
    ) or 0
    return max(0, DAILY_CAP - used)


def next_candidate(
    db: Session, profile_id: int, exclude: list[int] | None = None, now: datetime | None = None,
) -> tuple[JobListing, str] | None:
    """The next listing to re-visit and why, or None.

    Always needs a stored description (a job we never captured has no baseline, so "no
    description" would mean nothing), no expiry flag, and no applied match.
    1. ``blocking``: the LLM pipeline is waiting. Not matched yet, or matched at or above the
       auto-letter score with no letter, and not seen live within ``FRESH_FOR``. Newest first:
       a recent ad is the likelier to be live, and old ones cost nothing while they wait.
    2. ``listed``: an unhidden, unapplied match on the user's list (score >= MIN_DISPLAY_SCORE),
       letter-ready first, then by score.
    """
    now = now or now_utc()
    exclude = exclude or []
    # A subquery, not ``matches.any()``: the listed query below joins ``matches`` itself, and
    # an EXISTS on the same table would correlate to that join instead of any applied match.
    applied_jobs = select(Match.job_id).where(Match.applied_at.isnot(None))
    base = (
        select(JobListing)
        .where(JobListing.raw_description.isnot(None))
        .where(JobListing.expired_detected_at.is_(None))
        .where(JobListing.id.notin_(applied_jobs))
    )
    if exclude:
        base = base.where(JobListing.id.notin_(exclude))

    auto_min = letter_settings(db, profile_id)["auto_min_score"]
    mine = Match.user_id == profile_id
    needs_letter = and_(
        mine, Match.hidden_at.is_(None), Match.score >= auto_min,
        ~Match.cover_letter.has(),
    )
    stale = or_(
        JobListing.last_seen_live_at.is_(None), JobListing.last_seen_live_at < now - FRESH_FOR,
    )
    waiting = or_(~JobListing.matches.any(mine), JobListing.matches.any(needs_letter))
    job = db.scalars(
        base.where(waiting).where(stale).where(_due(RETRY_BLOCKING_AFTER, now))
        .order_by(JobListing.date_scraped.desc()).limit(1)
    ).first()
    if job is not None:
        return job, TIER_BLOCKING

    has_letter = select(CoverLetter.id).where(CoverLetter.match_id == Match.id).exists()
    job = db.scalars(
        base.join(Match, Match.job_id == JobListing.id)
        .where(mine, Match.hidden_at.is_(None), Match.score >= MIN_DISPLAY_SCORE)
        .where(_due(RECHECK_AFTER, now))
        .order_by(case((has_letter, 0), else_=1), Match.score.desc(), JobListing.id)
        .limit(1)
    ).first()
    return (job, TIER_LISTED) if job is not None else None


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
