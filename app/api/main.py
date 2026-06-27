"""FastAPI backend for the Seek Job Assistant Chrome extension.

Receives job listings the user's *real* browser scraped from Seek pages they
opened themselves (POST /ingest), upserts them into ``job_listings``, and fires
background tasks for LLM extraction + matching (stubbed for now). Also serves the
dashboard reads the extension's side panel uses (/jobs, /profile).

Run it with ``python scripts/run_api.py`` (uvicorn on 127.0.0.1:8000).
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from pathlib import Path
from typing import Iterator

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.profile_ui import router as profile_ui_router
from app.db import SessionLocal
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

app = FastAPI(title="Seek Job Assistant", version="0.1.0")

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
        except Exception:  # noqa: BLE001
            logger.exception("match_job failed for job %s", job_id)
        if with_cover_letter:
            try:
                generate_cover_letter(job_id, profile_id, force=True)
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
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict:
    """Upsert scraped listings; backfill ``raw_description`` on existing rows.

    Insert when ``(source='seek', source_job_id)`` is new. If the row already
    exists but has no description and this payload carries one, update it. Either
    case schedules extraction/matching as a background task.
    """
    received = len(body.listings)
    new = 0
    updated = 0
    to_process: list[tuple[int, bool]] = []

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
            db.flush()  # assign job.id
            new += 1
            to_process.append((job.id, item.raw_description is not None))
        elif existing.raw_description is None and item.raw_description:
            existing.raw_description = item.raw_description
            db.flush()
            updated += 1
            to_process.append((existing.id, True))
        # else: already present with a description (or no new description) — skip.

    db.commit()

    for job_id, has_description in to_process:
        background_tasks.add_task(_process_listing, job_id, body.profile_id, has_description)

    return {"received": received, "new": new, "updated": updated}


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

    Cover-letter generation only fires if the match score is >= the threshold
    (75); below that the call is a no-op and no quota is spent.
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
    )
    return {"status": "queued"}


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
