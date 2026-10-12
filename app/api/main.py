"""FastAPI backend for the Seek Job Assistant Chrome extension.

Receives job listings the user's *real* browser scraped from Seek pages they
opened themselves (POST /ingest) and upserts them into ``job_listings``. /ingest
makes no LLM call: the idle loop (``_processing_idle_loop``) does the AI work one
job at a time on a single worker thread, in this order: retention sweep, cover
letters, quick screen, extraction + matching. Also serves everything the side panel
and profile editor read and write (/jobs, /profile, /obligation, letters, screening).

Run it with ``python scripts/run_api.py real|test`` (uvicorn on 127.0.0.1, port
8000 for real and 8001 for test).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from contextlib import asynccontextmanager
from datetime import timedelta
from decimal import Decimal

from fastapi import Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app import retention
from app.api import events, idle_loop, paths
from app.api.deps import get_db  # re-exported: tests key dependency_overrides on it
from app.api.evidence import router as evidence_router
from app.api.health import router as health_router
from app.api.ingest import SOURCE, router as ingest_router
from app.api.job_actions import router as job_actions_router
from app.api.letters import router as letters_router
from app.api.obligation import router as obligation_router
from app.api.origin_guard import OriginGuardMiddleware
from app.api.profile import router as profile_router
from app.api.search import router as search_router
from app.api.profile_ui import router as profile_ui_router
from app.api.screening import router as screening_router
from app.llm.letter import view as letter_view
from app.models import (
    CoverLetter,
    JobListing,
    Match,
)
from app.preferences import get_preferences

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    events._event_loop = asyncio.get_running_loop()
    task = asyncio.create_task(idle_loop._processing_idle_loop())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="Seek Job Assistant", version="0.1.0", lifespan=lifespan)

# No CORS middleware, on purpose: with no Access-Control-* headers a web page cannot
# read any response or send a preflighted call (JSON body, PATCH/PUT/DELETE) to this
# server. The extension doesn't need them (its pages and background worker have
# host_permissions for localhost), and the profile editor is served from this same
# origin. See docs/cleanup-3-8-plan.md step 5.1.
# What CORS can't stop is a "simple" cross-site write (a body-less POST such as
# /regenerate), so OriginGuardMiddleware refuses writes from other websites.
app.add_middleware(OriginGuardMiddleware)

app.include_router(profile_ui_router)
app.include_router(letters_router)
app.include_router(screening_router)
app.include_router(obligation_router)
app.include_router(health_router)
app.include_router(events.router)
app.include_router(ingest_router)
app.include_router(evidence_router)
app.include_router(search_router)
app.include_router(job_actions_router)
app.include_router(profile_router)

app.mount("/static", StaticFiles(directory=str(paths._static_dir)), name="static")
app.mount("/screenshots", StaticFiles(directory=str(paths._screenshots_dir)), name="screenshots")


# ---------------------------------------------------------------------------
# Request/response schemas
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/jobs/known-ids")
def known_job_ids(db: Session = Depends(get_db)) -> dict:
    """Return all source_job_ids already in the database so the extension can skip re-scraping."""
    ids = db.scalars(select(JobListing.source_job_id)).all()
    return {"source_ids": ids}


@app.get("/jobs/pending-count")
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


@app.get("/jobs/by-source-id/{source_job_id}")
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


@app.get("/jobs")
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
            "has_cover_letter": match.cover_letter is not None,
            **retention.screenshot_fields(match, ttl),
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


@app.get("/jobs/{job_id}")
def get_job(job_id: int, profile_id: int = 1, db: Session = Depends(get_db)) -> dict:
    """Full detail for one job: listing fields + this profile's match + cover letter."""
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
        "raw_description": job.raw_description,
        "eligibility_notes": _eligibility_notes(job),
        "date_scraped": job.date_scraped.isoformat() if job.date_scraped else None,
        "match": (
            {
                "score": _score_to_number(match.score),
                "reasoning": match.reasoning,
                "gaps": _gaps_to_list(match.gaps),
                "status": match.status,
                "applied_at": match.applied_at.isoformat() if match.applied_at else None,
            }
            if match is not None
            else None
        ),
        "cover_letter": (
            {
                "generated_content": cover.generated_content,
                "edited_content": cover.edited_content,
                "status": cover.status,
            }
            if cover is not None
            else None
        ),
    }
