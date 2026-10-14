"""The side panel's background availability checks: what to look at next, and what it found.

GET /availability/next picks one stored listing for the panel to re-visit (in the user's own
browser; the backend never contacts Seek), and POST /availability/{job_id} records the
verdict. The choosing and the two-strike rule are in app/availability.py. Paced and capped
by the panel (extension/sidebar_availability.js) and by ``DAILY_CAP`` here.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import availability
from app.api.deps import get_db
from app.preferences import availability_checks_enabled

router = APIRouter()


class Verdict(BaseModel):
    verdict: Literal["live", "missing", "unknown"]


@router.get("/availability/next")
def next_check(profile_id: int = 1, exclude: str = "", db: Session = Depends(get_db)) -> dict:
    """The next listing to re-check, or ``job: null``.

    ``exclude`` is a comma-separated list of internal job ids the panel already looked at
    this session. ``remaining_today`` is the rolling 24-hour allowance left; at 0 (or with
    the checks switched off) no job is offered.
    """
    enabled = availability_checks_enabled(db, profile_id)
    remaining = availability.remaining_today(db)
    skip = [int(x) for x in exclude.split(",") if x.strip().isdigit()]
    found = availability.next_candidate(db, profile_id, skip) if enabled and remaining > 0 else None
    return {
        "enabled": enabled,
        "remaining_today": remaining,
        "job": (
            {
                "job_id": found[0].id,
                "source_job_id": found[0].source_job_id,
                "url": found[0].url,
                "tier": found[1],
            }
            if found else None
        ),
    }


@router.post("/availability/{job_id}")
def record_check(job_id: int, body: Verdict, db: Session = Depends(get_db)) -> dict:
    """Record what a re-check found: ``live``, ``missing`` (no description) or ``unknown``.

    The second ``missing`` in a row flags the job expired; ``live`` un-expires it. An applied
    job is never flagged (the response carries ``note: "applied"``).
    """
    try:
        return availability.record_verdict(db, job_id, body.verdict)
    except LookupError:
        raise HTTPException(status_code=404, detail="Job not found")
