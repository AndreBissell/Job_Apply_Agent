# Progress Log — Job Application Assistant

Running record of what's been built. Newest entries on top. Keep entries slim —
one block per milestone.

---

## 2026-06-15 — Seek scraper (Task 2) — code complete, NOT yet run ⏸️

Built the full scraping layer. **No scraping has been executed** (per the standing
constraint — selectors are unverified against live DOM). Code is structured so that
Step 0 exploration + a small `--limit 5` live run are the only remaining steps.

**Delivered**
- `app/scraper/selectors.py` — single source of truth for all Seek selectors
  (`data-automation` based) and the generic search-URL builder (`keywords`/`where`/
  `worktype`/`sortmode=ListedDate`). All selectors flagged "verify in Step 0".
- `app/scraper/browser.py` — `launch_browser()` context manager (headless Chromium,
  default realistic UA) + `build_proxy_from_env()`. Playwright imported lazily.
- `app/scraper/search.py` — **Component 1**: walks results pages, parses each card
  into a `ScrapedListing`, dedupes on `(source='seek', source_job_id)`, early-stops
  when a page has zero new listings. Does not cap (capping is orchestration's job).
- `app/scraper/detail.py` — **Component 2**: visits each detail page, extracts the
  ad body from the description container (noise sections excluded by being outside
  it), randomised 2–5 s delay *between* visits. Per-page errors logged, not fatal.
- `app/scraper/run.py` — `run_daily_scrape(max_new_per_search=20, limit=None)`:
  iterates active saved searches → Component 1 → cap → Component 2 → insert →
  logged `RunSummary` (new / processed / deferred / inserted / errors). Effective
  cap = `min(max_new_per_search, limit)`.
- `scripts/explore_seek.py` — one-shot dev tool: caches a search page + a detail
  page to `dev_data/` (gitignored) for offline selector work.
- `scripts/run_scrape.py` — CLI: `--max-new`, `--limit`, `--headed`, `-v`.
- `scripts/seed_saved_search.py` — idempotent seed: test profile + one saved
  search (keywords="software engineer", location="Brisbane QLD", work_type=NULL).
- `requirements.txt` (+`playwright`), `.env.example` (optional `PROXY_*` vars),
  `.gitignore` (+`dev_data/`).

**Verified (offline only)**
- All modules byte-compile and import via `.venv`; Playwright stays lazy (not
  imported until a browser is launched).
- URL builder produces correct generic URLs incl. worktype code + date sort.

**Schema change (agreed): classification / subclassification**
- Added nullable `classification` + `subclassification` `Text` columns to
  `job_listings` (Seek's own free categorisation — cheap pre-filter + UI badge,
  distinct from LLM-extracted `job_skills`). Now persisted in the upsert.
- Migration `aa057c74b513` (batch-mode, portable); `alembic upgrade head` +
  `alembic check` = no drift. `docs/database-schema.md` updated (table, note, and
  `idx_job_listings_classification` index).

**Remaining before this task is "done"**
1. `python -m playwright install chromium` (one-time).
2. Run `scripts/explore_seek.py` once; reconcile `app/scraper/selectors.py` against
   the cached HTML; check `au.seek.com/robots.txt`.
3. `python scripts/seed_saved_search.py`, then `python scripts/run_scrape.py
   --limit 5` to verify end-to-end (and a second run → 0 new = dedup confirmed).

**Next up:** verify selectors via Step 0, then the LLM extraction pass
(`job_skills` / `*_requirements`).

---

## 2026-06-15 — Database layer (Task 1) ✅

Built the full persistence layer per `docs/database-schema.md`. Nothing else yet
(no scraper, matching, generation, or UI).

**Delivered**
- Repo scaffold: `app/`, `alembic/`, `scripts/`, `docs/`, `requirements.txt`,
  `.env.example`, `.gitignore`.
- `app/db.py` — engine + `SessionLocal` + `Base` from `DATABASE_URL`
  (default `sqlite:///app.db`); SQLite `PRAGMA foreign_keys=ON` listener; loads
  `.env` via python-dotenv.
- `app/models.py` — all 11 tables as SQLAlchemy 2.0 typed models, portable
  across SQLite and Postgres.
- `alembic/` wired to the app's engine + metadata; initial migration
  `cf7f9fc0ffc2_initial_schema` creates all 11 tables.
- `scripts/smoke_test.py` — seeds a full graph, runs the "experiences that
  demonstrate skill X" evidence query, and verifies cascade delete.

**Verified**
- `alembic upgrade head` on a fresh `app.db` → 11 tables. Downgrade/upgrade
  round-trips. `alembic check` reports no drift (models = migration = DB).
- `python scripts/smoke_test.py` → PASS (evidence query + cascade + shared job
  survives).

**Decisions worth remembering**
- Boolean defaults use `text("false")` / `text("true")` (not `0/1` or
  `false()`), so the migration runs unchanged on both SQLite 3.23+ and Postgres.
- PKs use `BigInteger().with_variant(Integer, "sqlite")` to autoincrement on
  both dialects.
- Relationships use `passive_deletes=True`, so deletes rely on DB-level
  `ON DELETE CASCADE` (this is what the smoke test actually exercises).

**Environment notes**
- pip is behind a TLS-intercepting cert here; installs need
  `--trusted-host pypi.org --trusted-host files.pythonhosted.org`.
- `docs/job-application-assistant-plan.md` is referenced by CLAUDE.md but not yet
  present in the repo.

**Next up:** the Seek scraper (Feature 2) — not started.
