"""The cover-letter agent: a model decides the order of steps (plan §5.5, Phase 7).

The same tools, guardrails and run-ending as the fixed workflow (workflow.py); only
who picks the next step differs. Each turn is one ``complete_tools`` call on the mid
tier, and it is stateless: the orchestrator gets the state summary, the steps taken
so far and the result of its last call (or why it was refused), never the
conversation so far. So no thought signatures are replayed, and a run can resume
from the persisted state alone.

Code enforces, the model chooses:
- every call goes through a gate first (``registry``); a refused call runs nothing,
  costs no tool call, is logged as a ``refused:`` step, and its reason is the next
  turn's "last result";
- every tool runs through ``runner.execute_tool`` (timed, logged, costed, persisted);
- ``finish`` is code: accepted when ``guardrails.can_finish`` passes, or when the
  draft limit is reached with every check run (the best draft goes back, flagged);
- the budget is checked before every turn, and orchestrator calls count towards the
  run's USD budget (they log ``llm_usage`` with the run id). Refusals and text
  replies have their own cap, so a confused model can't loop for free.

When it stops short it returns exactly what the workflow would: ``outcome.conclude``.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.llm.client import BudgetExceededError, DailyQuotaError, LLMError, complete_tools
from app.llm.letter import guardrails
from app.llm.letter.outcome import GapPolicy, LetterResult, Stop, conclude, leave_out_gaps, open_run
from app.llm.letter.registry import FINISH, build_registry
from app.llm.letter.runner import execute_tool, finish_run, persist, record_step
from app.llm.letter.state import LetterState

ENGINE = "agent"
ORCHESTRATOR_TIER = "mid"
# Refused calls and text replies allowed per run before it stops. They run no tool,
# but each costs an orchestrator call.
MAX_REFUSALS = 4

SYSTEM_PROMPT = """\
You run a cover-letter job for a job seeker. Each turn you choose ONE tool to call \
next. The tools do the work; you never see the letter itself, only the run's state.

Goal: a cover letter that addresses every must-have requirement the candidate's \
profile supports, with no unsupported claim.
Done: on the LATEST draft, check_claims and check_requirements have passed and \
style_lint has no blocking issue. Then call finish.
Budget: the state shows tool calls and drafts used against their limits. Code stops \
the run at a limit and hands the user the best draft so far.

How to work:
- Start with analyze_job, then match_profile.
- If any requirement shows "needs a user decision", call ask_user before drafting. \
Never resolve a gap by writing around it.
- generate_letter writes draft 1 only. After every draft, run check_claims, \
check_requirements and style_lint on it. Checks belong to one draft.
- If a check failed, call revise_letter: it fixes only the failed checks' issues. \
Warnings never block finish, so never revise for warnings alone.
- If every check passed on the latest draft, call finish. If the draft limit is \
reached and a check still fails, call finish too: the user gets the best draft with \
its open issues flagged.
- A refused call comes back with the reason, and nothing ran. Read the reason and \
choose a different tool; never repeat a refused call unchanged."""


def turn_prompt(state: LetterState, steps: list[str], last: str) -> str:
    """The whole of what the orchestrator sees on one turn."""
    return "\n".join([
        "STATE",
        state.summary_for_orchestrator(),
        "",
        f"STEPS SO FAR: {', '.join(steps) if steps else 'none'}",
        f"LAST RESULT: {last}",
        "",
        "Call the next tool.",
    ])


def _brief(summary: dict[str, Any] | None) -> str:
    text = json.dumps(summary or {}, ensure_ascii=False)
    return text if len(text) <= 800 else text[:800] + "…"


def run_agent(
    db: Session,
    job_id: int,
    profile_id: int,
    *,
    gap_policy: GapPolicy = leave_out_gaps,
    engine: str = ENGINE,
    tier: str = ORCHESTRATOR_TIER,
    max_refusals: int = MAX_REFUSALS,
) -> LetterResult:
    """Write one cover letter for a scored job, with a model choosing each step.

    Same contract as ``workflow.run_workflow``: raises ``ValueError`` only when there
    is no job/match to run on; every other ending is a ``LetterResult``.
    """
    state, ctx = open_run(db, job_id, profile_id, engine)
    tools = build_registry(gap_policy)
    specs = [t.spec for t in tools.values()]
    steps: list[str] = []
    last = "none yet: this is the first turn"
    refusals = 0

    def refuse(name: str, args: dict[str, Any], reason: str) -> str:
        nonlocal refusals
        refusals += 1
        record_step(ctx, state, name, args, refused=reason)
        steps.append(f"{name}(refused)")
        if refusals > max_refusals:
            raise Stop("budget_stopped", f"too many refused calls ({refusals}); last: {name}: {reason}")
        return f"{name} was REFUSED and nothing ran: {reason}"

    status, reason, account = "done", None, None
    try:
        while True:
            persist(ctx, state)  # refresh the run's cost: orchestrator calls count too
            limit = state.budget_exceeded()
            if limit:
                if guardrails.can_finish(state) is None:
                    record_step(ctx, state, FINISH, summary={"by": "code", "reason": limit})
                    break
                raise Stop("budget_stopped", limit)

            try:
                step = complete_tools(
                    SYSTEM_PROMPT,
                    [{"role": "user", "content": turn_prompt(state, steps, last)}],
                    specs,
                    tier=tier,
                    task="orchestrate",
                    job_id=state.job.job_id,
                    match_id=ctx.run.match_id,
                    run_id=ctx.run.id,
                )
            except (BudgetExceededError, DailyQuotaError):
                raise
            except LLMError as exc:
                raise Stop("failed", f"orchestrator call failed: {exc}") from exc

            name, args = step.tool, step.args or {}
            tool = tools.get(name) if name else None
            if tool is None:
                what = f"unknown tool {name!r}" if name else "a text reply instead of a tool call"
                last = refuse(name or "(text)", args, f"{what}; call one of: {', '.join(tools)}")
                continue

            if name == FINISH:
                why = guardrails.can_finish(state)
                if why is None:
                    record_step(ctx, state, FINISH, args, summary={"accepted": True})
                    steps.append(FINISH)
                    break
                if guardrails.out_of_drafts(state):
                    record_step(ctx, state, FINISH, args, summary={"accepted": True, "out_of_drafts": True})
                    steps.append(FINISH)
                    failed = ", ".join(guardrails.failed_checks(state))
                    raise Stop("budget_stopped", f"draft limit reached; still failing: {failed}")
                last = refuse(name, args, why)
                continue

            why = tool.gate(state)
            if why:
                last = refuse(name, args, why)
                continue

            result = execute_tool(ctx, state, name, tool.fn)
            steps.append(name)
            if not result.ok:
                raise Stop("failed", f"{name} failed: {result.error}")
            last = f"{name} -> {_brief(result.summary)}"
            if state.waiting_on_user():
                raise Stop("waiting_user", "questions sent to the user")
    except Stop as stop:
        status, reason = stop.status, stop.reason
    except (BudgetExceededError, DailyQuotaError) as exc:
        status, reason, account = "budget_stopped", f"{type(exc).__name__}: {exc}", str(exc)
    except Exception:
        finish_run(ctx, state, "failed")
        raise
    return conclude(ctx, state, status, reason, account)
