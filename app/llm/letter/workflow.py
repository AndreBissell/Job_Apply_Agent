"""The fixed cover-letter workflow: code decides the order of steps (plan §5.1, Phase 6).

This is the baseline the agent (Phase 7) has to beat, and a usable engine on its
own: Phase 8 can call ``run_workflow`` from the idle loop as it is. It drives the
same tools through the same ``runner.execute_tool`` and obeys the same
``guardrails`` as the agent will, so the two are compared on the order of steps
alone:

    analyze_job -> match_profile -> [ask_user, if a must-have gap is pending] -> generate_letter
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

from typing import Any, Callable

from sqlalchemy.orm import Session

from app.llm.client import BudgetExceededError, DailyQuotaError
from app.llm.letter import guardrails
from app.llm.letter.gap_policy import ask_user_tool
from app.llm.letter.outcome import GapPolicy, LetterResult, Stop, conclude, leave_out_gaps, open_run, reopen_run
from app.llm.letter.runner import ToolContext, execute_tool, finish_run
from app.llm.letter.state import LetterState
from app.llm.letter.tools.analyze_job import analyze_job
from app.llm.letter.tools.check_claims import check_claims
from app.llm.letter.tools.check_requirements import check_requirements
from app.llm.letter.tools.generate import generate_letter
from app.llm.letter.tools.match_profile import match_profile
from app.llm.letter.tools.revise import revise_letter
from app.llm.letter.tools.style_lint import style_lint

ENGINE = "workflow"
# Revisions after draft 1. With Budget.max_drafts = 3 a full run is 14 tool calls:
# analyze + match + generate + 3 checks + 2 x (revise + 3 checks), inside max_tool_calls 15.
MAX_REVISIONS = 2

CHECKS: tuple[tuple[str, Callable[..., dict[str, Any]]], ...] = (
    ("check_claims", check_claims),
    ("check_requirements", check_requirements),
    ("style_lint", style_lint),
)

# The result shape is shared with the agent (outcome.py); the old name stays for callers.
WorkflowResult = LetterResult


def run_workflow(
    db: Session,
    job_id: int,
    profile_id: int,
    *,
    gap_policy: GapPolicy = leave_out_gaps,
    max_revisions: int = MAX_REVISIONS,
    engine: str = ENGINE,
) -> LetterResult:
    """Write one cover letter for a scored job, in a fixed order of tool calls.

    Every step is logged to ``letter_run_steps`` and the state is persisted to
    ``letter_runs`` after each one. Raises ``ValueError`` only when there is
    nothing to run on (no such job, or the job has no match for this profile);
    every other way a run can end is a ``LetterResult``.
    """
    state, ctx = open_run(db, job_id, profile_id, engine)
    return _drive(state, ctx, gap_policy, max_revisions)


def resume_workflow(
    db: Session,
    run_id: int,
    *,
    gap_policy: GapPolicy = leave_out_gaps,
    max_revisions: int = MAX_REVISIONS,
) -> LetterResult:
    """Continue a run the user has answered every ask_user question for. It skips what
    is already done: analysis, and the matching of every requirement the user did not
    add rows for (those were reset to ``unknown`` and are re-matched)."""
    state, ctx = reopen_run(db, run_id)
    return _drive(state, ctx, gap_policy, max_revisions)


def _drive(state: LetterState, ctx: ToolContext, gap_policy: GapPolicy, max_revisions: int) -> LetterResult:
    def step(name: str, fn: Callable[..., dict[str, Any]]) -> None:
        limit = state.budget_exceeded()
        if limit:
            raise Stop("budget_stopped", limit)
        result = execute_tool(ctx, state, name, fn)
        if not result.ok:
            raise Stop("failed", f"{name} failed: {result.error}")

    def run_checks() -> None:
        for name, fn in CHECKS:
            step(name, fn)

    status, reason, account = "done", None, None
    try:
        if not state.requirements:
            step("analyze_job", analyze_job)
        if any(r.status == "unknown" for r in state.requirements):
            step("match_profile", match_profile)
        if state.pending_gaps():
            step("ask_user", ask_user_tool(gap_policy))
        if state.waiting_on_user():
            raise Stop("waiting_user", "questions sent to the user")
        blocked = guardrails.drafting_blocked(state)
        if blocked:
            raise Stop("failed", blocked)
        if not state.drafts:
            step("generate_letter", generate_letter)
            run_checks()
        revisions = 0
        while guardrails.can_finish(state) is not None:
            if revisions >= max_revisions:
                failed = ", ".join(guardrails.failed_checks(state))
                raise Stop("budget_stopped", f"revision limit reached ({revisions}/{max_revisions}); "
                                              f"still failing: {failed}")
            refusal = guardrails.can_revise(state)
            if refusal:
                raise Stop("budget_stopped", refusal)
            step("revise_letter", revise_letter)
            revisions += 1
            run_checks()
    except Stop as stop:
        status, reason = stop.status, stop.reason
    except (BudgetExceededError, DailyQuotaError) as exc:
        status, reason, account = "budget_stopped", f"{type(exc).__name__}: {exc}", str(exc)
    except Exception:
        # Outside any tool (execute_tool turns tool errors into results): a bug or a
        # gap policy that raised. Record the run as failed, then let it surface.
        finish_run(ctx, state, "failed")
        raise

    return conclude(ctx, state, status, reason, account)
