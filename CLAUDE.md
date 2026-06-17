# CLAUDE.md — Job Application Assistant

> Project context for Claude Code. Read this first every session.
> **`docs/database-schema.md` is the source of truth for the data model.** If
> anything here conflicts with that file, the schema doc wins.

## What this project is

A tool that helps a job seeker find suitable roles on Seek and drafts tailored
cover letters from the user's own profile (qualifications, experience, skills).
The user reviews and submits every application manually — the tool never
auto-submits. It starts as a **local single-user app** and is designed to grow
into a **multi-user hosted** app later.

Full feature plan: `docs/job-application-assistant-plan.md`
Full DB design: `docs/database-schema.md`

---

## ⛔ NETWORKING POLICY — all SCRAPE-TARGET traffic MUST go through a proxy (NON-NEGOTIABLE)

> **2026-06-17 update:** The Playwright scraper is **abandoned** (Cloudflare loops the
> challenge on any automated browser, even with a proxy and a human solving it — see
> PROGRESS.md). The active pipeline is a **Chrome extension** that reads Seek pages the
> user opened in their *own* browser and POSTs them to a local API, so the app makes
> **no automated requests to Seek and needs no proxy.** This policy still binds any
> *future* code: do NOT add anything that makes a direct/automated request to a scrape
> target. All Seek data must come from the user's own browser via the extension.

**The risk being managed is an IP ban.** Any connection to a site we scrape —
Seek today, any other job board later — exposes the user's real IP to repeated
automated requests and MUST be routed through the configured proxy. A direct
connection to a scrape target is a critical failure.

**This does NOT mean every connection needs a proxy.** One-off software/infra
downloads from services built to serve them — PyPI (`pip install ...`), the
Playwright Chromium CDN (`playwright install chromium`), package registries,
docs — carry no ban risk and may go direct. The test is simple: *could repeated
automated requests from our IP get it flagged or banned by this site?* If yes
(scrape targets), proxy is mandatory. If no (normal downloads), proxy not needed.

Rules for anyone (human or AI) working in this repo:

1. **Never run the scraper, `explore_seek.py`, or any code that opens a browser /
   makes an HTTP request to a scrape target (Seek, etc.) unless a proxy is
   configured.** The scraper is **fail-closed**: `app/scraper/browser.py` raises
   `ProxyNotConfiguredError` and refuses to launch if `PROXY_SERVER` is not set.
   Do not weaken this — the browser is the only thing that ever hits a target site.
2. **`PROXY_SERVER` (and optionally `PROXY_USERNAME` / `PROXY_PASSWORD`) is
   REQUIRED for any scraping run**, not optional. Set it in the gitignored `.env`.
3. **Do NOT use `WebFetch` (or any agent-side fetch) against Seek or other scrape
   targets** — that traffic does not go through the user's proxy. If you need a
   target-site page (e.g. `au.seek.com/robots.txt`), fetch it **through the
   proxied browser**, never via a direct/agent-side request.
4. **When in doubt about whether a host is a scrape target, STOP and ask** before
   connecting. Do not "just test" against a live target site.

If a proxy is not yet configured, the correct action for any scrape-target
connection is to **halt and request the proxy details from the user**, not to
connect directly "just this once".

## Tech stack

- **Language:** Python 3.11+
- **ORM:** SQLAlchemy 2.0 (typed, declarative mapped style)
- **Migrations:** Alembic
- **Config:** `python-dotenv` — `app/db.py` loads `.env` so `DATABASE_URL` (and
  later secrets) can live in a gitignored `.env` file.
- **DB:** SQLite for local dev, **Postgres-ready** for hosting. The database URL
  comes from a `DATABASE_URL` env var, defaulting to `sqlite:///app.db`, so the
  SQLite → Postgres move is a config change, not a code change.
- Do **not** write SQLite-only SQL or rely on SQLite-specific behaviour. Keep
  everything portable so a Postgres `DATABASE_URL` works with no code changes.

## Proposed repo layout

```
job-app-assistant/
  CLAUDE.md
  docs/
    database-schema.md
    job-application-assistant-plan.md
  app/
    __init__.py
    db.py              # engine + session factory; reads DATABASE_URL
    models.py          # SQLAlchemy models (split into a models/ package if it grows)
  alembic/             # migration environment (from `alembic init`)
  alembic.ini
  scripts/
    smoke_test.py      # verifies the schema end-to-end
  requirements.txt
  .env.example         # DATABASE_URL=sqlite:///app.db
  .gitignore           # app.db, .env, __pycache__/, .venv/
```

## Key schema decisions to respect (do not "improve" these away)

These were deliberate; the schema doc explains the reasoning:

- **`experience_skills` is a pure junction** (composite PK of the two FKs). It has
  **no `strength`/relevance column** — relevance depends on the job and is judged
  by the LLM at generation time, not stored.
- **`matches.score`** (0–100) is the single source of truth. There is **no tier
  column** — strong/medium/reach buckets are derived at display time.
- **`job_skills` is NOT foreign-keyed to `skills`.** Job skills are extracted from
  listings independently and matched to user skills by name at match time.
- **CV lives at user level** (`user_cvs`), never per-job. **Cover letters** are one
  row per match (`cover_letters.match_id` unique), holding both `generated_content`
  and `edited_content` with a `status` of draft/edited/final.
- Passwords are stored **hashed** (`profiles.password_hash`). Do not implement auth
  logic in this task — just the column.
- Uniqueness constraints that must exist: `profiles.email`,
  `skills (user_id, name)`, `job_listings (source, source_job_id)`,
  `matches (user_id, job_id)`, `cover_letters.match_id`.
- FK delete behaviour: `ON DELETE CASCADE` for user-owned and job-owned children;
  `matches.cv_used_id` is `ON DELETE SET NULL`. See schema doc per-table.
- **Portability mechanics (don't "fix" these back):** integer PKs use
  `BigInteger().with_variant(Integer, "sqlite")`; boolean server defaults use
  `text("false")` / `text("true")` (NOT `0`/`1`, which break on a Postgres
  `BOOLEAN`, nor `false()`/`true()`, which don't exist in SQLite). ORM
  relationships set `passive_deletes=True` so deletes rely on DB-level cascade.

---

## 🔄 CURRENT TASK: Chrome extension + FastAPI backend (the capture pipeline)

> **Status: backend built + verified (2026-06-17); extension built, pending first live
> capture.** Replaces the abandoned Playwright scraper (Cloudflare — see the
> networking-policy note above and PROGRESS.md). Full system overview lives in
> `README.md`.

**Architecture:** the user browses Seek in their *own* browser; a Manifest-V3 Chrome
extension (`extension/`) reads the rendered DOM and POSTs listings to a local FastAPI
backend (`app/api/`), which upserts them into `job_listings` and fires background
tasks for LLM extraction + matching (currently stubs in `app/llm/`). No automated
requests to Seek; no proxy needed.

**Key files**
- `app/api/main.py` — endpoints: `/health`, `/ingest`, `/jobs`, `/jobs/{id}`,
  `/jobs/{id}/regenerate`, `/profile/{id}` (GET/PUT). `scripts/run_api.py` runs it.
- `app/llm/extract.py` + `match.py` — STUBS; real LLM work is the next task.
- `extension/` — `manifest.json`, `selectors.js` (**mirror of
  `app/scraper/selectors.py` — keep in sync**), `content_script.js`, `background.js`,
  `popup.{html,js}`, `sidebar.{html,js}`.

**Run/verify:** `pip install -r requirements.txt` (no VPN needed; on this machine add
`--trusted-host pypi.org --trusted-host files.pythonhosted.org` — AV TLS interception),
`alembic upgrade head`, `python scripts/seed_saved_search.py` (creates profile 1),
`python scripts/run_api.py`, then load `extension/` unpacked in Chrome
(`chrome://extensions`). Backend endpoints are verified; browse Seek to do the first
live capture. (Test the API with PowerShell `Invoke-RestMethod`, not `curl.exe` — PS
mangles embedded JSON quotes.) See `README.md` for the full walkthrough.

**`app/scraper/` is retained** for its selector logic (mirrored into the extension)
and `explore_seek.py`; it is no longer in the active pipeline. Do not re-activate any
code that makes automated requests to Seek.

**Next task after this:** implement the real `app/llm/extract.py` + `match.py`
(job-skill/requirement extraction, 0–100 match scoring, cover-letter drafts).

---

## ✅ COMPLETED TASK: build the database layer

> **Status: done (2026-06-15).** All acceptance criteria pass — see `PROGRESS.md`
> for the slim summary. Section kept below for history. Replace with the next task
> (the scraper) when starting it.

Implement **only** the persistence layer described in `docs/database-schema.md`.
Nothing else yet (no scraper, no LLM/matching, no cover-letter generation, no UI).

### Steps

1. **Scaffold** the repo layout above. Create `requirements.txt` with at least
   `sqlalchemy>=2.0` and `alembic`. Add `.gitignore` and `.env.example`.
2. **`app/db.py`** — create the engine from `DATABASE_URL`
   (default `sqlite:///app.db`) and a session factory. Enable SQLite foreign-key
   enforcement (PRAGMA foreign_keys=ON) via an event listener, since SQLite has it
   off by default and we rely on cascade behaviour.
3. **`app/models.py`** — implement all **11 tables** exactly as specified in
   `docs/database-schema.md`, using SQLAlchemy 2.0 typed models: `profiles`,
   `saved_searches`, `qualifications`, `experiences`, `skills`,
   `experience_skills`, `user_cvs`, `job_listings`, `job_skills`, `matches`,
   `cover_letters`. Match every column, type, nullability, default, unique
   constraint, FK + delete rule, and index from the doc.
4. **Alembic** — initialise it, point it at the models' metadata, autogenerate the
   initial migration, and confirm `alembic upgrade head` builds all tables on a
   fresh SQLite file.
5. **`scripts/smoke_test.py`** — prove the model works end to end:
   - Insert a `profile`, a `qualification`, an `experience`, two `skills`, and link
     both skills to the experience via `experience_skills`.
   - Add a `user_cv`, a `job_listing` with a couple of `job_skills`, a `match`, and
     a `cover_letter` for that match.
   - Run the key query: **"all experiences that demonstrate skill X"** by joining
     through `experience_skills`. Print the result.
   - Delete the `profile` and assert its qualifications, experiences, skills, links,
     CVs, and matches are gone (cascade works). Print pass/fail.

### Acceptance criteria

- `alembic upgrade head` on a fresh `app.db` creates all 11 tables.
- `python scripts/smoke_test.py` runs clean, prints the evidence-query result, and
  confirms the cascade delete.
- No SQLite-only SQL; pointing `DATABASE_URL` at Postgres would work unchanged.
- Models match `docs/database-schema.md` exactly — flag any ambiguity in the doc
  rather than guessing silently.

### Out of scope (do NOT build yet)

Scraper, matching/LLM scoring, cover-letter generation, web UI, authentication
logic. Database layer only.

---

## How to use this file (note for the human)

1. Create the repo, drop this file in the root as `CLAUDE.md`, and put
   `database-schema.md` + `job-application-assistant-plan.md` in `docs/`.
2. Open the repo in VS Code with Claude Code and tell it: *"Implement the Current
   Task section of CLAUDE.md."*
3. Once the DB layer passes the smoke test, replace the "CURRENT TASK" section with
   the next task (the scraper) — keep the project context and schema decisions above
   as permanent memory.
