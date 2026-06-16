# Job Application Assistant — Database Schema

## Purpose

This document defines the complete relational schema for the Job Application
Assistant: the user's profile (qualifications, experience, skills), scraped job
data, and the matching/generation layer that bridges them. It captures the design
decisions made during planning so the schema can be implemented directly.

---

## Conventions

- **Primary keys:** every table has a surrogate `id`. Junction tables use a
  composite primary key of their two foreign keys instead.
- **Foreign keys:** named `<entity>_id` (e.g. `user_id`, `job_id`). `ON DELETE
  CASCADE` is used where a child row is meaningless without its parent (a user's
  qualifications, a job's skills), so deleting a parent cleans up dependents.
- **Timestamps:** `created_at` / `updated_at` on tables that are edited over time.
- **Naming:** `snake_case`, plural table names.
- **Dialect:** DDL below is written for **PostgreSQL** (the eventual multi-user /
  hosted target). For **local single-user development on SQLite**, substitute:
  - `BIGINT GENERATED ALWAYS AS IDENTITY` -> `INTEGER PRIMARY KEY AUTOINCREMENT`
  - `TIMESTAMPTZ` -> `TEXT` (ISO-8601 strings)
  - `BOOLEAN` -> `INTEGER` (0/1)
  - `NUMERIC` -> `REAL`
  - `now()` -> `CURRENT_TIMESTAMP`

---

## Entity Overview

```
profiles (the user / account)
  |-< saved_searches        (scraper criteria; one user -> many searches)
  |-< qualifications        (degrees, diplomas, certs, licenses)
  |-< experiences           (jobs, internships, uni projects, assignments...)
  |     |-< experience_skills >-|   (many-to-many junction)
  |-< skills -------------------|
  |-< user_cvs              (master CV(s), reused across applications)
  |-< matches               (per-user relevance for a job)
        |-1 cover_letters   (generated doc for that match, if above threshold)

job_listings (global pool, scraped once, shared across users)
  |-< job_skills            (extracted required skills, hard/soft)
  |-< matches               (a listing can match many users)
```

Two halves meet at `matches`: the **profile side** (everything hanging off
`profiles`) and the **job side** (`job_listings` + `job_skills`). A job is stored
**once** globally; `matches` is what makes it personal, so two users matching the
same job produce one job row and two match rows.

---

## Tables

### profiles

The user account. Auth fields are included now because multi-user hosting is on
the roadmap; passwords are **always** stored hashed (bcrypt/argon2), never plain.

```sql
CREATE TABLE profiles (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name            TEXT        NOT NULL,
    email           TEXT        NOT NULL UNIQUE,   -- used for login
    password_hash   TEXT        NOT NULL,
    email_verified  BOOLEAN     NOT NULL DEFAULT FALSE,
    dob             DATE,
    visa_status     TEXT,                          -- 'citizen', 'PR', 'visa (subclass X)', ...
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

### saved_searches

Per-user scraper criteria. Modelled as one-to-many so a user can run several
distinct searches (e.g. "Junior Dev - Brisbane" and "Data Analyst - Remote"). The
daily scraper iterates over the active searches for each user.

> Added to support the scraper (Feature 2) and job-preferences (Feature 1) parts
> of the plan, which hadn't been schema'd during the table-by-table design.

```sql
CREATE TABLE saved_searches (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     BIGINT      NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    label       TEXT,                          -- human name for the search
    keywords    TEXT,                          -- e.g. "software engineer graduate"
    location    TEXT,
    work_type   TEXT,                          -- 'full_time','part_time','casual','contract'
    salary_min  NUMERIC,
    is_active   BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

### qualifications

Formal credentials - one row each. A single unified table covers degrees,
diplomas, certificates and licenses via `qualification_type`. `expiry_date`
supports certs/licenses that lapse, which degrees don't have.

```sql
CREATE TABLE qualifications (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id             BIGINT      NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    qualification_type  TEXT        NOT NULL,   -- 'degree','diploma','certificate','license'
    title               TEXT        NOT NULL,   -- "Bachelor of Software Engineering"
    institution         TEXT,                   -- university OR issuing body
    field_of_study      TEXT,                   -- "Software Engineering"
    grade               TEXT,                   -- free text: scales vary (WAM 5.04, GPA 6.5, Distinction)
    start_date          DATE,
    end_date            DATE,                   -- completion (nullable if in progress)
    expiry_date         DATE,                   -- certs/licenses only (nullable)
    status              TEXT,                   -- 'completed','in_progress','expected'
    notes               TEXT,                   -- coursework, honours, thesis topic
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

`grade` is deliberately **text**, not numeric, because grading scales differ
(WAM, GPA, percentage, classification) and aren't meaningfully comparable as a
single number.

---

### experiences

Everything the user has *done* - paid or not. One table, distinguished by
`experience_type`, rather than separate "formal/informal" tables (same shape, and
a single table keeps "find all experiences demonstrating skill X" a one-table
query).

- `on_cv` - does this appear on the formal CV? An assignment can be
  `on_cv = FALSE` yet still feed the cover-letter generator as supporting material.
- `description` - the user's plain-language summary; the LLM expands/refines it per
  job at generation time.

```sql
CREATE TABLE experiences (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id          BIGINT      NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    experience_type  TEXT        NOT NULL,   -- 'job','internship','university_project',
                                             -- 'assignment','personal_project','volunteer'
    on_cv            BOOLEAN     NOT NULL DEFAULT TRUE,
    title            TEXT        NOT NULL,
    organization     TEXT,                   -- company / university; null for personal projects
    start_date       DATE,
    end_date         DATE,                   -- nullable if ongoing
    description      TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

### skills

One row per **distinct** skill per user. Kept separate from experiences (rather
than embedding an `experience_id`) so a reused skill like "SQL" exists exactly
once and isn't duplicated across every experience that uses it.

```sql
CREATE TABLE skills (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     BIGINT      NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    name        TEXT        NOT NULL,        -- "C#", "React", "Stakeholder communication"
    category    TEXT,                        -- 'language','framework','tool','soft_skill'
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, name)                   -- no duplicate skills per user
);
```

---

### experience_skills

Junction table for the many-to-many between experiences and skills (one
experience shows many skills; one skill appears across many experiences). This is
also the lookup that answers *"what evidence does the user have for skill X?"* -
query by `skill_id`, get every linked experience.

Note there is **no `strength` / relevance column**: how strongly an experience
demonstrates a skill depends on the *job being applied to*, so relevance is
computed by the LLM at generation time, not stored.

```sql
CREATE TABLE experience_skills (
    experience_id  BIGINT NOT NULL REFERENCES experiences(id) ON DELETE CASCADE,
    skill_id       BIGINT NOT NULL REFERENCES skills(id)      ON DELETE CASCADE,
    PRIMARY KEY (experience_id, skill_id)
);
```

---

### user_cvs

Master CV(s) stored at the **user level**, not per job - a CV barely changes
between applications, so per-job copies would be pure waste. Supporting more than
one row lets a user keep variants (e.g. a "dev" CV and a "data" CV). Either
`content` (structured/generated text) or `file_path` (an uploaded PDF/docx) is
populated.

```sql
CREATE TABLE user_cvs (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     BIGINT      NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    label       TEXT,                        -- "Software Dev CV"
    content     TEXT,                        -- structured/generated text (nullable)
    file_path   TEXT,                        -- uploaded file reference (nullable)
    is_default  BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

### job_listings

The global pool of scraped jobs - stored **once**, shared across all users.
`source` + `source_job_id` future-proofs multi-site scraping and powers
deduplication ("have I already scraped this listing?"). `raw_description` holds
the full text (the source of truth the LLM writes from); the extracted
`*_requirements` fields are convenience text for matching.

`classification` / `subclassification` are Seek's **own** coarse categorisation
(e.g. "Information & Communication Technology" -> "Engineering - Software"),
served free on every search-result card. They are distinct from `job_skills`:
the classification is a cheap categorical label (useful as a pre-filter and as a
UI badge, no LLM needed), whereas `job_skills` are fine-grained skills extracted
from `raw_description` later. Both are nullable - a card may omit them.

```sql
CREATE TABLE job_listings (
    id                          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source                      TEXT        NOT NULL DEFAULT 'seek',
    source_job_id               TEXT        NOT NULL,   -- the site's own listing ID
    url                         TEXT        NOT NULL,   -- link the user clicks to apply
    title                       TEXT        NOT NULL,
    company                     TEXT,
    location                    TEXT,
    classification              TEXT,                   -- Seek's coarse category, e.g. 'Information & Communication Technology'
    subclassification           TEXT,                   -- Seek's sub-category, e.g. 'Engineering - Software'
    work_type                   TEXT,                   -- 'full_time','part_time','casual','contract'
    salary                      TEXT,                   -- free text; often a range or absent
    close_date                  DATE,
    start_date                  DATE,                   -- often n/a
    qualification_requirements  TEXT,                   -- extracted, LLM-consumed
    experience_requirements     TEXT,                   -- extracted, LLM-consumed
    raw_description             TEXT,                   -- full scraped text (source of truth)
    date_scraped                TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source, source_job_id)                      -- dedup key
);
```

---

### job_skills

Skills extracted from a listing. `skill_type` separates **hard** skills (SQL, C# -
precise, used for the keyword pre-filter) from **soft** skills (communication,
problem-solving - too common to filter on, but useful context for the LLM).

These are **not** foreign-keyed to the user `skills` table: job skills are
extracted independently and matched to user skills by name/fuzzy comparison at
match time.

```sql
CREATE TABLE job_skills (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id      BIGINT      NOT NULL REFERENCES job_listings(id) ON DELETE CASCADE,
    name        TEXT        NOT NULL,        -- "SQL", "Problem solving"
    skill_type  TEXT        NOT NULL DEFAULT 'hard',   -- 'hard' or 'soft'
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

### matches

The per-user relevance layer linking a profile to a job. `score` (0-100) is the
**single source of truth**; tiers (strong / medium / reach) are *derived at
display time* so you can switch between top-N and threshold strategies - or let
the user tune them - without recomputing. `reasoning` and `gaps` are the LLM's
explanation and flagged shortfalls. `status` tracks the application lifecycle
(including the "not interested" dismissal).

```sql
CREATE TABLE matches (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     BIGINT      NOT NULL REFERENCES profiles(id)     ON DELETE CASCADE,
    job_id      BIGINT      NOT NULL REFERENCES job_listings(id) ON DELETE CASCADE,
    score       NUMERIC,                     -- 0-100, source of truth
    reasoning   TEXT,                        -- LLM: why it matched
    gaps        TEXT,                        -- LLM: requirements not clearly met
    status      TEXT        NOT NULL DEFAULT 'new',
                -- 'new','shortlisted','applied','not_interested','rejected'
    cv_used_id  BIGINT      REFERENCES user_cvs(id) ON DELETE SET NULL,  -- which CV went out (optional)
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, job_id)                 -- one match row per user-job pair
);
```

On a re-scrape, an existing `(user_id, job_id)` row is **updated**, not
duplicated.

---

### cover_letters

The generated document for a match. One row per match (`UNIQUE match_id`), holding
both the LLM `generated_content` and the user's `edited_content`, with `status`
tracking the draft -> edited -> final lifecycle. A row is created **only for
above-threshold matches**, so junk jobs cost no storage and no API calls.

```sql
CREATE TABLE cover_letters (
    id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    match_id           BIGINT      NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    generated_content  TEXT,                 -- LLM draft
    edited_content     TEXT,                 -- user edits (nullable until edited)
    status             TEXT        NOT NULL DEFAULT 'draft',   -- 'draft','edited','final'
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (match_id)
);
```

---

## Indexes

Unique constraints above already create implicit indexes (`profiles.email`,
`job_listings(source, source_job_id)`, `matches(user_id, job_id)`,
`skills(user_id, name)`, `cover_letters.match_id`). Additional indexes for the
common access patterns:

```sql
CREATE INDEX idx_saved_searches_user    ON saved_searches(user_id);
CREATE INDEX idx_qualifications_user    ON qualifications(user_id);
CREATE INDEX idx_experiences_user       ON experiences(user_id);
CREATE INDEX idx_skills_user            ON skills(user_id);
CREATE INDEX idx_experience_skills_skill ON experience_skills(skill_id);   -- "evidence for skill X"
CREATE INDEX idx_user_cvs_user          ON user_cvs(user_id);
CREATE INDEX idx_job_listings_scraped   ON job_listings(date_scraped);     -- "today's listings"
CREATE INDEX idx_job_listings_classification ON job_listings(classification);  -- categorical pre-filter
CREATE INDEX idx_job_skills_job         ON job_skills(job_id);
CREATE INDEX idx_job_skills_name        ON job_skills(name);               -- keyword matching
CREATE INDEX idx_matches_user_score     ON matches(user_id, score DESC);   -- ranked dashboard
CREATE INDEX idx_matches_status         ON matches(status);
```

---

## How Data Flows Through the Schema (one job, end to end)

1. **Setup.** User signs up -> `profiles` row. They add a degree -> `qualifications`;
   their internship -> `experiences` (`on_cv = TRUE`); skills C#, React, SQL ->
   `skills`, linked to the internship via `experience_skills`. They save a search
   -> `saved_searches`. They upload their CV -> `user_cvs` (`is_default = TRUE`).

2. **Scrape.** The daily job runs each active `saved_searches` row, fetches Seek
   listings, and upserts into `job_listings` keyed on `(source, source_job_id)`.
   Extracted required skills go into `job_skills` tagged hard/soft.

3. **Pre-filter.** Cheap keyword check: do the listing's **hard** `job_skills`
   intersect the user's `skills`? Non-matches are dropped before any LLM call.

4. **Score.** Survivors go to the LLM with the user's profile + `raw_description`.
   It returns a score, reasoning, and gaps -> written to `matches`
   (`UNIQUE (user_id, job_id)` keeps it idempotent on re-runs).

5. **Generate.** For above-threshold matches, a cover letter is produced (eagerly
   for the strong tier, lazily on click for the rest) -> `cover_letters`
   (`status = 'draft'`). It pulls evidence by walking `experience_skills` for the
   skills the job wants.

6. **Review & apply.** The dashboard reads `matches` ordered by `score`, derives
   tiers, and shows each job's `url`, reasoning, the editable cover letter, and the
   default `user_cvs` entry. The user edits (`status -> 'edited'`), applies on Seek,
   marks the match `applied` (optionally recording `cv_used_id`).

This walkthrough touches every table, which is a good sign the model is complete
for the core pipeline.

---

## Deferred / Future Schema Considerations

These are intentionally **not** built yet, noted so they're easy to slot in later:

- **Sessions / auth tokens** - a `sessions` table (or stateless JWTs) for hosted
  login. Decoupled from the domain model; add when building real auth.
- **Embeddings for semantic matching** - a vector column (e.g. pgvector) or
  `*_embeddings` table to upgrade the keyword pre-filter to semantic similarity,
  catching good matches that use different wording. The v2 matching upgrade.
- **Profile versioning** - if you ever want to know *which* state of a profile
  produced a given match, snapshot/version the profile. Skipped for now.
- **Highlight / priority flags** - an `is_highlight` boolean on `skills` /
  `experiences` for the "mark as priority" user story, if global emphasis (as
  opposed to per-job relevance) proves useful.
- **Status history / audit** - a `match_status_history` table if a timeline of
  status changes is wanted rather than just the current `status`.
- **Application analytics** - response/outcome tracking for funnel metrics.
- **Soft deletes** - a `deleted_at` column on user-content tables if recoverable
  deletion is desired.
