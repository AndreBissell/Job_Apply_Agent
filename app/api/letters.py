"""Letter-run questions (ask_user) and the to-work-on list: the API the sidebar needs.

Endpoints
---------
GET  /letter-runs/waiting                         runs paused for the user, with their questions
GET  /letter-runs/{run_id}                        one run's status and questions
POST /letter-runs/{run_id}/answers                answer questions: No, or Yes + text
POST /letter-runs/{run_id}/questions/{qid}/confirm   confirm (or reject) a Yes's parsed rows
GET  /gaps/to-work-on                             the ranked to-work-on list
POST /gaps/{gap_id}/clear                         take an item off the list (history kept)

A Yes is parsed by a small-model call inside the request (a few seconds). Nothing
reaches the profile until the confirm call (plan Q11). Resuming an ``answered`` run
is the worker's job (engines.resume_letter); Phase 8 wires it into the idle loop.
The sidebar card that calls these is Phase 8 too.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import gaps
from app.api.profile_ui import get_db
from app.llm.client import DailyQuotaError, LLMError
from app.llm.letter import answers
from app.llm.letter.gap_policy import ANSWER_HINT
from app.llm.letter.state import LetterState
from app.models import JobListing, LetterRun, Match

router = APIRouter()


class AnswerIn(BaseModel):
    question_id: str
    choice: Literal["yes", "no"]
    text: str | None = None  # required for "yes"


class AnswersIn(BaseModel):
    answers: list[AnswerIn] = Field(min_length=1)


class ConfirmIn(BaseModel):
    accept: bool
    rows: answers.ProposedRows | None = None  # the proposal, edited by the user


def _run_view(db: Session, run: LetterRun, state: LetterState | None = None) -> dict:
    state = state or LetterState.model_validate_json(run.state)
    job = db.get(JobListing, state.job.job_id)
    return {
        "run_id": run.id,
        "status": run.status,
        "engine": run.engine,
        "job_id": state.job.job_id,
        "job_title": job.title if job else state.job.title,
        "company": job.company if job else state.job.company,
        "answer_hint": ANSWER_HINT,
        "questions": [q.model_dump() for q in state.user_questions],
    }


def _owned_run(db: Session, run_id: int, profile_id: int) -> LetterRun:
    run = db.get(LetterRun, run_id)
    match = db.get(Match, run.match_id) if run else None
    if run is None or match is None or match.user_id != profile_id:
        raise HTTPException(status_code=404, detail=f"letter run {run_id} not found")
    return run


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, LookupError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, answers.StateConflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, answers.AnswerError):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, DailyQuotaError):
        return HTTPException(status_code=503, detail="The language model's daily quota is used up; try later.")
    if isinstance(exc, LLMError):
        return HTTPException(status_code=502, detail=f"Couldn't read that answer: {exc}")
    raise exc


@router.get("/letter-runs/waiting")
def waiting_runs(profile_id: int = 1, db: Session = Depends(get_db)) -> dict:
    runs = db.scalars(
        select(LetterRun)
        .join(Match, Match.id == LetterRun.match_id)
        .where(LetterRun.status == "waiting_user", Match.user_id == profile_id)
        .order_by(LetterRun.id)
    )
    return {"runs": [_run_view(db, r) for r in runs]}


@router.get("/letter-runs/{run_id}")
def get_run(run_id: int, profile_id: int = 1, db: Session = Depends(get_db)) -> dict:
    return _run_view(db, _owned_run(db, run_id, profile_id))


@router.post("/letter-runs/{run_id}/answers")
def post_answers(run_id: int, body: AnswersIn, profile_id: int = 1, db: Session = Depends(get_db)) -> dict:
    """Apply each answer in order. A No is final; a Yes comes back as
    ``needs_confirm`` with ``proposal`` holding the rows that would be saved."""
    run = _owned_run(db, run_id, profile_id)
    state = None
    for a in body.answers:
        try:
            if a.choice == "no":
                state = answers.answer_no(db, run_id, a.question_id)
            else:
                state = answers.answer_yes(db, run_id, a.question_id, a.text or "")
        except (LookupError, answers.AnswerError, LLMError) as exc:
            raise _http(exc) from exc
    db.refresh(run)
    return _run_view(db, run, state)


@router.post("/letter-runs/{run_id}/questions/{question_id}/confirm")
def post_confirm(
    run_id: int, question_id: str, body: ConfirmIn, profile_id: int = 1, db: Session = Depends(get_db),
) -> dict:
    run = _owned_run(db, run_id, profile_id)
    try:
        state = answers.confirm(db, run_id, question_id, accept=body.accept, rows=body.rows)
    except (LookupError, answers.AnswerError) as exc:
        raise _http(exc) from exc
    db.refresh(run)
    return _run_view(db, run, state)


@router.get("/gaps/to-work-on")
def to_work_on(profile_id: int = 1, days: int = gaps.WINDOW_DAYS, db: Session = Depends(get_db)) -> dict:
    if not 1 <= days <= 3650:
        raise HTTPException(status_code=422, detail="days must be between 1 and 3650")
    return {"window_days": days, "items": [i.as_dict() for i in gaps.to_work_on(db, profile_id, days=days)]}


@router.post("/gaps/{gap_id}/clear")
def clear_gap(gap_id: int, profile_id: int = 1, db: Session = Depends(get_db)) -> dict:
    decision = gaps.clear(db, profile_id, gap_id)
    if decision is None:
        raise HTTPException(status_code=404, detail=f"to-work-on item {gap_id} not found")
    return {"ok": True, "id": decision.id, "cleared_at": decision.cleared_at.isoformat()}
