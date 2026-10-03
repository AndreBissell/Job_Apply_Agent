"""The fixed cover-letter workflow: code decides the order of steps (plan §5.1, Phase 6).

This is the baseline the agent (Phase 7) has to beat, and a usable engine on its
own: Phase 8 can call ``run_workflow`` from the idle loop as it is. It drives the
same tools through the same ``runner.execute_tool`` and obeys the same
``guardrails`` as the agent will, so the two are compared on the order of steps
alone:

    analyze_job -> match_profile -> gap policy -> generate_letter
      -> check_claims, check_requirements, style_lint
      -> while a check failed (at most MAX_REVISIONS times):
             revise_letter (always the LATEST draft) -> the three checks again
      -> finish

A run never crashes on a limit. When it stops short of a draft that passes every
check (the revision limit, the per-run budget, the account's USD guard, a tool
that failed), it hands back ``guardrails.best_draft`` with its open issues. A draft
that failed check_claims comes back flagged, never as clean (plan §5.7).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm.client import BudgetExceededError, DailyQuotaError
from app.llm.letter import guardrails
from app.llm.letter.runner import ToolContext, execute_tool, finish_run, start_run
from app.llm.letter.state import Draft, JobInfo, LetterState, UserDecision
from app.llm.letter.tools.analyze_job import analyze_job
from app.llm.letter.tools.check_claims import check_claims
from app.llm.letter.tools.check_requirements import check_requirements
from app.llm.letter.tools.generate import generate_letter
from app.llm.letter.tools.match_profile import match_profile
from app.llm.letter.tools.revise import revise_letter
from app.llm.letter.tools.style_lint import style_lint
from app.models import JobListing, Match

logger = logging.getLogger(__name__)

ENGINE = "workflow"
# Revisions after draft 1. With Budget.max_drafts = 3 a full run is 14 tool calls:
# analyze + match + generate + 3 checks + 2 x (revise + 3 checks), inside max_tool_calls 15.
MAX_REVISIONS = 2

CHECKS: tuple[tuple[str, Callable[..., dict[str, Any]]], ...] = (
    ("check_claims", check_claims),
    ("check_requirements", check_requirements),
    ("style_lint", style_lint),
)

# Decides every must-have gap before drafting. Phase 6 leaves them out; Phase 7
# swaps in ask_user, which posts questions and leaves the run waiting on the user.
GapPolicy = Callable[[LetterState, ToolContext], None]


def leave_out_gaps(state: LetterState, ctx: ToolContext) -> None:
    """Treat every undecided must-have gap as ``leave_out`` (no ask_user yet)."""
    for r in state.pending_gaps():
        r.user_decision = UserDecision(choice="leave_out", answer="unanswered gap left out (no ask_user yet)")


@dataclass
class WorkflowResult:
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


class _Stop(Exception):
    def __init__(self, status: str, reason: str):
        super().__init__(reason)
        self.status, self.reason = status, reason


def run_workflow(
    db: Session,
    job_id: int,
    profile_id: int,
    *,
    gap_policy: GapPolicy = leave_out_gaps,
    max_revisions: int = MAX_REVISIONS,
    engine: str = ENGINE,
) -> WorkflowResult:
    """Write one cover letter for a scored job, in a fixed order of tool calls.

    Every step is logged to ``letter_run_steps`` and the state is persisted to
    ``letter_runs`` after each one. Raises ``ValueError`` only when there is
    nothing to run on (no such job, or the job has no match for this profile);
    every other way a run can end is a ``WorkflowResult``.
    """
    job = db.get(JobListing, job_id)
    if job is None:
        raise ValueError(f"job {job_id} not found")
    match = db.scalar(select(Match).where(Match.job_id == job_id, Match.user_id == profile_id))
    if match is None:
        raise ValueError(f"job {job_id} has no match for profile {profile_id}: score it first")

    state = LetterState(profile_id=profile_id, job=JobInfo(job_id=job.id, title=job.title, company=job.company))
    ctx = start_run(db, match.id, engine, state)

    def step(name: str, fn: Callable[..., dict[str, Any]]) -> None:
        limit = state.budget_exceeded()
        if limit:
            raise _Stop("budget_stopped", limit)
        result = execute_tool(ctx, state, name, fn)
        if not result.ok:
            raise _Stop("failed", f"{name} failed: {result.error}")

    def run_checks() -> None:
        for name, fn in CHECKS:
            step(name, fn)

    status, reason, account = "done", None, None
    try:
        step("analyze_job", analyze_job)
        step("match_profile", match_profile)
        gap_policy(state, ctx)
        blocked = guardrails.drafting_blocked(state)
        if blocked:
            raise _Stop("waiting_user" if state.waiting_on_user() else "failed", blocked)
        step("generate_letter", generate_letter)
        run_checks()
        revisions = 0
        while guardrails.can_finish(state) is not None:
            if revisions >= max_revisions:
                failed = ", ".join(guardrails.failed_checks(state))
                raise _Stop("budget_stopped", f"revision limit reached ({revisions}/{max_revisions}); "
                                              f"still failing: {failed}")
            refusal = guardrails.can_revise(state)
            if refusal:
                raise _Stop("budget_stopped", refusal)
            step("revise_letter", revise_letter)
            revisions += 1
            run_checks()
    except _Stop as stop:
        status, reason = stop.status, stop.reason
    except (BudgetExceededError, DailyQuotaError) as exc:
        status, reason, account = "budget_stopped", f"{type(exc).__name__}: {exc}", str(exc)
    except Exception:
        # Outside any tool (execute_tool turns tool errors into results): a bug or a
        # gap policy that raised. Record the run as failed, then let it surface.
        finish_run(ctx, state, "failed")
        raise

    draft = guardrails.best_draft(state)
    clean = draft is not None and guardrails.is_clean(draft)
    if status == "done" and not clean:  # unreachable unless can_finish and is_clean disagree
        status, reason = "failed", "finished without a clean draft"
    finish_run(ctx, state, status, final_version=draft.version if draft else None)
    if reason:
        logger.info("letter run %s (job %s) ended %s: %s", ctx.run.id, job_id, status, reason)
    return WorkflowResult(
        run_id=ctx.run.id,
        status=status,
        state=state,
        draft=draft,
        clean=clean,
        open_issues=guardrails.open_issues(draft) if draft else [],
        stop_reason=reason,
        account_limit=account,
    )
