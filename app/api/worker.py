"""The single background worker, and the job pipeline it runs for /regenerate and the idle loop.

``_bg_executor`` is THE one worker thread: every LLM chain (the idle loop's work and
/regenerate) runs on it, so two chains never run at once. Other modules reach it, and
``_process_listing``, as ``worker.<name>`` at call time, never with
``from app.api.worker import ...``, so a test that patches this module is seen by every
caller (plan rule M1).
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from app import letter_bands
from app.api import events
from app.db import SessionLocal
from app.llm.client import BudgetExceededError, DailyQuotaError
from app.llm.extract import extract_job
from app.llm.letter import production as letter_production
from app.llm.match import match_job
from app.preferences import letter_settings

logger = logging.getLogger(__name__)

# One worker thread so LLM calls don't block the event loop.
_bg_executor = ThreadPoolExecutor(max_workers=1)


def _process_listing(
    job_id: int,
    profile_id: int,
    has_description: bool,
    with_cover_letter: bool = False,
    bypass_threshold: bool = False,
    force: bool = False,
) -> None:
    """Background task: extract + match (+ optionally cover letter) a listing.

    Runs after the response is sent. With only a card (no description yet) there's
    nothing to extract from, so we defer until a detail-page ingest fills it in.
    ``with_cover_letter=True`` is set by /regenerate so the letter is generated
    after matching completes — never set by /ingest to avoid burning quota on every
    scraped listing. ``force=True`` (also set by /regenerate) re-runs extraction and
    matching even if they already ran — e.g. to override a quick-screen auto-skip;
    this path never calls quick_screen, it goes straight to extraction+matching.
    """
    if has_description:
        # A daily-quota or budget stop applies to the whole account, so the later
        # steps would only make more doomed requests: tell the sidebar and stop.
        # (Any other failure is per-job and keeps going, as before.)
        try:
            extract_job(job_id, force=force)
        except (DailyQuotaError, BudgetExceededError) as exc:
            logger.warning("job %s: %s — stopping this job's processing", job_id, exc)
            events.broadcast_from_thread("llm_budget_blocked", {"reason": str(exc)})
            return
        except Exception:  # noqa: BLE001 — a bad extraction must not kill the task
            logger.exception("extract_job failed for job %s", job_id)
        try:
            match_job(job_id, profile_id, force=force)
            events.broadcast_from_thread("job_processed", {"job_id": job_id})
        except (DailyQuotaError, BudgetExceededError) as exc:
            logger.warning("job %s: %s — stopping this job's processing", job_id, exc)
            events.broadcast_from_thread("llm_budget_blocked", {"reason": str(exc)})
            return
        except Exception:  # noqa: BLE001 — a per-job failure is logged; quota/budget stops are caught above
            logger.exception("match_job failed for job %s", job_id)
        if with_cover_letter:
            try:
                with SessionLocal() as db:
                    landed = letter_production.generate_for(
                        db, job_id, profile_id, events.broadcast_from_thread, bypass_threshold=bypass_threshold,
                    )
                if landed is not None and landed.account_limit:
                    events.broadcast_from_thread("llm_budget_blocked", {"reason": landed.account_limit})
            except (DailyQuotaError, BudgetExceededError) as exc:
                logger.warning("job %s: %s — stopping this job's processing", job_id, exc)
                events.broadcast_from_thread("llm_budget_blocked", {"reason": str(exc)})
                return
            except Exception:  # noqa: BLE001 — a per-job failure is logged; quota/budget stops are caught above
                logger.exception("cover letter failed for job %s", job_id)
    else:
        logger.info(
            "Job %s stored as card only (no description) — deferring extraction/"
            "matching until a detail page is ingested.",
            job_id,
        )


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
