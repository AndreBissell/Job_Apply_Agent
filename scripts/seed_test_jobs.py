"""Seed 5 known test jobs for validating keyword matching + scoring.

These rows use ``source="test"`` (source_job_id test-001..test-005) so they are
trivially distinguishable from real Seek data and easy to clean up. Each carries
its raw_description plus a manually-curated set of job_skills and extracted
fields, so matching can run immediately WITHOUT spending any LLM extraction
quota (this is acceptable for deterministic test data — see the validation plan).

Idempotent: rows upsert on the UNIQUE (source, source_job_id) constraint and
job_skills are cleared + re-inserted, so re-running never duplicates.

    python scripts/seed_test_jobs.py
"""

from __future__ import annotations

import datetime
import json
import sys

sys.path.insert(0, ".")

from sqlalchemy import delete, select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import JobListing, JobSkill  # noqa: E402

SOURCE = "test"

# Each test job: the listing fields, the curated hard/soft skills, and the
# extracted fields matching what app/llm/extract.py would have produced.
TEST_JOBS = [
    dict(
        source_job_id="test-001",
        title="Junior Python Developer",
        company="Brisbane Digital Agency",
        location="Brisbane QLD 4000",
        work_type="Full time",
        seniority="junior",
        summary=(
            "A junior backend role building and maintaining Python services and "
            "REST APIs. Recent graduates are welcome to apply."
        ),
        key_responsibilities=[
            "Develop and maintain Python-based backend services",
            "Build and document REST APIs using FastAPI or Flask",
            "Write and optimise SQL queries against PostgreSQL databases",
            "Work with Git for version control in a team environment",
            "Contribute to code reviews and team stand-ups",
        ],
        hard_skills=["Python", "FastAPI", "SQL", "REST APIs", "Git", "PostgreSQL"],
        soft_skills=["Communication", "Teamwork"],
        qualifications=[
            dict(title="Degree", field="Computer Science", required=False),
        ],
        raw_description="""\
We are looking for a Junior Python Developer to join our growing team.

About the role:
- Develop and maintain Python-based backend services
- Build and document REST APIs using FastAPI or Flask
- Write and optimise SQL queries against PostgreSQL databases
- Work with Git for version control in a team environment
- Contribute to code reviews and team stand-ups

About you:
- Degree in Computer Science, IT, or related field (or equivalent experience)
- Strong Python skills - you have built something real with it
- Familiarity with REST API design
- SQL confidence - you can write a JOIN without googling it
- Git basics: branching, pull requests, merging
- Good communication and willingness to learn

Nice to have:
- Experience with FastAPI or SQLAlchemy
- Any exposure to cloud platforms (AWS, GCP)

We welcome applications from recent graduates.""",
    ),
    dict(
        source_job_id="test-002",
        title="Graduate Data Analyst",
        company="Queensland State Government",
        location="Brisbane QLD",
        work_type="Full time",
        seniority="graduate",
        summary=(
            "A graduate analytics role producing reports and Power BI dashboards "
            "from SQL data sources. No prior professional experience required."
        ),
        key_responsibilities=[
            "Analyse datasets and produce weekly and monthly reports",
            "Build and maintain Power BI dashboards for internal stakeholders",
            "Write SQL queries to extract and transform data from a data warehouse",
            "Maintain data quality and validation processes in Excel and SQL",
            "Present findings to non-technical stakeholders",
        ],
        hard_skills=["SQL", "Power BI", "Excel", "Data Analysis"],
        soft_skills=["Communication", "Attention to Detail"],
        qualifications=[
            dict(title="Degree", field="Information Technology", required=True),
        ],
        raw_description="""\
Join our data and insights team as a Graduate Data Analyst.

Responsibilities:
- Analyse datasets and produce weekly and monthly reports
- Build and maintain Power BI dashboards for internal stakeholders
- Write SQL queries to extract and transform data from our data warehouse
- Maintain data quality and validation processes in Excel and SQL
- Present findings to non-technical stakeholders

Requirements:
- Degree in Information Technology, Statistics, Business, or related field
- Experience with Power BI or similar BI tool
- SQL skills - able to write complex queries
- Excel proficiency
- Strong attention to detail and data accuracy
- Clear communication skills

This is a graduate position - no prior professional experience required.""",
    ),
    dict(
        source_job_id="test-003",
        title="Full Stack Developer",
        company="SaaS Startup",
        location="Sydney NSW (Remote OK)",
        work_type="Full time",
        seniority="mid",
        summary=(
            "A mid-level full-stack role owning features end to end across React "
            "frontends and Python/FastAPI backends on AWS. Requires 2+ years' "
            "professional experience."
        ),
        key_responsibilities=[
            "Build React frontends and Python/FastAPI backends",
            "Deploy and maintain services on AWS (EC2, Lambda, RDS)",
            "Write Dockerised applications and manage CI/CD pipelines",
            "Write comprehensive unit and integration tests",
            "Mentor junior developers",
        ],
        hard_skills=[
            "Python", "FastAPI", "React", "TypeScript", "AWS", "Docker",
            "PostgreSQL", "SQL", "Git",
        ],
        soft_skills=["Teamwork"],
        qualifications=[],
        raw_description="""\
We need an experienced Full Stack Developer to own features end to end.

You will:
- Build React frontends and Python/FastAPI backends
- Deploy and maintain services on AWS (EC2, Lambda, RDS)
- Write Dockerised applications and manage CI/CD pipelines
- Write comprehensive unit and integration tests (pytest, Jest)
- Mentor junior developers

You must have:
- 2+ years professional experience in a full-stack role
- Strong React.js and TypeScript proficiency
- Python backend experience (FastAPI preferred)
- AWS deployment experience
- Docker and CI/CD (GitHub Actions or similar)
- PostgreSQL or similar relational database

Nice to have: Redis, Kubernetes, Terraform""",
    ),
    dict(
        source_job_id="test-004",
        title="Senior Software Engineer - Platform",
        company="Fintech Company",
        location="Melbourne VIC",
        work_type="Full time",
        seniority="senior",
        summary=(
            "A senior engineering role leading a platform team, architecting "
            "large-scale distributed systems and mentoring engineers. Requires "
            "5+ years' experience."
        ),
        key_responsibilities=[
            "Architect and own large-scale distributed systems",
            "Lead technical design reviews and set engineering standards",
            "Mentor a team of 4-6 engineers",
            "Drive platform reliability, performance, and scalability",
            "Collaborate with product and engineering leadership",
        ],
        hard_skills=["Python", "AWS", "Kafka", "Redis", "Docker"],
        soft_skills=["Communication", "Problem Solving"],
        qualifications=[],
        raw_description="""\
We are hiring a Senior Software Engineer to lead our platform team.

Responsibilities:
- Architect and own large-scale distributed systems
- Lead technical design reviews and set engineering standards
- Mentor a team of 4-6 engineers
- Drive platform reliability, performance, and scalability
- Collaborate with product and engineering leadership

Requirements:
- 5+ years of software engineering experience
- Deep expertise in Python or Go
- Hands-on experience with Kafka, Redis, or similar messaging systems
- Cloud-native architecture experience (AWS or GCP)
- Strong track record of leading projects from design to production
- Experience managing or mentoring engineers""",
    ),
    dict(
        source_job_id="test-005",
        title="Marketing Coordinator",
        company="Retail Chain",
        location="Brisbane QLD",
        work_type="Full time",
        seniority="junior",
        summary=(
            "A marketing coordination role supporting national campaigns across "
            "social media, email, and events. Requires a marketing background."
        ),
        key_responsibilities=[
            "Coordinate social media content calendars",
            "Liaise with designers and copywriters to deliver assets",
            "Track campaign performance in Google Analytics and Meta Ads Manager",
            "Manage email marketing campaigns in Mailchimp",
            "Support events coordination and influencer outreach",
        ],
        hard_skills=["Google Analytics", "Mailchimp", "Social Media Management"],
        soft_skills=["Communication", "Attention to Detail", "Teamwork"],
        qualifications=[
            dict(title="Degree", field="Marketing", required=True),
        ],
        raw_description="""\
We are looking for a Marketing Coordinator to support our national campaigns.

Responsibilities:
- Coordinate social media content calendars across Instagram, Facebook, TikTok
- Liaise with graphic designers and copywriters to deliver assets on time
- Track campaign performance in Google Analytics and Meta Ads Manager
- Manage email marketing campaigns in Mailchimp
- Support events coordination and influencer outreach

Requirements:
- Degree in Marketing, Communications, or related field
- 1-2 years marketing experience preferred
- Experience with social media management tools (Hootsuite, Buffer)
- Familiarity with Google Analytics
- Strong copywriting and communication skills
- Creative eye and attention to detail""",
    ),
]


def _upsert_job(db, spec: dict) -> JobListing:
    """Create or update one test job listing + its extracted fields (idempotent)."""
    job = db.scalar(
        select(JobListing).where(
            JobListing.source == SOURCE,
            JobListing.source_job_id == spec["source_job_id"],
        )
    )
    if job is None:
        job = JobListing(source=SOURCE, source_job_id=spec["source_job_id"])
        db.add(job)

    job.url = f"https://example.test/job/{spec['source_job_id']}"
    job.title = spec["title"]
    job.company = spec["company"]
    job.location = spec["location"]
    job.work_type = spec["work_type"]
    job.raw_description = spec["raw_description"]
    job.seniority = spec["seniority"]
    job.summary = spec["summary"]
    job.key_responsibilities = json.dumps(spec["key_responsibilities"])
    job.qualification_requirements = json.dumps(spec["qualifications"])
    job.experience_requirements = json.dumps([])
    job.extracted_at = datetime.datetime.now(datetime.timezone.utc)
    db.flush()  # ensure job.id for the job_skills FKs

    # Clear + re-insert job_skills so re-running stays idempotent.
    db.execute(delete(JobSkill).where(JobSkill.job_id == job.id))
    for name in spec["hard_skills"]:
        db.add(JobSkill(job_id=job.id, name=name, skill_type="hard"))
    for name in spec["soft_skills"]:
        db.add(JobSkill(job_id=job.id, name=name, skill_type="soft"))
    return job


def main() -> int:
    with SessionLocal() as db:
        jobs = [_upsert_job(db, spec) for spec in TEST_JOBS]
        db.commit()

        print(f"Seeded {len(jobs)} test jobs:")
        for spec, job in zip(TEST_JOBS, jobs):
            print(f"  {spec['source_job_id']}: {spec['title']} (job_id={job.id})")
        print("All job_skills rows inserted. Ready for matching.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
