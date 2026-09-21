"""Pre-extraction quick screen: a cheap first pass that skips the two expensive
LLM calls (extraction + matching) for jobs that are obviously the wrong field.

Runs BEFORE extract_job. Sees only a short excerpt (title/company/location/work_type/
classification/subclassification + the first ~300 chars of raw_description) and a
minimal profile summary (target_role/target_location, skill names, qualification
titles) — deliberately not the full profile match.py sends, so this call stays cheap
relative to the extraction+match pair it's trying to save.

Deliberately conservative: the prompt instructs a HIGH default score whenever
uncertain, because a wrongly-skipped job is a real opportunity lost, while a
wrongly-passed job only costs one extra (comparatively cheap) extraction+match pair.

  * score  < SKIP_THRESHOLD -> writes a terminal ``matches`` row (same upsert shape
    match_job uses) with that score and an "auto-skipped" reasoning string, and
    leaves ``extracted_at`` NULL. No extraction/matching ever runs for it unless the
    job is later force-reprocessed (see /jobs/{id}/regenerate, which bypasses this
    screen entirely and goes straight to extraction+matching).
  * score >= SKIP_THRESHOLD -> stamps ``quick_screen_at``/``quick_screen_score`` and
    lets Phase 1 of the idle loop proceed as normal.
  * On any LLM failure other than a daily-quota error, this FAILS OPEN: stamps
    ``quick_screen_at`` with ``quick_screen_score=None`` and writes no skip row, so
    the job still reaches full extraction next iteration. A DailyQuotaError is
    re-raised so the idle loop can back off, same as extraction/matching do.
"""

from __future__ import annotations

import datetime
import logging

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db import SessionLocal
from app.llm.client import DailyQuotaError, complete_json
from app.models import JobListing, Match, Profile

logger = logging.getLogger(__name__)

SKIP_THRESHOLD = 20  # bottom of match.py's "0-24 Poor" band — same 0-100 scale


class QuickScreenOutput(BaseModel):
    score: int
    reason: str


_SYSTEM_PROMPT = (
    "You are doing a FAST, CHEAP first pass over a job listing, before the full "
    "listing is read by a more careful process. You only see a short excerpt, not "
    "the full description. Because your information is incomplete, you must be "
    "CONSERVATIVE and default to a HIGHER score whenever unsure: wrongly passing a "
    "bad job through costs one extra cheap step; wrongly rejecting a good job means "
    "the candidate never sees it at all. Return ONLY a JSON object — no prose, no "
    "markdown fences."
)

_USER_TEMPLATE = """\
Quickly gauge whether this job could PLAUSIBLY be relevant to this candidate, based \
on limited information. Return a JSON object with exactly these keys:

{{
  "score": <integer 0-100>,
  "reason": "<one short sentence>"
}}

Score on the SAME 0-100 scale a full match review would use, but this is only a \
coarse first pass on PARTIAL information:
- Score under 20 ONLY if the role is CLEARLY and OBVIOUSLY unrelated to the \
candidate's field, target role, and skillset — e.g. a hospitality role for a \
software engineer, or a senior executive role for a new graduate with no matching \
signals at all.
- In every other case — including when you're simply unsure, the excerpt is too \
short to judge, or there is ANY plausible overlap — score 40 or higher. Missing \
detail is NOT evidence of a bad fit; never treat it as one.
- The "reason" field must point to a specific, positive signal of mismatch if the \
score is low — never "not enough information" or similar.

--- CANDIDATE (brief) ---
{profile_summary}

--- JOB (partial — first ~300 characters only) ---
{job_summary}
"""


def _build_quick_profile_summary(profile: Profile) -> str:
    lines = ["CANDIDATE (brief)"]
    if profile.target_role or profile.target_location:
        bits = []
        if profile.target_role:
            bits.append(f"Target role: {profile.target_role}")
        if profile.target_location:
            bits.append(f"Target location: {profile.target_location}")
        lines.append(" | ".join(bits))
    quals = ", ".join(q.title for q in profile.qualifications) or "(none on file)"
    lines.append(f"Qualifications: {quals}")
    skills = ", ".join(s.name for s in profile.skills) or "(none on file)"
    lines.append(f"Skills: {skills}")
    return "\n".join(lines)


def _build_quick_job_summary(job: JobListing) -> str:
    lines = [
        f"JOB: {job.title} at {job.company or 'Unknown'}",
        f"Location: {job.location or '?'}  |  Work type: {job.work_type or '?'}",
    ]
    if job.classification or job.subclassification:
        lines.append(
            f"Category: {job.classification or '?'} / {job.subclassification or '?'}"
        )
    excerpt = (job.raw_description or "")[:300].strip()
    lines.append(f"Excerpt: {excerpt or '(no description)'}")
    return "\n".join(lines)


def _as_session():
    return SessionLocal()


def quick_screen(
    job_id: int,
    profile_id: int,
    session=None,
    force: bool = False,
) -> str:
    """Run the cheap pre-extraction screen for one job.

    Returns one of "skipped" | "passed" | "error-passed" | "not-run" describing
    the outcome. Self-contained: opens its own DB session when none is supplied,
    matching extract_job/match_job's convention.
    """
    own_session = session is None
    db = session or _as_session()
    try:
        job = db.get(JobListing, job_id)
        if job is None:
            logger.warning("quick_screen: job %s not found", job_id)
            return "not-run"
        if not job.raw_description:
            logger.warning(
                "quick_screen: job %s has no raw_description — skipping", job_id
            )
            return "not-run"
        if job.quick_screen_at is not None and not force:
            logger.info(
                "quick_screen: job %s already screened (%s) — skipping (use force)",
                job_id, job.quick_screen_at,
            )
            return "not-run"

        profile = db.scalar(
            select(Profile)
            .where(Profile.id == profile_id)
            .options(selectinload(Profile.skills), selectinload(Profile.qualifications))
        )
        if profile is None:
            logger.warning("quick_screen: profile %s not found", profile_id)
            return "not-run"

        prompt = _USER_TEMPLATE.format(
            profile_summary=_build_quick_profile_summary(profile),
            job_summary=_build_quick_job_summary(job),
        )

        try:
            data = complete_json(
                _SYSTEM_PROMPT, prompt, schema=QuickScreenOutput, temperature=0.1
            )
            result = QuickScreenOutput.model_validate(data)
            score = max(0, min(100, result.score))
            reason = result.reason
        except DailyQuotaError:
            raise
        except Exception:
            logger.exception(
                "quick_screen: job %s failed — failing open (proceeds to extraction)",
                job_id,
            )
            job.quick_screen_at = datetime.datetime.now(datetime.timezone.utc)
            job.quick_screen_score = None
            db.commit()
            return "error-passed"

        job.quick_screen_at = datetime.datetime.now(datetime.timezone.utc)
        job.quick_screen_score = score

        if score < SKIP_THRESHOLD:
            existing = db.scalar(
                select(Match).where(Match.user_id == profile_id, Match.job_id == job_id)
            )
            reasoning = f"Auto-skipped by pre-extraction quick screen: {reason}"
            if existing is not None:
                existing.score = score
                existing.reasoning = reasoning
                existing.gaps = "[]"
                existing.status = "new"
                existing.scored_at = datetime.datetime.now(datetime.timezone.utc)
            else:
                db.add(
                    Match(
                        user_id=profile_id,
                        job_id=job_id,
                        score=score,
                        reasoning=reasoning,
                        gaps="[]",
                        status="new",
                        scored_at=datetime.datetime.now(datetime.timezone.utc),
                    )
                )
            db.commit()
            logger.info(
                "quick_screen: job %s SKIPPED (score=%s): %s", job_id, score, reason
            )
            return "skipped"

        db.commit()
        logger.info(
            "quick_screen: job %s passed (score=%s): %s", job_id, score, reason
        )
        return "passed"
    except Exception:
        db.rollback()
        raise
    finally:
        if own_session:
            db.close()
