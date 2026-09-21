CLAUDE.md — Job Application Assistant


Project context for Claude Code. Read this first every session.
docs/database-schema.md is the source of truth for the data model. If
anything here conflicts with that file, the schema doc wins.



What this project is

A tool that helps a job seeker find suitable roles on Seek and drafts tailored
cover letters from the user's own profile (qualifications, experience, skills).
The user reviews and submits every application manually — the tool never
auto-submits. It starts as a local single-user app and is designed to grow
into a multi-user hosted app later.

Full feature plan: docs/job-application-assistant-plan.md
Full DB design: docs/database-schema.md


SEEK ACCESS POLICY — how this app is allowed to touch Seek


No proxy required (2026-06-17). The Playwright scraper is abandoned
(Cloudflare loops the challenge on any automated browser — see PROGRESS.md). The
active pipeline is a Chrome extension running in the user's own signed-in
browser, on the user's real IP. There are no backend/server-side automated
requests to Seek, so the old "all scrape-target traffic must go through a proxy"
rule no longer applies and is removed. The proxy code (require_proxy() etc.) stays
in the repo dormant and optional — do not delete it, but nothing depends on it.



What the extension IS allowed to do — the 1-hop rule (standing behaviour):


Read the DOM of any Seek page the user themselves opened.
Auto-navigate the user's active tab to job links that already appear on a page the
user opened — i.e. links on the opened search-results page are fair game. This is
1 hop: the page the user opened → the listings linked from it.
Paced and capped so it stays a trickle, not a crawl: ≥5s between pages
(SCAN_DELAY_MS = 5000), and a per-scan cap. The cap is now user-tunable in the
sidebar's Personalise panel (`scan_max_pages`, default 10) but hard-bounded at 25
by both the API (PreferencesUpdate le=25) and the sidebar (SCAN_PAGES_CEILING) —
that ceiling is the policy cap: raise it deliberately if needed, don't remove it.


The hard line that still holds — no second hop:


Never follow links found on the pages the scan itself visited. Links come only
from a page the user opened (parseSearchPage() / COLLECT_LINKS), never from the
detail pages the scan navigates to. No recursion, no crawler. One hop, full stop.
No backend/agent-side requests to Seek. All Seek traffic comes from the user's
browser. Do NOT use WebFetch or any server-side fetch against Seek; if you ever need
a Seek page agent-side (e.g. au.seek.com/robots.txt), STOP and ask the user first.
Keep it paced and capped. If the scan ever draws Cloudflare / anti-bot attention,
pull it back to user-initiated opens only.


Why this is defensible at this volume: it runs in the user's real browser/session
for their own personal job search, opens a handful of listings the user could have
clicked themselves, spaced like normal browsing. It is low-volume, non-commercial, and
the data is used only for the user's own applications. Seek's ToS does prohibit
automated access in general (clause 9b), so this is a deliberate, owner-accepted
personal-use trade-off — kept small on purpose. The line that keeps it bounded is the
no-second-hop rule above; do not cross it.

Normal downloads are unaffected: PyPI (pip install), package registries, docs,
etc. were never the concern and go direct as usual.

Tech stack


Language: Python 3.11+
ORM: SQLAlchemy 2.0 (typed, declarative mapped style)
Migrations: Alembic
API: FastAPI + uvicorn (local backend for the extension)
LLM: Groq (free tier, llama-3.3-70b-versatile) — see the LLM Layer section below
Config: python-dotenv — app/db.py loads .env so DATABASE_URL (and
later secrets) can live in a gitignored .env file.
DB: SQLite for local dev, Postgres-ready for hosting. The database URL
comes from a DATABASE_URL env var, defaulting to sqlite:///app.db, so the
SQLite → Postgres move is a config change, not a code change.
Do not write SQLite-only SQL or rely on SQLite-specific behaviour. Keep
everything portable so a Postgres DATABASE_URL works with no code changes.



🤖 LLM LAYER

Active provider: OpenAI (platform.openai.com), paid — chosen 2026-09-11 after
Groq deprecated its free-tier models (llama-3.1-8b-instant cut 2026-08-16,
llama-3.3-70b-versatile went enterprise-only 2026-08-26) and Gemini's fallback
default (gemini-2.0-flash) was retired 2026-06-01. Neither old default works
anymore. Cost is negligible at this project's volume — a few cents/month even
at dozens of applications — so the model choice below is optimised for quality
where it matters (cover letters), not absolute cheapness everywhere.

Split by task — two different models, not one:
  Extraction + matching (complete_json) → OPENAI_MODEL_SMALL, default
    gpt-5-nano. Structured JSON, low judgment required — the cheap tier is
    genuinely fine here.
  Cover letters (complete_text) → OPENAI_MODEL_LETTER, default gpt-5-mini.
    This is the one output a human (an employer) actually reads, so it gets
    the better model even though the dollar difference is trivial either way.

⚠️ gpt-5-mini is scheduled for shutdown 2026-12-11, successor is gpt-5.6-terra
(pricier tier — re-check current pricing/model landscape before migrating,
don't assume today's numbers still hold). Swap is a single .env change
(OPENAI_MODEL_LETTER), never a code change — see provider abstraction below.

Provider abstraction: ALL LLM calls go through app/llm/client.py
(complete_json for structured extraction; complete_text for prose). Provider
and models live in exactly ONE place. Do not call the OpenAI/Groq/Gemini SDKs
directly from extract.py / match.py / cover-letter code.

Temperature:
  Extraction → 0.1 (deterministic, consistent structure).
  Cover letters → higher (~0.7) for natural prose.

Rate-limit handling: on HTTP 429, client.py backs off and retries (max 3).
OpenAI/Groq provide a retry-after header; delays > 300s are treated as daily
exhaustion → DailyQuotaError. The idle processing loop (_processing_idle_loop
in main.py) serialises all LLM work through a single-worker executor and backs
off 3 minutes when extraction fails. Local throttle: LLM_RPM=8 (~7.5s spacing).

TLS note (this machine): OpenAI/Groq both use httpx internally. The AV does TLS
interception with a local CA cert that certifi doesn't trust. truststore's
inject_into_ssl() doesn't affect httpcore's start_tls path, so both httpx
clients are created with verify=False. Acceptable on a local dev machine with a
trusted AV proxy.

Env vars (gitignored .env):
  OPENAI_API_KEY — from platform.openai.com/api-keys
  OPENAI_MODEL_SMALL=gpt-5-nano
  OPENAI_MODEL_LETTER=gpt-5-mini
  LLM_PROVIDER=openai
  LLM_RPM=8

Dormant fallbacks (kept working, not required): Groq (LLM_PROVIDER=groq,
GROQ_API_KEY, GROQ_MODEL) and Gemini (LLM_PROVIDER=gemini, GEMINI_API_KEY,
GEMINI_MODEL) are still wired in client.py. Neither has a currently-valid free
default model as of 2026-09-11 — check current model availability before
switching back. Gemini TLS works via truststore.inject_into_ssl() (urllib3
path, unlike httpx).

Data privacy: check OpenAI's current data-usage terms for API traffic before
sending personal profile data (as of this writing, API inputs are not used for
training by default, unlike the old Groq free tier — verify this hasn't
changed if it matters for your use case).


Repo layout (actual)

job-app-assistant/
  CLAUDE.md
  README.md
  PROGRESS.md
  docs/
    database-schema.md
    job-application-assistant-plan.md
  app/
    __init__.py
    db.py              # engine + session factory; reads DATABASE_URL
    models.py          # SQLAlchemy models
    api/
      main.py          # FastAPI backend for the extension
    llm/
      client.py        # provider abstraction (Gemini now; Claude later) — ONE place
      extract.py       # job → structured fields (DONE 2026-06-23)
      prefilter.py     # cheap pre-LLM match signals (DONE 2026-06-23)
      match.py         # job vs profile → score/reasoning/gaps (DONE 2026-06-23)
      cover_letter.py  # match → cover-letter draft (DONE 2026-06-27)
    scraper/           # RETAINED but not in the active pipeline (selector logic reused)
  extension/           # Manifest V3 Chrome extension
  alembic/
  alembic.ini
  scripts/
    run_api.py
    seed_saved_search.py
    seed_profile.py    # richer profile-1 test data for matching
    seed_test_jobs.py  # 5 known test jobs (source="test") for scoring validation
    run_extraction.py  # batch LLM extraction
    run_matching.py    # batch LLM matching/scoring
    run_cover_letters.py  # batch cover-letter generation (dev/testing only)
    check_matching.py  # scoring diagnostic report vs expected bands
    check_llm.py       # validate the LLM key before a batch
    smoke_test.py
    explore_seek.py    # dev-only selector cache tool
  requirements.txt
  .env.example
  .gitignore

Key schema decisions to respect (do not "improve" these away)

These were deliberate; the schema doc explains the reasoning:


experience_skills is a pure junction (composite PK of the two FKs). It has
no strength/relevance column — relevance depends on the job and is judged
by the LLM at generation time, not stored.
matches.score (0–100) is the single source of truth. There is no tier
column — strong/medium/reach buckets are derived at display time.
job_skills is NOT foreign-keyed to skills. Job skills are extracted from
listings independently and matched to user skills by name at match time.
CV lives at user level (user_cvs), never per-job. Cover letters are one
row per match (cover_letters.match_id unique), holding both generated_content
and edited_content with a status of draft/edited/final.
Passwords are stored hashed (profiles.password_hash). Do not implement auth
logic in this task — just the column.
Uniqueness constraints that must exist: profiles.email,
skills (user_id, name), job_listings (source, source_job_id),
matches (user_id, job_id), cover_letters.match_id.
FK delete behaviour: ON DELETE CASCADE for user-owned and job-owned children;
matches.cv_used_id is ON DELETE SET NULL. See schema doc per-table.
Portability mechanics (don't "fix" these back): integer PKs use
BigInteger().with_variant(Integer, "sqlite"); boolean server defaults use
text("false") / text("true") (NOT 0/1, which break on a Postgres
BOOLEAN, nor false()/true(), which don't exist in SQLite). ORM
relationships set passive_deletes=True so deletes rely on DB-level cascade.



TWO ENVIRONMENTS — real vs test (2026-09-21)

`python scripts/run_api.py real|test` (the argument is required). Each environment
is a separate SQLite file, screenshots folder and port, and holds exactly ONE
profile, which is id 1 in both — so the hardcoded `profile_id=1` / `user_id == 1`
in the API, idle loop and extension stay correct. Do not put two profiles in one
DB without first removing those assumptions (`/profile-ui/data` uses
`select(Profile).limit(1)`, and `DELETE /profile-ui/data` would delete whichever
profile is first).
  real → real.db,  port 8000, app/screenshots_real/   (the profile you apply from)
  test → app.db,   port 8001, app/screenshots/        (fake "bob john" profile)
`app_env()` in app/db.py reads APP_ENV (set by run_api.py) and defaults to "test",
so pytest / bare uvicorn can never act as real. /health returns `env`. In real,
DELETE /profile-ui/data is a 403 (applied matches are Centrelink evidence), and
scripts/load_test_profile.py refuses to run unless /health says env=test.
Extension: extension/config.js defines BACKEND from chrome.storage.local
(`backendEnv`, default real); every context awaits `backendReady` first. The
sidebar's REAL/TEST pill flips it and reloads. Not verified in a loaded Chrome.

🔄 CURRENT TASK: TBD

Two fast-follows are outstanding, both needing a live Seek session rather than
guesswork — pick these up, or a new priority the user names:
1. §5.2's apply-flow detection (see the extension-revamp entry below).
2. Verify the classification capture added 2026-09-21. readJsonLdJobPosting()
   is the primary source and should work, but SELECTORS.DETAIL_CLASSIFICATION /
   DETAIL_SUBCLASSIFICATION (the fallback) are UNVERIFIED guesses. Open a real
   job page, check job_listings.classification is populated, and fix the
   selectors if the JSON-LD path ever stops covering it.


✅ COMPLETED: Rolling retention + post-profile-update weighting — 2026-09-21

Age-based deletion for the match history the suggestion miner learns from,
without moving its baseline. New `app/retention.py` + `app/screenshots.py`,
migration `a7d2c9e15b48` (`matches.scored_at`, `profiles.profile_revised_at`,
index on `matches.created_at`). Full reasoning in docs/database-schema.md
(*Retention* under `matches`); the load-bearing decisions:
- **Delete by age, never by score.** Age is uncorrelated with score so the
  baseline survives; score-based deletion is what `hidden_at` soft-deletes
  exist to avoid. Don't turn the bulk hide into a real DELETE.
- **122-day window + 150-match floor**, one function (`effective_cutoff`)
  shared by the daily sweep AND the miner's read, so applied matches (kept
  forever, skew high) can't inflate the baseline. Only `status='new'` with
  `applied_at IS NULL` is purgeable — a whitelist. Applied = Centrelink
  evidence, never deleted.
- **Screenshots**: file deleted 30 days after capture, `screenshot_taken_at`
  kept ("captured, since expired"); downscaled to 1200px on upload;
  `GET /jobs/evidence-export` zips CSV + surviving files; Applied cards show
  the expiry date.
- **Profile drift**: matches scored before the profile last changed get
  weight 0.35 in the miner (baseline weighted too) — but only when a fresh
  match exists to prefer (`relative_weights`); uniform discounting re-ordered
  the live suggestions with no new information.
- The sweep runs at the TOP of `_processing_idle_loop` (not the tail — LLM
  phases `continue` and would starve it), once/24h, on the single worker.
Not built: the "re-score against updated profile" button (item 10), and
nothing yet bumps `profiles.profile_revised_at` on a deletion because no
profile-edit endpoint exists for experiences/skills. Tunables live in
`profiles.preferences`; the sidebar does not expose them.
Verified: `python -m pytest` (175 passed, 43 new in tests/test_retention.py),
migration applied to the dev DB, live-data ranking unchanged. NOT verified:
the sweep against a real aged DB (dev data is all <1 day old, so it purged
nothing), and the sidebar's new expiry note / evidence button in Chrome.


✅ COMPLETED: Search-suggestion overhaul — 4-layer pipeline — 2026-09-21

Replaced the frequency-based suggested-searches miner, which ranked by how
often a phrase appeared rather than how well it scored. On the dev profile it
put "software engineer" top (12 titles, avg 60.9, against a 69.4 baseline) and
emitted fragments — "Intermediate Software" alongside "Intermediate Software
Engineer". Four layers, cheapest first; only the last costs money.
- **Layer 1 — app/search_suggest.py** (new, pure Python): rank by
  `(shrunk_mean - baseline) * log1p(support)` with a Bayesian shrink (K=3)
  toward the baseline, so one lucky 92 can't outrank a phrase with real
  support. Mines only the leading title segment (kills `| I.T Services |
  5 Days Onsite` junk), drops MODIFIER_TOKENS (seniority is a *filter* on
  Seek, not a keyword — "Intermediate" in a query shrinks the result set),
  gates on a phrase ending in a ROLE_NOUNS head noun (deletes essentially
  every fragment in one rule), then dedups nested phrases and role families.
  Ranks over the WHOLE score distribution, not just score>=75 — the low
  scores are what make the baseline mean anything.
- **Layer 2 — capture what was being thrown away**: `job_listings
  .discovered_query` (migration `c5b21d7f4e3a`) set from the search page, and
  classification/subclassification finally populated (the columns and /ingest
  accepted them since aa057c74b513; the extension never sent them, so all 25
  rows were NULL). Primary source is the page's schema.org JSON-LD JobPosting
  block — already-rendered DOM, no extra Seek request. /ingest now backfills
  any NULL field from a later capture without overwriting, so the card
  (query) and the detail page (description + taxonomy) complete each other.
- **Layer 3 — yield feedback**: `GET /jobs/search-performance` reports
  volume/hits/yield per query; a query with >=5 jobs and <15% yield halves the
  rank of any phrase it contains. Demotes, never deletes — the same role may
  still be worth searching under different wording.
- **Location scoping**: clicking a suggestion opens the search scoped to the
  profile's target_location, falling back to location, else nationwide
  (_search_location()). The URL keeps the slug path and puts the place in
  ?where=, because currentSearchQuery() reads keywords-then-path — so the
  location never enters the attribution key and Brisbane/Melbourne runs of the
  same role aggregate under one query in the yield stats.
- **Bulk hide is now a soft delete** (`matches.hidden_at`, migration
  `d3f8a1c60b92`). DELETE /jobs?below_score= stamps hidden_at and nulls the
  job's raw_description to reclaim space, instead of deleting the row. Hidden
  matches leave every /jobs view but still feed the baseline and the yield
  stats. This is load-bearing, not tidiness: hard-deleting the low scorers
  moved the dev baseline 69.4 → 82.5 in one click and flattened the ranking
  the whole pipeline depends on. Don't "clean this up" back into a real DELETE.
- **Layer 4 — optional LLM re-rank** (app/llm/search_refine.py), OFF by
  default behind the `llm_search_suggestions` preference and a checkbox in the
  sidebar's Personalise panel. One complete_json call on OPENAI_MODEL_SMALL
  that *picks and generalises among mined candidates* rather than generating
  freely — a hallucinated search term wastes a whole scan, which costs far
  more than the call. Debounced (REFRESH_AFTER_NEW_MATCHES=10) and cached in
  profiles.preferences; any failure falls back to the mined list.

Verified: `python -m pytest` (132 passed, 44 new across
tests/test_search_suggest.py + tests/test_search_endpoints.py), `node --check`
on the three edited JS files, and the endpoint run against the live dev DB —
suggestions went from ["Administration Assistant", "Intermediate Software",
"Intermediate Software Engineer"] to ["Administration Assistant", "Data
Analyst", "Software Engineering"].
Not verified: the sidebar UI in a loaded Chrome extension, and the
classification selectors against a live Seek page (see CURRENT TASK above).


Everything in docs/extension-revamp-plan.md landed 2026-09-11 (see the
✅ COMPLETED entry below) except one deliberate fast-follow: §5.2's
apply-flow-specific detection (auto-filling Seek's own Apply button/flow).
That needs a live session where the user clicks through a real Seek Quick
Apply flow so the real DOM/URL can be inspected — don't guess it. The
Quick-Apply panel today is keyed off the `/job/{id}` detail page only, which
is confirmed working. Pick that up next, or a new priority the user names.


✅ COMPLETED: Extension revamp — Centrelink-ready application tracking — 2026-09-11

Implemented the full plan in docs/extension-revamp-plan.md (see there for
the detailed rationale/design) except the §5.2 fast-follow noted above.
- **Schema**: `matches.applied_at` (migration `60d65ffb16df`), status
  vocabulary documented in docs/database-schema.md.
- **Backend**: `PATCH /jobs/{id}/status` (stamps `applied_at` once, on first
  transition to `applied`), `PATCH /jobs/{id}/cover-letter` (saves
  `edited_content`, shared by the sidebar and the Quick-Apply overlay),
  `GET /jobs/suggested-searches` (pure-Python bigram/trigram mining over
  score>=75 titles, zero LLM calls, excludes phrases already in an active
  `SavedSearch`), `GET /jobs` gained `top_skills` (top-3 hard job_skills) and
  an optional `status` filter (switches sort to `applied_at desc` for the
  Applied tab/CSV export).
- **Idle loop** (`app/api/main.py::_processing_idle_loop`): reordered so the
  cover-letter phase is checked first every iteration; extraction/matching
  only runs when there's no cover-letter backlog for score>=75 matches.
- **Sidebar** (`extension/sidebar.html`/`sidebar.js`): client-side score
  tiering (blue>=90/green>=75/amber 60-74/long-tail<60, long-tail collapsed
  behind a lazy "N other roles" fold), a third Applied tab (CSV export:
  Date Applied/Job Title/Employer/Location/Source URL), a per-card "Mark
  Applied" action, a cover-letter state machine (ready/pending/none) replacing
  the old always-there ✚ button, an editable cover-letter textarea with
  Copy/Save wired to the new PATCH endpoint, a dismissible resize hint, and a
  dismissible suggested-searches banner (7-day cooldown via
  `chrome.storage.local`). Dropped `pollForCoverLetter` — SSE
  (`cover_letter_ready` → reload) now drives the ready-state transition.
- **Quick-Apply overlay** (`extension/content_script.js`): Shadow-DOM floating
  panel on `/job/{id}` pages when a cover letter already exists for that job —
  title, editable textarea, Copy/Save edits/Mark Applied. No new
  `host_permissions`, no second-hop scanning change.
- **cover_letter.py**: system prompt now asks for a `Sincerely, {name}`
  sign-off and to foreground degree/university by name for recent grads.

Verified: `python -m pytest` (72 passed), manual TestClient round-trips of
the new PATCH endpoints against the live dev DB (idempotent `applied_at`,
then reverted the test writes), `node --check` on all three edited JS files.
Not verified: the sidebar UI in an actual loaded Chrome extension (no browser
harness in this session) — recommend the user do a quick visual pass,
especially the width breakpoint and the Quick-Apply panel on a real Seek job
page, before relying on it.


✅ COMPLETED: Cover-letter generation (app/llm/cover_letter.py) — 2026-06-27

generate_cover_letter(job_id, profile_id, force) — THRESHOLD=75 quality gate,
single complete_text call (temp 0.7), self-contained prompt with all profile context
(quals, experiences + experience_skills evidence map) + job fields + match
reasoning/gaps. Idempotent upsert on UNIQUE match_id; below threshold = no-op.
Wired into _process_listing(with_cover_letter=True) so /jobs/{id}/regenerate triggers
generation after extract+match — /ingest never does. scripts/run_cover_letters.py
for dev/testing (--force flag, DailyQuotaError-stops-batch).


✅ COMPLETED: Matching validation + keyword normalisation (2026-06-23)

5 test jobs seeded (test-001 to test-005). normalise_skill() alias map added to
prefilter.py. check_matching.py diagnostic script built. Score spread: 90 points
(12→90 pre-retuning, confirmed ≥40 after). 4/5 bands verified live; test-005
pending (0/3 hard overlap in unrelated field — expected 0–24 band).
Transient-503 retry fix added to client.py. DailyQuotaError bug fixed in client.py:
429s now classified by server retryDelay (>300s = daily stop; else back off + retry).
Free-tier limit is PER-MINUTE, not per-day — daily headroom is fine; see LLM Layer.


✅ COMPLETED: LLM matching + scoring (app/llm/match.py) — verified live 2026-06-23

prefilter.py (pure-Python signals: skill match/gap, overlap %, seniority_flag,
qual_match — context for the LLM, not a gate), match.py (match_job: eager-loads
profile+job, builds text summaries, complete_json with a Pydantic MatchScore schema +
new-grad-fair prompt at temp 0.1, clamps 0–100, idempotent upsert on
UNIQUE(user_id, job_id), gaps as JSON string, status 'new'), scripts/seed_profile.py
(richer profile-1 seed) + scripts/run_matching.py. /jobs + /jobs/{id} now return gaps
as a list. Cover letters were scoped OUT of this task (now the CURRENT TASK above). All
acceptance criteria pass: 3 real jobs scored (Graduate SWE 85 > AI Lead/Engineer 65 —
new-grad fairness holds), idempotent, /jobs ranks by score. See PROGRESS.md (2026-06-23).


✅ COMPLETED: LLM extraction (app/llm/extract.py) — verified live 2026-06-23

Real Gemini extraction via the new app/llm/client.py provider abstraction
(complete_json/complete_text; RPM throttle, 429 backoff, DailyQuotaError, truststore
TLS fix), extract.py (Pydantic-schema'd structured output → job_skills + JSON
requirements + seniority/summary/key_responsibilities/extracted_at, idempotent),
scripts/run_extraction.py + scripts/check_llm.py. Schema columns added via migration
f42bcd31e385. All acceptance criteria pass; 3 real jobs extracted. KEY NOTE: Gemini
access needed the Google Cloud $300 free-trial (the plain free tier was unavailable on
the user's account; AQ.* keys are ephemeral). See PROGRESS.md (2026-06-23).


✅ COMPLETED: Chrome extension + FastAPI capture pipeline (2026-06-17)

Backend (app/api/main.py: /health, /ingest, /jobs, /jobs/{id},
/jobs/{id}/regenerate, /profile/{id}) built and verified against live uvicorn.
Manifest-V3 extension (extension/) captures real Seek DOM → /ingest → job_listings
(first live capture confirmed: 3 detail pages, full descriptions stored). app/llm/*
were stubs at that point. See PROGRESS.md for the full slim summary, including the
live-DOM selector fixes (host is au.seek.com; card is [data-testid="job-card"];
search URLs are SEO slugs) and the 1-hop scan (now standing behaviour — see the Seek
Access Policy above: links on a page the user opened are fair game, ≥5s apart, capped,
never a second hop).

✅ COMPLETED: database layer (2026-06-15)

All 11 tables (SQLAlchemy 2.0, portable), Alembic initial migration, smoke test
(evidence query + cascade delete) passing. See PROGRESS.md.


How to use this file (note for the human)


Tell Claude Code: "Implement the Current Task section of CLAUDE.md."
When a task is done, demote it to a one-line ✅ COMPLETED entry here and add the slim
block to PROGRESS.md; promote the "Next task" into a new CURRENT TASK section.
Keep the project context, networking policy, LLM layer, and schema decisions above as
permanent memory.


Environment note (this machine): pip is behind a TLS-intercepting cert — installs
need --trusted-host pypi.org --trusted-host files.pythonhosted.org. Test the API with
PowerShell Invoke-RestMethod, not curl.exe (PS mangles embedded JSON quotes).