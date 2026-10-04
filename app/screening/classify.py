"""Layer 5: the small model sorts a question layers 1-4 left ``unknown`` (plan §10.1).

Once per bank row, never per job: the result is stored on the row
(``classified_by='model'``, status stays ``new`` so the review list shows it), and every
later job reuses it as a bank hit. Called only from the assist view, for a job that gets
help (a full-pipeline letter), so capture itself never costs anything.

Guards, all in code:
* a row that is not ``unknown``, or that a user corrected, is never sent;
* a question whose wording the `user` keyword filter recognises is never sent, even if
  its row somehow says ``unknown`` (it is sorted ``user`` by keyword instead);
* the role / skill the model names must be words from the question, else the answer is
  thrown away and the row stays ``unknown`` (shown as "not sorted yet", no help);
* a failure leaves the row ``unknown`` and the row is not retried for
  ``RETRY_AFTER_SECONDS``, so a page refresh can't loop on a broken call.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Literal

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.llm.client import LLMError, complete_json
from app.models import ScreeningQuestion
from app.screening.identity import normalise_text
from app.screening.sort import ASSISTED_STRATEGIES, user_topic

logger = logging.getLogger(__name__)

TIER = "small"
TASK = "screening_classify"
RETRY_AFTER_SECONDS = 600

# Strategies that need a subject, and which parameter holds it.
SUBJECT_PARAM = {
    "years_role_bracket": "role",
    "years_skill_text": "skill",
    "skill_in_role_yes_no": "skill",
    "free_text_describe": "skill",
}

_failed_at: dict[int, float] = {}
# One lock per row: the overlay and the sidebar (or the overlay twice) can ask at once.
# The second request waits for the first's answer and reads it, instead of a second call.
_row_locks: dict[int, threading.Lock] = {}
_lock = threading.Lock()
WAIT_SECONDS = 60


class ModelSorting(BaseModel):
    # No defaults: Gemini structured output rejects them.
    kind: Literal["user", "assisted"]
    strategy: Literal["user", *ASSISTED_STRATEGIES]
    role: str  # the role asked about ("" when none)
    skill: str  # the skill or technology asked about ("" when none)


_PROMPT = """\
You sort one question from a job application form. Return its kind, an answering \
strategy, and the role or skill it asks about.

kind:
- "user": personal or logistics questions only the applicant can answer: work rights, \
visa, salary, notice period, availability, work arrangement, location, how they heard \
about the job, identity, legal declarations, motivation or interest in the company.
- "assisted": questions about the applicant's experience, skills or background that the \
job ad relates to.

strategy (for "user" always "user"):
- "years_role_bracket": years of experience AS a role, answered by picking a range \
from a list. Set role.
- "years_skill_text": years of experience with a specific skill or technology, \
answered in free text. Set skill.
- "skill_in_role_yes_no": yes/no, has the applicant used a skill in a job or role. Set skill.
- "skill_multi_select": pick every skill/technology from a list that the applicant has.
- "free_text_describe": describe experience with something, in free text. Set skill \
when one is named.

role and skill: copy the words exactly as they appear in the question (for example \
"React Native with Expo", "full stack developer"). Use "" when the question names none. \
Never add words the question doesn't contain."""


def _content_words(phrase: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9#+.]+", normalise_text(phrase)) if w not in ("a", "an", "the")]


def words_from_question(phrase: str, question_text: str) -> bool:
    """Is every word of ``phrase`` a word of the question? (Order and case don't matter.)"""
    words = _content_words(phrase)
    if not words:
        return False
    question_words = set(re.findall(r"[a-z0-9#+.]+", normalise_text(question_text)))
    question_words |= {w.rstrip(".") for w in question_words}
    return all(w in question_words or w.rstrip(".") in question_words for w in words)


def _row_lock(row_id: int) -> threading.Lock:
    with _lock:
        return _row_locks.setdefault(row_id, threading.Lock())


def _cooling_down(row_id: int) -> bool:
    with _lock:
        t = _failed_at.get(row_id)
        return t is not None and time.monotonic() - t < RETRY_AFTER_SECONDS


def _failed(row_id: int) -> None:
    with _lock:
        _failed_at[row_id] = time.monotonic()


def validate(result: ModelSorting, row: ScreeningQuestion) -> tuple[str, str, dict] | None:
    """(kind, strategy, parameters) when the model's answer is usable, else None."""
    if result.kind == "user" or result.strategy == "user":
        return ("user", "user", {}) if result.kind == "user" else None
    if result.strategy not in ASSISTED_STRATEGIES:
        return None
    if result.strategy == "skill_multi_select" and not row.options:
        return None
    if result.strategy in ("years_role_bracket", "skill_in_role_yes_no") and row.input_type == "text":
        return None  # both answer by picking an option
    params: dict = {}
    for name, value in (("role", result.role), ("skill", result.skill)):
        value = " ".join((value or "").split())
        if value:
            if not words_from_question(value, row.text):
                logger.info("layer 5: dropped %s %r, not in question %s", name, value, row.id)
                return None
            params[name] = value
    needed = SUBJECT_PARAM.get(result.strategy)
    if needed and needed not in params and result.strategy != "free_text_describe":
        return None
    return "assisted", result.strategy, params


def classify(db: Session, row: ScreeningQuestion, *, job_id: int | None = None) -> bool:
    """Sort one ``unknown`` bank row with the small model. True when the row is now
    sorted (by the model, or by the keyword filter if it should never have been sent).
    Commits on a change."""
    if row.kind != "unknown" or row.classified_by == "user":
        return False
    topic = user_topic(row.text)
    if topic:  # belt and braces: a `user` question never reaches the model
        row.kind, row.strategy, row.classified_by = "user", "user", "keyword"
        row.parameters = json.dumps({"topic": topic})
        db.commit()
        return True
    lock = _row_lock(row.id)
    if not lock.acquire(timeout=WAIT_SECONDS):
        return False
    try:
        db.refresh(row)  # another request may have sorted it while this one waited
        if row.kind != "unknown" or row.classified_by == "user":
            return row.kind != "unknown"
        if _cooling_down(row.id):
            return False
        return _ask_model(db, row, job_id)
    finally:
        lock.release()


def _ask_model(db: Session, row: ScreeningQuestion, job_id: int | None) -> bool:
    options = json.loads(row.options) if row.options else []
    user = (
        f"QUESTION: {row.text}\n"
        f"ANSWER TYPE: {row.input_type}\n"
        + (f"OPTIONS: {' | '.join(options)}\n" if options else "")
    )
    try:
        data = complete_json(_PROMPT, user, schema=ModelSorting, tier=TIER, task=TASK, job_id=job_id)
        result = ModelSorting.model_validate(data)
    except (LLMError, ValueError) as exc:
        logger.warning("layer 5 failed for screening question %s: %s", row.id, exc)
        _failed(row.id)
        return False

    sorting = validate(result, row)
    if sorting is None:
        _failed(row.id)
        return False
    row.kind, row.strategy = sorting[0], sorting[1]
    row.parameters = json.dumps(sorting[2])
    row.classified_by = "model"
    db.commit()
    return True
