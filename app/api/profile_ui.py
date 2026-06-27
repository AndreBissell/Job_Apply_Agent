"""Profile UI router: single-page profile editor + CRUD endpoints.

Endpoints
---------
GET  /profile-ui          → serves profile_ui.html
GET  /profile-ui/data     → full profile snapshot (profile + quals + exps + skills)
PUT  /profile-ui/data     → full upsert (replace-strategy for children)
DELETE /profile-ui/data   → delete the profile (used by load_test_profile --reset)
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Iterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from app.db import SessionLocal
from app.models import Experience, Profile, Qualification, Skill

router = APIRouter()

_HTML_PATH = Path(__file__).parent.parent / "static" / "profile_ui.html"


# ---------------------------------------------------------------------------
# DB dependency
# ---------------------------------------------------------------------------
def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------
class ProfileIn(BaseModel):
    name: str
    email: str
    phone: str | None = None
    location: str | None = None
    summary: str | None = None
    target_role: str | None = None
    target_location: str | None = None


class QualIn(BaseModel):
    qualification_type: str = "degree"
    title: str
    institution: str | None = None
    field_of_study: str | None = None
    grade: str | None = None
    start_date: str | None = None   # "YYYY-MM"
    end_date: str | None = None     # "YYYY-MM"
    status: str | None = "completed"


class ExperienceIn(BaseModel):
    experience_type: str = "job"
    title: str
    organization: str | None = None
    start_date: str | None = None   # "YYYY-MM"
    end_date: str | None = None     # "YYYY-MM"
    is_current: bool = False
    description: str | None = None
    skills: list[str] = []


class ProfileData(BaseModel):
    profile: ProfileIn
    qualifications: list[QualIn] = []
    experiences: list[ExperienceIn] = []
    skills: list[str] = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _parse_month(s: str | None) -> datetime.date | None:
    """Convert "YYYY-MM" → first-of-month date. Returns None for empty/invalid."""
    if not s:
        return None
    try:
        parts = s.split("-")
        return datetime.date(int(parts[0]), int(parts[1]), 1)
    except (ValueError, IndexError, TypeError):
        return None


def _fmt_month(d: datetime.date | None) -> str | None:
    return d.strftime("%Y-%m") if d else None


def _get_profile(db: Session) -> Profile | None:
    return db.scalars(
        select(Profile)
        .options(
            selectinload(Profile.qualifications),
            selectinload(Profile.experiences).selectinload(Experience.skills),
            selectinload(Profile.skills),
        )
        .limit(1)
    ).first()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.get("/profile-ui", response_class=HTMLResponse)
def serve_ui() -> HTMLResponse:
    return HTMLResponse(_HTML_PATH.read_text(encoding="utf-8"))


@router.get("/profile-ui/data")
def get_profile_data(db: Session = Depends(get_db)) -> dict:
    profile = _get_profile(db)
    if profile is None:
        raise HTTPException(status_code=404, detail="No profile found")

    return {
        "profile": {
            "id": profile.id,
            "name": profile.name,
            "email": profile.email,
            "phone": profile.phone,
            "location": profile.location,
            "summary": profile.summary,
            "target_role": profile.target_role,
            "target_location": profile.target_location,
        },
        "qualifications": [
            {
                "id": q.id,
                "qualification_type": q.qualification_type,
                "title": q.title,
                "institution": q.institution,
                "field_of_study": q.field_of_study,
                "grade": q.grade,
                "start_date": _fmt_month(q.start_date),
                "end_date": _fmt_month(q.end_date),
                "status": q.status,
            }
            for q in profile.qualifications
        ],
        "experiences": [
            {
                "id": e.id,
                "experience_type": e.experience_type,
                "title": e.title,
                "organization": e.organization,
                "start_date": _fmt_month(e.start_date),
                "end_date": _fmt_month(e.end_date),
                "is_current": e.end_date is None,
                "description": e.description,
                "skills": [s.name for s in e.skills],
            }
            for e in profile.experiences
        ],
        "skills": [s.name for s in profile.skills],
    }


@router.put("/profile-ui/data")
def put_profile_data(body: ProfileData, db: Session = Depends(get_db)) -> dict:
    # 1. Upsert profile
    profile = db.scalars(select(Profile).limit(1)).first()
    if profile is None:
        profile = Profile(
            name=body.profile.name,
            email=body.profile.email,
            password_hash="not-set",
        )
        db.add(profile)
        db.flush()

    profile.name = body.profile.name
    profile.email = body.profile.email
    profile.phone = body.profile.phone or None
    profile.location = body.profile.location or None
    profile.summary = body.profile.summary or None
    profile.target_role = body.profile.target_role or None
    profile.target_location = body.profile.target_location or None
    db.flush()

    # 2. Delete experiences first (experience_skills cascade on experience_id)
    db.execute(delete(Experience).where(Experience.user_id == profile.id))
    db.execute(delete(Qualification).where(Qualification.user_id == profile.id))
    db.flush()

    # 3. Compute complete skill name set (master list + all experience skills)
    all_skill_names: set[str] = set()
    for name in body.skills:
        name = name.strip()
        if name:
            all_skill_names.add(name)
    for exp_in in body.experiences:
        for sname in exp_in.skills:
            sname = sname.strip()
            if sname:
                all_skill_names.add(sname)

    # 4. Remove skills no longer referenced (experience_skills already gone from step 2)
    for skill in db.scalars(select(Skill).where(Skill.user_id == profile.id)).all():
        if skill.name not in all_skill_names:
            db.delete(skill)
    db.flush()

    # 5. Upsert remaining/new skills; build name → Skill map
    skill_map: dict[str, Skill] = {}
    for name in all_skill_names:
        skill = db.scalar(
            select(Skill).where(Skill.user_id == profile.id, Skill.name == name)
        )
        if skill is None:
            skill = Skill(user_id=profile.id, name=name)
            db.add(skill)
            db.flush()
        skill_map[name] = skill

    # 6. Re-insert qualifications (skip blank titles)
    for q in body.qualifications:
        if not q.title.strip():
            continue
        db.add(Qualification(
            user_id=profile.id,
            qualification_type=q.qualification_type or "degree",
            title=q.title.strip(),
            institution=q.institution or None,
            field_of_study=q.field_of_study or None,
            grade=q.grade or None,
            start_date=_parse_month(q.start_date),
            end_date=_parse_month(q.end_date),
            status=q.status or "completed",
        ))

    # 7. Re-insert experiences + link experience_skills
    for exp_in in body.experiences:
        if not exp_in.title.strip():
            continue
        exp = Experience(
            user_id=profile.id,
            experience_type=exp_in.experience_type or "job",
            title=exp_in.title.strip(),
            organization=exp_in.organization or None,
            start_date=_parse_month(exp_in.start_date),
            end_date=None if exp_in.is_current else _parse_month(exp_in.end_date),
            description=exp_in.description or None,
        )
        db.add(exp)
        db.flush()
        for sname in exp_in.skills:
            sname = sname.strip()
            if sname and sname in skill_map:
                exp.skills.append(skill_map[sname])

    db.commit()
    return {"ok": True, "profile_id": profile.id}


@router.delete("/profile-ui/data")
def delete_profile_data(db: Session = Depends(get_db)) -> dict:
    """Delete the profile and all its children (used by --reset flag in loader script)."""
    profile = db.scalars(select(Profile).limit(1)).first()
    if profile is not None:
        db.delete(profile)
        db.commit()
    return {"ok": True}
