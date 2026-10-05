"""The question bank: upsert a job's captured questions, sort what's new, review.

``record_job_questions`` is the whole of POST /jobs/{id}/screening-questions:
for each captured question, find its bank row by identity (layer 1, a bank hit) or
create one, sort it with layers 2-4 if it is still ``unknown``, and link it to the
job. A bank row is never re-sorted once it has a kind, and a user's correction
(``classified_by='user'``) is never overwritten by code.

Nothing here reads or stores an answer: the payload only carries question text,
type and option labels/ids (the content script never reads checked / selected /
typed values).
"""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass, field

from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app.models import JobScreeningQuestion, ScreeningQuestion
from app.screening.identity import clean_text, identity_key, normalise_text, parse_library_id
from app.screening.sort import ASSISTED_STRATEGIES, sort_question


@dataclass
class CapturedOption:
    value: str
    label: str


@dataclass
class CapturedQuestion:
    seek_question_id: str
    field_name: str
    text: str
    input_type: str  # single | multi | dropdown | text
    options: list[CapturedOption] = field(default_factory=list)


class CorrectionError(ValueError):
    """A review correction that doesn't fit the kind/strategy vocabulary."""


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _loads(raw: str | None, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except ValueError:
        return default


def question_view(row: ScreeningQuestion) -> dict:
    return {
        "bank_id": row.id,
        "identity_key": row.identity_key,
        "library_id": row.library_id,
        "library_version": row.library_version,
        "text": row.text,
        "input_type": row.input_type,
        "options": _loads(row.options, []),
        "kind": row.kind,
        "strategy": row.strategy,
        "parameters": _loads(row.parameters, {}),
        "classified_by": row.classified_by,
        "status": row.status,
        "times_seen": row.times_seen,
    }


def _sort_new(row: ScreeningQuestion, seek_question_id: str) -> str | None:
    # The library table needs the real Seek id; a fingerprinted row has none stored.
    sorting = sort_question(seek_question_id, row.text, row.input_type, _loads(row.options, []))
    if sorting is None:
        return None
    row.kind = sorting.kind
    row.strategy = sorting.strategy
    row.parameters = json.dumps(sorting.parameters)
    row.classified_by = sorting.classified_by
    return sorting.classified_by


def record_job_questions(db: Session, job_id: int, questions: list[CapturedQuestion]) -> list[dict]:
    """Upsert one capture of a job's form into the bank and link it. Commits.

    Returns one view per question in form order, each with ``sorted_by`` (``bank``
    for a bank hit, else the layer that sorted it now, None if unsorted) and
    ``new_to_bank``.
    """
    now = _now()
    out: list[dict] = []
    seen_keys: set[str] = set()
    for position, q in enumerate(questions):
        text = clean_text(q.text)
        options = [CapturedOption(o.value, clean_text(o.label)) for o in q.options]
        options = [o for o in options if o.label]
        labels = [o.label for o in options]
        key = identity_key(q.seek_question_id, text, q.input_type, labels)
        if key in seen_keys:  # the same question twice on one form: link it once
            continue
        seen_keys.add(key)
        lib = parse_library_id(q.seek_question_id)

        row = db.scalar(select(ScreeningQuestion).where(ScreeningQuestion.identity_key == key))
        new_to_bank = row is None
        if row is None:
            row = ScreeningQuestion(
                identity_key=key,
                library_id=lib[0] if lib else None,
                library_version=lib[1] if lib else None,
                text=text,
                normalised_text=normalise_text(text),
                input_type=q.input_type,
                options=json.dumps(labels) if labels else None,
                kind="unknown",
                status="new",
                times_seen=0,
                first_seen_at=now,
                last_seen_at=now,
            )
            db.add(row)
            sorted_by = None
        else:
            row.last_seen_at = now
            if lib:  # a library question keeps its latest wording and version
                row.library_version = lib[1]
                row.text = text
                row.normalised_text = normalise_text(text)
                row.options = json.dumps(labels) if labels else None
            sorted_by = "bank" if row.kind != "unknown" else None

        if row.kind == "unknown" and row.classified_by != "user":
            sorted_by = _sort_new(row, q.seek_question_id)
        db.flush()

        link = db.get(JobScreeningQuestion, (job_id, row.id))
        option_values = json.dumps([{"value": o.value, "label": o.label} for o in options]) if options else None
        if link is None:
            db.add(JobScreeningQuestion(
                job_id=job_id, question_id=row.id, position=position,
                seek_question_id=q.seek_question_id, field_name=q.field_name,
                option_values=option_values, first_seen_at=now, last_seen_at=now,
            ))
            row.times_seen = (row.times_seen or 0) + 1
        else:
            link.position = position
            link.seek_question_id = q.seek_question_id
            link.field_name = q.field_name
            link.option_values = option_values
            link.last_seen_at = now
        db.flush()

        view = question_view(row)
        view.update(position=position, seek_question_id=q.seek_question_id,
                    sorted_by=sorted_by, new_to_bank=new_to_bank)
        out.append(view)
    db.commit()
    return out


def job_questions(db: Session, job_id: int) -> list[dict]:
    """The job's linked questions in form order (what the overlay/sidebar shows)."""
    links = db.scalars(
        select(JobScreeningQuestion)
        .where(JobScreeningQuestion.job_id == job_id)
        .order_by(JobScreeningQuestion.position)
    ).all()
    out = []
    for link in links:
        view = question_view(link.question)
        view.update(position=link.position, seek_question_id=link.seek_question_id)
        out.append(view)
    return out


def list_questions(db: Session, status: str | None = None) -> list[dict]:
    """The bank, unsorted first, then most seen. ``status='new'`` = the review list."""
    stmt = select(ScreeningQuestion).order_by(
        case((ScreeningQuestion.kind == "unknown", 0), else_=1),
        ScreeningQuestion.times_seen.desc(),
        ScreeningQuestion.id,
    )
    if status:
        stmt = stmt.where(ScreeningQuestion.status == status)
    return [question_view(r) for r in db.scalars(stmt).all()]


def review_question(db: Session, question_id: int, kind: str, strategy: str | None,
                    parameters: dict | None = None) -> dict:
    """Confirm or correct a bank row. Same kind and strategy = a confirmation (the
    layer that sorted it is kept); anything else = a correction (``classified_by =
    'user'``). Either way the row becomes ``confirmed`` and applies to every later
    job. Raises LookupError / CorrectionError. Commits."""
    row = db.get(ScreeningQuestion, question_id)
    if row is None:
        raise LookupError(f"screening question {question_id} not found")
    if kind == "user":
        strategy = "user"
    elif kind == "assisted":
        if strategy not in ASSISTED_STRATEGIES:
            raise CorrectionError(f"an assisted question needs one of {', '.join(ASSISTED_STRATEGIES)}")
    else:
        raise CorrectionError("kind must be 'user' or 'assisted'")

    unchanged = row.kind == kind and row.strategy == strategy
    if parameters is not None:
        row.parameters = json.dumps(parameters)
    elif not unchanged:
        row.parameters = json.dumps({})
    if not unchanged or parameters is not None:
        row.classified_by = "user"
    row.kind = kind
    row.strategy = strategy
    row.status = "confirmed"
    db.commit()
    return question_view(row)
