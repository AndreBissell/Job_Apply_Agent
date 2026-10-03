"""Runs one tool against a ``LetterState`` and records what happened.

Every tool call, in the fixed workflow and later the agent alike, goes through
``execute_tool`` so each one is timed, logged to ``letter_run_steps``, costed from
``llm_usage`` and persisted to ``letter_runs.state`` — which is what makes a run
debuggable ("which step caused this?") and resumable after a crash or an
``ask_user`` pause. See docs/cover-letter-loop-plan.md §5.2–5.3 and §7.

Tools are plain functions: ``fn(state, ctx, **args) -> dict`` that mutate the state
and return a small JSON-able summary. A tool that fails raises ``ToolError`` (or
anything else); ``execute_tool`` logs the failure and returns it rather than
raising, so the caller can decide what a failed step means. Budget and quota
errors are the exception: they stop a run, so they are logged and re-raised.
"""

from __future__ import annotations

import datetime
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.llm.client import BudgetExceededError, DailyQuotaError
from app.llm.letter.state import LetterState, ProfileIndex
from app.models import Experience, LetterRun, LetterRunStep, LlmUsage, Profile

logger = logging.getLogger(__name__)


class ToolError(RuntimeError):
    """A tool could not do its job. The message is written for the orchestrator."""


@dataclass
class ToolContext:
    db: Session
    run: LetterRun
    profile: Profile
    index: ProfileIndex


@dataclass
class ToolResult:
    ok: bool
    summary: dict[str, Any] | None = None
    error: str | None = None


def load_profile(db: Session, profile_id: int) -> Profile:
    profile = db.scalar(
        select(Profile)
        .where(Profile.id == profile_id)
        .options(
            selectinload(Profile.qualifications),
            selectinload(Profile.skills),
            selectinload(Profile.experiences).selectinload(Experience.skills),
        )
    )
    if profile is None:
        raise ToolError(f"profile {profile_id} not found")
    return profile


def start_run(db: Session, match_id: int, engine: str, state: LetterState) -> ToolContext:
    run = LetterRun(match_id=match_id, engine=engine, status="running", state=state.model_dump_json())
    db.add(run)
    db.commit()
    profile = load_profile(db, state.profile_id)
    return ToolContext(db=db, run=run, profile=profile, index=ProfileIndex(profile))


def _run_cost(db: Session, run_id: int) -> float:
    total = db.scalar(select(func.coalesce(func.sum(LlmUsage.cost_usd), 0)).where(LlmUsage.run_id == run_id))
    return float(total or 0)


def _persist(ctx: ToolContext, state: LetterState) -> None:
    state.budget.cost_usd = _run_cost(ctx.db, ctx.run.id)
    ctx.run.state = state.model_dump_json()
    ctx.run.tool_calls = state.budget.tool_calls
    ctx.run.cost_usd = state.budget.cost_usd
    ctx.db.commit()


def execute_tool(
    ctx: ToolContext,
    state: LetterState,
    name: str,
    fn: Callable[..., dict[str, Any]],
    **args: Any,
) -> ToolResult:
    """Run ``fn`` as step N of this run, log it, persist the state."""
    seq = (ctx.db.scalar(
        select(func.coalesce(func.max(LetterRunStep.seq), 0)).where(LetterRunStep.run_id == ctx.run.id)
    ) or 0) + 1
    state.budget.tool_calls += 1

    start = time.monotonic()
    summary: dict[str, Any] | None = None
    error: str | None = None
    stop: Exception | None = None
    try:
        summary = fn(state, ctx, **args)
    except (BudgetExceededError, DailyQuotaError) as exc:
        error, stop = f"{type(exc).__name__}: {exc}", exc
    except Exception as exc:  # noqa: BLE001 — a failed step is data, not a crash
        logger.exception("letter tool %s failed", name)
        error = str(exc)[:500] or type(exc).__name__

    if error is not None:
        ctx.db.rollback()  # a tool that died mid-write must not leave a half-applied session
    ctx.db.add(
        LetterRunStep(
            run_id=ctx.run.id,
            seq=seq,
            tool=name,
            args=json.dumps(args, default=str) if args else None,
            result_summary=json.dumps(summary) if summary is not None else None,
            error=error,
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    )
    _persist(ctx, state)
    if stop is not None:
        raise stop
    return ToolResult(ok=error is None, summary=summary, error=error)


def finish_run(ctx: ToolContext, state: LetterState, status: str) -> None:
    """status: done | budget_stopped | waiting_user | failed."""
    ctx.run.status = status
    if status != "waiting_user":
        ctx.run.finished_at = datetime.datetime.now(datetime.timezone.utc)
    final = state.latest_draft
    ctx.run.final_draft_version = final.version if final else None
    _persist(ctx, state)
