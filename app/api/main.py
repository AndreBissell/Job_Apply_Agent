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
import functools
import json
import logging
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterator

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.profile_ui import router as profile_ui_router
from app.db import SessionLocal
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

            # ── Phase 2: cover letters (checked first — see docstring) ────────
            cl_job_id: int | None = None
            cl_user_id: int | None = None
            with SessionLocal() as db:
                row = db.execute(
                    select(Match, JobListing)
                    .join(JobListing, Match.job_id == JobListing.id)
                    .outerjoin(CoverLetter, CoverLetter.match_id == Match.id)
                    .where(CoverLetter.id.is_(None))
                    .where(Match.score >= COVER_LETTER_THRESHOLD)
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


class IngestBody(BaseModel):
    listings: list[IngestListing]
    profile_id: int = 1


class ProfileUpdate(BaseModel):
    name: str | None = None
    email: str | None = None


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
                raw_description=item.raw_description,
            )
            db.add(job)
            db.flush()  # assign job.id before we report it back
            new += 1
            job_ids.append(job.id)
        else:
            if existing.raw_description is None and item.raw_description:
                existing.raw_description = item.raw_description
                updated += 1
            # else: already present with a description — nothing to update.
            job_ids.append(existing.id)

    db.commit()
    return {"received": received, "new": new, "updated": updated, "job_ids": job_ids}


@app.get("/jobs/known-ids")
def known_job_ids(db: Session = Depends(get_db)) -> dict:
    """Return all source_job_ids already in the database so the extension can skip re-scraping."""
    ids = db.scalars(select(JobListing.source_job_id)).all()
    return {"source_ids": ids}


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
    """
    query = (
        select(Match, JobListing)
        .join(JobListing, Match.job_id == JobListing.id)
        .options(selectinload(JobListing.job_skills))
        .where(Match.user_id == profile_id)
        .where(Match.score >= min_score)
    )
    if status is not None:
        query = query.where(Match.status == status).order_by(Match.applied_at.desc())
    else:
        if not include_expired:
            query = query.where(JobListing.expired_detected_at.is_(None))
        query = query.order_by(Match.score.desc())
    rows = db.execute(query.limit(limit).offset(offset)).all()

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
            "extracted_at": job.extracted_at.isoformat() if job.extracted_at else None,
            "top_skills": [
                js.name for js in job.job_skills if js.skill_type == "hard"
            ][:3],
        }
        for match, job in rows
    ]


# Words that carry no search-relevant meaning in a job title — stripped before
# mining bigrams/trigrams for suggested-searches so "Graduate" or a company
# name don't drown out the actual role.
_TITLE_STOPWORDS = {
    "a", "an", "the", "and", "or", "for", "to", "of", "in", "at", "with",
    "graduate", "senior", "junior", "mid", "level", "entry", "new", "role",
    "position", "opportunity", "wanted", "required", "urgent", "immediate",
}


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


@app.get("/jobs/suggested-searches")
def suggested_searches(profile_id: int = 1, db: Session = Depends(get_db)) -> dict:
    """Free (non-LLM) search-phrase suggestions, hybrid-ranked in priority order:

    1. ``Profile.target_role`` (if set) — the most direct statement of intent
       available, so it outranks even a rich title-mining history.
    2. Titles of score >= COVER_LETTER_THRESHOLD matches, tokenized into
       bigrams/trigrams after stripping stopwords and ranked by frequency
       (the original approach, unchanged).
    3. Profile skill names / qualification fields of study — a fallback pool
       only used to fill slots still empty after 1+2, fixing the cold-start
       case where a new profile has little match history for (2) to mine.

    Excludes anything already covered by an active saved search. Pure Python,
    no LLM call (see docs/extension-revamp-plan.md §7).

    Registered before ``/jobs/{job_id}`` (like ``/jobs/known-ids`` above it) so
    the literal path wins the route match instead of being swallowed as a
    ``job_id="suggested-searches"`` lookup.
    """
    profile = db.scalar(
        select(Profile)
        .where(Profile.id == profile_id)
        .options(selectinload(Profile.skills), selectinload(Profile.qualifications))
    )

    titles = db.scalars(
        select(JobListing.title)
        .join(Match, Match.job_id == JobListing.id)
        .where(Match.user_id == profile_id)
        .where(Match.score >= COVER_LETTER_THRESHOLD)
    ).all()

    active_keywords = {
        kw.lower()
        for kw in db.scalars(
            select(SavedSearch.keywords)
            .where(SavedSearch.user_id == profile_id)
            .where(SavedSearch.is_active.is_(True))
        ).all()
        if kw
    }

    phrase_counts: Counter[str] = Counter()
    for title in titles:
        words = [
            w for w in re.findall(r"[A-Za-z]+", title.lower())
            if w not in _TITLE_STOPWORDS
        ]
        for n in (2, 3):
            for i in range(len(words) - n + 1):
                phrase = " ".join(words[i : i + n])
                phrase_counts[phrase] += 1

    ranked = [phrase for phrase, _count in phrase_counts.most_common()]

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

    for phrase in ranked:
        if len(suggestions) >= 3:
            break
        _add(phrase.title())

    if len(suggestions) < 3:
        for phrase in _profile_phrase_candidates(profile):
            if len(suggestions) >= 3:
                break
            _add(phrase.title())

    return {"suggestions": suggestions[:3]}


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

    if match.screenshot_path:
        (_screenshots_dir / Path(match.screenshot_path).name).unlink(missing_ok=True)

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
def bulk_delete_jobs(
    below_score: float,
    profile_id: int = 1,
    db: Session = Depends(get_db),
) -> dict:
    """Delete all job listings whose match score for a profile is below ``below_score``."""
    jobs = db.scalars(
        select(JobListing)
        .join(Match, Match.job_id == JobListing.id)
        .where(Match.user_id == profile_id)
        .where(Match.score < below_score)
    ).all()
    count = len(jobs)
    for job in jobs:
        db.delete(job)
    db.commit()
    return {"deleted": count}


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
