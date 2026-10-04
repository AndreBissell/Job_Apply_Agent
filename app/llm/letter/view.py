"""What the sidebar shows about cover-letter runs: read-only, derived from the DB.

Two views, both built from ``letter_runs`` (the persisted ``LetterState``), so the
schema needs nothing extra:

``latest_runs``  one small dict per match for the job cards: is a run writing, waiting
                 on the user, or failed?
``letter_info``  everything about the letter on one card: what is still wrong with it
                 (open issues), what the letter doesn't claim and why, the ad's
                 eligibility notes and application instructions.

The run's findings describe the draft it handed back (``final_draft_version``). Once the
letter's ``generated_content`` is anything else (a later one-shot, a regenerate) they are
not shown: stale advice about text the user no longer has would mislead.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.llm.letter import guardrails
from app.llm.letter.runner import REFUSED
from app.llm.letter.state import LetterState, Requirement
from app.models import CoverLetter, LetterRun, LetterRunStep, Match

# A run in these states says something about the card; a finished one speaks only through
# the letter itself (open issues, via letter_info).
CARD_STATUSES = ("running", "waiting_user", "answered", "failed", "budget_stopped", "cancelled", "done")


def latest_runs(db: Session, match_ids: list[int]) -> dict[int, dict]:
    """The newest run per match, as ``{run_id, status, open_questions}``. Only a
    ``waiting_user`` run loads its state (to count questions), so a page of cards costs
    two cheap queries."""
    if not match_ids:
        return {}
    newest = (
        select(func.max(LetterRun.id))
        .where(LetterRun.match_id.in_(match_ids))
        .group_by(LetterRun.match_id)
    )
    out: dict[int, dict] = {}
    for run in db.execute(select(LetterRun.id, LetterRun.match_id, LetterRun.status).where(LetterRun.id.in_(newest))):
        out[run.match_id] = {"run_id": run.id, "status": run.status, "open_questions": 0}
    for match_id, info in out.items():
        if info["status"] == "waiting_user":
            state = db.scalar(select(LetterRun.state).where(LetterRun.id == info["run_id"]))
            if state:
                info["open_questions"] = sum(
                    1 for q in LetterState.model_validate_json(state).user_questions if q.status != "answered"
                )
    return out


def _why_not_claimed(r: Requirement) -> str:
    d = r.user_decision
    if d is not None and d.choice == "leave_out":
        return "You said you don't have this (remembered from an earlier ad)" if d.remembered else "You said you don't have this"
    return "Nothing in your profile backs this"


def not_claimed(state: LetterState) -> list[dict]:
    """Requirements the letter leaves out (a gap, or you said no), with the reason.
    Eligibility items are not here: they are the card's notes, never letter content."""
    return [
        {"id": r.id, "text": r.text, "importance": r.importance, "reason": _why_not_claimed(r)}
        for r in guardrails.do_not_claim(state)
    ]


def _last_error(db: Session, run_id: int) -> str | None:
    """The newest real step error of a run (refusals are not failures)."""
    for err in db.scalars(
        select(LetterRunStep.error)
        .where(LetterRunStep.run_id == run_id, LetterRunStep.error.isnot(None))
        .order_by(LetterRunStep.seq.desc())
    ):
        if not err.startswith(REFUSED):
            return err
    return None


def letter_info(db: Session, job_id: int, profile_id: int) -> dict | None:
    """The letter's story for one job card, or None when the job has no match.

    ``run`` is the pipeline run that wrote the letter now in ``cover_letters`` (None when
    it came from the one-shot, or the text has since moved on). ``waiting`` is a run
    paused on the user, ``failure`` the newest run that ended with nothing to show.
    """
    match = db.scalar(select(Match).where(Match.user_id == profile_id, Match.job_id == job_id))
    if match is None:
        return None
    letter = db.scalar(select(CoverLetter.generated_content).where(CoverLetter.match_id == match.id))
    runs = db.scalars(select(LetterRun).where(LetterRun.match_id == match.id).order_by(LetterRun.id.desc())).all()

    info: dict = {"job_id": job_id, "run": None, "waiting": None, "failure": None}

    waiting = next((r for r in runs if r.status in ("waiting_user", "answered")), None)
    if waiting is not None and runs and runs[0].id == waiting.id:  # only if it is the newest
        state = LetterState.model_validate_json(waiting.state)
        info["waiting"] = {
            "run_id": waiting.id, "status": waiting.status,
            "open_questions": sum(1 for q in state.user_questions if q.status != "answered"),
        }

    for run in runs:
        if run.status not in ("done", "budget_stopped", "failed") or run.final_draft_version is None or not run.state:
            continue
        state = LetterState.model_validate_json(run.state)
        draft = next((d for d in state.drafts if d.version == run.final_draft_version), None)
        if draft is None or letter is None or draft.text != letter:
            continue
        issues = guardrails.open_issues(draft)
        info["run"] = {
            "run_id": run.id, "engine": run.engine, "status": run.status, "draft_version": draft.version,
            "drafts_used": len(state.drafts), "clean": guardrails.is_clean(draft), "open_issues": issues,
            "warnings": [f"{n}: {w}" for n, c in draft.checks.items() for w in c.warnings],
            "cost_usd": float(run.cost_usd or 0),
            "not_claimed": not_claimed(state),
            "eligibility_notes": state.eligibility_notes(),
            "application_instructions": state.job.application_instructions,
        }
        break

    newest = runs[0] if runs else None
    if newest is not None and info["run"] is None and info["waiting"] is None and (
        newest.status == "failed" or (newest.status == "budget_stopped" and newest.final_draft_version is None)
    ):
        info["failure"] = {"run_id": newest.id, "status": newest.status, "error": _last_error(db, newest.id)}
    return info
