"""One entry point for the cover-letter engines (plan §6, §5.5).

``letter_engine`` picks the agent (the default, decided 2026-10-03) or the fixed
workflow; both share every tool and guardrail. Production runs use the
``ask_user_gaps`` policy, so a run with an unanswered must-have gap pauses as
``waiting_user``; once the user has answered (answers.py) it is ``answered`` and
``resume_letter`` continues it with the engine that started it. The evals call the
engines directly with ``leave_out_gaps`` instead.

Phase 8 wires these into the idle loop's single worker; until then nothing in the
app calls them automatically.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm.letter import agent, workflow
from app.llm.letter.gap_policy import ask_user_gaps
from app.llm.letter.outcome import GapPolicy, LetterResult
from app.models import LetterRun

ENGINES = (agent.ENGINE, workflow.ENGINE)
DEFAULT_ENGINE = agent.ENGINE


def run_letter(
    db: Session,
    job_id: int,
    profile_id: int,
    *,
    engine: str = DEFAULT_ENGINE,
    gap_policy: GapPolicy = ask_user_gaps,
    side_outputs: tuple[str, ...] = (),
) -> LetterResult:
    """``side_outputs`` names the side-output tools the run may call (the user's toggles,
    ``preferences.letter_settings``); a resumed run keeps the ones it started with."""
    if engine == agent.ENGINE:
        return agent.run_agent(db, job_id, profile_id, gap_policy=gap_policy, side_outputs_enabled=side_outputs)
    if engine == workflow.ENGINE:
        return workflow.run_workflow(db, job_id, profile_id, gap_policy=gap_policy,
                                     side_outputs_enabled=side_outputs)
    raise ValueError(f"unknown letter engine {engine!r}; expected one of {ENGINES}")


def resume_letter(db: Session, run_id: int, *, gap_policy: GapPolicy = ask_user_gaps) -> LetterResult:
    """Continue an ``answered`` run with the engine that started it."""
    run = db.get(LetterRun, run_id)
    if run is None:
        raise ValueError(f"letter run {run_id} not found")
    if run.engine == agent.ENGINE:
        return agent.resume_agent(db, run_id, gap_policy=gap_policy)
    if run.engine == workflow.ENGINE:
        return workflow.resume_workflow(db, run_id, gap_policy=gap_policy)
    raise ValueError(f"letter run {run_id} has engine {run.engine!r}, which can't be resumed")


def answered_runs(db: Session) -> list[int]:
    """Runs ready to resume, oldest first (what the worker picks up)."""
    return list(db.scalars(select(LetterRun.id).where(LetterRun.status == "answered").order_by(LetterRun.id)))
