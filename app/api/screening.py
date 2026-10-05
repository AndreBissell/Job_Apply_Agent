"""Quick Apply questions and the question bank (plan §10.1, Phases 9a and 9b).

Endpoints
---------
POST  /jobs/{job_id}/screening-questions   one capture of a job's "Answer employer
                                           questions" step: upsert into the bank, sort
                                           (layers 1-4), link to the job
GET   /jobs/{job_id}/screening-questions   the job's questions in form order
GET   /screening-questions?status=new      the bank (status=new = the review list)
PATCH /screening-questions/{id}            confirm or correct kind / strategy
GET   /jobs/{job_id}/screening-assist      9b: the questions with help (what the job wants
                                           / what your profile has), gated on the job's
                                           letter; layer 5 sorts an unknown question here
POST  /jobs/{job_id}/screening-gaps        9b: answer a wanted-but-missing skill: No is
                                           remembered; Yes returns proposed profile rows
POST  /jobs/{job_id}/screening-gaps/confirm   9b: save the (edited) rows of a Yes
POST  /jobs/{job_id}/screening-drafts  9c: draft ONE open-ended question ({bank_id}), on the
                                       user's click: one mid call, refused in code first for
                                       anything but an assisted free-text question on a
                                       full-pipeline job with help on. Stored with a
                                       fingerprint; the assist view shows it (or "redraft")

The content script posts what it read from the page the user opened: question
text, input type, option labels and option ids. Never an answer. Capture (POST
screening-questions) never calls a model; only the assist view can (layer 5, once per
bank row, full-pipeline jobs only), and a gap Yes with a typed description (one small
call to turn it into rows). A Yes never reopens or reruns the letter.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.profile_ui import get_db
from app.llm.client import BudgetExceededError, DailyQuotaError, LLMError
from app.llm.letter import answers
from app.models import JobListing
from app.screening import assist, bank, drafts
from app.screening.sort import ASSISTED_STRATEGIES, CLASSIFIED_BY, USER_TOPICS

router = APIRouter()

InputType = Literal["single", "multi", "dropdown", "text"]


class OptionIn(BaseModel):
    value: str = Field(max_length=300)
    label: str = Field(max_length=500)


class QuestionIn(BaseModel):
    seek_question_id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_\-]+$")
    field_name: str = Field(min_length=1, max_length=220)
    text: str = Field(min_length=1, max_length=2000)
    input_type: InputType
    options: list[OptionIn] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def _shape(self) -> "QuestionIn":
        if self.field_name != f"questionnaire.{self.seek_question_id}":
            raise ValueError("field_name must be questionnaire.<seek_question_id>")
        if not self.text.strip():
            raise ValueError("question text is empty")
        if self.input_type == "text" and self.options:
            raise ValueError("a free-text question has no options")
        if self.input_type != "text" and not any(o.label.strip() for o in self.options):
            raise ValueError(f"a {self.input_type} question needs at least one option")
        return self


class CaptureIn(BaseModel):
    questions: list[QuestionIn] = Field(min_length=1, max_length=60)
    step: str | None = Field(default=None, max_length=200)  # progress-bar label, for logs


class ReviewIn(BaseModel):
    kind: Literal["user", "assisted"]
    strategy: str | None = None
    parameters: dict | None = None


def _job_or_404(db: Session, job_id: int) -> JobListing:
    job = db.get(JobListing, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    return job


@router.post("/jobs/{job_id}/screening-questions")
def capture_screening_questions(job_id: int, body: CaptureIn, db: Session = Depends(get_db)) -> dict:
    _job_or_404(db, job_id)
    captured = [
        bank.CapturedQuestion(
            seek_question_id=q.seek_question_id, field_name=q.field_name, text=q.text,
            input_type=q.input_type,
            options=[bank.CapturedOption(o.value, o.label) for o in q.options],
        )
        for q in body.questions
    ]
    try:
        questions = bank.record_job_questions(db, job_id, captured)
    except IntegrityError:
        # Two captures of the same new question raced (the step re-rendered): the
        # other one created the bank row, so this pass finds it.
        db.rollback()
        questions = bank.record_job_questions(db, job_id, captured)
    return {"job_id": job_id, "questions": questions}


@router.get("/jobs/{job_id}/screening-questions")
def get_job_screening_questions(job_id: int, db: Session = Depends(get_db)) -> dict:
    _job_or_404(db, job_id)
    return {"job_id": job_id, "questions": bank.job_questions(db, job_id)}


@router.get("/screening-questions")
def list_screening_questions(
    status: Literal["new", "confirmed"] | None = None, db: Session = Depends(get_db)
) -> dict:
    return {
        "questions": bank.list_questions(db, status),
        # The vocabulary the review list offers.
        "kinds": ["user", "assisted"],
        "assisted_strategies": list(ASSISTED_STRATEGIES),
        "user_topics": list(USER_TOPICS),
        "classified_by": list(CLASSIFIED_BY),
    }


@router.patch("/screening-questions/{question_id}")
def review_screening_question(question_id: int, body: ReviewIn, db: Session = Depends(get_db)) -> dict:
    try:
        return bank.review_question(db, question_id, body.kind, body.strategy, body.parameters)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except bank.CorrectionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# 9b: help, and the run-less gap answers
# ---------------------------------------------------------------------------
class GapAnswerIn(BaseModel):
    skill: str = Field(min_length=1, max_length=200)
    choice: Literal["yes", "no"]
    text: str | None = Field(default=None, max_length=answers.MAX_ANSWER_CHARS)  # optional, Yes only
    requirement_text: str | None = Field(default=None, max_length=2000)
    importance: Literal["essential", "important", "nice_to_have"] | None = None


class GapConfirmIn(BaseModel):
    skill: str = Field(min_length=1, max_length=200)
    rows: answers.ProposedRows


@router.get("/jobs/{job_id}/screening-assist")
def get_screening_assist(job_id: int, profile_id: int = 1, db: Session = Depends(get_db)) -> dict:
    _job_or_404(db, job_id)
    return assist.assist_job(db, job_id, profile_id)


@router.post("/jobs/{job_id}/screening-gaps")
def answer_screening_gap(job_id: int, body: GapAnswerIn, profile_id: int = 1,
                         db: Session = Depends(get_db)) -> dict:
    job = _job_or_404(db, job_id)
    try:
        if body.choice == "no":
            decision = answers.answer_no_runless(
                db, profile_id, skill=body.skill, requirement_text=body.requirement_text,
                job_id=job_id, job_title=job.title, importance=body.importance,
            )
            return {"skill": body.skill, "choice": "no", "gap_id": decision.id, "label": decision.label}
        rows = answers.propose_skill(
            skill=body.skill, requirement_text=body.requirement_text or body.skill, job_title=job.title,
            company=job.company, job_id=job_id, text=body.text,
        )
    except answers.AnswerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DailyQuotaError as exc:
        raise HTTPException(status_code=503, detail="The language model's daily quota is used up; try later.") from exc
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=f"Couldn't read that answer: {exc}") from exc
    return {"skill": body.skill, "choice": "yes", "proposal": rows.model_dump()}


class DraftIn(BaseModel):
    bank_id: int = Field(ge=1)


@router.post("/jobs/{job_id}/screening-drafts")
def draft_screening_answer(job_id: int, body: DraftIn, profile_id: int = 1,
                           db: Session = Depends(get_db)) -> dict:
    _job_or_404(db, job_id)
    try:
        return drafts.draft_question(db, job_id, body.bank_id, profile_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except drafts.DraftRefused as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except BudgetExceededError as exc:
        raise HTTPException(
            status_code=429,
            detail=f"The LLM spending cap is reached, so nothing was drafted and nothing was spent ({exc}). "
                   "Raise the cap in Personalise or try again tomorrow.",
        ) from exc
    except DailyQuotaError as exc:
        raise HTTPException(status_code=503, detail="The language model's daily quota is used up; try later.") from exc
    except (LLMError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"Couldn't draft an answer: {exc}") from exc


@router.post("/jobs/{job_id}/screening-gaps/confirm")
def confirm_screening_gap(job_id: int, body: GapConfirmIn, profile_id: int = 1,
                          db: Session = Depends(get_db)) -> dict:
    _job_or_404(db, job_id)
    try:
        saved_as, cleared = answers.confirm_runless(db, profile_id, body.rows)
    except answers.AnswerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"skill": body.skill, "saved_as": saved_as, "auto_cleared": cleared}
