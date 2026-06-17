# Progress Log — Job Application Assistant

Running record of what's been built. Newest entries on top. Keep entries slim —
one block per milestone.

---

## 2026-06-17 — Extension: limited "Scan Page" (1-hop, 3-page) detail capture 🧪

**Goal:** confirm the detail-page → `/ingest` → extractor pipeline end to end on a
few real jobs, without a crawler. Owner-approved exception to the no-automated-Seek
policy, scoped tight (see CLAUDE.md "1-hop rule").

**What it does:** a **Scan Page** button in the side panel. On a Seek search results
page the user opened, it reads the job links already on that page, then navigates the
**active tab** to the first 3 of them — one at a time, **5s apart** — letting the
existing content script capture each detail page (full description) via `/ingest`.
It then returns to the search page and refreshes the matches list.

**Why the side panel (not the popup):** the panel survives the active tab navigating;
a popup is torn down on the first navigation, killing the loop.

**1-hop guarantee:** links come only from `parseSearchPage()` of the page the user
opened (`COLLECT_LINKS` message); the scan never collects links from the detail pages
it visits. Caps: `MAX_SCAN_PAGES = 3`, `SCAN_DELAY_MS = 5000` (`extension/sidebar.js`).

**Files touched:** `extension/sidebar.html` (+button/log), `extension/sidebar.js`
(self-contained scan via `chrome.scripting`), `extension/content_script.js`
(`collectJobLinks`, au.seek.com paths), `extension/manifest.json` (au.seek.com host +
broad match), `extension/selectors.js` + `app/scraper/selectors.py` (card selector).

**Live DOM fixes (real Seek differs from assumptions):**
- Host is **`au.seek.com`**, NOT `www.seek.com.au` — manifest/gate/base-URLs updated.
- Search URLs are SEO slugs (`/software-engineer-jobs/in-All-Brisbane-QLD`), not `/jobs`.
- Card container `[data-testid="job-card"]` (was `[data-automation="normalJob"]`, which
  missed premium/featured). `jobTitle`/`jobCompany`/`jobLocation`/`jobAdDetails` confirmed.
- The auto-injected content script wasn't injecting reliably, so the scan now injects its
  own collector/scraper via `chrome.scripting.executeScript` and POSTs to /ingest itself —
  no dependence on the declarative content script.

**VERIFIED LIVE 2026-06-17 ✅** — first successful real capture. Scan found 31 links,
visited 3 (5s apart), scraped full descriptions (4095/3343/3079 chars) → 3 rows in
`job_listings` with `raw_description`. Sidebar shows "No matched jobs" because `/jobs`
lists *scored* matches and `match.py` is still a stub — listings are stored fine.

---

## 2026-06-17 — Pivot: Playwright scraper ABANDONED → Chrome extension + API ✅

**Why:** Seek sits behind Cloudflare, which permanently loops the "Just a moment…"
challenge against any Playwright-driven browser — even with the mandatory proxy and
even when a human solves the challenge by hand (verified live 2026-06-16: the
automated client is never trusted, so clearance is re-challenged every page). Seek's
robots.txt also disallows the search (`*?`) and `*/job/` paths for generic agents,
and there is no free candidate-side Seek API. Pushing past Cloudflare would require
anti-bot evasion we won't build.

**New architecture (better product, zero detection surface):** the user browses Seek
in their *own real browser* (passes Cloudflare naturally, real IP — no proxy needed).
A **Chrome extension** reads the already-rendered DOM and POSTs listings to a **local
FastAPI backend**, which upserts them and (later) runs LLM extraction + matching.

**Delivered this session**
- `app/api/main.py` — FastAPI: `GET /health`, `POST /ingest` (upsert + backfill
  `raw_description` on existing rows; fires background tasks), `GET /jobs`,
  `GET /jobs/{id}`, `POST /jobs/{id}/regenerate`, `GET/PUT /profile/{id}`. CORS open
  for local dev; `get_db` session dependency.
- `app/llm/extract.py` + `match.py` — STUBS (log "TODO: implement") so the
  ingest → background-task pipeline runs end to end before the LLM work lands.
- `scripts/run_api.py` — uvicorn on 127.0.0.1:8000 (reload).
- `extension/` (Manifest V3): `manifest.json`, `selectors.js` (mirror of the Python
  selectors), `content_script.js` (parses /jobs + /job/ pages, polls for React
  render, POSTs to backend), `background.js` (badge + message relay), `popup.html/js`
  (backend health + open sidebar), `sidebar.html/js` (ranked matched-jobs list).
- `requirements.txt` += fastapi, uvicorn[standard], httpx.

**Kept (not deleted):** `app/scraper/` stays — selector logic is reused by the
extension (`extension/selectors.js` mirrors `app/scraper/selectors.py`), and
`explore_seek.py` is still a handy dev tool. The proxy infra (`require_proxy()` etc.)
is dormant but harmless; the new pipeline never makes automated requests to Seek.

**Out of scope this session:** real LLM extraction/matching, cover-letter generation,
profile UI, auth, Postgres. Stubs only.

**Verified this session**
- Deps installed (`fastapi 0.137`, `uvicorn 0.49`, `httpx 0.28`) — needed the
  `--trusted-host pypi.org --trusted-host files.pythonhosted.org` workaround (AV TLS
  interception on this machine; unrelated to the now-uninstalled Windscribe).
- `alembic upgrade head` + `scripts/seed_saved_search.py` → profile id=1 exists.
- All backend acceptance checks PASS against a live uvicorn:
  `/health` ok; `/ingest` new=1 then dedup new=0; detail re-ingest backfills
  `raw_description` (updated=1) then updated=0; `/jobs` `[]`; `/profile/1` ok;
  `/jobs/{id}` returns full detail / 404 when absent. Background tasks fire on ingest.
  (Note: send JSON to the API via PowerShell `Invoke-RestMethod`, not `curl.exe` —
  PS mangles embedded double-quotes.)

**Remaining (handed to the user):** load `extension/` unpacked in Chrome and do the
first live capture — this is also the first chance to verify the `data-automation`
selectors against a real Seek DOM (fix `extension/selectors.js` + its Python mirror
together if a capture returns 0 cards).

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
