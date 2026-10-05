"""Centrelink mutual obligation: progress per monthly period, and interviews.

Endpoints
---------
GET   /obligation                 progress this period + every period with applications
PATCH /jobs/{job_id}/interview    record (or undo) an interview for an applied job

One payload serves both the Overview tab (``current``, ``waiting_count``) and the Applied
tab (``periods``). "Applied" means ``matches.applied_at`` is set, counted on its local
date (app/obligation.py), hidden matches included: an application is Centrelink evidence
whatever the Jobs tab shows. Cost per job is ALL spend labelled with that job in
``llm_usage`` (scoring, the letter, Quick Apply help); it is an estimate, and a job with
no usage rows (applied before logging began) reads ``null`` and counts as ``uncosted``.

An interview only stamps ``matches.interview_at``; ``status`` stays ``applied`` so the
evidence export, the Applied CSV and retention are untouched.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import obligation, retention
from app.api.letters import waiting_runs
from app.api.profile_ui import get_db
from app.models import JobListing, LlmUsage, Match
from app.preferences import get_preferences, obligation_settings

router = APIRouter()


class InterviewIn(BaseModel):
    interview: bool  # true = record one, false = undo


def _job_costs(db: Session, job_ids: list[int]) -> dict[int, float]:
    if not job_ids:
        return {}
    rows = db.execute(
        select(LlmUsage.job_id, func.sum(LlmUsage.cost_usd))
        .where(LlmUsage.job_id.in_(job_ids))
        .group_by(LlmUsage.job_id)
    )
    return {job_id: float(total or 0) for job_id, total in rows}


@router.get("/obligation")
def get_obligation(profile_id: int = 1, db: Session = Depends(get_db)) -> dict:
    settings = obligation_settings(db, profile_id)
    bucket = obligation.bucket_for(settings["cycle_start"])
    today = obligation.local_today()
    current = bucket(today)
    ttl = timedelta(days=retention.screenshot_ttl_days(get_preferences(db, profile_id)))

    rows = db.execute(
        select(Match, JobListing)
        .join(JobListing, Match.job_id == JobListing.id)
        .where(Match.user_id == profile_id, Match.applied_at.is_not(None))
        .order_by(Match.applied_at.desc(), Match.id.desc())
    ).all()
    costs = _job_costs(db, [job.id for _, job in rows])

    periods: dict[tuple, list[dict]] = {current: []}
    for match, job in rows:
        cost = costs.get(job.id)
        periods.setdefault(bucket(obligation.to_local_date(match.applied_at)), []).append({
            "job_id": job.id,
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "url": job.url,
            "score": float(match.score) if match.score is not None else None,
            "applied_at": retention.utc(match.applied_at).isoformat(),
            "interview_at": retention.utc(match.interview_at).isoformat() if match.interview_at else None,
            "cost_usd": round(cost, 4) if cost is not None else None,
            **retention.screenshot_fields(match, ttl),
        })

    out = []
    for (start, end), jobs in sorted(periods.items(), key=lambda kv: kv[0][0], reverse=True):
        out.append({
            "start": start.isoformat(),
            "end": end.isoformat(),
            "is_current": (start, end) == current,
            "applied": len(jobs),
            "cost_usd": round(sum(j["cost_usd"] for j in jobs if j["cost_usd"] is not None), 4),
            "uncosted": sum(1 for j in jobs if j["cost_usd"] is None),
            "jobs": jobs,
        })

    start, end = current
    return {
        "target": settings["target"],
        "cycle_start": settings["cycle_start"].isoformat() if settings["cycle_start"] else None,
        "current": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "applied": len(periods[current]),
            "days_left": (end - today).days + 1,  # today included
        },
        # Letters paused on a question for the user (the same list as /letter-runs/waiting).
        "waiting_count": len(waiting_runs(profile_id, db)["runs"]),
        "periods": out,
    }


@router.patch("/jobs/{job_id}/interview")
def set_interview(job_id: int, body: InterviewIn, profile_id: int = 1, db: Session = Depends(get_db)) -> dict:
    match = db.scalar(select(Match).where(Match.user_id == profile_id, Match.job_id == job_id))
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    if match.applied_at is None:
        raise HTTPException(status_code=409, detail="Mark the job applied before recording an interview")
    if not body.interview:
        match.interview_at = None
    elif match.interview_at is None:  # idempotent: a second "yes" keeps the first date
        match.interview_at = datetime.now(timezone.utc)
    db.commit()
    return {
        "job_id": job_id,
        "interview_at": retention.utc(match.interview_at).isoformat() if match.interview_at else None,
    }
