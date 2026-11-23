"""The side panel's availability checks: what to look at next, and what it found.

GET /availability/next picks one stored listing for the panel to re-visit (in the user's own
browser; the backend never contacts Seek) and says in which mode (fast or background), and
POST /availability/{job_id} records the verdict. The choosing, fast mode and the two-strike
rule are in app/availability.py. The panel only paces the pages
(extension/sidebar_availability.js); the caps (fast run, ``DAILY_CAP``) are enforced here.
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
    # The mode GET /availability/next gave when the page was opened.
    mode: Literal["fast", "background"] = "background"


@router.get("/availability/next")
def next_check(profile_id: int = 1, exclude: str = "", db: Session = Depends(get_db)) -> dict:
    """The next listing to re-check and the mode to check it in, or ``job: null``.

    ``mode`` is ``fast`` (pace 5 s: getting the top jobs seen live again after time away) or
    ``background`` (pace 15 s, the trickle), null with no job. Asking never changes anything:
    a fast run starts when its first verdict is recorded. ``exclude`` is a comma-separated
    list of internal job ids the panel already looked at this session (they count towards the
    fast run's pages). ``remaining_today`` is the rolling 24-hour allowance left; at 0 (or
    with the checks switched off) no job is offered in either mode.
    """
    enabled = availability_checks_enabled(db, profile_id)
    remaining = availability.remaining_today(db)
    skip = [int(x) for x in exclude.split(",") if x.strip().isdigit()]
    found = availability.choose_next(db, profile_id, skip) if enabled and remaining > 0 else None
    return {
        "enabled": enabled,
        "remaining_today": remaining,
        "mode": found[2] if found else None,
        "job": (
            {
                "job_id": found[0].id,
                "source_job_id": found[0].source_job_id,
                "url": found[0].url,
                "group": found[1],
            }
            if found else None
        ),
    }


@router.post("/availability/{job_id}")
def record_check(job_id: int, body: Verdict, profile_id: int = 1, db: Session = Depends(get_db)) -> dict:
    """Record what a re-check found: ``live``, ``missing`` (no description) or ``unknown``,
    and the ``mode`` the page was opened in.

    The second ``missing`` in a row flags the job expired; ``live`` un-expires it. An applied
    job is never flagged (the response carries ``note: "applied"``). The first ``fast``
    verdict with no fast run this hour starts one.
    """
    try:
        return availability.record_verdict(db, job_id, body.verdict, mode=body.mode, profile_id=profile_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Job not found")
