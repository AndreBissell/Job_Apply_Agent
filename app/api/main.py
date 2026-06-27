"""FastAPI backend for the Seek Job Assistant Chrome extension.

Receives job listings the user's *real* browser scraped from Seek pages they
opened themselves (POST /ingest), upserts them into ``job_listings``, and fires
background tasks for LLM extraction + matching (stubbed for now). Also serves the
dashboard reads the extension's side panel uses (/jobs, /profile).

Run it with ``python scripts/run_api.py`` (uvicorn on 127.0.0.1:8000).
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Iterator

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.profile_ui import router as profile_ui_router
from app.db import SessionLocal
from app.llm.cover_letter import THRESHOLD as COVER_LETTER_THRESHOLD
from app.llm.cover_letter import generate_cover_letter
from app.llm.extract import extract_job
from app.llm.match import match_job
from app.models import (
    CoverLetter,
    Experience,
    JobListing,
    Match,
    Profile,
    Qualification,
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

    Phase 1 — extract+match any job that has a description but hasn't been
    extracted yet (jobs land here straight from /ingest, no burst).
    Phase 2 — generate cover letters for high-scored matches that don't have one.

    Only one LLM call chain runs at a time; the client's RPM throttle adds
    per-call spacing on top, so we never burst the Gemini free-tier limit.
    """
    await asyncio.sleep(15)  # let startup settle before first query
    while True:
        try:
            loop = asyncio.get_running_loop()

            # ── Phase 1: pending extraction+matching ──────────────────────────
            pending_id: int | None = None
            with SessionLocal() as db:
                job = db.scalar(
                    select(JobListing)
                    .where(JobListing.raw_description.isnot(None))
                    .where(JobListing.extracted_at.is_(None))
                    .order_by(JobListing.date_scraped.asc())
                    .limit(1)
                )
                if job:
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
                continue  # check for more pending jobs before cover letters

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

            # ── Phase 2: cover letters ────────────────────────────────────────
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
            else:
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


def _process_listing(
    job_id: int,
    profile_id: int,
    has_description: bool,
    with_cover_letter: bool = False,
    bypass_threshold: bool = False,
) -> None:
    """Background task: extract + match (+ optionally cover letter) a listing.

    Runs after the response is sent. With only a card (no description yet) there's
    nothing to extract from, so we defer until a detail-page ingest fills it in.
    ``with_cover_letter=True`` is set by /regenerate so the letter is generated
    after matching completes — never set by /ingest to avoid burning quota on every
    scraped listing.
    """
    if has_description:
        try:
            extract_job(job_id)
        except Exception:  # noqa: BLE001 — a bad extraction must not kill the task
            logger.exception("extract_job failed for job %s", job_id)
        try:
            match_job(job_id, profile_id)
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
    """
    received = len(body.listings)
    new = 0
    updated = 0

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
            new += 1
        elif existing.raw_description is None and item.raw_description:
            existing.raw_description = item.raw_description
            updated += 1
        # else: already present with a description — skip.

    db.commit()
    return {"received": received, "new": new, "updated": updated}


@app.get("/jobs/known-ids")
def known_job_ids(db: Session = Depends(get_db)) -> dict:
    """Return all source_job_ids already in the database so the extension can skip re-scraping."""
    ids = db.scalars(select(JobListing.source_job_id)).all()
    return {"source_ids": ids}


@app.get("/jobs")
def list_jobs(
    profile_id: int = 1,
    min_score: int = 0,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> list[dict]:
    """Matched jobs for a profile, ranked by score desc.

    Only returns jobs that have a scored ``matches`` row (>= ``min_score``); jobs
    awaiting the LLM pass simply don't appear yet.
    """
    rows = db.execute(
        select(Match, JobListing)
        .join(JobListing, Match.job_id == JobListing.id)
        .where(Match.user_id == profile_id)
        .where(Match.score >= min_score)
        .order_by(Match.score.desc())
        .limit(limit)
        .offset(offset)
    ).all()

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
            "has_cover_letter": match.cover_letter is not None,
            "extracted_at": job.extracted_at.isoformat() if job.extracted_at else None,
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
        "date_scraped": job.date_scraped.isoformat() if job.date_scraped else None,
        "match": (
            {
                "score": _score_to_number(match.score),
                "reasoning": match.reasoning,
                "gaps": _gaps_to_list(match.gaps),
                "status": match.status,
            }
            if match is not None
            else None
        ),
        "cover_letter": (
            {
                "generated_content": cover.generated_content,
                "status": cover.status,
            }
            if cover is not None
            else None
        ),
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
