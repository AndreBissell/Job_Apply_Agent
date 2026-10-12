"""The idle loop's helpers: match retries, the retention tick, start-up recovery, queue order.

Moved out of app/api/main.py (refactor step 9.5); the loop itself follows in step 9.6.
Module state (``_match_failures``) lives here only. Callers and tests reach every name as
``idle_loop.<name>`` at call time, never with ``from app.api.idle_loop import ...``, so a
patch on this module is seen everywhere (plan rule M1).
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from app import retention
from app.api import paths
from app.db import SessionLocal
from app.llm.client import BudgetExceededError, DailyQuotaError
from app.llm.letter import production as letter_production
from app.llm.match import match_job
from app.models import JobListing, Match, Profile

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
