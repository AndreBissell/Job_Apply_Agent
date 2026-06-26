"""Cheap, LLM-free pre-filter signals for job <-> profile matching.

Pure computation over already-loaded ORM objects: no DB writes, no LLM calls.
The result is passed as structured *context* into the scoring prompt — it does
NOT gate whether a job gets scored. Every extracted job still gets a score row
(per docs/database-schema.md / CLAUDE.md), so nothing silently vanishes; a job
with zero hard-skill overlap may still score on soft skills + qualifications.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from app.models import JobListing, Profile

logger = logging.getLogger(__name__)

# Seniority tokens that flag a role likely above a new-grad's level. Used only as
# a signal for the LLM (a "senior" title is a soft headwind, never a hard gate).
_SENIOR_TOKENS = {"senior", "lead", "principal", "manager", "head", "director"}

# Specific near-miss aliases collapsed to a canonical form before comparison.
# Extend this list as new near-misses surface (see scripts/check_matching.py's
# near-miss warnings). Keys are already lower-cased + punctuation-normalised.
_SKILL_ALIASES = {
    "powerbi": "power bi",
    "power bi": "power bi",
    "ms excel": "excel",
    "microsoft excel": "excel",
    "ms sql": "sql",
    "mysql": "sql",
    "postgresql": "sql",   # broad: treat any relational SQL dialect as "sql"
    "postgres": "sql",
    "restful apis": "rest apis",
    "restful api": "rest apis",
    "rest api": "rest apis",
    "python 3": "python",
    "python3": "python",
    "js": "javascript",
    "node js": "javascript",
    "nodejs": "javascript",
    "html css": "html/css",
    "css": "html/css",
    "html": "html/css",
    "git hub": "git",
    "github": "git",
    "gitlab": "git",
}


def normalise_skill(name: str) -> str:
    """Normalise a skill name for matching.

    Lower-cases, strips punctuation variants (``.-_/``) to spaces, collapses
    whitespace, then folds common aliases so near-identical skills compare equal
    (e.g. "PowerBI" / "Power BI", "PostgreSQL" / "SQL"). This is used ONLY for the
    comparison step — the original, un-normalised names are still what gets stored
    and shown to the LLM.
    """
    s = name.lower().strip()
    s = re.sub(r"[.\-_/]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return _SKILL_ALIASES.get(s, s)


@dataclass
class PrefilterResult:
    hard_skill_matches: list[str]   # job hard skills the user has (case-insensitive)
    hard_skill_gaps: list[str]      # job hard skills the user does NOT have
    soft_skill_matches: list[str]   # same for soft
    soft_skill_gaps: list[str]
    hard_overlap_count: int         # len(hard_skill_matches)
    hard_total_job: int             # total hard skills the job wants
    seniority_flag: bool            # True if job seniority is senior/lead/etc.
    qual_match: bool                # True if a user qual overlaps the job's qual text
    overlap_pct: float              # hard_overlap_count / hard_total_job (0.0 if none)


def _partition(job_skills, user_skill_names: set[str]) -> tuple[list[str], list[str]]:
    """Split job skill names into (matches, gaps) by normalised membership.

    Comparison is on ``normalise_skill`` of each name, but the ORIGINAL job skill
    names are returned so the LLM prompt and diagnostics show real names.
    """
    matches: list[str] = []
    gaps: list[str] = []
    for js in job_skills:
        if normalise_skill(js.name) in user_skill_names:
            matches.append(js.name)
        else:
            gaps.append(js.name)
    return matches, gaps


def _load_qual_requirements(raw: str | None) -> list[str]:
    """Job qualification_requirements is JSON; return a list of plain text strings.

    Extraction stores a list of {title, field, required} dicts, but tolerate a
    plain list of strings too. Parse errors degrade to an empty list.
    """
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    out: list[str] = []
    for item in data if isinstance(data, list) else []:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            out.append(" ".join(str(v) for v in (item.get("title"), item.get("field")) if v))
    return out


def prefilter(job: JobListing, profile: Profile) -> PrefilterResult:
    """Compute the cheap match signals for one (job, profile) pair."""
    user_skill_names = {normalise_skill(s.name) for s in profile.skills}

    hard_skills = [js for js in job.job_skills if js.skill_type == "hard"]
    soft_skills = [js for js in job.job_skills if js.skill_type == "soft"]

    hard_matches, hard_gaps = _partition(hard_skills, user_skill_names)
    soft_matches, soft_gaps = _partition(soft_skills, user_skill_names)

    seniority_flag = bool(job.seniority) and job.seniority.lower() in _SENIOR_TOKENS

    # qual_match: any 4+ char word from a user qualification's field_of_study/title
    # appearing in any job qualification requirement string (case-insensitive).
    qual_words: set[str] = set()
    for q in profile.qualifications:
        for field in (q.field_of_study, q.title):
            if field:
                qual_words.update(w for w in field.lower().split() if len(w) >= 4)
    qual_reqs = " ".join(_load_qual_requirements(job.qualification_requirements)).lower()
    qual_match = any(w in qual_reqs for w in qual_words) if qual_reqs else False

    hard_total = len(hard_skills)
    overlap_pct = (len(hard_matches) / hard_total) if hard_total else 0.0

    return PrefilterResult(
        hard_skill_matches=hard_matches,
        hard_skill_gaps=hard_gaps,
        soft_skill_matches=soft_matches,
        soft_skill_gaps=soft_gaps,
        hard_overlap_count=len(hard_matches),
        hard_total_job=hard_total,
        seniority_flag=seniority_flag,
        qual_match=qual_match,
        overlap_pct=overlap_pct,
    )
