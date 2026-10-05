"""When the side-output tools may run, and which are still due (plan §5.5, §5.6, Phase 7c).

The side outputs are extras beside the letter: ``answer_screening``, ``suggest_learning``
and ``suggest_resume_tweaks``. Each is behind its own preference toggle (plan §6), fixed
per run in ``state.side_outputs.enabled``. The rules both engines obey, in code:

- **Off means absent.** A disabled tool is not offered to the agent and is refused if
  named; with all three off a run is exactly the letter-only run of Phase 8.
- **Once per run.** A tool that ran (or failed) is never run again: a failure is shown,
  not retried, and never stops the letter.
- **Each waits for what it reads.** Screening answers and learning suggestions wait until
  every requirement is matched and every must-have gap decided (so an ask_user "Yes" can
  feed an answer); résumé notes wait for the final letter (``guardrails.letter_final``).
- **Due before finish.** A tool is *due* when it is enabled, has not run, and has
  something to work on (the ad has screening questions; the user confirmed a gap). The
  agent's ``finish`` is refused while one is due: plan §5.5 says "call each enabled
  side-output tool once", and leaving it to the model would make the outputs depend on
  its mood. The workflow runs every due one in a fixed order (``run_due``).
- **Their own budget.** They are not charged to the letter's tool-call cap (a full
  workflow run is already 14 of 15); each runs at most once, so three is their bound. The
  run's USD cap still covers them: ``run_due`` stops when it is reached.
"""

from __future__ import annotations

from typing import Any, Callable

from app.llm.letter import guardrails
from app.llm.letter.runner import ToolContext, execute_tool
from app.llm.letter.state import SIDE_OUTPUT_TOOLS, LetterState
from app.preferences import SIDE_OUTPUT_TOGGLES

# Tool -> the preference that switches it on (plan §6). All default True.
TOGGLES = SIDE_OUTPUT_TOGGLES

LATER, NOT_NEEDED = "later", "not_needed"


def not_ready(state: LetterState, tool: str) -> tuple[str, str] | None:
    """Why ``tool`` can't run yet, as (LATER | NOT_NEEDED, reason), or None when it can.
    LATER clears as the run goes on; NOT_NEEDED means there is nothing for it in this run."""
    if tool == "suggest_resume_tweaks":
        if guardrails.letter_final(state) is None:
            return LATER, (
                "the letter is not final yet: it reads the final draft, so call it once every check "
                "passed on the latest draft (or the draft limit is reached)"
            )
        return None
    if not state.requirements:
        return LATER, "the job is not analysed yet: call analyze_job first"
    if tool == "answer_screening" and not state.job.screening_questions:
        return NOT_NEEDED, "the ad has no screening questions"
    blocked = guardrails.drafting_blocked(state)
    if blocked:
        return LATER, f"wait until the gaps are settled ({blocked})"
    if tool == "suggest_learning" and not guardrails.confirmed_gaps(state):
        return NOT_NEEDED, "the user confirmed no gap (no 'no' to ask_user, now or on an earlier ad)"
    return None


def gate(tool: str) -> Callable[[LetterState], str | None]:
    """The registry gate for one side-output tool."""

    def check(state: LetterState) -> str | None:
        if tool not in state.side_outputs.enabled:
            return f"{tool} is switched off in the user's settings"
        if tool in state.side_outputs.ran:
            return f"{tool} already ran in this run; each side output runs once"
        why = not_ready(state, tool)
        return why[1] if why else None

    return check


def enabled(state: LetterState) -> list[str]:
    return [t for t in SIDE_OUTPUT_TOOLS if t in state.side_outputs.enabled]


def due(state: LetterState) -> list[str]:
    """Enabled side outputs that haven't run and can run now."""
    return [t for t in enabled(state) if t not in state.side_outputs.ran and not_ready(state, t) is None]


def finish_blocked(state: LetterState) -> str | None:
    """The agent's finish gate for side outputs; None when nothing is due."""
    pending = due(state)
    if not pending:
        return None
    return f"call {', '.join(pending)} first: each enabled side output runs once before finish"


def status_lines(state: LetterState) -> list[str]:
    """The side outputs in the orchestrator's state summary (none when all are off)."""
    tools = enabled(state)
    if not tools:
        return []
    lines = ["SIDE OUTPUTS (each runs once; finish is refused while one is due):"]
    for t in tools:
        if t in state.side_outputs.ran:
            failed = t in state.side_outputs.errors
            lines.append(f"  {t}: {'FAILED, not retried' if failed else 'done'}")
            continue
        why = not_ready(state, t)
        if why is None:
            lines.append(f"  {t}: due")
        else:
            lines.append(f"  {t}: {'not needed' if why[0] == NOT_NEEDED else 'not yet'} ({why[1]})")
    return lines


def run_due(ctx: ToolContext, state: LetterState, fns: dict[str, Callable[..., dict[str, Any]]]) -> list[str]:
    """Run every due side output in a fixed order (the workflow's way, and the agent's
    fallback when the letter's tool cap left it no turn). A failure is recorded and the
    next one still runs; the run's USD cap stops the rest. Returns the tools run."""
    ran: list[str] = []
    for tool in due(state):
        if state.budget.over_cost():
            break
        execute_tool(ctx, state, tool, fns[tool])
        ran.append(tool)
    return ran
