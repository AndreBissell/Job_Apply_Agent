"""Search suggestions: GET /jobs/search-performance and GET /jobs/suggested-searches.

Both must be matched before ``GET /jobs/{job_id}`` (this router is included before
``jobs.router``, and tests/test_route_table.py checks it). ``suggested_searches`` can make one
small model call (layer 4, ``search_refine``) when the preference is on; the tests stub it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app import retention, search_suggest
from app.api.deps import get_db
from app.llm import search_refine
from app.llm.cover_letter import THRESHOLD as COVER_LETTER_THRESHOLD
from app.models import JobListing, Match, Profile, SavedSearch
from app.preferences import get_preferences, set_preferences

router = APIRouter()


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


@router.get("/jobs/search-performance")
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


@router.get("/jobs/suggested-searches")
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
