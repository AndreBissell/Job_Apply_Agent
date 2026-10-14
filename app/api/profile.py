"""Profile and preferences endpoints: GET/PUT /profile/{id} and /profile/{id}/preferences.

``PreferencesUpdate.scan_max_pages`` carries the Seek access policy's standing cap
(``le=25``, CLAUDE.md); tests/test_scan_policy.py checks it.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models import Experience, Profile, Qualification, Skill, UserCv
from app.preferences import LIMIT_BOUNDS, get_preferences, set_preferences

router = APIRouter()


class ProfileUpdate(BaseModel):
    name: str | None = None
    email: str | None = None


class PreferencesUpdate(BaseModel):
    auto_cover_letter_min_score: int | None = Field(default=None, ge=0, le=100)
    # The full cover-letter pipeline (plan §6): master switch, the score bar for it
    # (the one-shot still covers auto_cover_letter_min_score up to this), and which
    # engine runs it.
    letter_loop_enabled: bool | None = None
    letter_loop_min_score: int | None = Field(default=None, ge=0, le=100)
    letter_engine: Literal["agent", "workflow"] | None = None
    # The pipeline's side outputs (plan §6): each off = that model call is never made.
    resume_advice_enabled: bool | None = None
    learning_suggestions_enabled: bool | None = None
    screening_answers_enabled: bool | None = None
    # Help with the Quick Apply questions (plan §10.1); off = every question is yours to answer.
    screening_question_help_enabled: bool | None = None
    # Background availability re-checks and the "seen live recently" gate (app/availability.py).
    availability_checks_enabled: bool | None = None
    # Per-run limits of the pipeline (plan 7); bounds in preferences.LIMIT_BOUNDS. A run
    # that reaches one stops with its best draft and the open issues flagged.
    letter_max_drafts: int | None = Field(default=None, ge=LIMIT_BOUNDS["letter_max_drafts"][0],
                                          le=LIMIT_BOUNDS["letter_max_drafts"][1])
    letter_max_tool_calls: int | None = Field(default=None, ge=LIMIT_BOUNDS["letter_max_tool_calls"][0],
                                              le=LIMIT_BOUNDS["letter_max_tool_calls"][1])
    llm_run_budget_usd: float | None = Field(default=None, ge=LIMIT_BOUNDS["llm_run_budget_usd"][0],
                                             le=LIMIT_BOUNDS["llm_run_budget_usd"][1])
    llm_search_suggestions: bool | None = None
    # Job pages opened per Scan Page. The upper bound is the Seek access policy's
    # standing cap (CLAUDE.md) — raise it deliberately, don't remove it.
    scan_max_pages: int | None = Field(default=None, ge=1, le=25)
    # Retention tunables — see app/retention.py. Bounds keep a typo from
    # turning the sweep into "delete everything" (window >= 7 days) or the
    # screenshot TTL into "delete evidence immediately" (>= 1 day).
    retention_window_days: int | None = Field(default=None, ge=7, le=730)
    retention_floor_matches: int | None = Field(default=None, ge=0, le=5000)
    screenshot_ttl_days: int | None = Field(default=None, ge=1, le=365)
    stale_profile_weight: float | None = Field(default=None, gt=0, le=1)
    # Centrelink mutual obligation (app/obligation.py): applications per period and the
    # date a period starts. Stored as "YYYY-MM-DD" (update_preferences dumps in JSON mode).
    obligation_target: int | None = Field(default=None, ge=1, le=100)
    obligation_cycle_start: date | None = None


@router.get("/profile/{profile_id}")
def get_profile(profile_id: int, db: Session = Depends(get_db)) -> dict:
    """Profile with nested qualifications, experiences, skills, and CVs."""
    profile = db.get(Profile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")

    qualifications = db.scalars(
        select(Qualification).where(Qualification.user_id == profile_id)
    ).all()
    experiences = db.scalars(
        select(Experience).where(Experience.user_id == profile_id)
    ).all()
    skills = db.scalars(select(Skill).where(Skill.user_id == profile_id)).all()
    cvs = db.scalars(select(UserCv).where(UserCv.user_id == profile_id)).all()

    return {
        "id": profile.id,
        "name": profile.name,
        "email": profile.email,
        "visa_status": profile.visa_status,
        "qualifications": [
            {"id": q.id, "title": q.title, "qualification_type": q.qualification_type,
             "institution": q.institution, "status": q.status}
            for q in qualifications
        ],
        "experiences": [
            {"id": e.id, "title": e.title, "organization": e.organization,
             "experience_type": e.experience_type, "on_cv": e.on_cv}
            for e in experiences
        ],
        "skills": [
            {"id": s.id, "name": s.name, "category": s.category} for s in skills
        ],
        "cvs": [
            {"id": c.id, "label": c.label, "is_default": c.is_default} for c in cvs
        ],
    }


@router.put("/profile/{profile_id}")
def update_profile(
    profile_id: int, body: ProfileUpdate, db: Session = Depends(get_db)
) -> dict:
    """Partial update of a profile's ``name`` / ``email``."""
    profile = db.get(Profile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")

    if body.name is not None:
        profile.name = body.name
    if body.email is not None:
        profile.email = body.email
    db.commit()

    return {"id": profile.id, "name": profile.name, "email": profile.email}


@router.get("/profile/{profile_id}/preferences")
def read_preferences(profile_id: int, db: Session = Depends(get_db)) -> dict:
    if db.get(Profile, profile_id) is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    return get_preferences(db, profile_id)


@router.put("/profile/{profile_id}/preferences")
def update_preferences(
    profile_id: int, body: PreferencesUpdate, db: Session = Depends(get_db)
) -> dict:
    """Partial update: only the keys present in the body change."""
    if db.get(Profile, profile_id) is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    # mode="json": a date must reach json.dumps as its ISO string.
    return set_preferences(db, profile_id, body.model_dump(mode="json", exclude_none=True))
