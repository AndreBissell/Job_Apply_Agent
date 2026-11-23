"""The idle loop: the letter loop and the scoring loop that do all background AI work.

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
from collections.abc import Awaitable, Callable

from sqlalchemy import select, true

from app import availability, job_claims, retention
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
    failed and a terminal 0 row was recorded), "quota" (daily quota / budget stop;
    not counted against the job) or "claimed" (another worker holds the job: nothing
    ran, nothing counted). Sync: the scoring loop runs it on ``_score_executor``.
    """
    with job_claims.claim(job_id) as claimed:
        if not claimed:
            return "claimed"
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


def _screen_job(job_id: int) -> str:
    """Phase 0's work on the worker: ``quick_screen``'s outcome, or "claimed" when another
    worker holds the job (nothing ran). Sync."""
    with job_claims.claim(job_id) as claimed:
        if not claimed:
            return "claimed"
        return quick_screen(job_id, 1)


def _extract_and_match_job(job_id: int) -> str:
    """Phase 1's work on the worker: "done" (extraction and matching ran; the caller checks
    whether extraction succeeded), or "claimed" when another worker holds the job. Sync."""
    with job_claims.claim(job_id) as claimed:
        if not claimed:
            return "claimed"
        worker._process_listing(job_id, 1, True, already_claimed=True)
        return "done"


def _unclaimed():
    """A WHERE clause leaving out the jobs a worker holds right now (app/job_claims.py),
    so the scorer moves on to the next job instead of stopping on a held one."""
    held = job_claims.held_ids()
    return JobListing.id.notin_(held) if held else true()


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
    which pending job the scoring loop picks next, so likely-relevant jobs reach
    the front of the scoring queue before likely-irrelevant ones,
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
    the user is NOT work, so it never blocks the loop. The work itself runs on the letter
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


# Pacing of the two loops (seconds).
_STARTUP_DELAY_S = 15  # let start-up settle before the first query
_NOTHING_TO_DO_S = 30  # nothing pending: check again shortly
_SCORING_BACKOFF_S = 180  # the quick screen hit the daily quota, or an extraction failed
_LOOP_ERROR_PAUSE_S = 60  # a pass raised: logged, then this pause


async def _processing_idle_loop() -> None:
    """All background AI work: the letter loop and the scoring loop, side by side, until
    cancelled (main's lifespan starts this once and cancels it on shutdown, which cancels
    both loops). docs/second-worker-plan.md D1.

    Each loop has its own worker thread (``worker._bg_executor`` / ``worker._score_executor``),
    its own pauses and its own error guard, so a letter that takes minutes no longer holds
    up the scoring of newly scanned jobs, a budget pause stops only letters, a scoring
    back-off stops only scoring, and a failing pass in one loop never stops the other.
    The client's LLM_RPM throttle is shared by both, so together they never burst the limit.
    """
    await asyncio.gather(_letter_loop(), _scoring_loop())


async def _letter_loop() -> None:
    """Cover letters (and the retention sweep), one at a time on ``worker._bg_executor``,
    the thread /regenerate, band letters and "Write a letter" also use. Runs that a previous
    server process left ``running`` are cleared once, before the first letter."""
    await asyncio.sleep(_STARTUP_DELAY_S)
    await asyncio.get_running_loop().run_in_executor(worker._bg_executor, _recover_orphaned_runs)
    await _run_passes("Letter loop", _letter_pass)


async def _scoring_loop() -> None:
    """Quick screen, extraction and matching, one job at a time on ``worker._score_executor``."""
    await asyncio.sleep(_STARTUP_DELAY_S)
    await _run_passes("Scoring loop", _scoring_pass)


async def _run_passes(name: str, one_pass: Callable[[], Awaitable[float]]) -> None:
    """Run ``one_pass`` forever, sleeping the seconds each pass returns. A pass that raises
    is logged and followed by a pause; only cancellation ends the loop."""
    while True:
        try:
            delay = await one_pass()
        except Exception:  # noqa: BLE001 — a loop must survive any one pass failing: logged, then a pause
            logger.exception("%s error", name)
            delay = _LOOP_ERROR_PAUSE_S
        await asyncio.sleep(delay)


async def _letter_pass() -> float:
    """One pass of the letter loop. Returns the seconds to wait before the next one.

    The retention sweep is checked every pass, not only when idle, so a letter backlog
    can't starve it. It is a no-op except once per 24h (retention.SWEEP_INTERVAL), and
    runs on the letter worker. Then at most one letter (``_letters_phase``, which paces
    itself after work, so the backlog is re-checked at once).
    """
    await asyncio.get_running_loop().run_in_executor(worker._bg_executor, _retention_tick)
    if await _letters_phase():
        return 0
    return _NOTHING_TO_DO_S


async def _scoring_pass() -> float:
    """One pass of the scoring loop: at most one job, on ``worker._score_executor``. Returns
    the seconds to wait before the next pass.

    Phase 0, quick screen: the oldest job with a description that hasn't been screened yet.
    Cheap next to extraction+matching (see app/llm/quickscreen.py). On pass it stamps
    quick_screen_at and Phase 1 picks it up next; on skip it writes a terminal low-score
    Match row and Phase 1 never sees it.
    Phase 1, extract+match: a screened job that isn't extracted or skip-terminated yet.
    Phase 1b, match: extracted but not yet matched.

    Every phase (and the letters) only picks a job that is not flagged expired and was
    seen live recently (app/availability.py llm_gate): a stale backlog job waits for the
    side panel to re-check it, and is skipped, not blocked on, so fresh jobs keep flowing.
    Every phase leaves out jobs another worker holds (app/job_claims.py) and claims its
    job before working on it; a job claimed since the query comes back as "claimed" and
    the pass ends at once, so the next pass takes the next job.
    """
    loop = asyncio.get_running_loop()

    screen_id = _next_to_screen()
    if screen_id is not None:
        logger.info("Idle: quick-screen job %s", screen_id)
        try:
            outcome = await loop.run_in_executor(
                worker._score_executor, functools.partial(_screen_job, screen_id),
            )
        except DailyQuotaError:
            logger.warning("Idle: quick-screen daily quota hit — backing off 3 min")
            return _SCORING_BACKOFF_S
        if outcome == "claimed":
            return 0
        if outcome == "skipped":
            # A skip writes a terminal Match row right away — the only quick-screen
            # outcome the sidebar has anything new to show for, so it's the only one that
            # needs a live refresh. A pass just moves the job on to Phase 1.
            events.broadcast_from_thread("job_processed", {"job_id": screen_id})
        return _IDLE_INTERVAL_S

    pending_id = _next_to_extract()
    if pending_id is not None:
        logger.info("Idle: extract+match job %s", pending_id)
        outcome = await loop.run_in_executor(
            worker._score_executor, functools.partial(_extract_and_match_job, pending_id),
        )
        if outcome == "claimed":
            return 0
        # If extraction failed (Gemini still rate-limited), extracted_at stays NULL and
        # the next pass would retry the same job at once. Back off 3 min instead.
        with SessionLocal() as db:
            job = db.get(JobListing, pending_id)
            succeeded = job is not None and job.extracted_at is not None
        if succeeded:
            return _IDLE_INTERVAL_S
        logger.warning("Idle: extraction failed for job %s — backing off 3 min", pending_id)
        return _SCORING_BACKOFF_S

    unmatched_id = _next_to_match()
    if unmatched_id is not None:
        logger.info("Idle: match job %s (already extracted)", unmatched_id)
        outcome = await loop.run_in_executor(
            worker._score_executor, functools.partial(_match_attempt, unmatched_id),
        )
        if outcome == "claimed":
            return 0
        if outcome in ("failed", "quota"):
            logger.warning(
                "Idle: scoring job %s: %s — pausing %ds", unmatched_id, outcome, _MATCH_RETRY_PAUSE_S,
            )
            return _MATCH_RETRY_PAUSE_S
        return _IDLE_INTERVAL_S

    return _NOTHING_TO_DO_S


def _next_to_screen() -> int | None:
    """Phase 0's job: the oldest one with a description that hasn't been screened yet."""
    with SessionLocal() as db:
        return db.scalar(
            select(JobListing.id)
            .where(JobListing.raw_description.isnot(None))
            .where(availability.llm_gate(db, 1))
            .where(JobListing.extracted_at.is_(None))
            .where(JobListing.quick_screen_at.is_(None))
            .where(_unclaimed())
            .order_by(JobListing.date_scraped.asc())
            .limit(1)
        )


def _next_to_extract() -> int | None:
    """Phase 1's job: the most likely-relevant one among the oldest backlog.

    Uses the free ``_raw_relevance_score`` signal (see its docstring). Capped at 50
    candidates so this stays cheap even with a large backlog; among ties (including "no
    candidates scored" -> all zero) max() keeps the first one in date_scraped order, i.e.
    still FIFO as a tiebreak. Restricted to jobs that passed the Phase 0 quick screen
    (quick_screen_at set) and haven't been skip-terminated by it (~matches.any()): a
    skipped job keeps extracted_at NULL forever, so it would otherwise loop right back
    into this pool.
    """
    with SessionLocal() as db:
        profile_skills = db.scalars(select(Skill.name).where(Skill.user_id == 1)).all()
        candidates = db.scalars(
            select(JobListing)
            .where(JobListing.raw_description.isnot(None))
            .where(availability.llm_gate(db, 1))
            .where(JobListing.extracted_at.is_(None))
            .where(JobListing.quick_screen_at.isnot(None))
            .where(~JobListing.matches.any())
            .where(_unclaimed())
            .order_by(JobListing.date_scraped.asc())
            .limit(50)
        ).all()
        if not candidates:
            return None
        return max(candidates, key=lambda j: _raw_relevance_score(j, profile_skills)).id


def _next_to_match() -> int | None:
    """Phase 1b's job: the oldest extracted job with no match yet."""
    with SessionLocal() as db:
        return db.scalar(
            select(JobListing.id)
            .where(JobListing.extracted_at.isnot(None))
            .where(availability.llm_gate(db, 1))
            .where(~JobListing.matches.any())
            .where(_unclaimed())
            .order_by(JobListing.date_scraped.asc())
            .limit(1)
        )
