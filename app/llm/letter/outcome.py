"""How a cover-letter run starts and ends, shared by both engines (plan §5.7).

The fixed workflow (Phase 6) and the agent (Phase 7) decide the order of steps
differently, but they must open a run the same way and hand back the same thing
when it ends: the best draft so far, whether it is clean, and what is still wrong
with it. Both call ``open_run`` and ``conclude`` here, so the two engines can't
drift apart on what a result means.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm.letter import guardrails
from app.llm.letter.runner import ToolContext, finish_run, start_run
from app.llm.letter.state import Draft, JobInfo, LetterState, UserDecision
from app.models import JobListing, Match

logger = logging.getLogger(__name__)

# Decides every must-have gap before drafting: the hook both engines share (the
# workflow calls it after match_profile; the agent's ask_user tool runs it). Phase 6
# leaves gaps out; Phase 7b's ask_user posts questions and leaves the run waiting.
GapPolicy = Callable[[LetterState, ToolContext], None]


def leave_out_gaps(state: LetterState, ctx: ToolContext) -> None:
    """Treat every undecided must-have gap as ``leave_out`` (no ask_user yet)."""
    for r in state.pending_gaps():
        r.user_decision = UserDecision(choice="leave_out", answer="unanswered gap left out (no ask_user yet)")


@dataclass
class LetterResult:
    run_id: int
    status: str  # done | budget_stopped | waiting_user | failed (letter_runs.status)
    state: LetterState
    draft: Draft | None  # the draft to show the user: the latest if clean, else the best
    clean: bool  # True only when ``draft`` passed all three checks
    open_issues: list[str] = field(default_factory=list)  # what is still wrong with ``draft``
    stop_reason: str | None = None  # why the run ended without a clean draft
    # Set when the account-level USD guard or the provider's daily quota stopped the
    # run: unlike the run's own limits, every later run will hit it too.
    account_limit: str | None = None

    @property
    def text(self) -> str | None:
        return self.draft.text if self.draft else None


class Stop(Exception):
    """Ends a run early with a status (budget_stopped | failed | waiting_user)."""

    def __init__(self, status: str, reason: str):
        super().__init__(reason)
        self.status, self.reason = status, reason


def open_run(db: Session, job_id: int, profile_id: int, engine: str) -> tuple[LetterState, ToolContext]:
    """Create the ``letter_runs`` row and an empty state for one scored job.

    Raises ``ValueError`` when there is nothing to run on: no such job, or the job
    has no match for this profile.
    """
    job = db.get(JobListing, job_id)
    if job is None:
        raise ValueError(f"job {job_id} not found")
    match = db.scalar(select(Match).where(Match.job_id == job_id, Match.user_id == profile_id))
    if match is None:
        raise ValueError(f"job {job_id} has no match for profile {profile_id}: score it first")
    state = LetterState(profile_id=profile_id, job=JobInfo(job_id=job.id, title=job.title, company=job.company))
    return state, start_run(db, match.id, engine, state)


def conclude(
    ctx: ToolContext,
    state: LetterState,
    status: str,
    reason: str | None = None,
    account_limit: str | None = None,
) -> LetterResult:
    """Pick the draft to hand back, record how the run ended, and build the result.

    The draft is ``guardrails.best_draft`` (on a clean finish that is the latest);
    it is "clean" only when all three checks ran and passed on it, otherwise its
    open issues come back with it. A draft that failed check_claims is never
    returned as clean (plan §5.7).
    """
    draft = guardrails.best_draft(state)
    clean = draft is not None and guardrails.is_clean(draft)
    if status == "done" and not clean:  # unreachable unless can_finish and is_clean disagree
        status, reason = "failed", "finished without a clean draft"
    finish_run(ctx, state, status, final_version=draft.version if draft else None)
    if reason:
        logger.info("letter run %s (%s, job %s) ended %s: %s",
                    ctx.run.id, ctx.run.engine, state.job.job_id, status, reason)
    return LetterResult(
        run_id=ctx.run.id,
        status=status,
        state=state,
        draft=draft,
        clean=clean,
        open_issues=guardrails.open_issues(draft) if draft else [],
        stop_reason=reason,
        account_limit=account_limit,
    )
