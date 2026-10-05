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
      -> [answer_screening, suggest_learning, suggest_resume_tweaks: each enabled one
          that has something to work on, once the letter is final]
      -> finish

The side outputs (Phase 7c) are the agent's own tools in a fixed order, after the letter
and never instead of it: same cost per tool, so the two engines still compare on the
order of steps alone. A failed one is recorded and skipped, never fatal.

A run never crashes on a limit. When it stops short of a draft that passes every
check (the revision limit, the per-run budget, the account's USD guard, a tool
that failed), it hands back ``guardrails.best_draft`` with its open issues. A draft
that failed check_claims comes back flagged, never as clean (plan §5.7).
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from sqlalchemy.orm import Session

from app.llm.client import BudgetExceededError, DailyQuotaError
from app.llm.letter import guardrails, side_outputs
from app.llm.letter.gap_policy import ask_user_tool
from app.llm.letter.outcome import GapPolicy, LetterResult, Stop, conclude, leave_out_gaps, open_run, reopen_run
from app.llm.letter.runner import ToolContext, execute_tool, finish_run
from app.llm.letter.state import LetterState
from app.llm.letter.tools.analyze_job import analyze_job
from app.llm.letter.tools.answer_screening import answer_screening
from app.llm.letter.tools.check_claims import check_claims
from app.llm.letter.tools.check_requirements import check_requirements
from app.llm.letter.tools.generate import generate_letter
from app.llm.letter.tools.match_profile import match_profile
from app.llm.letter.tools.revise import revise_letter
from app.llm.letter.tools.style_lint import style_lint
from app.llm.letter.tools.suggest_learning import suggest_learning
from app.llm.letter.tools.suggest_resume_tweaks import suggest_resume_tweaks

ENGINE = "workflow"
# Revisions after draft 1 at the default Budget.max_drafts = 3: a full run is then 14 tool
# calls (analyze + match + generate + 3 checks + 2 x (revise + 3 checks)), inside
# max_tool_calls 15. The run's actual limit is max_drafts - 1 (the user's preference), unless a
# caller passes max_revisions.
MAX_REVISIONS = 2

CHECKS: tuple[tuple[str, Callable[..., dict[str, Any]]], ...] = (
    ("check_claims", check_claims),
    ("check_requirements", check_requirements),
    ("style_lint", style_lint),
)


def side_tools() -> dict[str, Callable[..., dict[str, Any]]]:
    """Looked up when called, so tests can swap the module's functions."""
    return {
        "answer_screening": answer_screening,
        "suggest_learning": suggest_learning,
        "suggest_resume_tweaks": suggest_resume_tweaks,
    }


# The result shape is shared with the agent (outcome.py); the old name stays for callers.
WorkflowResult = LetterResult


def run_workflow(
    db: Session,
    job_id: int,
    profile_id: int,
    *,
    gap_policy: GapPolicy = leave_out_gaps,
    max_revisions: int | None = None,
    engine: str = ENGINE,
    side_outputs_enabled: tuple[str, ...] = (),
    limits: Mapping[str, float] | None = None,
) -> LetterResult:
    """Write one cover letter for a scored job, in a fixed order of tool calls.

    Every step is logged to ``letter_run_steps`` and the state is persisted to
    ``letter_runs`` after each one. Raises ``ValueError`` only when there is
    nothing to run on (no such job, or the job has no match for this profile);
    every other way a run can end is a ``LetterResult``.
    """
    state, ctx = open_run(db, job_id, profile_id, engine, side_outputs_enabled, limits)
    return _drive(state, ctx, gap_policy, max_revisions)


def resume_workflow(
    db: Session,
    run_id: int,
    *,
    gap_policy: GapPolicy = leave_out_gaps,
    max_revisions: int | None = None,
) -> LetterResult:
    """Continue a run the user has answered every ask_user question for. It skips what
    is already done: analysis, and the matching of every requirement the user did not
    add rows for (those were reset to ``unknown`` and are re-matched)."""
    state, ctx = reopen_run(db, run_id)
    return _drive(state, ctx, gap_policy, max_revisions)


def _drive(
    state: LetterState, ctx: ToolContext, gap_policy: GapPolicy, max_revisions: int | None,
) -> LetterResult:
    if max_revisions is None:  # one limit: the drafts the run may write (draft 1 + revisions)
        max_revisions = max(state.budget.max_drafts - 1, 0)
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

    def run_side_outputs() -> None:
        """Once the letter is final (clean, or out of drafts): every due side output."""
        if guardrails.letter_final(state) is not None:
            side_outputs.run_due(ctx, state, side_tools())

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
                run_side_outputs()
                failed = ", ".join(guardrails.failed_checks(state))
                raise Stop("budget_stopped", f"revision limit reached ({revisions}/{max_revisions}); "
                                              f"still failing: {failed}")
            refusal = guardrails.can_revise(state)
            if refusal:
                run_side_outputs()
                raise Stop("budget_stopped", refusal)
            step("revise_letter", revise_letter)
            revisions += 1
            run_checks()
        run_side_outputs()
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
