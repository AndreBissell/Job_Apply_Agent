"""Seed richer test data onto profile id=1 so matching produces meaningful scores.

Idempotent: skills upsert on UNIQUE (user_id, name); qualifications/experiences are
matched by (title, organization) so re-running won't duplicate. Creates the profile
if it doesn't exist yet (so this works on a fresh DB too).

    python scripts/seed_profile.py
"""

from __future__ import annotations

import datetime
import sys

sys.path.insert(0, ".")

from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    Experience,
    Profile,
    Qualification,
    Skill,
)

PROFILE_ID = 1

HARD_SKILLS = [
    "Python", "SQL", "FastAPI", "SQLAlchemy", "REST APIs", "Git", "JavaScript",
    "HTML/CSS", "Data Analysis", "Excel", "Power BI",
]
SOFT_SKILLS = [
    "Communication", "Problem Solving", "Teamwork", "Attention to Detail",
    "Time Management",
]

QUALIFICATION = dict(
    qualification_type="degree",
    title="Bachelor of Information Technology",
    institution="Queensland University of Technology",
    field_of_study="Information Technology",
    status="completed",
    end_date=datetime.date(2024, 11, 30),
)

EXPERIENCES = [
    dict(
        experience_type="internship",
        title="Junior Developer Intern",
        organization="Tech Startup Brisbane",
        start_date=datetime.date(2024, 1, 15),
        end_date=datetime.date(2024, 6, 30),
        description=(
            "Built internal tools using Python and FastAPI. Wrote SQL queries for "
            "reporting dashboards. Collaborated with senior devs via Git."
        ),
        skills=["Python", "FastAPI", "SQL", "Git", "REST APIs"],
    ),
    dict(
        experience_type="part_time",
        title="Data Entry & Reporting Officer",
        organization="Local Government",
        start_date=datetime.date(2022, 6, 1),
        end_date=datetime.date(2023, 12, 31),
        description=(
            "Maintained Excel spreadsheets and produced weekly Power BI reports. "
            "Improved data accuracy through validation scripts."
        ),
        skills=["Excel", "Power BI", "Data Analysis", "SQL", "Attention to Detail"],
    ),
    dict(
        experience_type="project",
        title="Final Year Capstone Project",
        organization="QUT",
        start_date=datetime.date(2024, 2, 1),
        end_date=datetime.date(2024, 10, 31),
        description=(
            "Built a full-stack web application for event management using Python, "
            "FastAPI, SQLAlchemy, and a React frontend. Delivered in a team of 4."
        ),
        skills=["Python", "FastAPI", "SQLAlchemy", "SQL", "JavaScript", "HTML/CSS",
                "Teamwork"],
    ),
]


def _ensure_profile(db) -> Profile:
    profile = db.get(Profile, PROFILE_ID)
    if profile is None:
        profile = Profile(
            name="Test Candidate",
            email="test-candidate@example.com",
            password_hash="not-a-real-hash",
        )
        db.add(profile)
        db.flush()
        print(f"Created profile id={profile.id}")
    else:
        print(f"Reusing profile id={profile.id} ({profile.email})")
    return profile


def _ensure_skills(db, user_id: int) -> dict[str, Skill]:
    """Return a name -> Skill map, creating any missing skills (idempotent)."""
    by_name: dict[str, Skill] = {}
    for name, category in (
        [(n, "hard") for n in HARD_SKILLS] + [(n, "soft") for n in SOFT_SKILLS]
    ):
        skill = db.scalar(
            select(Skill).where(Skill.user_id == user_id, Skill.name == name)
        )
        if skill is None:
            skill = Skill(user_id=user_id, name=name, category=category)
            db.add(skill)
            db.flush()
        by_name[name] = skill
    return by_name


def _ensure_qualification(db, user_id: int) -> None:
    existing = db.scalar(
        select(Qualification).where(
            Qualification.user_id == user_id,
            Qualification.title == QUALIFICATION["title"],
        )
    )
    if existing is None:
        db.add(Qualification(user_id=user_id, **QUALIFICATION))


def _ensure_experiences(db, user_id: int, skills: dict[str, Skill]) -> None:
    for spec in EXPERIENCES:
        skill_names = spec["skills"]
        fields = {k: v for k, v in spec.items() if k != "skills"}
        exp = db.scalar(
            select(Experience).where(
                Experience.user_id == user_id,
                Experience.title == fields["title"],
                Experience.organization == fields["organization"],
            )
        )
        if exp is None:
            exp = Experience(user_id=user_id, **fields)
            db.add(exp)
            db.flush()
        # Link skills (idempotent: set membership avoids duplicate junction rows).
        linked = {s.name for s in exp.skills}
        for name in skill_names:
            if name not in linked:
                exp.skills.append(skills[name])


def main() -> int:
    with SessionLocal() as db:
        profile = _ensure_profile(db)
        skills = _ensure_skills(db, profile.id)
        _ensure_qualification(db, profile.id)
        _ensure_experiences(db, profile.id, skills)
        db.commit()

        n_skills = len(
            db.scalars(select(Skill).where(Skill.user_id == profile.id)).all()
        )
        n_quals = len(
            db.scalars(select(Qualification).where(Qualification.user_id == profile.id)).all()
        )
        exps = db.scalars(select(Experience).where(Experience.user_id == profile.id)).all()
        n_links = sum(len(e.skills) for e in exps)

        print(
            f"Profile {profile.id}: {n_skills} skills, {n_quals} qualifications, "
            f"{len(exps)} experiences, {n_links} experience_skills links."
        )
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
