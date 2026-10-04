"""Running the cover-letter pipeline for real: what the idle loop and /regenerate call
(plan §6, Phase 8).

Until Phase 8 the engines (engines.py) were only driven by the evals. This module is the
glue that makes them production: it decides WHAT the single worker does next, runs it,
and lands the result in ``cover_letters``. It holds no model logic.

What the worker does next (``next_work``), in order:
  1. resume a run the user has finished answering (status ``answered``);
  2. else the best-scored match with no letter: the full pipeline at or above the
     effective bar (``letter_settings``), the one-shot writer below it (and for every
     letter when the master switch is off).
A match is passed over while it has a live run (``running`` / ``waiting_user`` /
``answered``), so a run waiting on the user never blocks the loop and is never retried;
and after repeated failures, so a deterministic failure can't spend money every 20 s.

How a finished run lands (``land_letter``): the best draft goes to
``cover_letters.generated_content``, even when the run stopped short of a clean one
(its open issues are read from the run's state, see view.py). ``edited_content`` is never
written: the user's edits survive a regenerate.

Everything here runs on the idle loop's single worker (or, for the explicit regenerate,
queued on that same worker), so it never writes the DB alongside another LLM job.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.llm.client import BudgetExceededError, DailyQuotaError
from app.llm.cover_letter import generate_cover_letter
from app.llm.letter.engines import run_letter, resume_letter
from app.llm.letter.outcome import LetterResult
from app.models import CoverLetter, JobListing, LetterRun, Match
from app.preferences import letter_settings

logger = logging.getLogger(__name__)

PIPELINE = "pipeline"  # a new full run (agent or workflow, per the letter_engine preference)
ONESHOT = "oneshot"  # the old single-call writer (cover_letter.py)
RESUME = "resume"  # continue an ``answered`` run

# Runs that hold a match: nothing else may start for it while one exists.
LIVE_STATUSES = ("running", "waiting_user", "answered")
# A match whose runs failed (or stopped with nothing to show) is retried after the
# cooldown, and given up on after this many attempts; an explicit regenerate ignores both.
MAX_FAILED_ATTEMPTS = 2
FAILED_COOLDOWN = datetime.timedelta(minutes=30)
# Candidates examined per pass: enough to step over a few blocked matches.
CANDIDATE_LIMIT = 25

Emit = Callable[[str, dict], None]


@dataclass(frozen=True)
class Work:
    kind: str  # PIPELINE | ONESHOT | RESUME
    job_id: int
    run_id: int | None = None  # RESUME only


@dataclass
class Landed:
    """What a unit of work did, for the loop's logging and back-off."""

    kind: str
    job_id: int
    status: str  # done | budget_stopped | waiting_user | failed | cancelled
    run_id: int | None = None
    letter: str | None = None  # the text now in cover_letters.generated_content, if any
    clean: bool = False  # all three checks passed on it (never true for the one-shot)
    open_issues: list[str] = field(default_factory=list)
    reason: str | None = None
    questions: int = 0  # open ask_user questions (waiting_user)
    account_limit: str | None = None  # the USD guard / daily quota stopped it: pause letters


def _utc(dt: datetime.datetime | None) -> datetime.datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


# ---------------------------------------------------------------------------
# Choosing what to do next
# ---------------------------------------------------------------------------
def recover_orphaned_runs(db: Session) -> int:
    """Mark every ``running`` run ``failed``. Only the single worker runs a pipeline, so
    a ``running`` row found at start-up was cut off by a crash or restart; left alone it
    would block its match for good. Returns how many were marked."""
    runs = db.scalars(select(LetterRun).where(LetterRun.status == "running")).all()
    for run in runs:
        run.status, run.finished_at = "failed", _now()
    if runs:
        db.commit()
        logger.warning("Marked %d orphaned letter run(s) failed (the server stopped mid-run)", len(runs))
    return len(runs)


def _attempt_blocked(db: Session, match_id: int, now: datetime.datetime) -> bool:
    """True when this match has a live run, or has used up its failed attempts or is
    still cooling down after the latest one."""
    runs = db.execute(
        select(LetterRun.status, LetterRun.final_draft_version, LetterRun.finished_at, LetterRun.started_at)
        .where(LetterRun.match_id == match_id)
    ).all()
    if any(r.status in LIVE_STATUSES for r in runs):
        return True
    # An attempt that failed or stopped with no draft to show. ``cancelled`` is not one:
    # it was set aside (a regenerate, or the account's USD guard), not tried and failed.
    failed = [
        r for r in runs
        if r.status == "failed" or (r.status == "budget_stopped" and r.final_draft_version is None)
    ]
    if len(failed) >= MAX_FAILED_ATTEMPTS:
        return True
    latest = max((_utc(r.finished_at or r.started_at) for r in failed), default=None)
    return latest is not None and now - latest < FAILED_COOLDOWN


def kind_for(settings: dict, score: float | None) -> str:
    """The full pipeline for a score at or above the effective bar, else the one-shot."""
    if settings["enabled"] and score is not None and score >= settings["pipeline_min_score"]:
        return PIPELINE
    return ONESHOT


def next_work(db: Session, profile_id: int, *, now: datetime.datetime | None = None) -> Work | None:
    """The one thing the worker does on this pass, or None when there is no letter work."""
    now = now or _now()

    resumable = db.execute(
        select(LetterRun.id, Match.job_id)
        .join(Match, Match.id == LetterRun.match_id)
        .where(LetterRun.status == "answered", Match.user_id == profile_id, Match.hidden_at.is_(None))
        .order_by(LetterRun.id)
        .limit(1)
    ).first()
    if resumable:
        return Work(RESUME, resumable.job_id, run_id=resumable.id)

    settings = letter_settings(db, profile_id)
    rows = db.execute(
        select(Match.id, Match.job_id, Match.score)
        .join(JobListing, Match.job_id == JobListing.id)
        .outerjoin(CoverLetter, CoverLetter.match_id == Match.id)
        .where(CoverLetter.id.is_(None))
        .where(Match.user_id == profile_id)
        .where(Match.hidden_at.is_(None))  # never write a letter for a hidden job
        .where(Match.score >= settings["auto_min_score"])
        .where(JobListing.extracted_at.isnot(None))
        .order_by(Match.score.desc())
        .limit(CANDIDATE_LIMIT)
    ).all()
    for match_id, job_id, score in rows:
        if _attempt_blocked(db, match_id, now):
            continue
        return Work(kind_for(settings, float(score) if score is not None else None), job_id)
    return None


# ---------------------------------------------------------------------------
# Landing a letter
# ---------------------------------------------------------------------------
def land_letter(db: Session, match_id: int, text: str) -> CoverLetter:
    """Write ``text`` to the match's ``generated_content`` (one row per match).

    ``edited_content`` is never touched: the user's edits survive a regenerate. With
    edits the status stays ``edited`` (they are still what the sidebar shows); without,
    the row is a fresh ``draft``.
    """
    cl = db.scalar(select(CoverLetter).where(CoverLetter.match_id == match_id))
    if cl is None:
        cl = CoverLetter(match_id=match_id, generated_content=text, status="draft")
        db.add(cl)
    else:
        cl.generated_content = text
        if not cl.edited_content:
            cl.status = "draft"
    db.commit()
    return cl


def supersede_open_runs(db: Session, match_id: int) -> int:
    """A regenerate replaces any run still waiting on the user or about to resume:
    mark them ``cancelled`` so the question card goes away. Returns how many."""
    runs = db.scalars(
        select(LetterRun).where(LetterRun.match_id == match_id, LetterRun.status.in_(("waiting_user", "answered")))
    ).all()
    for run in runs:
        run.status, run.finished_at = "cancelled", _now()
    if runs:
        db.commit()
    return len(runs)


# ---------------------------------------------------------------------------
# Running work
# ---------------------------------------------------------------------------
def _emit(emit: Emit | None, event: str, data: dict) -> None:
    """Tell the sidebar. A broken listener must never break a letter run."""
    if emit is None:
        return
    try:
        emit(event, data)
    except Exception:  # noqa: BLE001
        logger.exception("emitting %s failed", event)


def run_work(work: Work, profile_id: int, emit: Emit | None = None, *, db: Session | None = None) -> Landed:
    """Do one unit of ``next_work``. Opens its own session unless ``db`` is given.

    Never raises for a failed run or a USD / quota stop: both come back as a ``Landed``
    (the engines already record the run). The idle loop reads ``account_limit`` to pause.
    """
    own = db is None
    session = db or SessionLocal()
    try:
        if work.kind == ONESHOT:
            return _run_oneshot(session, work.job_id, profile_id, emit)
        return _run_pipeline(session, work.job_id, profile_id, emit, run_id=work.run_id)
    finally:
        if own:
            session.close()


def generate_for(
    db: Session, job_id: int, profile_id: int, emit: Emit | None = None, *, bypass_threshold: bool = True,
) -> Landed | None:
    """The explicit regenerate (the user clicked): a fresh letter now, replacing any run
    still waiting on them. With the master switch on this is the full pipeline whatever
    the score (the "Polish" button of plan §6); off, the one-shot. Without
    ``bypass_threshold`` the usual score bars apply. None when there is no match to write for."""
    match = db.scalar(select(Match).where(Match.user_id == profile_id, Match.job_id == job_id))
    if match is None:
        logger.warning("generate_for: no match for job %s / profile %s", job_id, profile_id)
        return None
    settings = letter_settings(db, profile_id)
    score = float(match.score) if match.score is not None else None
    if not bypass_threshold and (score is None or score < settings["auto_min_score"]):
        return None
    kind = PIPELINE if (settings["enabled"] and bypass_threshold) else kind_for(settings, score)
    supersede_open_runs(db, match.id)
    if kind == ONESHOT:
        return _run_oneshot(db, job_id, profile_id, emit, force=True)
    return _run_pipeline(db, job_id, profile_id, emit)


def _run_oneshot(db: Session, job_id: int, profile_id: int, emit: Emit | None, *, force: bool = False) -> Landed:
    try:
        cl = generate_cover_letter(job_id, profile_id, session=db, force=force, bypass_threshold=force)
    except (BudgetExceededError, DailyQuotaError) as exc:
        return Landed(ONESHOT, job_id, "budget_stopped", reason=str(exc), account_limit=str(exc))
    if cl is None:
        return Landed(ONESHOT, job_id, "failed", reason="no letter written")
    _emit(emit, "cover_letter_ready", {"job_id": job_id, "content": cl.generated_content})
    return Landed(ONESHOT, job_id, "done", letter=cl.generated_content)


def _open_questions(result: LetterResult) -> int:
    return sum(1 for q in result.state.user_questions if q.status != "answered")


def _run_pipeline(db: Session, job_id: int, profile_id: int, emit: Emit | None, *, run_id: int | None = None) -> Landed:
    settings = letter_settings(db, profile_id)
    _emit(emit, "letter_run_started", {
        "job_id": job_id, "run_id": run_id, "engine": settings["engine"], "resumed": run_id is not None,
    })
    try:
        if run_id is not None:
            result = resume_letter(db, run_id)
        else:
            result = run_letter(db, job_id, profile_id, engine=settings["engine"],
                                side_outputs=settings["side_outputs"], limits=settings["limits"])
    except Exception as exc:  # noqa: BLE001 — the engines record the run as failed, then re-raise
        logger.exception("letter run failed for job %s", job_id)
        db.rollback()
        reason = str(exc)[:300] or type(exc).__name__
        _emit(emit, "letter_run_failed", {"job_id": job_id, "run_id": run_id, "reason": reason})
        return Landed(RESUME if run_id else PIPELINE, job_id, "failed", run_id=run_id, reason=reason)

    kind = RESUME if run_id is not None else PIPELINE
    base = dict(kind=kind, job_id=job_id, run_id=result.run_id, reason=result.stop_reason,
                account_limit=result.account_limit)

    if result.status == "waiting_user":
        n = _open_questions(result)
        _emit(emit, "letter_run_waiting", {"job_id": job_id, "run_id": result.run_id, "questions": n})
        return Landed(status="waiting_user", questions=n, **base)

    if result.draft is None:
        if result.account_limit:
            # Stopped by the account's USD guard / quota before any draft: not an attempt
            # that failed, so don't count it against the match; it is retried after the pause.
            run = db.get(LetterRun, result.run_id)
            if run is not None:
                run.status = "cancelled"
                db.commit()
            return Landed(status="cancelled", **base)
        _emit(emit, "letter_run_failed", {"job_id": job_id, "run_id": result.run_id, "reason": result.stop_reason})
        return Landed(status=result.status if result.status != "done" else "failed", **base)

    match = db.scalar(select(Match).where(Match.user_id == profile_id, Match.job_id == job_id))
    land_letter(db, match.id, result.draft.text)
    _emit(emit, "cover_letter_ready", {"job_id": job_id, "content": result.draft.text})
    _emit(emit, "letter_run_done", {
        "job_id": job_id, "run_id": result.run_id, "status": result.status,
        "clean": result.clean, "open_issues": result.open_issues,
    })
    return Landed(status=result.status, letter=result.draft.text, clean=result.clean,
                  open_issues=result.open_issues, **base)
