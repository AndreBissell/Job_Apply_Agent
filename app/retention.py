"""Rolling data retention + post-profile-update weighting for the suggestion miner.

Two problems, one module, because they share a clock:

**Retention.** Every scored match is evidence for the search-suggestion miner
(``app/search_suggest.py``), so matches are kept — but only for a rolling
window, then permanently deleted. Deleting by AGE is safe where deleting by
SCORE is not: age is uncorrelated with score, so dropping a time slice leaves
the surviving distribution the same shape and the baseline barely moves. (The
bulk "hide below score" action is the opposite — score-correlated — which is
why that one is a soft delete; see ``matches.hidden_at``.)

**Profile drift.** A match scored before the user edited their experience was
scored against a *different profile*, so it is wrong rather than merely old.
Those matches are down-weighted (``match_weight``) instead of dropped.

The miner's READ window and the sweep's DELETE window are the same function
(``effective_cutoff``) so they cannot drift apart. That matters: applied
matches are never deleted and skew high-scoring, so if the miner read
"everything that survives" its baseline would creep upward forever.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import Experience, JobListing, Match, Profile, Qualification, Skill
from app.preferences import get_preferences, set_preferences
from app.screenshots import expire_screenshots, unlink_screenshot

logger = logging.getLogger(__name__)

# Data older than this is deleted, unless it is inside the recency floor.
# ~4 months: long enough that a quiet stretch doesn't starve the miner, short
# enough that the corpus reflects the market and the profile as they are now.
DEFAULT_WINDOW_DAYS = 122

# The newest N matches are always kept (and always read by the miner)
# regardless of age. Without it, a month of not searching would age the whole
# corpus out and leave the miner with nothing. 150 matches is only ~375 KB.
DEFAULT_FLOOR_MATCHES = 150

# Screenshot files are deleted this long after capture. Shorter than the match
# window because they are the only heavy artefact (~1 MB each) and the user
# uploads them to Centrelink straight away.
DEFAULT_SCREENSHOT_TTL_DAYS = 30

# Weight of a match scored BEFORE the profile last changed, against 1.0 for one
# scored after. Binary on purpose: "scored against the current profile" or not
# is the honest distinction; anything finer would invent precision.
DEFAULT_STALE_WEIGHT = 0.35

SWEEP_INTERVAL = timedelta(hours=24)

# Matches deleted per sweep run — keeps a large first-time backlog from holding
# the single worker thread for long. A full batch means "more to do", and the
# sweep is then not marked complete so the next loop iteration continues it.
PURGE_BATCH = 500

# Only matches in this status are ever purged. A whitelist, not "anything but
# applied": every other status (shortlisted, interviewing, rejected, ...) means
# the user acted on the job, and applied matches are Centrelink evidence.
PURGEABLE_STATUS = "new"


# --------------------------------------------------------------------------
# Tunables (stored in profiles.preferences; see app/preferences.py)
# --------------------------------------------------------------------------
def _int_pref(prefs: dict, key: str, default: int, minimum: int) -> int:
    value = prefs.get(key)
    ok = isinstance(value, int) and not isinstance(value, bool) and value >= minimum
    return value if ok else default


def window_days(prefs: dict) -> int:
    return _int_pref(prefs, "retention_window_days", DEFAULT_WINDOW_DAYS, 7)


def floor_matches(prefs: dict) -> int:
    return _int_pref(prefs, "retention_floor_matches", DEFAULT_FLOOR_MATCHES, 0)


def screenshot_ttl_days(prefs: dict) -> int:
    return _int_pref(prefs, "screenshot_ttl_days", DEFAULT_SCREENSHOT_TTL_DAYS, 1)


def stale_weight(prefs: dict) -> float:
    value = prefs.get("stale_profile_weight")
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value <= 1
    return float(value) if ok else DEFAULT_STALE_WEIGHT


# --------------------------------------------------------------------------
# Time helpers
# --------------------------------------------------------------------------
def utc(dt: datetime | None) -> datetime | None:
    """Aware-UTC view of a datetime. SQLite hands back naive values (UTC by
    convention here); Postgres hands back aware ones. Comparing the two raises."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# The window — shared by the miner (read) and the sweep (delete)
# --------------------------------------------------------------------------
def effective_cutoff(
    db: Session, profile_id: int, now: datetime, prefs: dict
) -> datetime | None:
    """Matches created before this are outside the window; ``None`` = no limit.

    The OLDER of "now minus the window" and "creation time of the Nth-newest
    match" — i.e. whichever rule keeps more data. ``None`` when the user has
    fewer matches than the floor, meaning everything is inside it.
    """
    window_cutoff = now - timedelta(days=window_days(prefs))
    floor = floor_matches(prefs)
    if floor <= 0:
        return window_cutoff

    floor_cutoff = db.scalar(
        select(Match.created_at)
        .where(Match.user_id == profile_id)
        .order_by(Match.created_at.desc())
        .offset(floor - 1)
        .limit(1)
    )
    if floor_cutoff is None:
        return None
    return min(window_cutoff, utc(floor_cutoff))


# --------------------------------------------------------------------------
# Post-profile-update weighting
# --------------------------------------------------------------------------
def profile_revised_at(db: Session, profile_id: int) -> datetime | None:
    """When the profile's content last changed, or ``None`` if never/unknown.

    Derived from ``updated_at`` on experiences, skills and qualifications —
    already maintained by ``onupdate`` — plus ``profiles.profile_revised_at``,
    the explicit marker that covers deletions (which leave no row behind to
    carry a newer timestamp). Needs no edit endpoint to exist.
    """
    candidates = [
        db.scalar(select(func.max(model.updated_at)).where(model.user_id == profile_id))
        for model in (Experience, Skill, Qualification)
    ]
    profile = db.get(Profile, profile_id)
    if profile is not None:
        candidates.append(profile.profile_revised_at)
    known = [utc(c) for c in candidates if c is not None]
    return max(known) if known else None


def match_weight(
    scored_at: datetime | None,
    created_at: datetime | None,
    revised_at: datetime | None,
    stale: float = DEFAULT_STALE_WEIGHT,
) -> float:
    """1.0 if scored against the current profile, ``stale`` if scored before it changed."""
    if revised_at is None:
        return 1.0
    scored = utc(scored_at or created_at)
    return 1.0 if scored is None or scored >= revised_at else stale


def relative_weights(weights: list[float]) -> list[float]:
    """Apply the stale discount only when there is fresher evidence to prefer.

    If EVERY match predates the profile change, discounting them all equally
    tells the miner nothing about which is better, yet it is not neutral:
    shrinkage and ``log1p(support)`` are not scale-invariant, so uniformly
    scaled-down evidence re-orders the ranking (seen on the dev DB, where a
    re-seeded profile made all 16 matches stale and flipped the top
    suggestion). So with no fresh match, everything counts equally; the
    discount switches on as soon as one post-change match exists to prefer.
    """
    if weights and max(weights) < 1.0:
        return [1.0] * len(weights)
    return weights


# --------------------------------------------------------------------------
# The sweep
# --------------------------------------------------------------------------
@dataclass
class SweepResult:
    matches_purged: int = 0
    jobs_purged: int = 0
    screenshots_expired: int = 0
    more_pending: bool = False


def purge_old_matches(
    db: Session,
    profile_id: int,
    screenshots_dir: Path,
    now: datetime | None = None,
    batch: int = PURGE_BATCH,
) -> tuple[int, int, bool]:
    """Hard-delete matches outside the window. Returns ``(matches, jobs, more)``.

    Structurally excludes anything the user acted on: only ``status == 'new'``
    with ``applied_at IS NULL`` is selectable. Cover letters go with the match
    (DB cascade), and a job listing goes only once NO match from any user
    references it — job_listings is a shared pool.
    """
    now = now or now_utc()
    prefs = get_preferences(db, profile_id)
    cutoff = effective_cutoff(db, profile_id, now, prefs)
    if cutoff is None:
        return 0, 0, False

    rows = db.execute(
        select(Match.id, Match.job_id, Match.screenshot_path)
        .where(Match.user_id == profile_id)
        .where(Match.created_at < cutoff)
        .where(Match.status == PURGEABLE_STATUS)
        .where(Match.applied_at.is_(None))
        .order_by(Match.created_at.asc())
        .limit(batch)
    ).all()
    if not rows:
        return 0, 0, False

    match_ids = [r.id for r in rows]
    job_ids = {r.job_id for r in rows}
    for row in rows:
        unlink_screenshot(screenshots_dir, row.screenshot_path)

    db.execute(delete(Match).where(Match.id.in_(match_ids)))

    still_referenced = set(
        db.scalars(select(Match.job_id).where(Match.job_id.in_(job_ids))).all()
    )
    orphaned = job_ids - still_referenced
    if orphaned:
        db.execute(delete(JobListing).where(JobListing.id.in_(orphaned)))  # job_skills cascade
    db.commit()
    return len(match_ids), len(orphaned), len(rows) == batch


def run_sweep(
    db: Session, profile_id: int, screenshots_dir: Path, now: datetime | None = None
) -> SweepResult:
    now = now or now_utc()
    prefs = get_preferences(db, profile_id)
    result = SweepResult()
    result.screenshots_expired = expire_screenshots(
        db, screenshots_dir, now - timedelta(days=screenshot_ttl_days(prefs)), profile_id
    )
    result.matches_purged, result.jobs_purged, result.more_pending = purge_old_matches(
        db, profile_id, screenshots_dir, now
    )
    return result


def run_sweep_if_due(
    db: Session, profile_id: int, screenshots_dir: Path, now: datetime | None = None
) -> SweepResult | None:
    """``run_sweep`` at most once per ``SWEEP_INTERVAL``; ``None`` if not due.

    The timestamp is only advanced once the backlog is fully drained, so a
    first run over a large history continues on the following loop iterations
    rather than waiting a day between batches.
    """
    now = now or now_utc()
    last = get_preferences(db, profile_id).get("retention_last_run")
    try:
        last_dt = datetime.fromisoformat(last) if last else None
    except (TypeError, ValueError):
        last_dt = None
    if last_dt is not None and now - utc(last_dt) < SWEEP_INTERVAL:
        return None

    result = run_sweep(db, profile_id, screenshots_dir, now)
    if not result.more_pending:
        set_preferences(db, profile_id, {"retention_last_run": now.isoformat()})
    if result.matches_purged or result.screenshots_expired:
        logger.info(
            "Retention sweep (profile %s): %s matches, %s jobs, %s screenshots expired",
            profile_id, result.matches_purged, result.jobs_purged, result.screenshots_expired,
        )
    return result
