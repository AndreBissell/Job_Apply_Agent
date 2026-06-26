"""Structured extraction of a job listing's ``raw_description``.

Reads ``job_listings.raw_description`` for one job, asks Gemini (Flash-Lite, via
``app/llm/client.py``) to pull out the fields the matching engine and cover-letter
generator need, and persists them:

  * hard / soft skills        -> ``job_skills`` rows (skill_type 'hard' / 'soft')
  * qualification requirements -> ``job_listings.qualification_requirements`` (JSON)
  * experience requirements    -> ``job_listings.experience_requirements`` (JSON)
  * seniority / summary        -> ``job_listings.seniority`` / ``.summary``
  * key responsibilities       -> ``job_listings.key_responsibilities`` (JSON)
  * ``extracted_at``           -> set on success (NULL means "needs extraction")

Idempotent: prior extracted rows/fields for the job are cleared before re-insert,
so re-running never duplicates. ``force=True`` re-extracts an already-done job.
"""

from __future__ import annotations

import datetime
import json
import logging
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.llm.client import complete_json
from app.models import JobListing, JobSkill

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Response schema (Gemini constrains output to this via response_schema)
# ---------------------------------------------------------------------------
class QualificationRequirement(BaseModel):
    title: str
    field: str | None = None
    required: bool


class ExperienceRequirement(BaseModel):
    description: str
    years: float | None = None
    required: bool


Seniority = Literal[
    "intern", "graduate", "junior", "mid", "senior", "lead", "unknown"
]


class JobExtraction(BaseModel):
    hard_skills: list[str]
    soft_skills: list[str]
    qualifications: list[QualificationRequirement]
    experience: list[ExperienceRequirement]
    seniority: Seniority
    key_responsibilities: list[str]
    summary: str


_SYSTEM_PROMPT = """\
You extract structured data from a single job advertisement. Return ONLY valid \
JSON matching the provided schema — no prose, no markdown.

Rules:
- Extract ONLY what the ad states or clearly implies. Never invent requirements, \
skills, or qualifications that aren't supported by the text.
- hard_skills: concrete tools, languages, frameworks, platforms, methodologies \
(e.g. "Python", "AWS", "React", "Agile"). soft_skills: interpersonal/behavioural \
traits (e.g. "communication", "teamwork", "problem solving").
- For qualifications and experience, set required=false when the ad frames it as \
"preferred", "desirable", "nice to have", "bonus", "advantageous", or similar; \
set required=true for must-haves ("required", "must have", "essential").
- qualifications: each {title, field (optional), required}. title is the credential \
(e.g. "Bachelor's degree"); field is the discipline if stated (e.g. "Computer Science").
- experience: each {description, years (optional number), required}. Put a numeric \
years value only if the ad gives one.
- seniority: infer one of intern, graduate, junior, mid, senior, lead from the \
title, stated years, and language; use "unknown" if genuinely unclear.
- key_responsibilities: 3-6 short phrases, in the ad's own framing.
- summary: 2-3 neutral sentences describing the role. No salesy language.
Return empty arrays where a section has nothing to extract.
"""


def _as_session():
    return SessionLocal()


def extract_job(
    job_id: int,
    session=None,
    force: bool = False,
) -> None:
    """Extract + persist structured fields for one job listing.

    Self-contained: opens its own DB session when one isn't supplied (the API
    calls this as a post-response background task, after the request session is
    closed). Skips (with a warning/log) jobs that have no ``raw_description`` or
    that were already extracted unless ``force=True``.
    """
    own_session = session is None
    db = session or _as_session()
    try:
        job = db.get(JobListing, job_id)
        if job is None:
            logger.warning("extract_job: job %s not found", job_id)
            return
        if not job.raw_description:
            logger.warning(
                "extract_job: job %s has no raw_description — skipping", job_id
            )
            return
        if job.extracted_at is not None and not force:
            logger.info(
                "extract_job: job %s already extracted (%s) — skipping (use force)",
                job_id, job.extracted_at,
            )
            return

        data = complete_json(
            _SYSTEM_PROMPT,
            f"JOB TITLE: {job.title}\n\nJOB DESCRIPTION:\n{job.raw_description}",
            schema=JobExtraction,
            temperature=0.1,
        )
        extraction = JobExtraction.model_validate(data)

        # Idempotent: clear any prior extracted rows/fields for this job first.
        db.execute(delete(JobSkill).where(JobSkill.job_id == job_id))

        seen: set[str] = set()
        n_hard = n_soft = 0
        for name in extraction.hard_skills:
            key = name.strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            db.add(JobSkill(job_id=job_id, name=name.strip(), skill_type="hard"))
            n_hard += 1
        for name in extraction.soft_skills:
            key = name.strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            db.add(JobSkill(job_id=job_id, name=name.strip(), skill_type="soft"))
            n_soft += 1

        job.qualification_requirements = json.dumps(
            [q.model_dump(exclude_none=True) for q in extraction.qualifications]
        )
        job.experience_requirements = json.dumps(
            [e.model_dump(exclude_none=True) for e in extraction.experience]
        )
        job.key_responsibilities = json.dumps(extraction.key_responsibilities)
        job.seniority = extraction.seniority
        job.summary = extraction.summary
        job.extracted_at = datetime.datetime.now(datetime.timezone.utc)

        db.commit()
        logger.info(
            "extract_job: job %s done — %d hard / %d soft skills, %d quals, "
            "%d exp, seniority=%s",
            job_id, n_hard, n_soft, len(extraction.qualifications),
            len(extraction.experience), extraction.seniority,
        )
    except Exception:
        db.rollback()
        raise
    finally:
        if own_session:
            db.close()
