"""The idle loop: the single background loop that does all AI work, and its helpers.

Moved out of app/api/main.py (refactor steps 9.5 and 9.6). main's lifespan starts
``_processing_idle_loop`` as ``idle_loop._processing_idle_loop()``, looked up at start-up so
a test can patch it. Module state (``_match_failures``, ``_letters_paused_until``) lives
here only. Callers and tests reach every name as ``idle_loop.<name>`` at call time, never
with ``from app.api.idle_loop import ...``, so a patch on this module is seen everywhere
(plan rule M1).
"""

from __future__ import annotations

import asyncio
import functools
import logging
import time

from sqlalchemy import select

from app import availability, retention
from app.api import events, paths, worker
from app.db import SessionLocal
from app.llm.client import BudgetExceededError, DailyQuotaError
from app.llm.letter import production as letter_production
from app.llm.match import match_job
from app.llm.quickscreen import quick_screen
from app.models import JobListing, Match, Profile, Skill

logger = logging.getLogger(__name__)

# Idle-loop matching (Phase 1b): a failed score is retried, not written as a 0.
# After _MATCH_MAX_ATTEMPTS failures for the same job the last attempt records a
# terminal 0 row so one bad ad can't block the queue (it always takes the OLDEST
# unmatched job). In memory only: a restart gives each job three more tries.
_MATCH_MAX_ATTEMPTS = 3
_MATCH_RETRY_PAUSE_S = 180
_match_failures: dict[int, int] = {}


def _match_attempt(job_id: int) -> str:
    """One idle-loop scoring attempt for an extracted, unmatched job.

    Returns "ok" (scored), "failed" (will be retried), "gave_up" (the last attempt
    failed and a terminal 0 row was recorded) or "quota" (daily quota / budget stop;
    not counted against the job). Sync: the loop runs it on ``_bg_executor``.
    """
    failures = _match_failures.get(job_id, 0)
    last_try = failures + 1 >= _MATCH_MAX_ATTEMPTS
    try:
        match_job(job_id, 1, record_failure=last_try)
    except (DailyQuotaError, BudgetExceededError):
        return "quota"
    except Exception:  # noqa: BLE001 — match_job already logged the traceback
        _match_failures[job_id] = failures + 1
        return "failed"
    _match_failures.pop(job_id, None)
    with SessionLocal() as db:
        recorded = db.scalar(
            select(Match.reasoning).where(Match.user_id == 1, Match.job_id == job_id)
        )
    return "gave_up" if recorded == "Parse error" else "ok"


def _retention_tick() -> None:
    """Run the retention sweep for every profile that is due. Never raises: a
    sweep failure must not take the LLM idle loop down with it."""
    try:
        with SessionLocal() as db:
            for profile_id in db.scalars(select(Profile.id)).all():
                retention.run_sweep_if_due(db, profile_id, paths._screenshots_dir)
    except Exception:  # noqa: BLE001 — background guard: a sweep failure is logged, never fatal to the loop
        logger.exception("Retention sweep failed")


def _recover_orphaned_runs() -> None:
    """Runs the previous server process left ``running`` can never finish: mark them
    failed so they stop blocking their matches (letter_production.recover_orphaned_runs)."""
    try:
        with SessionLocal() as db:
            letter_production.recover_orphaned_runs(db)
    except Exception:  # noqa: BLE001 — startup guard: logged, and the server must still come up
        logger.exception("Recovering orphaned letter runs failed")


def _raw_relevance_score(job: JobListing, skill_names: list[str]) -> int:
    """Zero-cost queue-ordering signal: how many of the profile's skill names
    appear as substrings in the job's title/raw description.

    Not a gate — every job still gets extracted+matched regardless of this
    score (see app/llm/prefilter.py's "no silent gate" rule). It only decides
    which pending job the idle loop picks next, so likely-relevant jobs reach
    the front of the single-worker LLM queue before likely-irrelevant ones,
    without spending an extra LLM call to find out.
    """
    text = f"{job.title or ''} {job.raw_description or ''}".lower()
    return sum(1 for name in skill_names if name and name.lower() in text)


# Seconds to wait between idle cover-letter generations (respect per-minute quota).
_IDLE_INTERVAL_S = 20

# When a mid/strong call is refused by the USD budget guard, stop trying cover
# letters for this long (monotonic seconds) so extraction/matching (`small`, never
# blocked) keeps flowing instead of the loop re-hitting the cap every pass.
_BUDGET_PAUSE_S = 600
_letters_paused_until = 0.0


async def _letters_phase() -> bool:
    """One pass of the cover-letter phase. True when it did letter work (so the loop
    re-checks the backlog after a pause); False when there was none, or letters are
    paused by the budget guard.

    What runs is decided in app/llm/letter/production.py (``next_work``): a run the user
    has finished answering resumes first, then the best-scored match without a letter
    gets the full pipeline (score at or above the bar) or the one-shot. A run waiting on
    the user is NOT work, so it never blocks the loop. The work itself runs on the single
    worker, and reports progress as SSE events (letter_run_started / _waiting / _done /
    _failed, plus cover_letter_ready when a letter lands).
    """
    global _letters_paused_until
    if time.monotonic() < _letters_paused_until:
        return False
    loop = asyncio.get_running_loop()
    with SessionLocal() as db:
        work = letter_production.next_work(db, 1)
    if work is None:
        return False

    logger.info("Idle: cover letter (%s) for job %s", work.kind, work.job_id)
    landed = await loop.run_in_executor(
        worker._bg_executor,
        functools.partial(letter_production.run_work, work, 1, events.broadcast_from_thread),
    )
    if landed.account_limit:
        # The USD guard (or the provider's daily quota) is hit: every later run would be
        # too, so pause letters. Scanning and scoring (`small`) keep flowing.
        _letters_paused_until = time.monotonic() + _BUDGET_PAUSE_S
        logger.warning("Idle: %s Pausing cover letters for %ds.", landed.account_limit, _BUDGET_PAUSE_S)
        await events._broadcast("llm_budget_blocked", {"reason": landed.account_limit})
    await asyncio.sleep(_IDLE_INTERVAL_S)
    return True


async def _processing_idle_loop() -> None:
    """Single idle loop handling all LLM work, serialised through _bg_executor.

    Phase 2 (checked FIRST) — generate cover letters for high-scored matches
    that don't have one yet. Phase 0 — cheap pre-extraction quick screen (see
    app/llm/quickscreen.py) for any job that hasn't been screened yet; auto-skips
    obvious mismatches before they cost a full extraction+match pair. Phase 1 —
    extract+match any screened, not-yet-skipped job that hasn't been extracted
    yet. Phase 1b — extracted but not yet matched.

    Cover letters are checked first, every iteration, so that idle time goes
    into finishing letters for jobs we already know are great before it goes
    into extracting/matching a backlog of new, as-yet-unscored listings. Only
    fall through to extraction/matching when there's no cover-letter backlog.
    See docs/extension-revamp-plan.md §3.

    Only one LLM call chain runs at a time; the client's RPM throttle adds
    per-call spacing on top, so we never burst the free-tier limit.
    """
    await asyncio.sleep(15)  # let startup settle before first query
    await asyncio.get_running_loop().run_in_executor(worker._bg_executor, _recover_orphaned_runs)
    while True:
        try:
            loop = asyncio.get_running_loop()

            # ── Retention sweep ───────────────────────────────────────────────
            # Checked every iteration rather than only when idle: the LLM phases
            # `continue` while there is any backlog, so a tail-of-loop sweep
            # could starve indefinitely. It is a no-op except once per 24h
            # (retention.SWEEP_INTERVAL), and runs on the single worker so it
            # never interleaves with an LLM job writing to the same DB.
            await loop.run_in_executor(worker._bg_executor, _retention_tick)

            # ── Phase 2: cover letters (checked first — see docstring) ────────
            if await _letters_phase():
                continue  # re-check the cover-letter backlog before extraction/matching

            # ── Phase 0: pre-extraction quick screen ───────────────────────────
            # Oldest job with a description that hasn't been screened yet. Cheap
            # relative to extraction+matching — see app/llm/quickscreen.py for the
            # cost/conservatism rationale. Runs once per job; on pass it stamps
            # quick_screen_at and Phase 1 below picks it up next; on skip it writes
            # a terminal low-score Match row and Phase 1 never sees it.
            # Every phase below (and the letters phase) only picks a job that is not flagged
            # expired and was seen live recently (app/availability.py llm_gate): a stale
            # backlog job waits for the side panel to re-check it, and is skipped, not
            # blocked on, so fresh jobs keep flowing.
            screen_id: int | None = None
            with SessionLocal() as db:
                job = db.scalar(
                    select(JobListing)
                    .where(JobListing.raw_description.isnot(None))
                    .where(availability.llm_gate(db, 1))
                    .where(JobListing.extracted_at.is_(None))
                    .where(JobListing.quick_screen_at.is_(None))
                    .order_by(JobListing.date_scraped.asc())
                    .limit(1)
                )
                if job:
                    screen_id = job.id

            if screen_id is not None:
                logger.info("Idle: quick-screen job %s", screen_id)
                try:
                    outcome = await loop.run_in_executor(
                        worker._bg_executor,
                        functools.partial(quick_screen, screen_id, 1),
                    )
                    if outcome == "skipped":
                        # A skip writes a terminal Match row right away — the only
                        # quick-screen outcome the sidebar has anything new to show
                        # for, so it's the only one that needs a live refresh. A
                        # pass just moves the job on to Phase 1, with nothing to
                        # display yet.
                        events.broadcast_from_thread("job_processed", {"job_id": screen_id})
                    await asyncio.sleep(_IDLE_INTERVAL_S)
                except DailyQuotaError:
                    logger.warning("Idle: quick-screen daily quota hit — backing off 3 min")
                    await asyncio.sleep(180)
                continue

            # ── Phase 1: pending extraction+matching ──────────────────────────
            # Picks the most likely-relevant job among the oldest backlog, using
            # the free _raw_relevance_score signal — see its docstring. Capped at
            # 50 candidates so this stays cheap even with a large backlog; among
            # ties (including "no candidates scored" -> all zero) max() keeps the
            # first one in date_scraped order, i.e. still FIFO as a tiebreak.
            # Restricted to jobs that passed the Phase 0 quick screen (quick_screen_at
            # set) and haven't already been skip-terminated by it (~matches.any()) —
            # a skipped job keeps extracted_at NULL forever, so without this it would
            # otherwise loop right back into this candidate pool.
            pending_id: int | None = None
            with SessionLocal() as db:
                profile_skills = db.scalars(
                    select(Skill.name).where(Skill.user_id == 1)
                ).all()
                candidates = db.scalars(
                    select(JobListing)
                    .where(JobListing.raw_description.isnot(None))
                    .where(availability.llm_gate(db, 1))
                    .where(JobListing.extracted_at.is_(None))
                    .where(JobListing.quick_screen_at.isnot(None))
                    .where(~JobListing.matches.any())
                    .order_by(JobListing.date_scraped.asc())
                    .limit(50)
                ).all()
                if candidates:
                    job = max(candidates, key=lambda j: _raw_relevance_score(j, profile_skills))
                    pending_id = job.id

            if pending_id is not None:
                logger.info("Idle: extract+match job %s", pending_id)
                await loop.run_in_executor(
                    worker._bg_executor,
                    functools.partial(worker._process_listing, pending_id, 1, True),
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
                continue

            # ── Phase 1b: extracted but not yet matched ───────────────────────
            unmatched_id: int | None = None
            with SessionLocal() as db:
                job = db.scalar(
                    select(JobListing)
                    .where(JobListing.extracted_at.isnot(None))
                    .where(availability.llm_gate(db, 1))
                    .where(~JobListing.matches.any())
                    .order_by(JobListing.date_scraped.asc())
                    .limit(1)
                )
                if job:
                    unmatched_id = job.id

            if unmatched_id is not None:
                logger.info("Idle: match job %s (already extracted)", unmatched_id)
                outcome = await loop.run_in_executor(
                    worker._bg_executor, functools.partial(_match_attempt, unmatched_id),
                )
                if outcome in ("failed", "quota"):
                    logger.warning(
                        "Idle: scoring job %s: %s — pausing %ds",
                        unmatched_id, outcome, _MATCH_RETRY_PAUSE_S,
                    )
                    await asyncio.sleep(_MATCH_RETRY_PAUSE_S)
                else:
                    await asyncio.sleep(_IDLE_INTERVAL_S)
                continue

            await asyncio.sleep(30)  # nothing pending — check again shortly

        except Exception:  # noqa: BLE001 — the idle loop must survive any one pass failing: logged, then a 60 s pause
            logger.exception("Idle processing loop error")
            await asyncio.sleep(60)
