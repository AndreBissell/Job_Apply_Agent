"""Cover-letter generation: draft a tailored letter for a strong match.

Called ON DEMAND only — never automatically on ingest. Triggered by the
/jobs/{job_id}/regenerate endpoint when the user requests a letter.

One complete_text call per letter (temp 0.7). Evidence is pulled from real
experience_skills links — nothing invented. Below THRESHOLD: no call, no row.

All LLM access goes through client.complete_text (provider/model from .env).
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db import SessionLocal
from app.llm.client import complete_text
from app.models import CoverLetter, Experience, JobListing, Match, Profile

logger = logging.getLogger(__name__)

# Only generate for matches at or above this score (0-100). Quality gate:
# a weak match produces a weak letter and wastes an LLM call.
THRESHOLD = 75

_SYSTEM_PROMPT = (
    "You are an expert cover letter writer. "
    "Write a compelling, tailored cover letter in first person based ONLY on evidence "
    "from the candidate profile supplied — never invent or embellish experience. "
    "3–4 concise paragraphs, natural professional prose. No placeholders, no subject "
    "line, no date headers — just the body of the letter."
)


def _json_list(raw: str | None) -> list:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return data if isinstance(data, list) else []


def _fmt_year(d) -> str:
    return str(d.year) if d else "present"


def _build_prompt(profile: Profile, job: JobListing, match: Match) -> str:
    """Self-contained prompt: all profile + job + match context in one block."""
    lines: list[str] = []

    # ── Candidate ─────────────────────────────────────────────────────────────
    lines.append("=== CANDIDATE PROFILE ===")
    lines.append(f"Name: {profile.name}")

    lines.append("\nQualifications:")
    for q in profile.qualifications:
        parts = [q.title]
        if q.institution:
            parts.append(q.institution)
        if q.field_of_study:
            parts.append(q.field_of_study)
        status = f" ({q.status})" if q.status else ""
        lines.append(f"  - {', '.join(parts)}{status}")
    if not profile.qualifications:
        lines.append("  (none on file)")

    lines.append("\nExperience:")
    for e in profile.experiences:
        org = f" at {e.organization}" if e.organization else ""
        span = f"{_fmt_year(e.start_date)}–{_fmt_year(e.end_date)}"
        lines.append(f"  [{e.experience_type}] {e.title}{org} ({span})")
        if e.description:
            lines.append(f"    {e.description}")
        skill_names = ", ".join(sk.name for sk in e.skills)
        if skill_names:
            lines.append(f"    Skills demonstrated: {skill_names}")
    if not profile.experiences:
        lines.append("  (none on file)")

    # ── Job ───────────────────────────────────────────────────────────────────
    lines.append("\n=== JOB ===")
    lines.append(f"Role: {job.title} at {job.company or 'Unknown'}")
    lines.append(
        f"Location: {job.location or '?'}  |  Work type: {job.work_type or '?'}"
        f"  |  Level: {job.seniority or '?'}"
    )
    if job.summary:
        lines.append(f"\nRole summary: {job.summary}")

    responsibilities = _json_list(job.key_responsibilities)
    if responsibilities:
        lines.append("\nKey responsibilities:")
        lines.extend(f"  - {r}" for r in responsibilities)

    hard_skills = [js.name for js in job.job_skills if js.skill_type == "hard"]
    soft_skills = [js.name for js in job.job_skills if js.skill_type == "soft"]
    if hard_skills:
        lines.append(f"\nCore skills required: {', '.join(hard_skills)}")
    if soft_skills:
        lines.append(f"Soft skills valued: {', '.join(soft_skills)}")

    # ── Evidence map: which experiences back the job's required skills ────────
    # Walk experience_skills to find real evidence — never invent it.
    if hard_skills and profile.experiences:
        skill_to_exps: dict[str, list[str]] = {}
        for e in profile.experiences:
            label = e.title + (f" @ {e.organization}" if e.organization else "")
            for sk in e.skills:
                skill_to_exps.setdefault(sk.name.lower(), []).append(label)

        evidence_lines: list[str] = []
        for js_name in hard_skills:
            backing = skill_to_exps.get(js_name.lower(), [])
            if backing:
                evidence_lines.append(f"  - {js_name}: {', '.join(backing)}")

        if evidence_lines:
            lines.append("\nEvidence for core skills (use this; don't claim more):")
            lines.extend(evidence_lines)

    # ── Match context ─────────────────────────────────────────────────────────
    lines.append("\n=== MATCH CONTEXT ===")
    if match.reasoning:
        lines.append(f"Fit summary: {match.reasoning}")
    gaps = _json_list(match.gaps)
    if gaps:
        lines.append(
            f"Gaps (be honest — don't overclaim these): {', '.join(gaps)}"
        )

    lines.append(
        "\nWrite the cover letter now. Lead with the strongest evidence. "
        "Don't claim skills or experiences not listed above."
    )
    return "\n".join(lines)


def generate_cover_letter(
    job_id: int,
    profile_id: int,
    session=None,
    force: bool = False,
) -> CoverLetter | None:
    """Draft a cover letter for a job/profile match, if score >= THRESHOLD.

    Idempotent: a second call updates the existing row rather than duplicating.
    Returns the CoverLetter row when generated/updated, or None when below
    threshold (no LLM call made).

    Self-contained: opens its own DB session when none is supplied.
    """
    own_session = session is None
    db = session or SessionLocal()
    try:
        match = db.scalar(
            select(Match)
            .where(Match.user_id == profile_id, Match.job_id == job_id)
            .options(selectinload(Match.cover_letter))
        )
        if match is None:
            logger.warning(
                "generate_cover_letter: no match for job %s / profile %s",
                job_id, profile_id,
            )
            return None

        if match.score is None or float(match.score) < THRESHOLD:
            logger.info(
                "generate_cover_letter: job %s score %s below threshold %s — skipping",
                job_id, match.score, THRESHOLD,
            )
            return None

        if match.cover_letter is not None and not force:
            logger.info(
                "generate_cover_letter: letter already exists for match %s — skipping "
                "(pass force=True to regenerate)",
                match.id,
            )
            return match.cover_letter

        job = db.scalar(
            select(JobListing)
            .where(JobListing.id == job_id)
            .options(selectinload(JobListing.job_skills))
        )
        profile = db.scalar(
            select(Profile)
            .where(Profile.id == profile_id)
            .options(
                selectinload(Profile.qualifications),
                selectinload(Profile.experiences).selectinload(Experience.skills),
            )
        )
        if job is None or profile is None:
            logger.warning(
                "generate_cover_letter: job %s or profile %s not found",
                job_id, profile_id,
            )
            return None

        prompt = _build_prompt(profile, job, match)
        content = complete_text(_SYSTEM_PROMPT, prompt, temperature=0.7)

        existing = match.cover_letter
        if existing is not None:
            existing.generated_content = content
            existing.status = "draft"
            cl = existing
        else:
            cl = CoverLetter(
                match_id=match.id,
                generated_content=content,
                status="draft",
            )
            db.add(cl)

        db.commit()
        logger.info(
            "Generated cover letter for job %s / profile %s (match %s, score %s)",
            job_id, profile_id, match.id, match.score,
        )
        return cl
    except Exception:
        db.rollback()
        raise
    finally:
        if own_session:
            db.close()
