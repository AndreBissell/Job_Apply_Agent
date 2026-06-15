"""End-to-end smoke test for the database layer.

Proves the schema works against whatever ``DATABASE_URL`` points at:

  1. Inserts a full profile graph (profile, qualification, experience, two
     skills, the experience<->skill links, a CV, a job listing with job skills,
     a match, and a cover letter).
  2. Runs the key evidence query: "all experiences that demonstrate skill X",
     joining through ``experience_skills``.
  3. Deletes the profile and asserts every user-owned child is gone (ON DELETE
     CASCADE), and that the shared job listing survives.

Run it after ``alembic upgrade head``:

    python scripts/smoke_test.py

It uses a transaction it rolls nothing back from — it commits — so run it
against a throwaway dev ``app.db`` (the default), not real data.
"""

from __future__ import annotations

import datetime
import sys
from decimal import Decimal

from sqlalchemy import func, select

# Allow running as `python scripts/smoke_test.py` from the repo root.
sys.path.insert(0, ".")

from app.db import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    CoverLetter,
    Experience,
    JobListing,
    JobSkill,
    Match,
    Profile,
    Qualification,
    Skill,
    UserCv,
)

TARGET_SKILL = "SQL"


def seed(session) -> tuple[int, int]:
    """Create the full graph; return (profile_id, job_id)."""
    profile = Profile(
        name="Ada Lovelace",
        email="ada@example.com",
        password_hash="not-a-real-hash",
    )

    profile.qualifications.append(
        Qualification(
            qualification_type="degree",
            title="Bachelor of Software Engineering",
            institution="University of Example",
            field_of_study="Software Engineering",
            grade="Distinction",
            status="completed",
        )
    )

    internship = Experience(
        experience_type="internship",
        title="Software Engineering Intern",
        organization="Acme Corp",
        start_date=datetime.date(2024, 1, 1),
        end_date=datetime.date(2024, 6, 30),
        description="Built internal tooling backed by a relational database.",
    )
    profile.experiences.append(internship)

    sql_skill = Skill(name=TARGET_SKILL, category="language")
    python_skill = Skill(name="Python", category="language")
    profile.skills.extend([sql_skill, python_skill])

    # Link both skills to the internship via the experience_skills junction.
    internship.skills.extend([sql_skill, python_skill])

    profile.cvs.append(
        UserCv(label="Software Dev CV", content="<<cv text>>", is_default=True)
    )

    session.add(profile)
    session.flush()  # assign profile.id and child ids

    # Job side — global, shared across users.
    job = JobListing(
        source="seek",
        source_job_id="12345",
        url="https://www.seek.com.au/job/12345",
        title="Junior Software Engineer",
        company="Globex",
        raw_description="We need someone comfortable with SQL and Python.",
    )
    job.job_skills.append(JobSkill(name="SQL", skill_type="hard"))
    job.job_skills.append(JobSkill(name="Communication", skill_type="soft"))
    session.add(job)
    session.flush()

    # The bridge: a match + its cover letter.
    match = Match(
        user_id=profile.id,
        job_id=job.id,
        score=Decimal("87"),
        reasoning="Strong overlap on SQL and Python.",
        gaps="No professional cloud experience mentioned.",
        status="new",
        cv_used_id=profile.cvs[0].id,
    )
    match.cover_letter = CoverLetter(
        generated_content="Dear Globex, ...",
        status="draft",
    )
    session.add(match)

    session.commit()
    return profile.id, job.id


def evidence_query(session, skill_name: str) -> list[Experience]:
    """All experiences that demonstrate ``skill_name`` (join via junction)."""
    stmt = (
        select(Experience)
        .join(Experience.skills)
        .where(Skill.name == skill_name)
        .order_by(Experience.title)
    )
    return list(session.scalars(stmt))


def count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def main() -> int:
    ok = True

    with SessionLocal() as session:
        profile_id, job_id = seed(session)
        print(f"Seeded profile id={profile_id}, job listing id={job_id}\n")

        # --- Evidence query --------------------------------------------------
        print(f'Evidence query - experiences demonstrating "{TARGET_SKILL}":')
        results = evidence_query(session, TARGET_SKILL)
        for exp in results:
            print(f"  - {exp.title} @ {exp.organization} ({exp.experience_type})")
        if not results:
            print("  (none - unexpected!)")
            ok = False
        print()

        # --- Cascade delete --------------------------------------------------
        profile = session.get(Profile, profile_id)
        session.delete(profile)
        session.commit()

        remaining = {
            "profiles": count(session, Profile),
            "qualifications": count(session, Qualification),
            "experiences": count(session, Experience),
            "skills": count(session, Skill),
            "user_cvs": count(session, UserCv),
            "matches": count(session, Match),
            "cover_letters": count(session, CoverLetter),
        }
        # experience_skills has no model; count its rows directly.
        from app.models import experience_skills  # noqa: E402

        remaining["experience_skills"] = (
            session.scalar(
                select(func.count()).select_from(experience_skills)
            )
            or 0
        )

        print("After deleting the profile, user-owned rows remaining (expect 0):")
        for table, n in remaining.items():
            flag = "OK" if n == 0 else "FAIL"
            if n != 0:
                ok = False
            print(f"  {table:<18} {n}  [{flag}]")

        # The shared job listing and its skills must survive.
        jobs_left = count(session, JobListing)
        job_skills_left = count(session, JobSkill)
        job_ok = jobs_left == 1 and job_skills_left == 2
        ok = ok and job_ok
        print(
            f"\nShared job listing survived: job_listings={jobs_left} "
            f"job_skills={job_skills_left}  "
            f"[{'OK' if job_ok else 'FAIL'}]"
        )

        # Clean up the job so re-runs start fresh.
        session.delete(session.get(JobListing, job_id))
        session.commit()

    print("\n" + ("PASS - smoke test succeeded." if ok else "FAIL - see above."))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
