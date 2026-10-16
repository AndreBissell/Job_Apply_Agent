"""The Overview's "no letter yet" bands (docs/overview-applied-rework-plan.md, Step 3, D3-D6).

Scored jobs below the auto-letter bar get no letter from the idle loop. The Overview shows
them as 5-point score bands anchored on that bar (bar 80 -> 79-75, 74-70, ...; bar 77 ->
76-72, 71-67, ...), the 6 highest non-empty bands, highest first. The top band's
"+ cover letters" queues one-shot letters for its best jobs (at most ``WRITE_CAP`` a click)
on the single worker (``worker._write_band_letters``).

Pure queries and band maths, plus ``queued``: the in-process set of job ids waiting for that
worker. A queued job leaves the bands and shows in Keep Applying as being written (GET /jobs
``letter_queued``), and a second click can't queue it twice. No LLM.
"""

from __future__ import annotations

import math
import threading
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import availability
from app.models import CoverLetter, JobListing, LetterRun, LlmUsage, Match
from app.preferences import letter_settings

WIDTH = 5       # points per band
MAX_BANDS = 6   # the cards shown, highest bands first
WRITE_CAP = 10  # letters queued per click
# The "Write 3? ≈ X" estimate per letter: the mean of the latest ESTIMATE_SAMPLE one-pass letter
# calls; before there are ESTIMATE_MIN of them, the pipeline's draft call (the same strong model
# writing a comparable letter); before either, ESTIMATE_DEFAULT_USD.
ESTIMATE_SAMPLE = 20
ESTIMATE_MIN = 3
ESTIMATE_DEFAULT_USD = 0.10

# Runs that already hold a match (production.LIVE_STATUSES): a one-shot must not land
# underneath one. Repeated here so this module stays free of the letter engines.
_LIVE_RUNS = ("running", "waiting_user", "answered")

_lock = threading.Lock()
queued: set[int] = set()  # job ids waiting for (or being written by) the worker


def band_of(score_floor: int, bar: int) -> tuple[int, int]:
    """The (lo, hi) band holding a whole score below ``bar``; the lowest band stops at 0."""
    k = (bar - 1 - score_floor) // WIDTH
    hi = bar - 1 - WIDTH * k
    return max(hi - WIDTH + 1, 0), hi


def _eligible(profile_id: int, bar: int | None):
    """D4: a full score below the bar, no letter, not applied, hidden or flagged closed.
    ``bar=None`` leaves out the score cap (the rest still applies)."""
    stmt = (
        select(Match.job_id, Match.score)
        .join(JobListing, JobListing.id == Match.job_id)
        .outerjoin(CoverLetter, CoverLetter.match_id == Match.id)
        .where(Match.user_id == profile_id, CoverLetter.id.is_(None))
        .where(Match.score.is_not(None))
        .where(Match.applied_at.is_(None), Match.hidden_at.is_(None))
        # Quick-screen skips are never extracted and can't get a letter.
        .where(JobListing.extracted_at.is_not(None), JobListing.expired_detected_at.is_(None))
    )
    return stmt if bar is None else stmt.where(Match.score < bar)


def queued_ids() -> set[int]:
    """A snapshot of the job ids waiting for (or being written by) the band writer."""
    with _lock:
        return set(queued)


def letter_cost_estimate(db: Session) -> tuple[float, str]:
    """(USD per one-pass letter, basis) from what letters really cost here (``llm_usage``).

    basis ``one_pass``: the latest one-pass letters (task ``cover_letter``); ``draft``: none
    yet, so the full pipeline's draft call (``generate_letter``: the same strong model writing
    a comparable letter); ``default``: no letter logged at all.
    """
    for task, basis in (("cover_letter", "one_pass"), ("generate_letter", "draft")):
        costs = db.scalars(
            select(LlmUsage.cost_usd)
            .where(LlmUsage.task == task, LlmUsage.cost_usd.is_not(None))
            .order_by(LlmUsage.id.desc())
            .limit(ESTIMATE_SAMPLE)
        ).all()
        if len(costs) >= ESTIMATE_MIN:
            return round(sum(float(c) for c in costs) / len(costs), 4), basis
    return ESTIMATE_DEFAULT_USD, "default"


def summary(db: Session, profile_id: int, now: datetime | None = None) -> dict:
    """GET /jobs/letter-bands: ``{bar, width, total, writing, est_usd_per_letter, est_basis,
    bands: [{lo, hi, count, ready, waiting_check}]}``. ``ready`` passes the freshness gate
    (D6); the rest wait for an availability check. Jobs queued for a letter are left out of
    the bands (they show in Keep Applying as being written) and counted in ``writing``."""
    bar = letter_settings(db, profile_id)["auto_min_score"]
    writing = queued_ids()
    rows = [r for r in db.execute(_eligible(profile_id, bar)).all() if r.job_id not in writing]
    gate = availability.llm_gate(db, profile_id, now)
    ready = {r.job_id for r in db.execute(_eligible(profile_id, bar).where(gate))}

    bands: dict[tuple[int, int], dict] = {}
    for job_id, score in rows:
        lo, hi = band_of(math.floor(float(score)), bar)
        b = bands.setdefault((lo, hi), {"lo": lo, "hi": hi, "count": 0, "ready": 0, "waiting_check": 0})
        b["count"] += 1
        b["ready" if job_id in ready else "waiting_check"] += 1
    top = sorted(bands.values(), key=lambda b: b["hi"], reverse=True)[:MAX_BANDS]
    est, basis = letter_cost_estimate(db)
    return {
        "bar": bar, "width": WIDTH, "total": len(rows), "writing": len(writing),
        "est_usd_per_letter": est, "est_basis": basis, "bands": top,
    }


def claim_band(db: Session, profile_id: int, lo: int, hi: int, now: datetime | None = None) -> dict:
    """POST /jobs/letter-bands/write: pick the band's jobs from the database (never from the
    client), best first, and mark up to ``WRITE_CAP`` of them queued. Returns
    ``{job_ids, waiting_check, skipped}``; ``skipped`` were ready but already queued or over
    the cap. ValueError when (lo, hi) isn't a band below the current bar."""
    bar = letter_settings(db, profile_id)["auto_min_score"]
    if not (0 <= hi < bar and band_of(hi, bar) == (lo, hi)):
        raise ValueError(f"{lo}-{hi} is not a band below the auto-letter score ({bar})")
    # floor(score) in [lo, hi], written so it works on any database
    in_band = _eligible(profile_id, bar).where(Match.score >= lo, Match.score < hi + 1)
    count = len(db.execute(in_band).all())
    gate = availability.llm_gate(db, profile_id, now)
    ready = [r.job_id for r in db.execute(in_band.where(gate).order_by(Match.score.desc(), Match.job_id))]
    with _lock:
        fresh = [j for j in ready if j not in queued][:WRITE_CAP]
        queued.update(fresh)
    return {"job_ids": fresh, "waiting_check": count - len(ready), "skipped": len(ready) - len(fresh)}


def claim_job(db: Session, profile_id: int, job_id: int, now: datetime | None = None) -> dict:
    """POST /jobs/{job_id}/write-letter (the Pipeline's "Write a letter", docs/pipeline-tab-plan.md
    D7): the band rules for one job. Returns ``{"job_ids": [job_id] or [], "reason": ...}``;
    ``reason`` is ``queued`` when it was claimed, else why not: ``not_found``, ``has_letter``,
    ``not_eligible`` (applied, hidden, closed or never fully scored), ``at_or_above_bar`` (the
    idle loop writes those), ``writing`` (already queued or a run is live) or ``waiting_check``."""
    bar = letter_settings(db, profile_id)["auto_min_score"]
    row = db.execute(
        select(Match, JobListing)
        .join(JobListing, JobListing.id == Match.job_id)
        .where(Match.user_id == profile_id, Match.job_id == job_id)
    ).first()
    if row is None:
        return {"job_ids": [], "reason": "not_found"}
    match, job = row
    if db.scalar(select(CoverLetter.id).where(CoverLetter.match_id == match.id)) is not None:
        return {"job_ids": [], "reason": "has_letter"}
    if db.execute(_eligible(profile_id, None).where(Match.job_id == job_id)).first() is None:
        return {"job_ids": [], "reason": "not_eligible"}
    if float(match.score) >= bar:
        return {"job_ids": [], "reason": "at_or_above_bar"}
    live = db.scalar(
        select(LetterRun.id).where(LetterRun.match_id == match.id, LetterRun.status.in_(_LIVE_RUNS)).limit(1)
    )
    if live is not None:
        return {"job_ids": [], "reason": "writing"}
    gated_in = db.execute(
        select(JobListing.id).where(JobListing.id == job.id).where(availability.llm_gate(db, profile_id, now))
    ).first()
    if gated_in is None:
        return {"job_ids": [], "reason": "waiting_check"}
    with _lock:
        if job_id in queued:
            return {"job_ids": [], "reason": "writing"}
        queued.add(job_id)
    return {"job_ids": [job_id], "reason": "queued"}


def release(*job_ids: int) -> None:
    """The worker is done with these ids (written, skipped or failed)."""
    with _lock:
        queued.difference_update(job_ids)


def still_eligible(db: Session, profile_id: int, job_id: int, now: datetime | None = None) -> float | None:
    """The worker's re-check just before writing: the job's score if it is still in a band
    and still gated in (no letter, application, hide, closed ad or stale sighting since the
    click, and still below the bar), and has no live letter run; else None."""
    bar = letter_settings(db, profile_id)["auto_min_score"]
    row = db.execute(
        _eligible(profile_id, bar)
        .where(Match.job_id == job_id)
        .where(availability.llm_gate(db, profile_id, now))
    ).first()
    if row is None:
        return None
    live = db.scalar(
        select(LetterRun.id)
        .join(Match, Match.id == LetterRun.match_id)
        .where(Match.user_id == profile_id, Match.job_id == job_id, LetterRun.status.in_(_LIVE_RUNS))
        .limit(1)
    )
    return None if live is not None else float(row.score)
