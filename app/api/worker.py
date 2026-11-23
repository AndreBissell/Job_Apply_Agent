"""The two background worker threads, and the job pipeline they run for /regenerate and the idle loop.

``_bg_executor`` is the letter worker: every letter (the idle loop's, /regenerate, band
letters, "Write a letter") runs on it, so two letters never run at once.
``_score_executor`` is the scoring worker: the idle loop's quick screen, extraction and
matching, one job at a time (docs/second-worker-plan.md). A job both reach at once is
guarded by app/job_claims.py. Other modules reach them, and
``_process_listing``, as ``worker.<name>`` at call time, never with
``from app.api.worker import ...``, so a test that patches this module is seen by every
caller (plan rule M1).
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from app import job_claims, letter_bands
from app.api import events
from app.db import SessionLocal
from app.llm.client import BudgetExceededError, DailyQuotaError
from app.llm.extract import extract_job
from app.llm.letter import production as letter_production
from app.llm.match import match_job
from app.models import CoverLetter, Match
from app.preferences import letter_settings

logger = logging.getLogger(__name__)

# Two worker threads, so LLM calls don't block the event loop (docs/second-worker-plan.md D1).
# _bg_executor: every letter (the idle loop's letters, /regenerate, band letters, "Write a
# letter") and the retention sweep, one at a time.
# _score_executor: the idle loop's scoring only (quick screen, extraction, matching; all
# `small` tier). Nothing letter-related is ever submitted to it.
_bg_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="letters")
_score_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="scoring")

# How long a user's /regenerate waits for the scorer to finish THIS job before it goes
# ahead anyway (docs/second-worker-plan.md D6). Not the wait in the letter queue.
_CLAIM_WAIT_S = 120.0


def _process_listing(
    job_id: int,
    profile_id: int,
    has_description: bool,
    with_cover_letter: bool = False,
    bypass_threshold: bool = False,
    force: bool = False,
    *,
    already_claimed: bool = False,
) -> None:
    """Background task: extract + match (+ optionally cover letter) a listing.

    Runs after the response is sent. With only a card (no description yet) there's
    nothing to extract from, so we defer until a detail-page ingest fills it in.
    ``with_cover_letter=True`` is set by /regenerate so the letter is generated
    after matching completes — never set by /ingest to avoid burning quota on every
    scraped listing. ``force=True`` (also set by /regenerate) re-runs extraction and
    matching even if they already ran — e.g. to override a quick-screen auto-skip;
    this path never calls quick_screen, it goes straight to extraction+matching.

    Extraction and matching run under the job's claim (app/job_claims.py). The scorer
    claims the job itself and passes ``already_claimed=True``; any other caller
    (/regenerate) waits here for the scorer to finish the job first (see
    ``_wait_for_scorer``). The letter is written after the claim is released.
    """
    if not has_description:
        logger.info(
            "Job %s stored as card only (no description) — deferring extraction/"
            "matching until a detail page is ingested.",
            job_id,
        )
        return
    claim = contextlib.nullcontext() if already_claimed else _wait_for_scorer(job_id)
    with claim:
        if not _extract_and_match(job_id, profile_id, force):
            return
    if with_cover_letter:
        _write_letter(job_id, profile_id, bypass_threshold)


@contextlib.contextmanager
def _wait_for_scorer(job_id: int) -> Iterator[None]:
    """Hold the job's claim for the block, waiting first if the scorer has it (D3).

    After ``_CLAIM_WAIT_S`` the block runs anyway, without the claim, and a WARNING says
    how long the scorer had held it (D6): a leaked claim or a hung model call must not
    block the user's letter, and the cost of going ahead is at most a repeated small call.
    """
    with job_claims.claim(job_id, wait=_CLAIM_WAIT_S) as claimed:
        if not claimed:
            held = job_claims.held_for(job_id)
            logger.warning(
                "job %s: still being scored after waiting %.0f s (claim held %s) — going ahead anyway",
                job_id, _CLAIM_WAIT_S, "until just now" if held is None else f"{held:.0f} s",
            )
        yield


def _extract_and_match(job_id: int, profile_id: int, force: bool) -> bool:
    """Extract, then match. False when the whole account is stopped (daily quota or the
    USD budget): the later steps would only make more doomed requests, so the sidebar is
    told and the caller stops. Any other failure is per-job, logged, and the next step
    still runs (True)."""
    try:
        extract_job(job_id, force=force)
    except (DailyQuotaError, BudgetExceededError) as exc:
        _account_stopped(job_id, exc)
        return False
    except Exception:  # noqa: BLE001 — a bad extraction must not kill the task
        logger.exception("extract_job failed for job %s", job_id)
    try:
        match_job(job_id, profile_id, force=force)
        events.broadcast_from_thread("job_processed", {"job_id": job_id})
    except (DailyQuotaError, BudgetExceededError) as exc:
        _account_stopped(job_id, exc)
        return False
    except Exception:  # noqa: BLE001 — a per-job failure is logged; quota/budget stops are caught above
        logger.exception("match_job failed for job %s", job_id)
    return True


def _account_stopped(job_id: int, exc: Exception) -> None:
    logger.warning("job %s: %s — stopping this job's processing", job_id, exc)
    events.broadcast_from_thread("llm_budget_blocked", {"reason": str(exc)})


def _write_letter(job_id: int, profile_id: int, bypass_threshold: bool) -> None:
    """The letter step of ``_process_listing`` (/regenerate): never raises; a daily-quota,
    budget or account-limit stop is broadcast to the sidebar."""
    try:
        with SessionLocal() as db:
            landed = letter_production.generate_for(
                db, job_id, profile_id, events.broadcast_from_thread, bypass_threshold=bypass_threshold,
            )
        if landed is not None and landed.account_limit:
            events.broadcast_from_thread("llm_budget_blocked", {"reason": landed.account_limit})
    except (DailyQuotaError, BudgetExceededError) as exc:
        _account_stopped(job_id, exc)
    except Exception:  # noqa: BLE001 — a per-job failure is logged; quota/budget stops are caught above
        logger.exception("cover letter failed for job %s", job_id)


def _rewrite_letter(job_id: int, profile_id: int, has_description: bool, discard_edits: bool = False) -> None:
    """Background task: an explicit Rewrite (docs/letter-rewrite-plan.md). /regenerate's work
    (re-extract, re-score, a fresh letter), then, with ``discard_edits`` (D1), the user's edits
    cleared, but only once a NEW letter has landed, so a failed rewrite loses nothing. Always
    releases the ``letter_bands`` claim and tells the sidebar, so no card stays "Writing…"."""
    try:
        before = _generated_at(job_id, profile_id)
        _process_listing(
            job_id, profile_id, has_description, with_cover_letter=True, bypass_threshold=True, force=True,
        )
        if discard_edits:
            with SessionLocal() as db:
                cl = _cover_letter(db, job_id, profile_id)
                if cl is not None and cl.edited_content and cl.generated_at is not None and cl.generated_at != before:
                    cl.edited_content = None
                    cl.status = "draft"
                    db.commit()
    finally:
        letter_bands.release(job_id)
        events.broadcast_from_thread("letter_rewrite_done", {"job_id": job_id})


def _cover_letter(db, job_id: int, profile_id: int) -> CoverLetter | None:
    return db.scalar(
        select(CoverLetter).join(Match, Match.id == CoverLetter.match_id)
        .where(Match.user_id == profile_id, Match.job_id == job_id)
    )


def _generated_at(job_id: int, profile_id: int):
    with SessionLocal() as db:
        cl = _cover_letter(db, job_id, profile_id)
        return cl.generated_at if cl is not None else None


def _write_band_letters(job_ids: list[int], profile_id: int) -> None:
    """Background task: the Overview's "+ cover letters" for a score band below the
    auto-letter bar (docs/overview-applied-rework-plan.md, Step 3, D5/D6), best first.

    Each job is checked again first: a letter, an application, a hide, a closed ad or a
    stale sighting since the click drops it. The kind comes from ``kind_for``, so below the
    bar it is the one-shot. A USD guard / daily quota stop ends the batch and tells the
    sidebar, as the idle loop does. Every id leaves ``letter_bands.queued`` when its turn
    ends, and the rest do when the batch stops.
    """
    try:
        for job_id in job_ids:
            try:
                with SessionLocal() as db:
                    score = letter_bands.still_eligible(db, profile_id, job_id)
                    if score is None:
                        logger.info("band letter: job %s no longer qualifies, skipped", job_id)
                        continue
                    kind = letter_production.kind_for(letter_settings(db, profile_id), score)
                    landed = letter_production.run_work(
                        letter_production.Work(kind, job_id, below_bar=True),
                        profile_id, events.broadcast_from_thread, db=db,
                    )
                if landed.account_limit:
                    logger.warning("band letters: %s — stopping the batch", landed.account_limit)
                    events.broadcast_from_thread("llm_budget_blocked", {"reason": landed.account_limit})
                    return
            except (DailyQuotaError, BudgetExceededError) as exc:
                logger.warning("band letters: %s — stopping the batch", exc)
                events.broadcast_from_thread("llm_budget_blocked", {"reason": str(exc)})
                return
            except Exception:  # noqa: BLE001 — one bad job must not end the batch
                logger.exception("band letter failed for job %s", job_id)
            finally:
                letter_bands.release(job_id)
    finally:
        letter_bands.release(*job_ids)
