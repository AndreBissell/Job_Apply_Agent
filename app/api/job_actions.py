"""The job write endpoints: quick screen, status, Quick Apply sent, expired, cover letter,
regenerate, delete.

``regenerate`` queues its work on the single worker as ``worker._bg_executor.submit(
worker._process_listing, ...)`` (looked up at call time, plan rule M1). ``delete_job`` reads the
screenshots folder as ``paths._screenshots_dir``. Applied jobs are Centrelink evidence:
``delete_job`` refuses them with a 409 unless told otherwise, and bulk hide is a soft delete.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app import availability
from app.api import events, paths, worker
from app.api.deps import get_db
from app.llm.client import DailyQuotaError
from app.llm.quickscreen import quick_screen
from app.models import JobListing, Match
from app.screenshots import unlink_screenshot

router = APIRouter()


class StatusUpdate(BaseModel):
    # The documented vocabulary (docs/database-schema.md, matches.status).
    status: Literal["new", "shortlisted", "applied", "interviewing", "rejected", "withdrawn"]


class CoverLetterUpdate(BaseModel):
    edited_content: str


@router.post("/jobs/{job_id}/quick-screen")
def run_quick_screen(job_id: int, profile_id: int = 1, db: Session = Depends(get_db)) -> dict:
    """Synchronously run the pre-extraction quick screen for one job.

    Runs on this request's own thread rather than the idle loop's shared
    _bg_executor, so a caller gets an immediate answer instead of waiting
    behind whatever unrelated background work is already queued — used by the
    sidebar's Scan Page loop to check each freshly-scraped job's rough
    relevance right away, so it can stop scraping a search early after a few
    consecutive weak results (see app/llm/quickscreen.py) without waiting on
    the idle loop's own pacing to get to it. Still goes through client.py's
    shared LLM_RPM throttle, so it can't burst past quota even though it
    bypasses the idle loop's queue. If the idle loop's Phase 0 reaches this exact
    job at the same time, quick_screen() claims the job before its model call, so
    only one of the two calls runs and the other reports "not-run" (score null,
    which the sidebar already treats as "already processed"). Without the claim
    both would pay for the model and race to write the same match row.
    """
    job = db.get(JobListing, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    try:
        outcome = quick_screen(job_id, profile_id, session=db)
    except DailyQuotaError as exc:
        raise HTTPException(status_code=429, detail=str(exc))

    db.refresh(job)
    return {"job_id": job_id, "outcome": outcome, "score": job.quick_screen_score}


@router.patch("/jobs/{job_id}/status")
def update_status(
    job_id: int,
    body: StatusUpdate,
    profile_id: int = 1,
    db: Session = Depends(get_db),
) -> dict:
    """Set a match's application-lifecycle status.

    Transitioning to ``'applied'`` stamps ``applied_at`` — but only the first
    time; re-marking an already-applied job as applied again is a no-op on the
    timestamp, so it stays accurate for Centrelink reporting.

    Once ``applied_at`` is set the status can only be ``'applied'`` (409 otherwise):
    an applied match is Centrelink evidence. Sending ``'applied'`` again is a no-op
    that also repairs a row whose date is set but whose status is not.
    """
    match = db.scalar(
        select(Match).where(Match.user_id == profile_id, Match.job_id == job_id)
    )
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    if match.applied_at is not None and body.status != "applied":
        raise HTTPException(
            status_code=409,
            detail="This job is marked applied (Centrelink evidence); its status can't change.",
        )

    match.status = body.status
    if body.status == "applied":
        _stamp_applied(db, match, job_id, via="manual")
    db.commit()

    return {
        "job_id": job_id,
        "status": match.status,
        "applied_at": match.applied_at.isoformat() if match.applied_at else None,
        "applied_via": match.applied_via,
    }


def _stamp_applied(db: Session, match: Match, job_id: int, via: str) -> bool:
    """Mark a match applied. ``applied_at`` and ``applied_via`` are set only the first
    time (True then); a repeat keeps the original date and how it was recorded. The
    caller commits.

    An applied job is evidence, never "closed": a flag that arrived before the user
    applied is lifted, so the job stays on the Jobs tab beside its "Applied" mark.
    """
    match.status = "applied"
    stamped = match.applied_at is None
    if stamped:
        match.applied_at = datetime.now(timezone.utc)
        match.applied_via = via
    job = db.get(JobListing, job_id)
    if job is not None:
        job.expired_detected_at = None
        job.availability_misses = 0
    return stamped


@router.post("/jobs/{job_id}/quick-apply-sent")
def quick_apply_sent(job_id: int, profile_id: int = 1, db: Session = Depends(get_db)) -> dict:
    """The extension saw Seek's Quick Apply "application sent" page for this job.

    Called by extension/apply_flow.js on ``/job/{id}/apply/success`` once
    ``h1#applicationSent`` is on the page (docs/centrelink-ease-apply-plan.md): Seek has
    accepted the application, so it is recorded with no click. Marks the job
    ``apply_type='quick'`` (the flow itself is the proof) and stamps ``applied_at`` with
    ``applied_via='auto'``.

    Idempotent: a refresh or a return to the page keeps the first date (and a manual mark
    made earlier stays ``'manual'``); ``newly_applied`` says whether this call recorded it.

    Creates the match when there is none yet (a job the user applied to before it was
    scored): unscored, status ``applied``. An application counts whether or not the app
    scored the job, and the idle loop never scores a job that already has a match, so this
    costs no AI call.
    """
    job = db.get(JobListing, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    job.apply_type = "quick"

    match = db.scalar(
        select(Match).where(Match.user_id == profile_id, Match.job_id == job_id)
    )
    if match is None:
        match = Match(user_id=profile_id, job_id=job_id, status="applied")
        db.add(match)
    newly = _stamp_applied(db, match, job_id, via="auto")
    db.commit()

    # The side panel reloads its lists (Overview count, Applied tab) on this.
    events.broadcast_from_thread("job_applied", {"job_id": job_id, "applied_via": match.applied_via})
    return {
        "job_id": job_id,
        "applied_at": match.applied_at.isoformat(),
        "applied_via": match.applied_via,
        "newly_applied": newly,
    }


@router.patch("/jobs/{job_id}/expired")
def mark_expired(job_id: int, db: Session = Depends(get_db)) -> dict:
    """Flag a listing as no longer available.

    Called by content_script.js when a revisit to a job's detail page finds no
    description where one was previously captured (see main()'s waitFor-timeout
    branch) — the only compliant signal available, since the Seek Access Policy
    forbids the backend from checking listings on its own. Idempotent: stamps
    expired_detected_at only the first time. A job the user has applied to is never
    flagged (Centrelink evidence): the call is then a no-op and ``expired_detected_at``
    comes back null. The side panel's background re-checks report through
    POST /availability/{job_id} instead, which adds the two-strike rule.
    """
    job = db.get(JobListing, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if availability.flag_expired(db, job):
        db.commit()

    return {
        "job_id": job_id,
        "expired_detected_at": job.expired_detected_at.isoformat() if job.expired_detected_at else None,
    }


@router.patch("/jobs/{job_id}/cover-letter")
def update_cover_letter(
    job_id: int,
    body: CoverLetterUpdate,
    profile_id: int = 1,
    db: Session = Depends(get_db),
) -> dict:
    """Save a user's edits to a generated cover letter.

    Called from both the sidebar's expanded card and the Quick-Apply overlay on
    Seek itself, so edits made from either surface stay in sync.
    """
    match = db.scalar(
        select(Match)
        .where(Match.user_id == profile_id, Match.job_id == job_id)
        .options(selectinload(Match.cover_letter))
    )
    if match is None or match.cover_letter is None:
        raise HTTPException(status_code=404, detail="Cover letter not found")

    match.cover_letter.edited_content = body.edited_content
    match.cover_letter.status = "edited"
    db.commit()

    return {"job_id": job_id, "status": match.cover_letter.status}


@router.post("/jobs/{job_id}/regenerate")
def regenerate(
    job_id: int,
    profile_id: int = 1,
    db: Session = Depends(get_db),
) -> dict:
    """Re-run extraction + matching + cover letter for one job.

    Always bypasses the score threshold — the user is making a deliberate
    choice to generate for this specific listing.
    """
    job = db.get(JobListing, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # On the single worker, behind whatever the idle loop is running: a full letter run
    # takes minutes, and two LLM jobs must not write the DB or hit the rate limit together.
    worker._bg_executor.submit(
        worker._process_listing,
        job_id,
        profile_id,
        job.raw_description is not None,
        with_cover_letter=True,
        bypass_threshold=True,
        force=True,
    )
    return {"status": "queued"}


@router.delete("/jobs")
def bulk_hide_jobs(
    below_score: float,
    profile_id: int = 1,
    db: Session = Depends(get_db),
) -> dict:
    """Hide every match for a profile scoring below ``below_score``.

    A *soft* delete, despite the verb: the match row survives with
    ``hidden_at`` stamped, and only ``raw_description`` — the bulk of the
    storage — is discarded. The user sees the same thing either way (the job
    leaves every list in the sidebar), but the score survives, and the score is
    what the suggestion miner needs.

    Hard-deleting these was actively harmful. Phrase ranking is
    ``(shrunk_mean - baseline) * log1p(support)``, so the low scorers are the
    contrast that makes a good phrase measurably good; deleting them raised the
    dev profile's baseline from 69.4 to 82.5 in one click and flattened the
    signal. ``GET /jobs/search-performance`` needs them for the same reason —
    a query's yield is meaningless if the misses are erased and only the hits
    remain.

    Applied matches (``applied_at`` set) are skipped: never hidden, and they keep
    their ``raw_description``. They are the application record and Centrelink
    evidence, and the Applied tab shows the ad text.

    Idempotent: already-hidden matches are skipped, so re-running it at the
    same threshold reports 0.
    """
    rows = db.execute(
        select(Match, JobListing)
        .join(JobListing, Match.job_id == JobListing.id)
        .where(Match.user_id == profile_id)
        .where(Match.score < below_score)
        .where(Match.hidden_at.is_(None))
        .where(Match.applied_at.is_(None))
    ).all()

    now = datetime.now(timezone.utc)
    for match, job in rows:
        match.hidden_at = now
        # Reclaim the space. Keeping the description would make this a
        # storage no-op, and nothing downstream reads it once a match exists:
        # the idle loop only picks up jobs that have no match row at all.
        job.raw_description = None
    db.commit()
    return {"deleted": len(rows), "hidden": len(rows)}


@router.delete("/jobs/{job_id}")
def delete_job(
    job_id: int,
    profile_id: int = 1,
    allow_applied: bool = False,
    db: Session = Depends(get_db),
) -> dict:
    """Delete a job listing and all its children (matches, cover letters, skills).

    An applied job is Centrelink evidence, so deleting one is refused (409) unless the
    caller opts in with ``allow_applied=true`` (the Applied tab's own Delete). The
    match's screenshot file goes too; the cascade only removes the row pointing at it.
    """
    job = db.get(JobListing, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    match = db.scalar(
        select(Match).where(Match.user_id == profile_id, Match.job_id == job_id)
    )
    if match is not None and match.applied_at is not None and not allow_applied:
        raise HTTPException(
            status_code=409,
            detail="This job is marked applied (Centrelink evidence). "
                   "Delete it from the Applied tab instead.",
        )
    screenshot_path = match.screenshot_path if match is not None else None
    db.delete(job)
    db.commit()
    unlink_screenshot(paths._screenshots_dir, screenshot_path)
    return {"deleted": job_id}
