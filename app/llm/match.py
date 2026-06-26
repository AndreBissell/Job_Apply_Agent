"""Profile-to-job matching: score a candidate against an extracted listing.

For one (job, profile) pair this:
  1. loads the profile (qualifications, experiences + linked skills, skills) and
     the job (job_skills + extracted fields),
  2. computes cheap pre-filter signals (``app/llm/prefilter.py``) — context, not a gate,
  3. asks Gemini (Flash-Lite, via ``app/llm/client.py``) for {score, reasoning, gaps},
  4. upserts the ``matches`` row on the UNIQUE (user_id, job_id) constraint.

Idempotent: a second run for the same pair updates the existing row, never
duplicates. ``force=True`` re-scores an already-matched pair. Cover-letter
generation is a SEPARATE later task — nothing here writes ``cover_letters``.

All LLM access goes through ``client.complete_json`` (provider/model from .env).
"""

from __future__ import annotations

import json
import logging

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db import SessionLocal
from app.llm.client import complete_json
from app.llm.prefilter import PrefilterResult, prefilter
from app.models import Experience, JobListing, Match, Profile

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Response schema (Gemini constrains output to this via response_schema)
# ---------------------------------------------------------------------------
class MatchScore(BaseModel):
    score: int
    reasoning: str
    gaps: list[str]


_SYSTEM_PROMPT = (
    "You are an expert career advisor scoring how well a candidate fits a job "
    "listing. Return ONLY a JSON object — no prose, no markdown fences."
)

_USER_TEMPLATE = """\
Score this candidate against this job. Return a JSON object with exactly these keys:

{{
  "score": <integer 0-100>,
  "reasoning": "<2-4 sentence explanation of the score>",
  "gaps": ["<gap 1>", "<gap 2>", ...]
}}

Scoring rules — judge the WHOLE picture: core skills, real evidence, the role's
level, and whether it's even in the candidate's field. Calibrate to these bands:
- 85-100: Excellent. Meets essentially ALL core hard requirements with direct,
  demonstrated evidence AND fits the role's level. Reserve the very top (95+) for
  candidates whose experience clearly operates AT or above the job's seniority —
  rare for a new grad, whose evidence is shallow by nature.
- 65-84:  Strong. Meets most core requirements with real evidence; only minor gaps,
  or slightly below the stated level but clearly capable.
- 45-64:  Partial. Meaningful overlap BUT missing one or more CORE required skills
  the role centres on, or a clear level/scope gap. Transferable skills soften this
  but do NOT erase a missing core requirement.
- 25-44:  Weak. Few of the role's core skills present; fit rests mostly on generic
  or soft skills, or only loosely transferable experience.
- 0-24:   Poor. Little to no overlap in core skills AND the role sits in a different
  field or domain from the candidate's background.

new-grad fairness — apply WITHIN the bands above; it does not override them:
- "X years of experience" is a SOFT gap, not a disqualifier. A relevant internship,
  capstone, or project is real evidence and can lift a candidate by roughly one band.
- BUT a missing CORE hard skill the role is built around (e.g. the primary frontend
  framework for a front-end-heavy role) is a genuine gap that keeps the score in the
  partial band, no matter how strong the transferable skills.
- Do NOT score a candidate highly for a role in a different field just because their
  soft/communication skills are good.
- Gaps must be specific and honest, but "not enough years" alone is never the reason
  for a low score.

--- CANDIDATE ---
{profile_summary}

--- JOB ---
{job_summary}
"""


def _fmt_year(d) -> str:
    return str(d.year) if d else "?"


def _build_profile_summary(profile: Profile) -> str:
    lines = ["CANDIDATE PROFILE", "-----------------", "Qualifications:"]
    if profile.qualifications:
        for q in profile.qualifications:
            bits = [q.title]
            if q.institution:
                bits.append(q.institution)
            status = f" ({q.status})" if q.status else ""
            lines.append(f"  - {', '.join(bits)}{status}")
    else:
        lines.append("  (none on file)")

    skills = ", ".join(s.name for s in profile.skills) or "(none on file)"
    lines.append("")
    lines.append(f"Skills: {skills}")

    lines.append("")
    lines.append("Experience:")
    if profile.experiences:
        for i, e in enumerate(profile.experiences, 1):
            org = f" @ {e.organization}" if e.organization else ""
            span = f"({_fmt_year(e.start_date)} – {_fmt_year(e.end_date)})"
            lines.append(f"  {i}. {e.title}{org} {span}")
            used = ", ".join(sk.name for sk in e.skills)
            if used:
                lines.append(f"     Skills used: {used}")
            if e.description:
                lines.append(f"     Description: {e.description}")
    else:
        lines.append("  (none on file)")
    return "\n".join(lines)


def _json_list(raw: str | None) -> list:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return data if isinstance(data, list) else []


def _fmt_requirement(item) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        parts = [str(v) for v in (item.get("title"), item.get("description"),
                                  item.get("field")) if v]
        years = item.get("years")
        if years:
            parts.append(f"{years}+ yrs")
        if item.get("required") is False:
            parts.append("(preferred)")
        return " ".join(parts)
    return str(item)


def _build_job_summary(job: JobListing, pf: PrefilterResult) -> str:
    hard = ", ".join(js.name for js in job.job_skills if js.skill_type == "hard") or "(none)"
    soft = ", ".join(js.name for js in job.job_skills if js.skill_type == "soft") or "(none)"

    lines = [
        f"JOB: {job.title} at {job.company or 'Unknown'}",
        f"Location: {job.location or '?'}  |  Work type: {job.work_type or '?'}  "
        f"|  Seniority: {job.seniority or '?'}",
        "",
        f"Summary: {job.summary or '(none)'}",
        "",
        "Key responsibilities:",
    ]
    resp = _json_list(job.key_responsibilities)
    lines += [f"  - {r}" for r in resp] if resp else ["  (none listed)"]

    lines += ["", f"Hard skills wanted: {hard}", f"Soft skills wanted: {soft}", ""]

    lines.append("Qualification requirements:")
    quals = _json_list(job.qualification_requirements)
    lines += [f"  - {_fmt_requirement(q)}" for q in quals] if quals else ["  (none listed)"]

    lines.append("Experience requirements:")
    exps = _json_list(job.experience_requirements)
    lines += [f"  - {_fmt_requirement(e)}" for e in exps] if exps else ["  (none listed)"]

    lines += [
        "",
        "PRE-FILTER SIGNALS (computed before this prompt):",
        f"  Hard skill overlap: {pf.hard_overlap_count} of {pf.hard_total_job} matched",
        f"  Matched: {pf.hard_skill_matches}",
        f"  Gaps:    {pf.hard_skill_gaps}",
        f"  Seniority flag (senior/lead role): {pf.seniority_flag}",
        f"  Qualification text match: {pf.qual_match}",
    ]
    return "\n".join(lines)


def match_job(
    job_id: int,
    profile_id: int,
    session=None,
    force: bool = False,
) -> None:
    """Score one job against one profile and upsert the ``matches`` row.

    Self-contained: opens its own DB session when none is supplied (the API calls
    this as a post-response background task). Skips (with a log) jobs that aren't
    extracted yet, and already-scored pairs unless ``force=True``.
    """
    own_session = session is None
    db = session or SessionLocal()
    try:
        job = db.scalar(
            select(JobListing)
            .where(JobListing.id == job_id)
            .options(selectinload(JobListing.job_skills))
        )
        if job is None:
            logger.warning("match_job: job %s not found", job_id)
            return
        if job.extracted_at is None:
            logger.warning(
                "match_job: job %s not yet extracted — run extraction first", job_id
            )
            return

        profile = db.scalar(
            select(Profile)
            .where(Profile.id == profile_id)
            .options(
                selectinload(Profile.skills),
                selectinload(Profile.qualifications),
                selectinload(Profile.experiences).selectinload(Experience.skills),
            )
        )
        if profile is None:
            logger.warning("match_job: profile %s not found", profile_id)
            return

        existing = db.scalar(
            select(Match).where(Match.user_id == profile_id, Match.job_id == job_id)
        )
        if existing is not None and not force:
            logger.info(
                "match_job: match already exists for job %s / profile %s — skipping "
                "(use force)", job_id, profile_id,
            )
            return

        pf = prefilter(job, profile)
        logger.info(
            "Job %s pre-filter: %d/%d hard skills matched | matched=%s gaps=%s "
            "seniority_flag=%s qual_match=%s",
            job_id, pf.hard_overlap_count, pf.hard_total_job, pf.hard_skill_matches,
            pf.hard_skill_gaps, pf.seniority_flag, pf.qual_match,
        )

        prompt = _USER_TEMPLATE.format(
            profile_summary=_build_profile_summary(profile),
            job_summary=_build_job_summary(job, pf),
        )

        try:
            data = complete_json(_SYSTEM_PROMPT, prompt, schema=MatchScore, temperature=0.1)
            result = MatchScore.model_validate(data)
            score = max(0, min(100, result.score))
            reasoning = result.reasoning
            gaps = result.gaps
        except Exception as exc:  # noqa: BLE001 — record a row rather than vanish
            logger.exception("match_job: scoring job %s failed to parse", job_id)
            score = 0
            reasoning = "Parse error"
            gaps = [f"LLM response could not be parsed: {exc}"]

        gaps_json = json.dumps(gaps)
        if existing is not None:
            existing.score = score
            existing.reasoning = reasoning
            existing.gaps = gaps_json
            existing.status = "new"
        else:
            db.add(
                Match(
                    user_id=profile_id,
                    job_id=job_id,
                    score=score,
                    reasoning=reasoning,
                    gaps=gaps_json,
                    status="new",
                )
            )

        db.commit()
        logger.info("Scored job %s for profile %s: %s/100", job_id, profile_id, score)
    except Exception:
        db.rollback()
        raise
    finally:
        if own_session:
            db.close()
