# Progress Log — Job Application Assistant

Running record of what's been built. Newest entries on top. Keep entries slim —
one block per milestone.

---

## 2026-09-21 — Search-suggestion overhaul: 4-layer pipeline — DONE ✅

**Goal:** the suggested-searches banner was offering "Administration Assistant
· Intermediate Software · Intermediate Software Engineer". Two of those are the
same idea and neither is a phrase anyone types into Seek. Replace frequency
ranking with something that measures payoff, and stop discarding the signals
that would make it measurable.

**Diagnosis** (run against the live dev DB, 25 matches, baseline avg 69.4):
frequency ranked `software engineer` top at 12 occurrences despite averaging
60.9 — *below* baseline. Frequency measures what the user already searched for,
not what worked. It also emitted nested n-grams and non-role fragments
(`experienced software`, `engineer front`, `systems automation`).

**Delivered**
- **Layer 1 — `app/search_suggest.py`** (new, pure Python, no LLM). Ranks by
  `(shrunk_mean - baseline) * log1p(support)`; Bayesian shrink K=3 stops a
  single 92-point job outranking a phrase with real support. Leading-segment-
  only mining, MODIFIER_TOKENS stripping (seniority words are Seek *filters*,
  not keywords), a ROLE_NOUNS head-noun gate, then nested + family dedup.
  Ranks over the whole score distribution — the low scores are what make the
  baseline meaningful.
- **Layer 2 — capture.** `job_listings.discovered_query` (migration
  `c5b21d7f4e3a`) from the search page; classification/subclassification now
  actually sent by the extension (columns existed since `aa057c74b513`, all 25
  rows were NULL) via the page's schema.org JSON-LD, with a selector fallback.
  `/ingest` backfills NULL fields without overwriting, so card and detail
  captures complete each other.
- **Layer 3 — `GET /jobs/search-performance`.** Volume/hits/yield per query;
  >=5 jobs and <15% yield halves the rank of any phrase that query contains.
  Demote, not delete. Surfaced as a table in the Personalise panel.
- **Layer 4 — `app/llm/search_refine.py`**, OFF by default behind a checkbox.
  One `complete_json` on OPENAI_MODEL_SMALL that picks/generalises among mined
  candidates rather than generating freely; debounced at 10 new matches and
  cached in `profiles.preferences`. Failure falls back to the mined list.

- **Location scoping.** A clicked suggestion opens the search scoped to
  `target_location`, falling back to `location`, else nationwide. Slug path +
  `?where=` deliberately, so `currentSearchQuery()` still attributes the job to
  the bare phrase and the same role in two cities aggregates under one query.
- **Bulk hide became a soft delete** (`matches.hidden_at`, migration
  `d3f8a1c60b92`). Found this mid-session: the dev DB had gone 25 jobs → 16
  and the baseline 69.4 → 82.5 because everything under 70 had been deleted,
  which is exactly the contrast Layer 1 measures against. `DELETE /jobs` now
  stamps `hidden_at` and nulls `raw_description` (the bulk of the storage)
  rather than removing the row. Hidden matches leave every `/jobs` view and
  are skipped by the cover-letter loop, but still feed the baseline and the
  yield stats.

**Result on live data:** `["Administration Assistant", "Data Analyst",
"Software Engineering"]`. Both "Intermediate" fragments gone. "Administration
Assistant" survives on merit — avg 86 vs a 69.4 baseline.

**Verified:** `python -m pytest` — 132 passed (44 new). `node --check` on the
three edited JS files. Endpoint exercised against the live dev DB. Attribution
round-trip (`seekSearchUrl` → `currentSearchQuery`) checked directly, including
the `/in-Brisbane-QLD-4000` form Seek redirects `?where=` into.
**Not verified:** sidebar UI in a loaded Chrome extension; the classification
selectors against a real Seek page (JSON-LD is primary, selectors are a
fallback guess — see CLAUDE.md CURRENT TASK).

---

## 2026-09-11 — Extension revamp: Centrelink-ready application tracking — DONE ✅

**Goal:** implement docs/extension-revamp-plan.md — score-tiered card UI,
idle-loop reprioritization (cover letters before new extraction), application
tracking for Centrelink mutual-obligation reporting, a cleaner cover-letter
card state, a Quick-Apply overlay on Seek, and free search-phrase suggestions.

**Delivered**
- Migration `60d65ffb16df`: `matches.applied_at`. Status vocabulary
  (`new -> shortlisted -> applied -> interviewing -> rejected/withdrawn`)
  documented in docs/database-schema.md.
- `app/api/main.py`: `PATCH /jobs/{id}/status` (idempotent `applied_at`
  stamping), `PATCH /jobs/{id}/cover-letter`, `GET /jobs/suggested-searches`
  (pure-Python bigram/trigram mining, no LLM call, registered before
  `/jobs/{job_id}` to avoid the route-match collision — same pattern as the
  existing `/jobs/known-ids`), `GET /jobs` gained `top_skills` + a `status`
  filter (sorts by `applied_at desc` when set). Idle loop reordered — cover
  letters (score>=75, no letter yet) checked first every iteration.
- `extension/sidebar.html` + `sidebar.js`: client-side tiering
  (blue>=90/green>=75/amber 60-74/hidden<60 behind a lazy fold), Applied tab +
  CSV export, "Mark Applied" per card, cover-letter ready/pending/none state
  machine with an editable Copy/Save textarea, dismissible resize hint,
  dismissible suggested-searches banner (`chrome.storage.local`, 7-day
  cooldown). Dropped `pollForCoverLetter` in favor of SSE-driven reload.
- `extension/content_script.js`: Shadow-DOM Quick-Apply panel on `/job/{id}`
  pages when a cover letter exists — title, editable textarea, Copy/Save
  edits/Mark Applied, all through the existing PATCH endpoints. Apply-flow
  auto-detection deliberately deferred (needs live inspection of Seek's
  actual Apply DOM — see CLAUDE.md CURRENT TASK).
- `app/llm/cover_letter.py`: system prompt now requests a `Sincerely, {name}`
  sign-off and foregrounds degree/university for recent grads.

**Verified:** `python -m pytest` (72 passed); manual TestClient round-trips of
both new PATCH endpoints against the live dev DB, including idempotency of
`applied_at` on repeat "mark applied" (then reverted the test writes);
`node --check` on all three edited extension JS files. **Not verified:** the
sidebar/Quick-Apply UI in an actual loaded Chrome extension — no browser
harness available this session.

---

## 2026-06-23 — LLM matching + scoring (match.py) — DONE, verified live ✅

**Goal:** score each extracted job against profile id=1 into a `matches` row
(0–100 + reasoning + gaps). Followed the matching plan, which scoped **cover
letters OUT** of this pass (separate next task) — so nothing writes `cover_letters`.

**Delivered**
- `app/llm/prefilter.py` — pure-Python, zero-LLM `prefilter(job, profile) ->
  PrefilterResult`: hard/soft skill match/gap lists, overlap %, `seniority_flag`
  (senior/lead/etc.), `qual_match` (user qual words vs job qual-requirement text).
  Context for the LLM, **not** a gate — every extracted job still gets scored.
- `app/llm/match.py` — `match_job(job_id, profile_id, session=None, force=False)`.
  Eager-loads profile (skills, quals, experiences+linked skills) + job (job_skills),
  builds plain-text candidate/job summaries + prefilter signals, calls
  `complete_json` (Pydantic `MatchScore`, temp 0.1) with a new-grad-fair prompt
  ("X years" = soft gap, weight quals/projects). Clamps score 0–100, upserts on
  UNIQUE(user_id, job_id), status `new`, `gaps` stored as a JSON string. Parse
  failure records a 0-score row rather than vanishing. Skips unextracted jobs and
  already-scored pairs unless `force`.
- `scripts/seed_profile.py` — idempotent richer seed for profile 1 (16 skills,
  1 qualification, 3 experiences with linked skills via `experience_skills`).
- `scripts/run_matching.py` — `--profile-id/--job-id/--limit/--force/-v`; default =
  extracted jobs with no match row for the profile; per-job try/except;
  `DailyQuotaError` stops the batch.
- API: `GET /jobs` + `/jobs/{id}` now return `gaps` as a list (`_gaps_to_list`,
  `json.loads`); `/jobs` already joined `matches` and ordered by score desc.

**Verified live 2026-06-23** (all acceptance criteria PASS):
- All 3 extracted jobs scored: Graduate SWE **85**, AI Engineer **65**, AI Lead **65**
  — relevant grad role clearly out-scores the senior/lead roles, and the lead role
  was NOT auto-zeroed (new-grad fairness holds). Reasoning + specific gaps populated.
- Re-run without `--force` → "no jobs to match", row count stays 3 (idempotent;
  UNIQUE user_id+job_id holds).
- `GET /jobs` (TestClient) → 3 rows ordered by score desc, `gaps` as JSON arrays.

**Next task:** cover-letter generation — `complete_text` (temp ~0.7) for
above-threshold matches → one `cover_letters` draft per match (UNIQUE match_id),
pulling real evidence by walking `experience_skills`. No new schema needed.

---

## 2026-06-23 — LLM extraction (extract.py) — DONE, verified live ✅

**Goal:** replace the `extract.py` stub with real Gemini extraction of structured
fields from `job_listings.raw_description` (CLAUDE.md "Current Task").

**Delivered**
- `app/llm/client.py` — provider abstraction (the ONE place model/provider live).
  `complete_json(system, user, schema, temp)` + `complete_text(...)`. Reads
  `LLM_PROVIDER`/`GEMINI_MODEL`/`GEMINI_API_KEY`(or `GOOGLE_API_KEY`)/`LLM_RPM`
  from env; in-process RPM throttle; 429 backoff (2/4/8, max 3); a *daily*-quota
  429 raises `DailyQuotaError` to stop a batch. Loads `.env` itself (no import-order
  dependency). Calls `truststore.inject_into_ssl()` so TLS uses the Windows cert
  store (AV TLS-interception breaks certifi — same root cause as the pip quirk).
- `app/llm/extract.py` — `extract_job(job_id, session=None, force=False)`. Pydantic
  `JobExtraction` schema (hard/soft skills, qualifications[], experience[], seniority
  enum, key_responsibilities[], summary) drives Gemini structured output. Idempotent:
  clears prior `job_skills` + extracted fields, dedupes skills, sets `extracted_at`.
  Skips null-`raw_description` and already-extracted (unless `force`).
- `scripts/run_extraction.py` — `--limit/--job-id/--force/-v`; per-job try/except;
  stops cleanly on `DailyQuotaError`; prints processed/succeeded/failed summary.
- API: `_process_listing` now wraps `extract_job`/`match_job` in try/except so a bad
  job can't kill the background task.
- Schema: added `seniority`, `key_responsibilities`, `summary`, `extracted_at` to
  `job_listings` (migration `f42bcd31e385`, batch-mode; `alembic check` clean).
  `*_requirements` now hold JSON. `docs/database-schema.md` updated.
- `requirements.txt` += `google-genai`, `truststore`.

**Self-test tool:** `scripts/check_llm.py` — key present → `list()` → one tiny
generate; prints PASS or a diagnosed FAIL. Use to validate any key before a batch.

**Verified live 2026-06-23** (all acceptance criteria PASS):
- `run_extraction.py --job-id 4` extracted the Graduate SWE ad → 5 hard + 11 soft
  `job_skills`, qualifications/experience JSON (must-have vs nice-to-have split
  correctly, e.g. Elixir → required:false), `seniority=graduate`, summary,
  key_responsibilities; `extracted_at` set.
- Re-run without `--force` → **skipped** (logged, no LLM call); `--force` →
  re-extracted with **no duplicate rows** (16→17, not 32 — clear-before-insert works).
- Null-`raw_description` row → **skipped with a warning, no crash**.
- All 3 real captures extracted: job 2 `lead`, job 3 `mid`, job 4 `graduate`.

**The key blocker, resolved:** the `AQ.Ab8…` keys are short-lived Google AI Studio
*ephemeral tokens*, and the user's personal Google account had no free-tier Gemini
quota ("project quota tier unavailable / contact your project administrator"). Fix
that worked: signed up for the **Google Cloud $300 free-trial** (billing account with
promo credit) → the project got paid-tier access drawn from the $300 credit. At
~$0.0003/job this is effectively free. Key lives in `.env` as `GOOGLE_API_KEY`.

**Next task:** real `app/llm/match.py` — 0–100 scoring of job vs profile (reasoning,
gaps) into `matches`, + cover-letter draft into `cover_letters`.

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
