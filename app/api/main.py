"""FastAPI backend for the Seek Job Assistant Chrome extension.

Receives job listings the user's *real* browser scraped from Seek pages they
opened themselves (POST /ingest), upserts them into ``job_listings``, and fires
background tasks for LLM extraction + matching (stubbed for now). Also serves the
dashboard reads the extension's side panel uses (/jobs, /profile).

Run it with ``python scripts/run_api.py`` (uvicorn on 127.0.0.1:8000).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import csv
import functools
import io
import json
import logging
import re
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterator

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app import retention, search_suggest
from app.api.profile_ui import router as profile_ui_router
from app.db import SessionLocal
from app.llm import search_refine
from app.llm.client import DailyQuotaError
from app.llm.cover_letter import THRESHOLD as COVER_LETTER_THRESHOLD
from app.llm.cover_letter import generate_cover_letter
from app.llm.extract import extract_job
from app.llm.match import match_job
from app.llm.quickscreen import quick_screen
from app.models import (
    CoverLetter,
    Experience,
    JobListing,
    Match,
    Profile,
    Qualification,
    SavedSearch,
    Skill,
    UserCv,
)
from app.preferences import get_auto_letter_min_score, get_preferences, set_preferences
from app.screenshots import downscale_png, unlink_screenshot

logger = logging.getLogger(__name__)

SOURCE = "seek"

# One worker thread so LLM calls don't block the event loop.
_bg_executor = ThreadPoolExecutor(max_workers=1)

# Seconds to wait between idle cover-letter generations (respect per-minute quota).
_IDLE_INTERVAL_S = 20

# ---------------------------------------------------------------------------
# SSE event broadcasting
# ---------------------------------------------------------------------------
_sse_clients: list[asyncio.Queue] = []
_event_loop: asyncio.AbstractEventLoop | None = None


async def _broadcast(event: str, data: dict) -> None:
    """Push an SSE event to all connected sidebar clients."""
    if not _sse_clients:
        return
    msg = f"event: {event}\ndata: {json.dumps(data)}\n\n"
    for q in list(_sse_clients):
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            pass


def broadcast_from_thread(event: str, data: dict) -> None:
    """Thread-safe wrapper — call from sync background tasks."""
    if _event_loop is None:
        return
    asyncio.run_coroutine_threadsafe(_broadcast(event, data), _event_loop)


def _retention_tick() -> None:
    """Run the retention sweep for every profile that is due. Never raises: a
    sweep failure must not take the LLM idle loop down with it."""
    try:
        with SessionLocal() as db:
            for profile_id in db.scalars(select(Profile.id)).all():
                retention.run_sweep_if_due(db, profile_id, _screenshots_dir)
    except Exception:
        logger.exception("Retention sweep failed")


async def _processing_idle_loop() -> None:
    """Single idle loop handling all LLM work, serialised through _bg_executor.

    Phase 2 (checked FIRST) — generate cover letters for high-scored matches
    that don't have one yet. Phase 0 — cheap pre-extraction quick screen (see
    app/llm/quickscreen.py) for any job that hasn't been screened yet; auto-skips
    obvious mismatches before they cost a full extraction+match pair. Phase 1 —
    extract+match any screened, not-yet-skipped job that hasn't been extracted
    yet. Phase 1b — extracted but not yet matched.

    Cover letters are checked first, every iteration, so that idle time goes
    into finishing letters for jobs we already know are great before it goes
    into extracting/matching a backlog of new, as-yet-unscored listings. Only
    fall through to extraction/matching when there's no cover-letter backlog.
    See docs/extension-revamp-plan.md §3.

    Only one LLM call chain runs at a time; the client's RPM throttle adds
    per-call spacing on top, so we never burst the free-tier limit.
    """
    await asyncio.sleep(15)  # let startup settle before first query
    while True:
        try:
            loop = asyncio.get_running_loop()

            # ── Retention sweep ───────────────────────────────────────────────
            # Checked every iteration rather than only when idle: the LLM phases
            # `continue` while there is any backlog, so a tail-of-loop sweep
            # could starve indefinitely. It is a no-op except once per 24h
            # (retention.SWEEP_INTERVAL), and runs on the single worker so it
            # never interleaves with an LLM job writing to the same DB.
            await loop.run_in_executor(_bg_executor, _retention_tick)

            # ── Phase 2: cover letters (checked first — see docstring) ────────
            cl_job_id: int | None = None
            cl_user_id: int | None = None
            with SessionLocal() as db:
                # Same per-profile threshold generate_cover_letter() gates on —
                # they must agree, or this would pick a match the gate then refuses.
                auto_min = get_auto_letter_min_score(db, 1)
                row = db.execute(
                    select(Match, JobListing)
                    .join(JobListing, Match.job_id == JobListing.id)
                    .outerjoin(CoverLetter, CoverLetter.match_id == Match.id)
                    .where(CoverLetter.id.is_(None))
                    .where(Match.user_id == 1)
                    .where(Match.hidden_at.is_(None))  # never write a letter for a hidden job
                    .where(Match.score >= auto_min)
                    .where(JobListing.extracted_at.isnot(None))
                    .order_by(Match.score.desc())
                    .limit(1)
                ).first()
                if row:
                    match, job = row
                    cl_job_id, cl_user_id = job.id, match.user_id

            if cl_job_id is not None:
                logger.info("Idle: cover letter for job %s", cl_job_id)
                cl = await loop.run_in_executor(
                    _bg_executor,
                    functools.partial(generate_cover_letter, cl_job_id, cl_user_id),
                )
                if cl:
                    await _broadcast("cover_letter_ready", {"job_id": cl_job_id, "content": cl.generated_content})
                await asyncio.sleep(_IDLE_INTERVAL_S)
                continue  # re-check the cover-letter backlog before extraction/matching

            # ── Phase 0: pre-extraction quick screen ───────────────────────────
            # Oldest job with a description that hasn't been screened yet. Cheap
            # relative to extraction+matching — see app/llm/quickscreen.py for the
            # cost/conservatism rationale. Runs once per job; on pass it stamps
            # quick_screen_at and Phase 1 below picks it up next; on skip it writes
            # a terminal low-score Match row and Phase 1 never sees it.
            screen_id: int | None = None
            with SessionLocal() as db:
                job = db.scalar(
                    select(JobListing)
                    .where(JobListing.raw_description.isnot(None))
                    .where(JobListing.extracted_at.is_(None))
                    .where(JobListing.quick_screen_at.is_(None))
                    .order_by(JobListing.date_scraped.asc())
                    .limit(1)
                )
                if job:
                    screen_id = job.id

            if screen_id is not None:
                logger.info("Idle: quick-screen job %s", screen_id)
                try:
                    outcome = await loop.run_in_executor(
                        _bg_executor,
                        functools.partial(quick_screen, screen_id, 1),
                    )
                    if outcome == "skipped":
                        # A skip writes a terminal Match row right away — the only
                        # quick-screen outcome the sidebar has anything new to show
                        # for, so it's the only one that needs a live refresh. A
                        # pass just moves the job on to Phase 1, with nothing to
                        # display yet.
                        broadcast_from_thread("job_processed", {"job_id": screen_id})
                    await asyncio.sleep(_IDLE_INTERVAL_S)
                except DailyQuotaError:
                    logger.warning("Idle: quick-screen daily quota hit — backing off 3 min")
                    await asyncio.sleep(180)
                continue

            # ── Phase 1: pending extraction+matching ──────────────────────────
            # Picks the most likely-relevant job among the oldest backlog, using
            # the free _raw_relevance_score signal — see its docstring. Capped at
            # 50 candidates so this stays cheap even with a large backlog; among
            # ties (including "no candidates scored" -> all zero) max() keeps the
            # first one in date_scraped order, i.e. still FIFO as a tiebreak.
            # Restricted to jobs that passed the Phase 0 quick screen (quick_screen_at
            # set) and haven't already been skip-terminated by it (~matches.any()) —
            # a skipped job keeps extracted_at NULL forever, so without this it would
            # otherwise loop right back into this candidate pool.
            pending_id: int | None = None
            with SessionLocal() as db:
                profile_skills = db.scalars(
                    select(Skill.name).where(Skill.user_id == 1)
                ).all()
                candidates = db.scalars(
                    select(JobListing)
                    .where(JobListing.raw_description.isnot(None))
                    .where(JobListing.extracted_at.is_(None))
                    .where(JobListing.quick_screen_at.isnot(None))
                    .where(~JobListing.matches.any())
                    .order_by(JobListing.date_scraped.asc())
                    .limit(50)
                ).all()
                if candidates:
                    job = max(candidates, key=lambda j: _raw_relevance_score(j, profile_skills))
                    pending_id = job.id

            if pending_id is not None:
                logger.info("Idle: extract+match job %s", pending_id)
                await loop.run_in_executor(
                    _bg_executor,
                    functools.partial(_process_listing, pending_id, 1, True),
                )
                # If extraction failed (Gemini still rate-limited), extracted_at stays
                # NULL and we'd immediately retry the same job. Back off 3 min instead.
                with SessionLocal() as db:
                    _job = db.get(JobListing, pending_id)
                    _succeeded = _job is not None and _job.extracted_at is not None
                if _succeeded:
                    await asyncio.sleep(_IDLE_INTERVAL_S)
                else:
                    logger.warning("Idle: extraction failed for job %s — backing off 3 min", pending_id)
                    await asyncio.sleep(180)
                continue

            # ── Phase 1b: extracted but not yet matched ───────────────────────
            unmatched_id: int | None = None
            with SessionLocal() as db:
                job = db.scalar(
                    select(JobListing)
                    .where(JobListing.extracted_at.isnot(None))
                    .where(~JobListing.matches.any())
                    .order_by(JobListing.date_scraped.asc())
                    .limit(1)
                )
                if job:
                    unmatched_id = job.id

            if unmatched_id is not None:
                logger.info("Idle: match job %s (already extracted)", unmatched_id)
                await loop.run_in_executor(
                    _bg_executor,
                    functools.partial(match_job, unmatched_id, 1),
                )
                await asyncio.sleep(_IDLE_INTERVAL_S)
                continue

            await asyncio.sleep(30)  # nothing pending — check again shortly

        except Exception:
            logger.exception("Idle processing loop error")
            await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _event_loop
    _event_loop = asyncio.get_running_loop()
    task = asyncio.create_task(_processing_idle_loop())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="Seek Job Assistant", version="0.1.0", lifespan=lifespan)

# CORS: the extension calls from a chrome-extension:// origin and the sidebar from
# localhost. Wide-open for local dev; lock down (specific extension id) later.
# NOTE: allow_credentials must stay False while allow_origins=["*"] (the spec
# forbids "*" + credentials), which is fine — we use no cookies.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(profile_ui_router)

_static_dir = Path(__file__).parent.parent / "static"
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

_screenshots_dir = Path(__file__).parent.parent / "screenshots"
_screenshots_dir.mkdir(parents=True, exist_ok=True)
app.mount("/screenshots", StaticFiles(directory=str(_screenshots_dir)), name="screenshots")


# ---------------------------------------------------------------------------
# DB session dependency
# ---------------------------------------------------------------------------
def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Request/response schemas
# ---------------------------------------------------------------------------
class IngestListing(BaseModel):
    source_job_id: str
    url: str
    title: str
    company: str | None = None
    location: str | None = None
    classification: str | None = None
    subclassification: str | None = None
    work_type: str | None = None
    salary: str | None = None
    raw_description: str | None = None
    discovered_query: str | None = None


class IngestBody(BaseModel):
    listings: list[IngestListing]
    profile_id: int = 1


class ProfileUpdate(BaseModel):
    name: str | None = None
    email: str | None = None


class PreferencesUpdate(BaseModel):
    auto_cover_letter_min_score: int | None = Field(default=None, ge=0, le=100)
    llm_search_suggestions: bool | None = None
    # Retention tunables — see app/retention.py. Bounds keep a typo from
    # turning the sweep into "delete everything" (window >= 7 days) or the
    # screenshot TTL into "delete evidence immediately" (>= 1 day).
    retention_window_days: int | None = Field(default=None, ge=7, le=730)
    retention_floor_matches: int | None = Field(default=None, ge=0, le=5000)
    screenshot_ttl_days: int | None = Field(default=None, ge=1, le=365)
    stale_profile_weight: float | None = Field(default=None, gt=0, le=1)


class StatusUpdate(BaseModel):
    status: str


class CoverLetterUpdate(BaseModel):
    edited_content: str


class ScreenshotUpload(BaseModel):
    data_url: str  # "data:image/png;base64,...." — see POST /jobs/{id}/screenshot


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


def _raw_relevance_score(job: JobListing, skill_names: list[str]) -> int:
    """Zero-cost queue-ordering signal: how many of the profile's skill names
    appear as substrings in the job's title/raw description.

    Not a gate — every job still gets extracted+matched regardless of this
    score (see app/llm/prefilter.py's "no silent gate" rule). It only decides
    which pending job the idle loop picks next, so likely-relevant jobs reach
    the front of the single-worker LLM queue before likely-irrelevant ones,
    without spending an extra LLM call to find out.
    """
    text = f"{job.title or ''} {job.raw_description or ''}".lower()
    return sum(1 for name in skill_names if name and name.lower() in text)


def _process_listing(
    job_id: int,
    profile_id: int,
    has_description: bool,
    with_cover_letter: bool = False,
    bypass_threshold: bool = False,
    force: bool = False,
) -> None:
    """Background task: extract + match (+ optionally cover letter) a listing.

    Runs after the response is sent. With only a card (no description yet) there's
    nothing to extract from, so we defer until a detail-page ingest fills it in.
    ``with_cover_letter=True`` is set by /regenerate so the letter is generated
    after matching completes — never set by /ingest to avoid burning quota on every
    scraped listing. ``force=True`` (also set by /regenerate) re-runs extraction and
    matching even if they already ran — e.g. to override a quick-screen auto-skip;
    this path never calls quick_screen, it goes straight to extraction+matching.
    """
    if has_description:
        try:
            extract_job(job_id, force=force)
        except Exception:  # noqa: BLE001 — a bad extraction must not kill the task
            logger.exception("extract_job failed for job %s", job_id)
        try:
            match_job(job_id, profile_id, force=force)
            broadcast_from_thread("job_processed", {"job_id": job_id})
        except Exception:  # noqa: BLE001
            logger.exception("match_job failed for job %s", job_id)
        if with_cover_letter:
            try:
                cl = generate_cover_letter(job_id, profile_id, force=True, bypass_threshold=bypass_threshold)
                if cl:
                    broadcast_from_thread("cover_letter_ready", {"job_id": job_id, "content": cl.generated_content})
            except Exception:  # noqa: BLE001
                logger.exception("generate_cover_letter failed for job %s", job_id)
    else:
        logger.info(
            "Job %s stored as card only (no description) — deferring extraction/"
            "matching until a detail page is ingested.",
            job_id,
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    """Liveness check the extension uses to confirm the backend is up."""
    profile_id = db.scalar(select(Profile.id).order_by(Profile.id).limit(1))
    return {"status": "ok", "profile_id": profile_id}


@app.post("/ingest")
def ingest(
    body: IngestBody,
    db: Session = Depends(get_db),
) -> dict:
    """Upsert scraped listings; backfill ``raw_description`` on existing rows.

    Insert when ``(source='seek', source_job_id)`` is new. If the row already
    exists but has no description and this payload carries one, update it.
    LLM extraction+matching is handled by the idle loop — no burst on ingest.

    Returns each listing's internal ``job_id`` (same order as the request body)
    so a caller that just ingested a single detail page — e.g. the sidebar's
    Scan Page early-exit loop — can immediately act on that specific job
    (currently: POST /jobs/{id}/quick-screen) without a second lookup.
    """
    received = len(body.listings)
    new = 0
    updated = 0
    job_ids: list[int] = []

    for item in body.listings:
        existing = db.scalar(
            select(JobListing).where(
                JobListing.source == SOURCE,
                JobListing.source_job_id == item.source_job_id,
            )
        )

        if existing is None:
            job = JobListing(
                source=SOURCE,
                source_job_id=item.source_job_id,
                url=item.url,
                title=item.title,
                company=item.company,
                location=item.location,
                classification=item.classification,
                subclassification=item.subclassification,
                work_type=item.work_type,
                salary=item.salary,
                discovered_query=item.discovered_query,
                raw_description=item.raw_description,
            )
            db.add(job)
            db.flush()  # assign job.id before we report it back
            new += 1
            job_ids.append(job.id)
        else:
            # A listing is typically ingested twice — once as a search-results
            # card (which carries discovered_query but no description or
            # taxonomy) and once as a detail page (the reverse). Backfill any
            # field this payload can fill and the row is still missing, so
            # whichever arrives second completes the row. Existing values are
            # never overwritten: the first capture of a field is the one made
            # while the page was actually open.
            changed = False
            for attr in (
                "raw_description",
                "classification",
                "subclassification",
                "discovered_query",
            ):
                if getattr(existing, attr) is None and getattr(item, attr):
                    setattr(existing, attr, getattr(item, attr))
                    changed = True
            if changed:
                updated += 1
            job_ids.append(existing.id)

    db.commit()
    return {"received": received, "new": new, "updated": updated, "job_ids": job_ids}


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


@app.post("/jobs/{job_id}/quick-screen")
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
    bypasses the idle loop's queue. If the idle loop's Phase 0 happens to reach
    this exact job first, quick_screen()'s own idempotency check makes this a
    harmless no-op that just reports the existing verdict.
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


@app.get("/jobs")
def list_jobs(
    profile_id: int = 1,
    min_score: int = 0,
    status: str | None = None,
    include_expired: bool = False,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> list[dict]:
    """Matched jobs for a profile.

    Only returns jobs that have a scored ``matches`` row (>= ``min_score``); jobs
    awaiting the LLM pass simply don't appear yet. Without ``status``, ranked by
    score desc (the normal Jobs tab). With ``status`` (e.g. ``applied``), ranked
    by ``applied_at`` desc instead — that's the Applied tab / Centrelink export,
    where recency of application matters more than match quality.

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
    """
    query = (
        select(Match, JobListing)
        .join(JobListing, Match.job_id == JobListing.id)
        .options(selectinload(JobListing.job_skills))
        .where(Match.user_id == profile_id)
        .where(Match.hidden_at.is_(None))
        .where(Match.score >= min_score)
    )
    if status is not None:
        query = query.where(Match.status == status).order_by(Match.applied_at.desc())
    else:
        if not include_expired:
            query = query.where(JobListing.expired_detected_at.is_(None))
        query = query.order_by(Match.score.desc())
    rows = db.execute(query.limit(limit).offset(offset)).all()
    ttl = timedelta(days=retention.screenshot_ttl_days(get_preferences(db, profile_id)))

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
            "screenshot_taken_at": (
                match.screenshot_taken_at.isoformat() if match.screenshot_taken_at else None
            ),
            "screenshot_url": (
                f"/screenshots/{Path(match.screenshot_path).name}"
                if match.screenshot_path else None
            ),
            # When the FILE will be deleted (null once it already has been:
            # screenshot_taken_at set with no screenshot_url means "expired").
            "screenshot_expires_at": (
                (retention.utc(match.screenshot_taken_at) + ttl).isoformat()
                if match.screenshot_path and match.screenshot_taken_at else None
            ),
            "extracted_at": job.extracted_at.isoformat() if job.extracted_at else None,
            "top_skills": [
                js.name for js in job.job_skills if js.skill_type == "hard"
            ][:3],
        }
        for match, job in rows
    ]


def _profile_phrase_candidates(profile: Profile | None) -> list[str]:
    """Fallback phrase candidates straight from the profile — skill names, then
    qualification fields of study, deduped in that order.

    Used only to fill remaining suggestion slots when target_role + title-mining
    don't reach 3 — the cold-start case, where a new profile has little or no
    score>=75 match history yet for title-mining to work with.
    """
    if profile is None:
        return []
    candidates: list[str] = []
    seen: set[str] = set()
    for skill in profile.skills:
        name = skill.name.strip()
        key = name.lower()
        if name and key not in seen:
            seen.add(key)
            candidates.append(name)
    for qual in profile.qualifications:
        field = (qual.field_of_study or "").strip()
        key = field.lower()
        if field and key not in seen:
            seen.add(key)
            candidates.append(field)
    return candidates


# A query needs at least this many captured jobs before its yield is treated as
# a real signal rather than noise, and must fall below this hit rate to be
# called under-performing. Both are deliberately forgiving: the penalty below
# demotes a phrase, it never deletes it.
_YIELD_MIN_VOLUME = 5
_YIELD_LOW_THRESHOLD = 0.15
_YIELD_PENALTY = 0.5


def _search_performance(db: Session, profile_id: int) -> list[dict]:
    """Per-query yield: how much of what each Seek search surfaced was any good.

    ``yield`` is the share of a query's captured jobs that scored at or above
    the cover-letter threshold; ``volume`` doubles as its cost, since every
    captured job is an LLM call. A high-volume, low-yield query is the thing
    worth retiring, and neither number means anything without the other.

    Only counts jobs captured since migration c5b21d7f4e3a — earlier rows have
    no ``discovered_query`` and are invisible here by design.

    Reads the same rolling window as the suggestion miner, and per query
    prefers matches scored AFTER the profile last changed: a query that
    under-performed for the old profile should not demote phrases for the new
    one. It falls back to the whole window while a query has fewer than
    ``_YIELD_MIN_VOLUME`` post-update matches, so a fresh profile edit doesn't
    blank the table.
    """
    prefs = get_preferences(db, profile_id)
    cutoff = retention.effective_cutoff(db, profile_id, retention.now_utc(), prefs)
    revised = retention.profile_revised_at(db, profile_id)

    query = (
        select(JobListing.discovered_query, Match.score, Match.scored_at, Match.created_at)
        .join(Match, Match.job_id == JobListing.id)
        .where(Match.user_id == profile_id)
        .where(JobListing.discovered_query.isnot(None))
    )
    if cutoff is not None:
        query = query.where(Match.created_at >= cutoff)

    # query -> [(score, scored_against_current_profile)]
    by_query: dict[str, list[tuple[float | None, bool]]] = {}
    for search, score, scored_at, created_at in db.execute(query).all():
        key = (search or "").strip()
        if not key:
            continue
        fresh = retention.match_weight(scored_at, created_at, revised) == 1.0
        by_query.setdefault(key, []).append((None if score is None else float(score), fresh))

    performance = []
    for search, entries in by_query.items():
        fresh_entries = [e for e in entries if e[1]]
        used = fresh_entries if len(fresh_entries) >= _YIELD_MIN_VOLUME else entries
        hits = sum(1 for score, _f in used if score is not None and score >= COVER_LETTER_THRESHOLD)
        performance.append(
            {
                "query": search,
                "volume": len(used),
                "hits": hits,
                "yield": round(hits / len(used), 3),
            }
        )
    return sorted(performance, key=lambda r: (-r["yield"], -r["volume"]))


def _underperforming_queries(performance: list[dict]) -> list[str]:
    """Queries with enough volume to judge and too few hits to keep trusting."""
    return [
        row["query"].lower()
        for row in performance
        if row["volume"] >= _YIELD_MIN_VOLUME and row["yield"] < _YIELD_LOW_THRESHOLD
    ]


@app.get("/jobs/search-performance")
def search_performance(profile_id: int = 1, db: Session = Depends(get_db)) -> dict:
    """Yield per Seek search — Layer 3 of the suggestion pipeline.

    Exposed on its own so the sidebar can show the user which of their searches
    are earning their keep, independent of whether any of it feeds back into
    the suggestions.
    """
    performance = _search_performance(db, profile_id)
    return {
        "performance": performance,
        "underperforming": _underperforming_queries(performance),
        "min_volume": _YIELD_MIN_VOLUME,
        "low_yield_threshold": _YIELD_LOW_THRESHOLD,
    }


def _csv_safe(value: object) -> str:
    """Neutralise spreadsheet formula injection. Titles/employers come from
    scraped pages, and a cell starting with = + - @ is executed by Excel."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@app.get("/jobs/evidence-export")
def evidence_export(profile_id: int = 1, db: Session = Depends(get_db)) -> Response:
    """Zip of every applied job's record plus whichever screenshots still exist.

    The safety net for screenshot expiry (``screenshot_ttl_days``): the image
    files are deleted after their TTL, so this is how the user takes a copy
    first. The CSV always lists every application; ``Screenshot`` says
    "yes", "expired" (taken, file since deleted) or "no" (never captured), and
    the screenshots themselves sit alongside it under ``screenshots/``.

    Registered before ``/jobs/{job_id}`` so the literal path wins the match.
    """
    rows = db.execute(
        select(Match, JobListing)
        .join(JobListing, Match.job_id == JobListing.id)
        .where(Match.user_id == profile_id)
        .where(Match.status == "applied")
        .order_by(Match.applied_at.desc())
    ).all()

    csv_buf = io.StringIO()
    writer = csv.writer(csv_buf)
    writer.writerow(
        ["Date Applied", "Job Title", "Employer", "Location", "Source URL",
         "Screenshot", "Screenshot Taken", "Screenshot File"]
    )
    files: list[tuple[str, Path]] = []
    for match, job in rows:
        path = (
            _screenshots_dir / Path(match.screenshot_path).name
            if match.screenshot_path else None
        )
        on_disk = path is not None and path.is_file()
        if on_disk:
            files.append((f"screenshots/{path.name}", path))
        if on_disk:
            state = "yes"
        elif match.screenshot_taken_at:
            state = "expired"
        else:
            state = "no"
        writer.writerow([
            match.applied_at.date().isoformat() if match.applied_at else "",
            _csv_safe(job.title), _csv_safe(job.company), _csv_safe(job.location), job.url,
            state,
            match.screenshot_taken_at.date().isoformat() if match.screenshot_taken_at else "",
            f"screenshots/{path.name}" if on_disk else "",
        ])

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("applied-jobs.csv", csv_buf.getvalue())
        for arcname, path in files:
            zf.write(path, arcname)

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="application-evidence-{stamp}.zip"'},
    )


@app.get("/jobs/suggested-searches")
def suggested_searches(
    profile_id: int = 1,
    use_llm: bool | None = None,
    db: Session = Depends(get_db),
) -> dict:
    """Search-phrase suggestions, mined from the user's own match history.

    Four layers, cheapest first — see ``app/search_suggest.py`` for why the
    original frequency counting was replaced:

    1. **Score-weighted mining** over every scored title: shrunk lift x log
       support, gated on a role noun, deduplicated by nesting then by family.
       Pure Python.
    2. **Yield demotion** — a phrase already covered by a high-volume,
       low-yield saved query gets its rank halved. Demoted, not dropped: the
       same role may still be worth searching under different wording.
    3. **Cold-start fallback** — ``target_role``, then skill names and fields
       of study, used only to fill slots the mining could not, which is the
       case on a new profile with no match history to mine.
    4. **Optional LLM re-rank** (``app/llm/search_refine.py``), off unless the
       user ticks it on. Debounced and cached; on failure or an empty result
       the mined list stands.

    ``use_llm`` overrides the stored preference for one call — the sidebar's
    "Refresh now" button uses it to force a refine without flipping the
    persisted setting.

    Registered before ``/jobs/{job_id}`` (like ``/jobs/known-ids`` above it) so
    the literal path wins the route match instead of being swallowed as a
    ``job_id="suggested-searches"`` lookup.
    """
    profile = db.scalar(
        select(Profile)
        .where(Profile.id == profile_id)
        .options(selectinload(Profile.skills), selectinload(Profile.qualifications))
    )

    # The whole distribution, not just the good matches: the low scores are
    # what make the baseline meaningful (see search_suggest.rank_phrases) —
    # but only the rolling window of it (retention.effective_cutoff, the same
    # rule the sweep deletes by), so applied matches that outlive the sweep
    # can't drag the baseline upward. Each match carries a weight: 1.0 if it
    # was scored against the current profile, less if scored before the
    # profile last changed.
    prefs = get_preferences(db, profile_id)
    cutoff = retention.effective_cutoff(db, profile_id, retention.now_utc(), prefs)
    revised = retention.profile_revised_at(db, profile_id)
    stale = retention.stale_weight(prefs)
    titles_query = (
        select(JobListing.title, Match.score, Match.scored_at, Match.created_at)
        .join(Match, Match.job_id == JobListing.id)
        .where(Match.user_id == profile_id)
        .where(Match.score.isnot(None))
    )
    if cutoff is not None:
        titles_query = titles_query.where(Match.created_at >= cutoff)
    title_rows = db.execute(titles_query).all()
    weights = retention.relative_weights(
        [retention.match_weight(sa, ca, revised, stale) for _t, _s, sa, ca in title_rows]
    )
    scored_titles = [
        (title, float(score), weight)
        for (title, score, _sa, _ca), weight in zip(title_rows, weights)
    ]

    active_keywords = {
        kw.lower()
        for kw in db.scalars(
            select(SavedSearch.keywords)
            .where(SavedSearch.user_id == profile_id)
            .where(SavedSearch.is_active.is_(True))
        ).all()
        if kw
    }

    stats = search_suggest.mine(scored_titles)

    weak_queries = _underperforming_queries(_search_performance(db, profile_id))
    for stat in stats:
        if any(stat.phrase in query for query in weak_queries):
            stat.rank *= _YIELD_PENALTY
    stats.sort(key=lambda s: -s.rank)

    suggestions: list[str] = []
    seen_lower: set[str] = set()

    def _add(phrase: str) -> None:
        key = phrase.strip().lower()
        if not key or key in seen_lower or key in active_keywords:
            return
        seen_lower.add(key)
        suggestions.append(phrase)

    if profile is not None and profile.target_role:
        _add(profile.target_role.strip())

    for stat in stats:
        if len(suggestions) >= 3:
            break
        _add(stat.phrase.title())

    if len(suggestions) < 3:
        for phrase in _profile_phrase_candidates(profile):
            if len(suggestions) >= 3:
                break
            _add(phrase.title())

    candidates = [stat.as_dict() for stat in stats]
    llm_enabled = prefs.get("llm_search_suggestions", False) if use_llm is None else use_llm
    llm_used = False
    if llm_enabled and candidates:
        refined = _refined_suggestions(
            db, profile_id, profile, candidates, sorted(active_keywords), force=bool(use_llm)
        )
        if refined:
            llm_used = True
            suggestions = [p for p in refined if p.lower() not in active_keywords][:3]

    return {
        "suggestions": suggestions[:3],
        "candidates": candidates[:search_refine.MAX_CANDIDATES],
        "llm_used": llm_used,
        # Where the sidebar scopes the search it opens. target_location (where
        # the user wants to WORK) always wins; `location` (where they live) is
        # only a fallback for a profile that never set a target. They can
        # legitimately differ — someone in Brisbane targeting Melbourne — so
        # the order matters. Null means a nationwide search.
        "location": _search_location(profile),
    }


def _search_location(profile: Profile | None) -> str | None:
    """``target_location``, falling back to ``location``. See above for why."""
    if profile is None:
        return None
    for value in (profile.target_location, profile.location):
        cleaned = (value or "").strip()
        if cleaned:
            return cleaned
    return None


def _refined_suggestions(
    db: Session,
    profile_id: int,
    profile: Profile | None,
    candidates: list[dict],
    active_keywords: list[str],
    force: bool = False,
) -> list[str]:
    """Layer 4: cached, debounced LLM re-rank of the mined candidates.

    The cache key is the user's scored-match count: a result stays current
    until enough new matches have landed to plausibly change the ranking (see
    ``search_refine.REFRESH_AFTER_NEW_MATCHES``). ``force`` bypasses that for
    an explicit user-triggered refresh.
    """
    prefs = get_preferences(db, profile_id)
    cached = prefs.get("llm_search_suggestions_cache")
    match_count = db.scalar(
        select(func.count())
        .select_from(Match)
        .where(Match.user_id == profile_id)
        .where(Match.score.isnot(None))
    ) or 0

    # A profile edit changes what the best searches are without changing the
    # match count, so the count-based debounce alone would serve a stale list.
    revised = retention.profile_revised_at(db, profile_id)
    revised_key = revised.isoformat() if revised else None
    profile_changed = bool(cached) and cached.get("revised_at") != revised_key

    if not force and not profile_changed and not search_refine.should_refresh(cached, match_count):
        return list(cached.get("searches", [])) if cached else []

    searches = search_refine.refine(profile, candidates, active_keywords)
    if not searches:
        # Don't poison the cache with a failure — fall through to the mined
        # list now and retry on the next request.
        return list(cached.get("searches", [])) if cached else []

    set_preferences(
        db,
        profile_id,
        {
            "llm_search_suggestions_cache": {
                "searches": searches,
                "match_count": match_count,
                "revised_at": revised_key,
            }
        },
    )
    return searches


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


@app.patch("/jobs/{job_id}/status")
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
    """
    match = db.scalar(
        select(Match).where(Match.user_id == profile_id, Match.job_id == job_id)
    )
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")

    match.status = body.status
    if body.status == "applied" and match.applied_at is None:
        match.applied_at = datetime.now(timezone.utc)
    db.commit()

    return {
        "job_id": job_id,
        "status": match.status,
        "applied_at": match.applied_at.isoformat() if match.applied_at else None,
    }


@app.patch("/jobs/{job_id}/expired")
def mark_expired(job_id: int, db: Session = Depends(get_db)) -> dict:
    """Flag a listing as no longer available.

    Called by content_script.js when a revisit to a job's detail page finds no
    description where one was previously captured (see main()'s waitFor-timeout
    branch) — the only compliant signal available, since the Seek Access Policy
    forbids the backend from checking listings on its own. Idempotent: stamps
    expired_detected_at only the first time.
    """
    job = db.get(JobListing, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.expired_detected_at is None:
        job.expired_detected_at = datetime.now(timezone.utc)
        db.commit()

    return {"job_id": job_id, "expired_detected_at": job.expired_detected_at.isoformat()}


@app.patch("/jobs/{job_id}/cover-letter")
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


@app.post("/jobs/{job_id}/screenshot")
def upload_screenshot(
    job_id: int,
    body: ScreenshotUpload,
    profile_id: int = 1,
    db: Session = Depends(get_db),
) -> dict:
    """Persist a Quick-Apply screenshot as durable Centrelink evidence.

    Accepts the raw data: URL the extension already holds in memory from
    chrome.tabs.captureVisibleTab — no multipart re-encode needed. Overwrites
    (deletes) any previous screenshot file for this match so repeat captures
    don't grow disk usage unbounded; one screenshot per match, latest wins.
    Files expire after ``screenshot_ttl_days`` (default 30) — see
    app/retention.py; the application record itself never does.
    """
    match = db.scalar(
        select(Match).where(Match.user_id == profile_id, Match.job_id == job_id)
    )
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")

    m = re.match(r"^data:image/png;base64,(.+)$", body.data_url, re.DOTALL)
    if not m:
        raise HTTPException(status_code=400, detail="Expected a PNG data URL")
    try:
        png_bytes = base64.b64decode(m.group(1), validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="Invalid base64 image data")
    if not png_bytes:
        raise HTTPException(status_code=400, detail="Empty image data")

    # Store at <=1200px wide; falls back to the untouched bytes on any failure.
    png_bytes = downscale_png(png_bytes)

    unlink_screenshot(_screenshots_dir, match.screenshot_path)

    ts = datetime.now(timezone.utc)
    filename = f"{job_id}-{profile_id}-{ts.strftime('%Y%m%dT%H%M%SZ')}.png"
    (_screenshots_dir / filename).write_bytes(png_bytes)

    match.screenshot_path = f"screenshots/{filename}"
    match.screenshot_taken_at = ts
    db.commit()

    return {
        "job_id": job_id,
        "screenshot_path": match.screenshot_path,
        "screenshot_taken_at": ts.isoformat(),
    }


@app.post("/jobs/{job_id}/regenerate")
def regenerate(
    job_id: int,
    background_tasks: BackgroundTasks,
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

    background_tasks.add_task(
        _process_listing,
        job_id,
        profile_id,
        job.raw_description is not None,
        with_cover_letter=True,
        bypass_threshold=True,
        force=True,
    )
    return {"status": "queued"}


@app.delete("/jobs")
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

    Idempotent: already-hidden matches are skipped, so re-running it at the
    same threshold reports 0.
    """
    rows = db.execute(
        select(Match, JobListing)
        .join(JobListing, Match.job_id == JobListing.id)
        .where(Match.user_id == profile_id)
        .where(Match.score < below_score)
        .where(Match.hidden_at.is_(None))
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


@app.delete("/jobs/{job_id}")
def delete_job(job_id: int, db: Session = Depends(get_db)) -> dict:
    """Delete a job listing and all its children (matches, cover letters, skills)."""
    job = db.get(JobListing, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    db.delete(job)
    db.commit()
    return {"deleted": job_id}


@app.get("/events")
async def sse_events(request: Request):
    """Server-Sent Events stream. Pushes job_processed and cover_letter_ready events."""
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=50)
    _sse_clients.append(queue)

    async def stream():
        try:
            yield "event: ping\ndata: {}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=25)
                    yield msg
                except asyncio.TimeoutError:
                    yield "event: ping\ndata: {}\n\n"  # keepalive
        finally:
            if queue in _sse_clients:
                _sse_clients.remove(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/profile/{profile_id}")
def get_profile(profile_id: int, db: Session = Depends(get_db)) -> dict:
    """Profile with nested qualifications, experiences, skills, and CVs."""
    profile = db.get(Profile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")

    qualifications = db.scalars(
        select(Qualification).where(Qualification.user_id == profile_id)
    ).all()
    experiences = db.scalars(
        select(Experience).where(Experience.user_id == profile_id)
    ).all()
    skills = db.scalars(select(Skill).where(Skill.user_id == profile_id)).all()
    cvs = db.scalars(select(UserCv).where(UserCv.user_id == profile_id)).all()

    return {
        "id": profile.id,
        "name": profile.name,
        "email": profile.email,
        "visa_status": profile.visa_status,
        "qualifications": [
            {"id": q.id, "title": q.title, "qualification_type": q.qualification_type,
             "institution": q.institution, "status": q.status}
            for q in qualifications
        ],
        "experiences": [
            {"id": e.id, "title": e.title, "organization": e.organization,
             "experience_type": e.experience_type, "on_cv": e.on_cv}
            for e in experiences
        ],
        "skills": [
            {"id": s.id, "name": s.name, "category": s.category} for s in skills
        ],
        "cvs": [
            {"id": c.id, "label": c.label, "is_default": c.is_default} for c in cvs
        ],
    }


@app.put("/profile/{profile_id}")
def update_profile(
    profile_id: int, body: ProfileUpdate, db: Session = Depends(get_db)
) -> dict:
    """Partial update of a profile's ``name`` / ``email``."""
    profile = db.get(Profile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")

    if body.name is not None:
        profile.name = body.name
    if body.email is not None:
        profile.email = body.email
    db.commit()

    return {"id": profile.id, "name": profile.name, "email": profile.email}


@app.get("/profile/{profile_id}/preferences")
def read_preferences(profile_id: int, db: Session = Depends(get_db)) -> dict:
    if db.get(Profile, profile_id) is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    return get_preferences(db, profile_id)


@app.put("/profile/{profile_id}/preferences")
def update_preferences(
    profile_id: int, body: PreferencesUpdate, db: Session = Depends(get_db)
) -> dict:
    """Partial update: only the keys present in the body change."""
    if db.get(Profile, profile_id) is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    return set_preferences(db, profile_id, body.model_dump(exclude_none=True))
