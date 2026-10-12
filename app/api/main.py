"""FastAPI backend for the Seek Job Assistant Chrome extension.

Receives job listings the user's *real* browser scraped from Seek pages they
opened themselves (POST /ingest) and upserts them into ``job_listings``. /ingest
makes no LLM call: the idle loop (``_processing_idle_loop``) does the AI work one
job at a time on a single worker thread, in this order: retention sweep, cover
letters, quick screen, extraction + matching. Also serves everything the side panel
and profile editor read and write (/jobs, /profile, /obligation, letters, screening).

Run it with ``python scripts/run_api.py real|test`` (uvicorn on 127.0.0.1, port
8000 for real and 8001 for test).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import csv
import io
import json
import logging
import re
import zipfile
from collections import Counter
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app import retention, search_suggest
from app.api import events, idle_loop, paths, worker
from app.api.deps import get_db  # re-exported: tests key dependency_overrides on it
from app.api.health import router as health_router
from app.api.letters import router as letters_router
from app.api.obligation import router as obligation_router
from app.api.origin_guard import OriginGuardMiddleware
from app.api.profile import router as profile_router
from app.api.profile_ui import router as profile_ui_router
from app.api.screening import router as screening_router
from app.llm import search_refine
from app.llm.client import DailyQuotaError
from app.llm.cover_letter import THRESHOLD as COVER_LETTER_THRESHOLD
from app.llm.letter import view as letter_view
from app.llm.quickscreen import quick_screen
from app.models import (
    CoverLetter,
    JobListing,
    Match,
    Profile,
    SavedSearch,
)
from app.preferences import get_preferences, set_preferences
from app.screenshots import downscale_png, unlink_screenshot

logger = logging.getLogger(__name__)

SOURCE = "seek"

@asynccontextmanager
async def lifespan(app: FastAPI):
    events._event_loop = asyncio.get_running_loop()
    task = asyncio.create_task(idle_loop._processing_idle_loop())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="Seek Job Assistant", version="0.1.0", lifespan=lifespan)

# No CORS middleware, on purpose: with no Access-Control-* headers a web page cannot
# read any response or send a preflighted call (JSON body, PATCH/PUT/DELETE) to this
# server. The extension doesn't need them (its pages and background worker have
# host_permissions for localhost), and the profile editor is served from this same
# origin. See docs/cleanup-3-8-plan.md step 5.1.
# What CORS can't stop is a "simple" cross-site write (a body-less POST such as
# /regenerate), so OriginGuardMiddleware refuses writes from other websites.
app.add_middleware(OriginGuardMiddleware)

app.include_router(profile_ui_router)
app.include_router(letters_router)
app.include_router(screening_router)
app.include_router(obligation_router)
app.include_router(health_router)
app.include_router(events.router)
app.include_router(profile_router)

app.mount("/static", StaticFiles(directory=str(paths._static_dir)), name="static")
app.mount("/screenshots", StaticFiles(directory=str(paths._screenshots_dir)), name="screenshots")


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
    discovered_query: str | None = None


class IngestBody(BaseModel):
    listings: list[IngestListing]
    profile_id: int = 1


class StatusUpdate(BaseModel):
    # The documented vocabulary (docs/database-schema.md, matches.status).
    status: Literal["new", "shortlisted", "applied", "interviewing", "rejected", "withdrawn"]


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


def _eligibility_notes(job: JobListing) -> list[str]:
    """Requirements the cover-letter pipeline judged not-for-a-letter (work rights,
    licence, clearance, availability), for display on the job card as a heads-up.

    Empty both when there are none AND when the job has not been analysed yet —
    ``analyze_job`` only runs for jobs headed for a letter, so most cards have no
    checklist. Costs nothing: it only reads the cached JSON.
    """
    if not job.requirements_checklist:
        return []
    try:
        checklist = json.loads(job.requirements_checklist)
    except (json.JSONDecodeError, TypeError):
        return []
    reqs = checklist.get("requirements") if isinstance(checklist, dict) else None
    return [
        r["text"] for r in (reqs or [])
        if isinstance(r, dict) and r.get("letter_role") == "not_for_letter" and r.get("text")
    ]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
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
                discovered_query=item.discovered_query,
                raw_description=item.raw_description,
            )
            db.add(job)
            db.flush()  # assign job.id before we report it back
            new += 1
            job_ids.append(job.id)
        else:
            # A listing is typically ingested twice — once as a search-results
            # card (which carries discovered_query but no description or
            # taxonomy) and once as a detail page (the reverse). Backfill any
            # field this payload can fill and the row is still missing, so
            # whichever arrives second completes the row. Existing values are
            # never overwritten: the first capture of a field is the one made
            # while the page was actually open.
            changed = False
            for attr in (
                "raw_description",
                "classification",
                "subclassification",
                "discovered_query",
                # Card fields too: a job first captured from its detail page
                # used to keep company/location NULL forever, even after its
                # search card (which carries them) was ingested.
                "company",
                "location",
                "work_type",
                "salary",
            ):
                if getattr(existing, attr) is None and getattr(item, attr):
                    setattr(existing, attr, getattr(item, attr))
                    changed = True
            if changed:
                updated += 1
            job_ids.append(existing.id)

    db.commit()
    return {"received": received, "new": new, "updated": updated, "job_ids": job_ids}


@app.get("/jobs/known-ids")
def known_job_ids(db: Session = Depends(get_db)) -> dict:
    """Return all source_job_ids already in the database so the extension can skip re-scraping."""
    ids = db.scalars(select(JobListing.source_job_id)).all()
    return {"source_ids": ids}


@app.get("/jobs/pending-count")
def pending_job_count(profile_id: int = 1, db: Session = Depends(get_db)) -> dict:
    """How many captured jobs are still waiting on the LLM pass (no match row yet).

    The sidebar shows this at the bottom of the list ("N more scanned, analysing")
    since /jobs only returns jobs that already have a scored match. A quick-screen
    skip writes a terminal match row, so skipped jobs are already out of this count.
    Jobs with no description can't be processed and are excluded.
    """
    pending = db.scalar(
        select(func.count())
        .select_from(JobListing)
        .where(JobListing.raw_description.isnot(None))
        .where(JobListing.expired_detected_at.is_(None))
        .where(
            ~JobListing.matches.any(Match.user_id == profile_id)
        )
    )
    return {"pending": pending or 0}


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
    bypasses the idle loop's queue. If the idle loop's Phase 0 reaches this exact
    job at the same time, quick_screen() claims the job before its model call, so
    only one of the two calls runs and the other reports "not-run" (score null,
    which the sidebar already treats as "already processed"). Without the claim
    both would pay for the model and race to write the same match row.
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
    ready: bool = False,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> list[dict]:
    """Matched jobs for a profile.

    Only returns jobs that have a scored ``matches`` row (>= ``min_score``); jobs
    awaiting the LLM pass simply don't appear yet. Without ``status``, ranked by
    score desc (the normal Jobs tab). With ``status`` (e.g. ``applied``), ranked
    by ``applied_at`` desc instead — that's the Applied tab / Centrelink export,
    where recency of application matters more than match quality. ``status=applied``
    means ``applied_at IS NOT NULL``; any other value filters the ``status`` column.

    Jobs flagged ``expired_detected_at`` (see PATCH /jobs/{id}/expired) are
    excluded from the default (no-``status``) view — the Jobs tab should only
    ever show listings the user can actually still apply to. This filter is
    skipped for ``status`` queries: a listing closing after the user already
    applied doesn't invalidate that application as Centrelink evidence.
    ``include_expired=true`` lifts the filter on the default view too, mainly
    for debugging/verification.

    Matches hidden by the bulk low-score action (``hidden_at``) are excluded
    from every view here, including the Applied tab. They still exist, and
    still feed the suggestion baseline and the search-performance yield — see
    DELETE /jobs for why they are kept rather than deleted.

    ``ready=true`` (the Overview's Keep Applying row): only jobs with a cover letter
    that aren't applied yet, by score. Filtered here rather than in the sidebar so the
    ``limit`` can't drop ready jobs behind letter-less ones.
    """
    query = (
        select(Match, JobListing)
        .join(JobListing, Match.job_id == JobListing.id)
        .options(selectinload(JobListing.job_skills))
        .where(Match.user_id == profile_id)
        .where(Match.hidden_at.is_(None))
        .where(Match.score >= min_score)
    )
    if ready:
        query = query.where(Match.applied_at.is_(None)).where(
            select(CoverLetter.id).where(CoverLetter.match_id == Match.id).exists()
        )
    if status is not None:
        # "Applied" means applied_at is set (same rule as the dashboard and the export).
        if status == "applied":
            query = query.where(Match.applied_at.is_not(None))
        else:
            query = query.where(Match.status == status)
        query = query.order_by(Match.applied_at.desc())
    else:
        if not include_expired:
            query = query.where(JobListing.expired_detected_at.is_(None))
        query = query.order_by(Match.score.desc())
    rows = db.execute(query.limit(limit).offset(offset)).all()
    ttl = timedelta(days=retention.screenshot_ttl_days(get_preferences(db, profile_id)))
    runs = letter_view.latest_runs(db, [match.id for match, _ in rows])

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
            **retention.screenshot_fields(match, ttl),
            "extracted_at": job.extracted_at.isoformat() if job.extracted_at else None,
            "top_skills": [
                js.name for js in job.job_skills if js.skill_type == "hard"
            ][:3],
            "eligibility_notes": _eligibility_notes(job),
            # The newest cover-letter pipeline run: {run_id, status, open_questions}, or
            # null when none has run. Drives the card's "writing / needs your answer".
            "letter_run": runs.get(match.id),
        }
        for match, job in rows
    ]


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


@app.get("/jobs/search-performance")
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


def _csv_safe(value: object) -> str:
    """Neutralise spreadsheet formula injection. Titles/employers come from
    scraped pages, and a cell starting with = + - @ is executed by Excel."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@app.get("/jobs/evidence-export")
def evidence_export(profile_id: int = 1, db: Session = Depends(get_db)) -> Response:
    """Zip of every applied job's record plus whichever screenshots still exist.

    The safety net for screenshot expiry (``screenshot_ttl_days``): the image
    files are deleted after their TTL, so this is how the user takes a copy
    first. The CSV always lists every application; ``Screenshot`` says
    "yes", "expired" (taken, file since deleted) or "no" (never captured), and
    the screenshots themselves sit alongside it under ``screenshots/``.

    Registered before ``/jobs/{job_id}`` so the literal path wins the match.
    """
    rows = db.execute(
        select(Match, JobListing)
        .join(JobListing, Match.job_id == JobListing.id)
        .where(Match.user_id == profile_id)
        # applied_at, not status: the same definition as the dashboard (GET /obligation)
        # and retention, so the export can never disagree with the period counts.
        .where(Match.applied_at.is_not(None))
        .order_by(Match.applied_at.desc())
    ).all()

    csv_buf = io.StringIO()
    writer = csv.writer(csv_buf)
    writer.writerow(
        ["Date Applied", "Job Title", "Employer", "Location", "Source URL",
         "Screenshot", "Screenshot Taken", "Screenshot File"]
    )
    files: list[tuple[str, Path]] = []
    for match, job in rows:
        path = (
            paths._screenshots_dir / Path(match.screenshot_path).name
            if match.screenshot_path else None
        )
        on_disk = path is not None and path.is_file()
        if on_disk:
            files.append((f"screenshots/{path.name}", path))
        if on_disk:
            state = "yes"
        elif match.screenshot_taken_at:
            state = "expired"
        else:
            state = "no"
        writer.writerow([
            match.applied_at.date().isoformat() if match.applied_at else "",
            _csv_safe(job.title), _csv_safe(job.company), _csv_safe(job.location), job.url,
            state,
            match.screenshot_taken_at.date().isoformat() if match.screenshot_taken_at else "",
            f"screenshots/{path.name}" if on_disk else "",
        ])

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("applied-jobs.csv", csv_buf.getvalue())
        for arcname, path in files:
            zf.write(path, arcname)

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="application-evidence-{stamp}.zip"'},
    )


@app.get("/jobs/suggested-searches")
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
        "eligibility_notes": _eligibility_notes(job),
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

    Once ``applied_at`` is set the status can only be ``'applied'`` (409 otherwise):
    an applied match is Centrelink evidence. Sending ``'applied'`` again is a no-op
    that also repairs a row whose date is set but whose status is not.
    """
    match = db.scalar(
        select(Match).where(Match.user_id == profile_id, Match.job_id == job_id)
    )
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    if match.applied_at is not None and body.status != "applied":
        raise HTTPException(
            status_code=409,
            detail="This job is marked applied (Centrelink evidence); its status can't change.",
        )

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
    Files expire after ``screenshot_ttl_days`` (default 30) — see
    app/retention.py; the application record itself never does.
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

    # Store at <=1200px wide; falls back to the untouched bytes on any failure.
    png_bytes = downscale_png(png_bytes)

    unlink_screenshot(paths._screenshots_dir, match.screenshot_path)

    ts = datetime.now(timezone.utc)
    filename = f"{job_id}-{profile_id}-{ts.strftime('%Y%m%dT%H%M%SZ')}.png"
    (paths._screenshots_dir / filename).write_bytes(png_bytes)

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

    # On the single worker, behind whatever the idle loop is running: a full letter run
    # takes minutes, and two LLM jobs must not write the DB or hit the rate limit together.
    worker._bg_executor.submit(
        worker._process_listing,
        job_id,
        profile_id,
        job.raw_description is not None,
        with_cover_letter=True,
        bypass_threshold=True,
        force=True,
    )
    return {"status": "queued"}


@app.delete("/jobs")
def bulk_hide_jobs(
    below_score: float,
    profile_id: int = 1,
    db: Session = Depends(get_db),
) -> dict:
    """Hide every match for a profile scoring below ``below_score``.

    A *soft* delete, despite the verb: the match row survives with
    ``hidden_at`` stamped, and only ``raw_description`` — the bulk of the
    storage — is discarded. The user sees the same thing either way (the job
    leaves every list in the sidebar), but the score survives, and the score is
    what the suggestion miner needs.

    Hard-deleting these was actively harmful. Phrase ranking is
    ``(shrunk_mean - baseline) * log1p(support)``, so the low scorers are the
    contrast that makes a good phrase measurably good; deleting them raised the
    dev profile's baseline from 69.4 to 82.5 in one click and flattened the
    signal. ``GET /jobs/search-performance`` needs them for the same reason —
    a query's yield is meaningless if the misses are erased and only the hits
    remain.

    Idempotent: already-hidden matches are skipped, so re-running it at the
    same threshold reports 0.
    """
    rows = db.execute(
        select(Match, JobListing)
        .join(JobListing, Match.job_id == JobListing.id)
        .where(Match.user_id == profile_id)
        .where(Match.score < below_score)
        .where(Match.hidden_at.is_(None))
    ).all()

    now = datetime.now(timezone.utc)
    for match, job in rows:
        match.hidden_at = now
        # Reclaim the space. Keeping the description would make this a
        # storage no-op, and nothing downstream reads it once a match exists:
        # the idle loop only picks up jobs that have no match row at all.
        job.raw_description = None
    db.commit()
    return {"deleted": len(rows), "hidden": len(rows)}


@app.delete("/jobs/{job_id}")
def delete_job(
    job_id: int,
    profile_id: int = 1,
    allow_applied: bool = False,
    db: Session = Depends(get_db),
) -> dict:
    """Delete a job listing and all its children (matches, cover letters, skills).

    An applied job is Centrelink evidence, so deleting one is refused (409) unless the
    caller opts in with ``allow_applied=true`` (the Applied tab's own Delete). The
    match's screenshot file goes too; the cascade only removes the row pointing at it.
    """
    job = db.get(JobListing, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    match = db.scalar(
        select(Match).where(Match.user_id == profile_id, Match.job_id == job_id)
    )
    if match is not None and match.applied_at is not None and not allow_applied:
        raise HTTPException(
            status_code=409,
            detail="This job is marked applied (Centrelink evidence). "
                   "Delete it from the Applied tab instead.",
        )
    screenshot_path = match.screenshot_path if match is not None else None
    db.delete(job)
    db.commit()
    unlink_screenshot(paths._screenshots_dir, screenshot_path)
    return {"deleted": job_id}
