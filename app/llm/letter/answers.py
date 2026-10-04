"""The user's answers to a paused run's ``ask_user`` questions (plan §5.5, Q11, Q12).

The run is ``waiting_user`` and no worker holds it, so these are called from API
requests. Each question is resolved one of two ways:

    No   the requirement is left out of the letter, the skill is remembered in
         ``gap_decisions`` (never asked again; on the to-work-on list), and this ad
         counts as a sighting.
    Yes  a small-model call turns the typed answer into PROPOSED profile rows
         (experiences / qualifications / skills). Nothing is saved yet: the user
         sees them and confirms with one click (Q11), because a misread row would
         become evidence in every later letter. On confirm the rows are written with
         ``origin = 'ask_user'`` (Q12), the requirement is reset to ``unknown`` so
         ``match_profile`` re-judges it against them on resume, and any remembered
         "no" the new skills cover is cleared.

When no question is left unresolved the run becomes ``answered``; the worker
resumes it from the persisted state (engines.resume_letter).
"""

from __future__ import annotations

import datetime
import logging
from typing import Literal

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import gaps
from app.llm.client import complete_json
from app.llm.letter.runner import context_for, record_step
from app.llm.letter.state import LetterState, UserDecision, UserQuestion
from app.llm.prefilter import normalise_skill
from app.models import Experience, LetterRun, Profile, Qualification, Skill
from app.retention import now_utc

logger = logging.getLogger(__name__)

ORIGIN = "ask_user"
ORIGIN_LABEL = "added while applying to a job"
PARSE_TIER = "small"
MAX_ANSWER_CHARS = 2000


class AnswerError(ValueError):
    """The answer can't be applied; the message is written for the user."""


class StateConflict(AnswerError):
    """The run or question is no longer in a state that takes this answer (it was
    already answered, or the run moved on)."""


# ---------------------------------------------------------------------------
# Proposed rows (also the confirm payload the sidebar may send back edited).
# No defaults on the model-facing schema: Gemini structured output rejects them.
# ---------------------------------------------------------------------------
class ProposedExperience(BaseModel):
    experience_type: Literal["job", "internship", "university_project", "assignment", "personal_project", "volunteer"]
    title: str
    organization: str  # "" when the user named none
    start: str  # "YYYY-MM" or ""
    end: str  # "YYYY-MM", or "" (ongoing or not stated)
    description: str
    skills: list[str]


class ProposedQualification(BaseModel):
    qualification_type: Literal["degree", "diploma", "certificate", "license"]
    title: str
    institution: str
    status: Literal["completed", "in_progress", "expected"]


class ProposedRows(BaseModel):
    experiences: list[ProposedExperience]
    qualifications: list[ProposedQualification]
    skills: list[str]  # skills the user says they have that no experience above covers

    def is_empty(self) -> bool:
        return not (self.experiences or self.qualifications or self.skills)


_PARSE_PROMPT = """\
You turn a job seeker's short answer into rows for their profile. A job ad asked \
for a requirement; the candidate said they DO have experience that covers it and \
described it in their own words. Record only what they wrote.

Rules:
- Use only facts in the answer. Never add a date, organisation, number, tool or \
outcome the answer does not state; leave the field empty ("") instead.
- Do not add the requirement's own skill unless the answer says the candidate used \
or holds it. "I used Tableau at uni" for a Power BI requirement is Tableau, not \
Power BI.
- experiences: something the candidate did (a job, internship, university project, \
assignment, personal project, volunteering). title: a short role or project name. \
organization: the employer, university or client they named, else "". start/end: \
"YYYY-MM" only if the answer gives a month or year (use -01 for a year alone), else \
"". description: one to three plain sentences in the candidate's own words, without \
embellishment. skills: each tool, technology or skill the answer says they used there.
- qualifications: only a degree, diploma, certificate or licence the answer says \
they hold or are completing.
- skills: a skill the answer says they have that is not already listed under one of \
the experiences above. Usually empty.
- If the answer describes nothing concrete, return three empty lists."""


def parse_answer(state: LetterState, question: UserQuestion, text: str, *, run_id: int) -> ProposedRows:
    """Ask the small model for proposed rows. Raises AnswerError if none come back."""
    return parse_text(
        requirement_text=question.requirement_text, skill=question.skill, job_title=state.job.title,
        company=state.job.company, text=text, job_id=state.job.job_id, run_id=run_id,
    )


def parse_text(
    *, requirement_text: str, skill: str, job_title: str, company: str | None, text: str,
    job_id: int | None, run_id: int | None = None, task: str = "ask_user_parse",
) -> ProposedRows:
    """The parse behind a Yes, with or without a run (Quick Apply has none). Raises
    AnswerError if no rows come back."""
    user = (
        f"REQUIREMENT (in the employer's words): {requirement_text}\n"
        f"SKILL IT NAMES: {skill or '(none)'}\n"
        f"JOB: {job_title} at {company or 'unknown company'}\n\n"
        f"THE CANDIDATE'S ANSWER:\n{text}"
    )
    data = complete_json(
        _PARSE_PROMPT, user, schema=ProposedRows, tier=PARSE_TIER, task=task,
        job_id=job_id, run_id=run_id,
    )
    rows = clean_rows(ProposedRows.model_validate(data), answer=text)
    if rows.is_empty():
        raise AnswerError(
            "Couldn't turn that answer into anything to save. Say what you did, where and "
            "roughly how long, or answer No."
        )
    return rows


def _month(value: str) -> str:
    try:
        datetime.date.fromisoformat(f"{value.strip()}-01")
        return value.strip()
    except ValueError:
        return ""


def clean_rows(rows: ProposedRows, *, answer: str = "") -> ProposedRows:
    """Drop what can't be saved (blank titles, bad dates, duplicate skills)."""
    exps = []
    for e in rows.experiences:
        title = " ".join(e.title.split())
        if not title:
            continue
        skills = list(dict.fromkeys(s.strip() for s in e.skills if s.strip()))
        exps.append(e.model_copy(update={
            "title": title, "organization": e.organization.strip(), "start": _month(e.start),
            "end": _month(e.end), "description": e.description.strip(), "skills": skills,
        }))
    if len(exps) == 1 and not exps[0].description and answer.strip():
        exps[0] = exps[0].model_copy(update={"description": answer.strip()})  # their own words
    quals = [
        q.model_copy(update={"title": " ".join(q.title.split()), "institution": q.institution.strip()})
        for q in rows.qualifications if q.title.strip()
    ]
    in_exps = {normalise_skill(s) for e in exps for s in e.skills}
    skills = [s for s in dict.fromkeys(s.strip() for s in rows.skills if s.strip())
              if normalise_skill(s) not in in_exps]
    return ProposedRows(experiences=exps, qualifications=quals, skills=skills)


def _date(value: str) -> datetime.date | None:
    return datetime.date.fromisoformat(f"{value}-01") if value else None


def save_rows(db: Session, profile_id: int, rows: ProposedRows) -> list[str]:
    """Write confirmed rows to the profile, tagged ``origin='ask_user'``. A skill the
    profile already has (same normalised name) is linked, not duplicated. Returns
    pointers to the experiences, qualifications and skills involved. Flushes only."""
    existing = {normalise_skill(s.name): s for s in db.query(Skill).filter(Skill.user_id == profile_id)}
    pointers: list[str] = []

    def skill_for(name: str) -> Skill:
        key = normalise_skill(name)
        if key not in existing:
            sk = Skill(user_id=profile_id, name=name, origin=ORIGIN)
            db.add(sk)
            db.flush()
            existing[key] = sk
        return existing[key]

    for e in rows.experiences:
        exp = Experience(
            user_id=profile_id, experience_type=e.experience_type, title=e.title,
            organization=e.organization or None, start_date=_date(e.start), end_date=_date(e.end),
            description=e.description or None, on_cv=False, origin=ORIGIN,
        )
        db.add(exp)
        db.flush()
        pointers.append(f"experience:{exp.id}")
        for name in e.skills:
            sk = skill_for(name)
            if sk not in exp.skills:
                exp.skills.append(sk)
            pointers.append(f"skill:{sk.id}")
    for q in rows.qualifications:
        qual = Qualification(
            user_id=profile_id, qualification_type=q.qualification_type, title=q.title,
            institution=q.institution or None, status=q.status, origin=ORIGIN,
        )
        db.add(qual)
        db.flush()
        pointers.append(f"qualification:{qual.id}")
    for name in rows.skills:
        pointers.append(f"skill:{skill_for(name).id}")
    db.flush()
    return list(dict.fromkeys(pointers))


# ---------------------------------------------------------------------------
# Applying answers to a waiting run
# ---------------------------------------------------------------------------
def load_waiting(db: Session, run_id: int) -> tuple[LetterRun, LetterState]:
    run = db.get(LetterRun, run_id)
    if run is None:
        raise LookupError(f"letter run {run_id} not found")
    if run.status != "waiting_user":
        raise StateConflict(f"this run is not waiting for answers (status: {run.status})")
    return run, LetterState.model_validate_json(run.state)


def _question(state: LetterState, question_id: str, allowed: tuple[str, ...]) -> UserQuestion:
    q = state.question(question_id)
    if q is None:
        raise LookupError(f"question {question_id} not found")
    if q.status not in allowed:
        raise StateConflict(f"question {question_id} is {q.status}, so it can't take this answer")
    return q


def _save(db: Session, run: LetterRun, state: LetterState, step: str, summary: dict) -> None:
    """Log the answer as a step (no tool call is charged), then mark the run answered
    when nothing is left for the user."""
    if not state.waiting_on_user():
        run.status = "answered"
    record_step(context_for(db, run, state), state, step, summary=summary)


def answer_no(db: Session, run_id: int, question_id: str) -> LetterState:
    run, state = load_waiting(db, run_id)
    q = _question(state, question_id, ("open", "needs_confirm"))
    req = state.requirement(q.requirement_id)
    q.status, q.choice, q.proposal, q.answer = "answered", "no", None, None
    if req is not None:
        req.user_decision = UserDecision(choice="leave_out", answer="the user said no")
    decision = gaps.save_no(
        db, state.profile_id, label=q.skill or q.requirement_text[:80], key=q.skill_key,
        requirement_text=q.requirement_text,
    )
    gaps.record_sighting(
        db, decision, job_id=state.job.job_id, job_title=state.job.title, source="letter_run",
        importance=req.importance if req else None,
    )
    _save(db, run, state, "user_answer", {"question": q.id, "choice": "no", "gap_id": decision.id})
    return state


def answer_yes(db: Session, run_id: int, question_id: str, text: str) -> LetterState:
    """Parse a Yes into proposed rows and wait for the user's confirm. Saves nothing
    to the profile. The parse call is charged to the run."""
    text = (text or "").strip()
    if not text:
        raise AnswerError("Tell us what you did, where and roughly how long.")
    run, state = load_waiting(db, run_id)
    q = _question(state, question_id, ("open", "needs_confirm"))
    rows = parse_answer(state, q, text[:MAX_ANSWER_CHARS], run_id=run.id)
    q.status, q.choice, q.answer, q.proposal = "needs_confirm", "yes", text, rows.model_dump()
    _save(db, run, state, "user_answer", {"question": q.id, "choice": "yes", "proposed": _counts(rows)})
    return state


def confirm(
    db: Session, run_id: int, question_id: str, *, accept: bool, rows: ProposedRows | None = None,
) -> LetterState:
    """The user's one click on a Yes. Accept saves the proposed rows (or the user's
    edited ``rows``); reject reopens the question so they can retype or say No."""
    run, state = load_waiting(db, run_id)
    q = _question(state, question_id, ("needs_confirm",))
    if not accept:
        q.status, q.choice, q.proposal = "open", None, None
        _save(db, run, state, "user_confirm", {"question": q.id, "accepted": False})
        return state

    final = clean_rows(rows if rows is not None else ProposedRows.model_validate(q.proposal), answer=q.answer or "")
    if final.is_empty():
        raise AnswerError("There is nothing left to save. Edit the rows, or answer No.")
    pointers, cleared = save_confirmed(db, state.profile_id, final)

    req = state.requirement(q.requirement_id)
    if req is not None:
        req.user_decision = UserDecision(choice="have_it", answer=q.answer, saved_as=pointers)
        req.status, req.evidence, req.note = "unknown", [], "re-match against the new profile rows"
    q.status, q.saved_as = "answered", pointers
    _save(db, run, state, "user_confirm",
          {"question": q.id, "accepted": True, "saved_as": pointers, "auto_cleared": cleared})
    return state


def save_confirmed(db: Session, profile_id: int, rows: ProposedRows) -> tuple[list[str], list[str]]:
    """Save confirmed rows, mark the profile revised, commit, and clear any remembered
    "no" the new skills cover. Returns (pointers, auto-cleared labels)."""
    pointers = save_rows(db, profile_id, rows)
    profile = db.get(Profile, profile_id)
    if profile is not None:
        profile.profile_revised_at = now_utc()
    db.commit()
    return pointers, gaps.auto_clear(db, profile_id)


# ---------------------------------------------------------------------------
# Run-less answers (Quick Apply questions, plan §10.1, Phase 9b)
# ---------------------------------------------------------------------------
# A wanted skill the profile has no trace of, asked about on the Quick Apply page. No
# run is involved and none is reopened or rerun (user decision 2026-10-04): a No is
# remembered like a run's No; a Yes only offers to add the skill to the profile, through
# the same proposed rows -> one-click confirm. The proposal is not stored server-side:
# the confirm call sends the (possibly edited) rows back.
def propose_skill(
    *, skill: str, requirement_text: str, job_title: str, company: str | None, job_id: int,
    text: str | None = None,
) -> ProposedRows:
    """The rows a Yes would add. With no text: the skill alone, no model call. With the
    user's own description of where they used it: the small model's parse, as for a run."""
    skill = " ".join((skill or "").split())
    if not skill:
        raise AnswerError("Which skill is this about?")
    text = (text or "").strip()
    if not text:
        return ProposedRows(experiences=[], qualifications=[], skills=[skill])
    return parse_text(
        requirement_text=requirement_text, skill=skill, job_title=job_title, company=company,
        text=text[:MAX_ANSWER_CHARS], job_id=job_id, task="quick_apply_gap_parse",
    )


def confirm_runless(db: Session, profile_id: int, rows: ProposedRows) -> tuple[list[str], list[str]]:
    """Save a confirmed Quick Apply Yes (``origin='ask_user'``). Commits."""
    final = clean_rows(rows)
    if final.is_empty():
        raise AnswerError("There is nothing left to save. Edit the rows, or answer No.")
    return save_confirmed(db, profile_id, final)


def answer_no_runless(
    db: Session, profile_id: int, *, skill: str, requirement_text: str | None, job_id: int,
    job_title: str | None, importance: str | None = None,
) -> gaps.GapDecision:
    """Remember a Quick Apply No and count this ad (sighting source ``quick_apply``).
    Commits. Returns the gap decision."""
    skill = " ".join((skill or "").split())
    key = gaps.skill_key(skill, requirement_text)
    if not key:
        raise AnswerError("Which skill is this about?")
    decision = gaps.save_no(db, profile_id, label=skill or key, key=key, requirement_text=requirement_text)
    gaps.record_sighting(db, decision, job_id=job_id, job_title=job_title, source="quick_apply",
                         importance=importance)
    db.commit()
    return decision


def _counts(rows: ProposedRows) -> dict:
    return {"experiences": len(rows.experiences), "qualifications": len(rows.qualifications),
            "skills": len(rows.skills)}
