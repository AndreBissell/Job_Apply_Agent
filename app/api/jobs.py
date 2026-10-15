"""The job read endpoints: known-ids, pending-count, by-source-id, GET /jobs, GET /jobs/{job_id};
plus the Overview's letter bands (GET /jobs/letter-bands and its POST .../write).

ORDER MATTERS: ``GET /jobs/{job_id}`` (``job_id: int``) must stay LAST in this file and this
router must be included after every other router that has a ``GET /jobs/<literal>`` path
(ingest, evidence, search, job_actions), or the literal paths answer 422 instead of their
data. tests/test_route_table.py enforces both.
"""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app import letter_bands, retention
from app.api import worker
from app.api.deps import get_db
from app.api.ingest import SOURCE
from app.llm.letter import view as letter_view
from app.models import CoverLetter, JobListing, Match
from app.preferences import get_preferences

router = APIRouter()


def _score_to_number(score: Decimal | None) -> float | None:
    return float(score) if score is not None else None


def _gaps_to_list(gaps: str | None) -> list[str]:
    """``matches.gaps`` is stored as a JSON string; return it as a list for the API."""
    if not gaps:
        return []
    try:
        data = json.loads(gaps)
    except (json.JSONDecodeError, TypeError):
        return [gaps]
    return data if isinstance(data, list) else [str(data)]


def _json_list(raw: str | None) -> list[str]:
    """A JSON-array string column (``key_responsibilities``) as a list of strings.

    Empty for None, invalid JSON or anything that isn't a list.
    """
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return [item for item in data if isinstance(item, str)] if isinstance(data, list) else []


def _iso(dt) -> str | None:
    """Aware-UTC ISO text, so the panel's ``new Date()`` reads it as UTC (SQLite hands back naive)."""
    return retention.utc(dt).isoformat() if dt is not None else None


def _eligibility_notes(job: JobListing) -> list[str]:
    """Requirements the cover-letter pipeline judged not-for-a-letter (work rights,
    licence, clearance, availability), for display on the job card as a heads-up.

    Empty both when there are none AND when the job has not been analysed yet —
    ``analyze_job`` only runs for jobs headed for a letter, so most cards have no
    checklist. Costs nothing: it only reads the cached JSON.
    """
    if not job.requirements_checklist:
        return []
    try:
        checklist = json.loads(job.requirements_checklist)
    except (json.JSONDecodeError, TypeError):
        return []
    reqs = checklist.get("requirements") if isinstance(checklist, dict) else None
    return [
        r["text"] for r in (reqs or [])
        if isinstance(r, dict) and r.get("letter_role") == "not_for_letter" and r.get("text")
    ]


@router.get("/jobs/known-ids")
def known_job_ids(db: Session = Depends(get_db)) -> dict:
    """Return all source_job_ids already in the database so the extension can skip re-scraping."""
    ids = db.scalars(select(JobListing.source_job_id)).all()
    return {"source_ids": ids}


@router.get("/jobs/pending-count")
def pending_job_count(profile_id: int = 1, db: Session = Depends(get_db)) -> dict:
    """How many captured jobs are still waiting on the LLM pass (no match row yet).

    The sidebar shows this at the bottom of the list ("N more scanned, analysing")
    since /jobs only returns jobs that already have a scored match. A quick-screen
    skip writes a terminal match row, so skipped jobs are already out of this count.
    Jobs with no description can't be processed and are excluded.
    """
    pending = db.scalar(
        select(func.count())
        .select_from(JobListing)
        .where(JobListing.raw_description.isnot(None))
        .where(JobListing.expired_detected_at.is_(None))
        .where(
            ~JobListing.matches.any(Match.user_id == profile_id)
        )
    )
    return {"pending": pending or 0}


@router.get("/jobs/by-source-id/{source_job_id}")
def job_by_source_id(source_job_id: str, db: Session = Depends(get_db)) -> dict:
    """Resolve Seek's own job id (from the page URL) to this app's internal job_id.

    The extension only ever sees Seek's id in the DOM/URL; several endpoints
    (job detail, status, screenshot, expired) key off the internal id instead.
    Used by content_script.js whenever a /job/{id} visit doesn't also run
    /ingest this time (e.g. the description failed to load), so it still has
    a reliable id to act on.
    """
    job = db.scalar(
        select(JobListing).where(
            JobListing.source == SOURCE,
            JobListing.source_job_id == source_job_id,
        )
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job_id": job.id}


@router.get("/jobs")
def list_jobs(
    profile_id: int = 1,
    min_score: int = 0,
    status: str | None = None,
    include_expired: bool = False,
    ready: bool = False,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> list[dict]:
    """Matched jobs for a profile.

    Only returns jobs that have a scored ``matches`` row (>= ``min_score``); jobs
    awaiting the LLM pass simply don't appear yet. Without ``status``, ranked by
    score desc (the normal Jobs tab). With ``status`` (e.g. ``applied``), ranked
    by ``applied_at`` desc instead — that's the Applied tab / Centrelink export,
    where recency of application matters more than match quality. ``status=applied``
    means ``applied_at IS NOT NULL``; any other value filters the ``status`` column.

    Jobs flagged ``expired_detected_at`` (see PATCH /jobs/{id}/expired) are
    excluded from the default (no-``status``) view — the Jobs tab should only
    ever show listings the user can actually still apply to. This filter is
    skipped for ``status`` queries: a listing closing after the user already
    applied doesn't invalidate that application as Centrelink evidence.
    ``include_expired=true`` lifts the filter on the default view too, mainly
    for debugging/verification.

    Matches hidden by the bulk low-score action (``hidden_at``) are excluded
    from every view here, including the Applied tab. They still exist, and
    still feed the suggestion baseline and the search-performance yield — see
    DELETE /jobs for why they are kept rather than deleted.

    ``ready=true`` (the Overview's Keep Applying row): only jobs with a cover letter
    that aren't applied yet, by score. Filtered here rather than in the sidebar so the
    ``limit`` can't drop ready jobs behind letter-less ones.
    """
    query = (
        select(Match, JobListing)
        .join(JobListing, Match.job_id == JobListing.id)
        .options(selectinload(JobListing.job_skills))
        .where(Match.user_id == profile_id)
        .where(Match.hidden_at.is_(None))
        .where(Match.score >= min_score)
    )
    if ready:
        query = query.where(Match.applied_at.is_(None)).where(
            select(CoverLetter.id).where(CoverLetter.match_id == Match.id).exists()
        )
    if status is not None:
        # "Applied" means applied_at is set (same rule as the dashboard and the export).
        if status == "applied":
            query = query.where(Match.applied_at.is_not(None))
        else:
            query = query.where(Match.status == status)
        query = query.order_by(Match.applied_at.desc())
    else:
        if not include_expired:
            query = query.where(JobListing.expired_detected_at.is_(None))
        query = query.order_by(Match.score.desc())
    rows = db.execute(query.limit(limit).offset(offset)).all()
    ttl = timedelta(days=retention.screenshot_ttl_days(get_preferences(db, profile_id)))
    runs = letter_view.latest_runs(db, [match.id for match, _ in rows])
    reported = retention.reported_match_ids(db, [match.id for match, _ in rows])

    return [
        {
            "job_id": job.id,
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "url": job.url,
            "score": _score_to_number(match.score),
            "reasoning": match.reasoning,
            "gaps": _gaps_to_list(match.gaps),
            "status": match.status,
            "applied_at": match.applied_at.isoformat() if match.applied_at else None,
            "applied_via": match.applied_via,
            "apply_type": job.apply_type,
            "has_cover_letter": match.cover_letter is not None,
            **retention.screenshot_fields(match, ttl, match.id in reported),
            "extracted_at": job.extracted_at.isoformat() if job.extracted_at else None,
            "top_skills": [
                js.name for js in job.job_skills if js.skill_type == "hard"
            ][:3],
            "eligibility_notes": _eligibility_notes(job),
            # The newest cover-letter pipeline run: {run_id, status, open_questions}, or
            # null when none has run. Drives the card's "writing / needs your answer".
            "letter_run": runs.get(match.id),
        }
        for match, job in rows
    ]


class BandWrite(BaseModel):
    lo: int
    hi: int


@router.get("/jobs/letter-bands")
def get_letter_bands(profile_id: int = 1, db: Session = Depends(get_db)) -> dict:
    """The Overview's "no letter yet" cards: scored jobs below the auto-letter score, in
    5-point bands anchored on it, the 6 highest non-empty bands first (app/letter_bands.py)."""
    return letter_bands.summary(db, profile_id)


@router.post("/jobs/letter-bands/write")
def write_letter_band(body: BandWrite, profile_id: int = 1, db: Session = Depends(get_db)) -> dict:
    """Queue one-shot letters for a band's best jobs (at most 10) on the single worker.

    The jobs come from the database, not the request: the same filter as the cards, and
    only jobs seen live recently (the availability gate); the rest are counted as
    ``waiting_check``. 400 when (lo, hi) is not a band below the auto-letter score.
    """
    try:
        claim = letter_bands.claim_band(db, profile_id, body.lo, body.hi)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if claim["job_ids"]:
        try:
            worker._bg_executor.submit(worker._write_band_letters, claim["job_ids"], profile_id)
        except Exception:
            letter_bands.release(*claim["job_ids"])
            raise
    return {"queued": len(claim["job_ids"]), "waiting_check": claim["waiting_check"], "skipped": claim["skipped"]}


@router.get("/jobs/{job_id}")
def get_job(job_id: int, profile_id: int = 1, db: Session = Depends(get_db)) -> dict:
    """Full detail for one job: listing fields + this profile's match + cover letter.

    Also serves the Applied tab's read-only record, so it carries the extracted fields
    (summary, responsibilities, seniority, skills), the availability stamps, the
    interview date and the letter's timestamps. Keys are only ever added: the Seek
    overlay and the sidebar read the older ones.
    """
    job = db.get(JobListing, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    match = db.scalar(
        select(Match).where(Match.user_id == profile_id, Match.job_id == job_id)
    )
    cover = match.cover_letter if match is not None else None

    return {
        "job_id": job.id,
        "source": job.source,
        "source_job_id": job.source_job_id,
        "url": job.url,
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "classification": job.classification,
        "subclassification": job.subclassification,
        "work_type": job.work_type,
        "salary": job.salary,
        "apply_type": job.apply_type,
        "raw_description": job.raw_description,
        "eligibility_notes": _eligibility_notes(job),
        "date_scraped": _iso(job.date_scraped),
        "summary": job.summary,
        "key_responsibilities": _json_list(job.key_responsibilities),
        "seniority": job.seniority,
        "discovered_query": job.discovered_query,
        "skills": {
            "hard": [js.name for js in job.job_skills if js.skill_type == "hard"],
            "soft": [js.name for js in job.job_skills if js.skill_type == "soft"],
        },
        "expired_detected_at": _iso(job.expired_detected_at),
        "last_seen_live_at": _iso(job.last_seen_live_at),
        "match": (
            {
                "score": _score_to_number(match.score),
                "reasoning": match.reasoning,
                "gaps": _gaps_to_list(match.gaps),
                "status": match.status,
                "applied_at": match.applied_at.isoformat() if match.applied_at else None,
                "applied_via": match.applied_via,
                "interview_at": _iso(match.interview_at),
            }
            if match is not None
            else None
        ),
        "cover_letter": (
            {
                "generated_content": cover.generated_content,
                "edited_content": cover.edited_content,
                "status": cover.status,
                "created_at": _iso(cover.created_at),
                "updated_at": _iso(cover.updated_at),
            }
            if cover is not None
            else None
        ),
    }
