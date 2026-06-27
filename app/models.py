"""SQLAlchemy 2.0 models for the Job Application Assistant.

This is the implementation of ``docs/database-schema.md`` — that document is the
source of truth. Eleven tables across two halves that meet at ``matches``:

    profile side : profiles, saved_searches, qualifications, experiences,
                   skills, experience_skills, user_cvs
    job side     : job_listings, job_skills
    bridge       : matches, cover_letters

Portability notes (SQLite local dev <-> Postgres hosting):
  * ``BIG_INT_PK`` renders ``BIGINT`` on Postgres but ``INTEGER`` on SQLite, so
    surrogate keys autoincrement correctly on both (SQLite only aliases the
    rowid for an ``INTEGER PRIMARY KEY``).
  * ``DateTime(timezone=True)`` + ``func.now()`` maps to ``TIMESTAMPTZ`` /
    ``CURRENT_TIMESTAMP`` respectively — no dialect-specific SQL is written.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

# Surrogate primary key: BIGINT on Postgres, INTEGER (rowid alias) on SQLite.
BIG_INT_PK = BigInteger().with_variant(Integer, "sqlite")

# Foreign-key column type — must match the referenced PK's variant behaviour.
BIG_INT_FK = BigInteger().with_variant(Integer, "sqlite")


# ---------------------------------------------------------------------------
# Profile side
# ---------------------------------------------------------------------------
class Profile(Base):
    """The user account. Passwords are always stored hashed, never plain."""

    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(BIG_INT_PK, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    email_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    dob: Mapped[datetime.date | None] = mapped_column(Date)
    visa_status: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    target_role: Mapped[str | None] = mapped_column(Text)
    target_location: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), 
        nullable=False, 
        server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Children — cascade mirrors the DB-level ON DELETE CASCADE so the ORM and
    # the database agree when a profile is removed.
    saved_searches: Mapped[list["SavedSearch"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan", passive_deletes=True
    )
    qualifications: Mapped[list["Qualification"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan", passive_deletes=True
    )
    experiences: Mapped[list["Experience"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan", passive_deletes=True
    )
    skills: Mapped[list["Skill"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan", passive_deletes=True
    )
    cvs: Mapped[list["UserCv"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan", passive_deletes=True
    )
    matches: Mapped[list["Match"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan", passive_deletes=True
    )


class SavedSearch(Base):
    """Per-user scraper criteria; one user has many searches."""

    __tablename__ = "saved_searches"

    id: Mapped[int] = mapped_column(BIG_INT_PK, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BIG_INT_FK,
        ForeignKey("profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    label: Mapped[str | None] = mapped_column(Text)
    keywords: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    work_type: Mapped[str | None] = mapped_column(Text)
    salary_min: Mapped[Decimal | None] = mapped_column(Numeric)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), 
        nullable=False, 
        server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    profile: Mapped["Profile"] = relationship(back_populates="saved_searches")

    __table_args__ = (Index("idx_saved_searches_user", "user_id"),)


class Qualification(Base):
    """Formal credentials: degrees, diplomas, certificates, licenses."""

    __tablename__ = "qualifications"

    id: Mapped[int] = mapped_column(BIG_INT_PK, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BIG_INT_FK,
        ForeignKey("profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    qualification_type: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    institution: Mapped[str | None] = mapped_column(Text)
    field_of_study: Mapped[str | None] = mapped_column(Text)
    grade: Mapped[str | None] = mapped_column(Text)  # free text; scales vary
    start_date: Mapped[datetime.date | None] = mapped_column(Date)
    end_date: Mapped[datetime.date | None] = mapped_column(Date)
    expiry_date: Mapped[datetime.date | None] = mapped_column(Date)
    status: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    profile: Mapped["Profile"] = relationship(back_populates="qualifications")

    __table_args__ = (Index("idx_qualifications_user", "user_id"),)


class Experience(Base):
    """Everything the user has done — paid or not."""

    __tablename__ = "experiences"

    id: Mapped[int] = mapped_column(BIG_INT_PK, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BIG_INT_FK,
        ForeignKey("profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    experience_type: Mapped[str] = mapped_column(Text, nullable=False)
    on_cv: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    organization: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[datetime.date | None] = mapped_column(Date)
    end_date: Mapped[datetime.date | None] = mapped_column(Date)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    profile: Mapped["Profile"] = relationship(back_populates="experiences")
    # Many-to-many to skills via the experience_skills junction.
    skills: Mapped[list["Skill"]] = relationship(
        secondary="experience_skills",
        back_populates="experiences",
        passive_deletes=True,
    )

    __table_args__ = (Index("idx_experiences_user", "user_id"),)


class Skill(Base):
    """One row per distinct skill per user."""

    __tablename__ = "skills"

    id: Mapped[int] = mapped_column(BIG_INT_PK, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BIG_INT_FK,
        ForeignKey("profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    profile: Mapped["Profile"] = relationship(back_populates="skills")
    experiences: Mapped[list["Experience"]] = relationship(
        secondary="experience_skills",
        back_populates="skills",
        passive_deletes=True,
    )

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_skills_user_name"),
        Index("idx_skills_user", "user_id"),
    )


# Pure junction: composite PK of the two FKs, no extra columns. Relevance of a
# skill to a job is judged by the LLM at generation time, never stored here.
experience_skills = Table(
    "experience_skills",
    Base.metadata,
    Column(
        "experience_id",
        BIG_INT_FK,
        ForeignKey("experiences.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "skill_id",
        BIG_INT_FK,
        ForeignKey("skills.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Index("idx_experience_skills_skill", "skill_id"),  # "evidence for skill X"
)


class UserCv(Base):
    """Master CV(s) at the user level — reused across applications."""

    __tablename__ = "user_cvs"

    id: Mapped[int] = mapped_column(BIG_INT_PK, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BIG_INT_FK,
        ForeignKey("profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    label: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str | None] = mapped_column(Text)
    file_path: Mapped[str | None] = mapped_column(Text)
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    profile: Mapped["Profile"] = relationship(back_populates="cvs")

    __table_args__ = (Index("idx_user_cvs_user", "user_id"),)


# ---------------------------------------------------------------------------
# Job side
# ---------------------------------------------------------------------------
class JobListing(Base):
    """The global pool of scraped jobs — stored once, shared across users."""

    __tablename__ = "job_listings"

    id: Mapped[int] = mapped_column(BIG_INT_PK, primary_key=True)
    source: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="seek"
    )
    source_job_id: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    company: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    # Seek's own categorisation, served free on each card (distinct from
    # job_skills, which are LLM-extracted from raw_description later).
    classification: Mapped[str | None] = mapped_column(Text)
    subclassification: Mapped[str | None] = mapped_column(Text)
    work_type: Mapped[str | None] = mapped_column(Text)
    salary: Mapped[str | None] = mapped_column(Text)
    close_date: Mapped[datetime.date | None] = mapped_column(Date)
    start_date: Mapped[datetime.date | None] = mapped_column(Date)
    qualification_requirements: Mapped[str | None] = mapped_column(Text)
    experience_requirements: Mapped[str | None] = mapped_column(Text)
    raw_description: Mapped[str | None] = mapped_column(Text)
    # LLM-extracted fields (populated by app/llm/extract.py). seniority is a single
    # token; key_responsibilities is a JSON array of short phrases; summary is prose.
    # extracted_at marks a successful extraction (NULL = not yet / needs (re)extracting).
    seniority: Mapped[str | None] = mapped_column(Text)
    key_responsibilities: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    extracted_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    date_scraped: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    job_skills: Mapped[list["JobSkill"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", passive_deletes=True
    )
    matches: Mapped[list["Match"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        UniqueConstraint(
            "source", "source_job_id", name="uq_job_listings_source_source_job_id"
        ),
        Index("idx_job_listings_scraped", "date_scraped"),
        Index("idx_job_listings_classification", "classification"),  # categorical pre-filter
    )


class JobSkill(Base):
    """Skills extracted from a listing; not FK'd to the user skills table."""

    __tablename__ = "job_skills"

    id: Mapped[int] = mapped_column(BIG_INT_PK, primary_key=True)
    job_id: Mapped[int] = mapped_column(
        BIG_INT_FK,
        ForeignKey("job_listings.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    skill_type: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="hard"
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    job: Mapped["JobListing"] = relationship(back_populates="job_skills")

    __table_args__ = (
        Index("idx_job_skills_job", "job_id"),
        Index("idx_job_skills_name", "name"),  # keyword matching
    )


# ---------------------------------------------------------------------------
# Bridge: matches + cover_letters
# ---------------------------------------------------------------------------
class Match(Base):
    """Per-user relevance linking a profile to a job. ``score`` is the truth."""

    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(BIG_INT_PK, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BIG_INT_FK,
        ForeignKey("profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    job_id: Mapped[int] = mapped_column(
        BIG_INT_FK,
        ForeignKey("job_listings.id", ondelete="CASCADE"),
        nullable=False,
    )
    score: Mapped[Decimal | None] = mapped_column(Numeric)  # 0-100, source of truth
    reasoning: Mapped[str | None] = mapped_column(Text)
    gaps: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="new"
    )
    cv_used_id: Mapped[int | None] = mapped_column(
        BIG_INT_FK,
        ForeignKey("user_cvs.id", ondelete="SET NULL"),
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    profile: Mapped["Profile"] = relationship(back_populates="matches")
    job: Mapped["JobListing"] = relationship(back_populates="matches")
    cv_used: Mapped["UserCv | None"] = relationship()
    cover_letter: Mapped["CoverLetter | None"] = relationship(
        back_populates="match", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        UniqueConstraint("user_id", "job_id", name="uq_matches_user_job"),
        Index("idx_matches_user_score", "user_id", score.desc()),  # ranked dashboard
        Index("idx_matches_status", "status"),
    )


class CoverLetter(Base):
    """The generated document for a match — one row per match."""

    __tablename__ = "cover_letters"

    id: Mapped[int] = mapped_column(BIG_INT_PK, primary_key=True)
    match_id: Mapped[int] = mapped_column(
        BIG_INT_FK,
        ForeignKey("matches.id", ondelete="CASCADE"),
        nullable=False,
    )
    generated_content: Mapped[str | None] = mapped_column(Text)
    edited_content: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="draft"
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    match: Mapped["Match"] = relationship(back_populates="cover_letter")

    __table_args__ = (UniqueConstraint("match_id", name="uq_cover_letters_match"),)
