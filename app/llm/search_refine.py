"""Optional LLM re-ranker for search suggestions. Off by default.

This is Layer 4 of the suggestion pipeline and the only part that costs money.
It exists because the pure-Python miner in ``app/search_suggest.py`` can only
recombine words the user's own match history already contains: it will never
propose "Business Analyst" to someone whose matches all say "Data Analyst",
even when the profile clearly supports both.

Two design rules keep it cheap and keep it honest:

*   **It picks, it does not invent.** The prompt supplies the mined candidates
    and asks the model to select and canonicalise among them. It may generalise
    a phrase (drop a qualifier so the search casts wider) but is told not to
    introduce a role the evidence does not support. A hallucinated search term
    wastes a scan, which is far more expensive than the call itself.
*   **It is debounced, not live.** ``should_refresh`` gates on how many new
    scored matches have landed since the cached result was written, so a
    steady drip of new jobs does not mean a call per job. The result is cached
    in ``profiles.preferences``.

Uses ``OPENAI_MODEL_SMALL`` (gpt-5-nano by default) via the provider
abstraction — never the SDK directly. One call is roughly 1.5k tokens.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from app.llm.client import complete_json

logger = logging.getLogger(__name__)

# Minimum number of newly-scored matches since the last refine before another
# call is worth making. The miner's ranking barely moves on a handful of new
# rows, so refining more often buys nothing.
REFRESH_AFTER_NEW_MATCHES = 10

MAX_CANDIDATES = 15
MAX_SUGGESTIONS = 5


class RefinedSearches(BaseModel):
    """Schema handed to ``complete_json``."""

    searches: list[str] = Field(
        default_factory=list,
        description="Seek search phrases, best first, 2-4 words each.",
    )


_SYSTEM_PROMPT = """You choose job-search keyword phrases for a Seek (Australian \
job board) user.

You are given: the user's profile summary, their skills, the search phrases \
already saved and active, and a ranked list of candidate phrases mined from job \
titles the user actually matched well against. Each candidate carries the number \
of matching jobs (support) and their average match score out of 100.

Return the best %(count)d phrases to search next, best first.

Rules:
- Prefer candidates with strong evidence (high average score, higher support).
- You may GENERALISE a candidate so it casts a wider net on Seek — dropping a \
qualifier is good when the qualifier is rare in real job titles.
- Do NOT introduce a role the candidates and profile do not support. No \
speculative career pivots.
- Omit anything already covered by an active saved search.
- Omit seniority words (graduate, junior, senior, intermediate, experienced). \
They are filters on Seek, not useful keywords, and including them shrinks the \
result set.
- Each phrase must be a real job title someone would type: 2-4 words, ending in \
a role noun (engineer, analyst, developer, assistant, officer, ...).
- Return only the phrases. No explanation.""" % {"count": MAX_SUGGESTIONS}


def should_refresh(cached: dict | None, scored_match_count: int) -> bool:
    """True when a refine call is justified.

    ``cached`` is the stored ``llm_search_suggestions_cache`` (or None). A
    cache written when the user had N scored matches is considered current
    until they have N + REFRESH_AFTER_NEW_MATCHES.
    """
    if not cached or not cached.get("searches"):
        return True
    previous = cached.get("match_count")
    if not isinstance(previous, int):
        return True
    # abs(): the retention sweep deletes old matches, so the count can fall as
    # well as rise, and a corpus that shrank by N is as changed as one that grew.
    return abs(scored_match_count - previous) >= REFRESH_AFTER_NEW_MATCHES


def _profile_block(profile) -> str:
    if profile is None:
        return "(no profile on file)"
    lines = []
    if profile.target_role:
        lines.append(f"Target role: {profile.target_role}")
    if profile.summary:
        lines.append(f"Summary: {profile.summary}")
    skills = [s.name for s in profile.skills][:25]
    if skills:
        lines.append(f"Skills: {', '.join(skills)}")
    quals = [
        f"{q.title}{f' ({q.field_of_study})' if q.field_of_study else ''}"
        for q in profile.qualifications
    ][:5]
    if quals:
        lines.append(f"Qualifications: {'; '.join(quals)}")
    return "\n".join(lines) or "(profile has no searchable detail)"


def refine(profile, candidates: list[dict], active_keywords: list[str]) -> list[str]:
    """One ``complete_json`` call turning mined candidates into search phrases.

    Returns ``[]`` on any failure — the caller falls back to the mined list, so
    a dead API key or a rate limit degrades the feature instead of breaking it.
    """
    if not candidates:
        return []

    candidate_lines = "\n".join(
        f"- {c['phrase']} (support={c['support']}, avg score={c['avg_score']})"
        for c in candidates[:MAX_CANDIDATES]
    )
    active = ", ".join(active_keywords) if active_keywords else "(none)"

    prompt = (
        f"PROFILE\n{_profile_block(profile)}\n\n"
        f"ACTIVE SAVED SEARCHES\n{active}\n\n"
        f"CANDIDATE PHRASES (mined from well-matched job titles, best first)\n"
        f"{candidate_lines}\n"
    )

    try:
        data = complete_json(
            _SYSTEM_PROMPT, prompt, schema=RefinedSearches, temperature=0.1
        )
        result = RefinedSearches.model_validate(data)
    except Exception:  # noqa: BLE001 — suggestions are a nicety, never fatal
        logger.exception("LLM search refine failed; falling back to mined phrases")
        return []

    seen: set[str] = set()
    out: list[str] = []
    for phrase in result.searches:
        cleaned = " ".join((phrase or "").split()).strip()
        key = cleaned.lower()
        if cleaned and key not in seen:
            seen.add(key)
            out.append(cleaned)
    return out[:MAX_SUGGESTIONS]
