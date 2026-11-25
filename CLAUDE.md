# CLAUDE.md — Job Application Assistant

Project context for Claude Code. Read this first every session.
docs/database-schema.md is the source of truth for the data model. If
anything here conflicts with that file, the schema doc wins.


## How this file works

- This file holds **permanent rules** plus ONE short **Now** section (about 15 lines at most).
  Nothing else: no build logs, no finished-task blocks.
- **History goes in PROGRESS.md** (newest on top, one slim block per milestone). Step-by-step
  detail goes in the plan doc's Decision log (docs/<task>-plan.md).
- **When a task finishes:** (1) add its PROGRESS.md entry, (2) delete its lines from Now,
  (3) move any rule that must outlive it into the permanent sections below, (4) put any open
  manual check on the "Open manual checks" list.
- Deferred ideas go in future_work/ (one file each, indexed in its README), not here.


## What this project is

A tool that helps a job seeker find suitable roles on Seek and drafts tailored
cover letters from the user's own profile (qualifications, experience, skills).
The user reviews and submits every application manually — the tool never
auto-submits. It starts as a local single-user app and is designed to grow
into a multi-user hosted app later. It also keeps the user's Centrelink job-search
evidence (applied dates, Quick Apply screenshots, upload-ready PDFs).

It is also the owner's practice project for agent engineering: learning where an
LLM agent's cost and quality bottlenecks are and how to optimise them.

Overview: README.md. Architecture: docs/ARCHITECTURE.md. DB design: docs/database-schema.md.


## Now (updated 2026-11-24)

- **Stage: optimising the cover-letter agent** — higher judge-v1 scores, lower cost per letter.
  Plan: docs/agent-optimisation-plan.md (DRAFT v0.1, branch `cost_perform_analysis`). Every
  paid experiment (Gemini calls, judge runs) needs the owner's go-ahead first; free analysis
  of existing data is fine. Measure on the eval set's dev split with judge v1 (rules below).
- **Deadlines:** the Gemini trial ends **2026-12-30** (Flash intro prices double 2027-01-01);
  OpenAI gpt-5-mini (a dormant fallback) shuts down 2026-12-11.

Open manual checks (the owner drives them; each list is at the end of its plan):
- Letter rewrite + letter date — docs/letter-rewrite-plan.md
- ⚙ Settings simplified — docs/settings-simplify-plan.md
- Pipeline tab — docs/pipeline-tab-plan.md
- Centrelink-ready PDFs (Quick Apply evidence part 2) — docs/centrelink-ease-apply-plan.md


## PUBLIC REPO — nothing private gets committed (owner, 2026-10-22)

This repo is PUBLIC on GitHub and is cloned by scrapers. The owner's name (Andre
Bissell) and the commit email are fine to appear. NOTHING ELSE personal may be
committed: no phone, address, date of birth, visa/work-rights details, Centrelink
or other government IDs, real cover letters, real profile/CV text, writing samples,
screening answers, screenshots, databases, API keys or GCP project ids.
- Test data and examples use fake people ("Liam Chen", "Bob", @example.com) and
  fake employers ("Acme", "LogiCo"), never the real profile.
- Real data stays in gitignored places (real.db, app.db, .env, screenshots*/,
  evals/jobs|runs|panels|candidates|work, evals/sets/** except MANIFEST.json,
  reports/, backups/, dev_data/). Before adding a new folder that holds real data,
  add it to .gitignore first.
- Before every commit, check the staged diff (`git diff --cached`) for personal
  data; if unsure whether something counts, ask the owner before committing.
- ENFORCED by git hooks (docs/private-data-guard-plan.md): pre-commit + commit-msg run
  scripts/check_private_data.py against the owner's list `.git/private-data.txt` and block
  added lines that match it, binary files and the list itself. Install once per clone:
  `python scripts/install_hooks.py` (`--check` after hooks/ or the checker change). Never
  read, edit or print the list; never `--no-verify`. The list only knows what's on it, so
  the staged-diff check above still applies.
- Already in history and accepted by the owner: the name, the old commit email, and
  the existing references to the real CV in tests/test_style_lint.py and a few docs.
  Don't add more.


## SEEK ACCESS POLICY — how this app is allowed to touch Seek

All Seek traffic comes from the Chrome extension in the user's own signed-in browser,
on the user's real IP. The backend never requests Seek. (The Playwright scraper was
abandoned: Cloudflare loops the challenge on any automated browser. The dormant proxy
code, `require_proxy()` etc., stays in the repo; nothing depends on it.)

**What the extension IS allowed to do — the 1-hop rule:**
- Read the DOM of any Seek page the user themselves opened.
- Open job links that already appear on a page the user opened, one at a time in a
  background tab (`chrome.tabs.create({active: false})`), read it, then close it. This is
  1 hop: the page the user opened → the listings linked from it.
- Paced and capped so it stays a trickle, not a crawl: ≥5s between pages
  (`SCAN_DELAY_MS = 5000`) and a per-scan cap, user-tunable in ⚙ Settings (`scan_max_pages`,
  default 10) but hard-bounded at 25 by both the API (`PreferencesUpdate le=25`) and the
  sidebar (`SCAN_PAGES_CEILING`). That ceiling is the policy cap: raise it deliberately if
  needed, don't remove it. Both constants and the scan code (`scanPage`,
  `pageCollectJobLinks`, `pageScrapeDetail`) live in extension/sidebar_scan.js;
  tests/test_scan_policy.py checks them wherever they are.
- Scan more (docs/overview-applied-rework-plan.md D7/D8): the Overview lists the top 3
  suggested searches, each with its own button. A click opens ONE Seek search page, that
  row's search (`seekSearchUrl`), in a FOREGROUND tab (`active: true`), then runs the same
  `scanPage` on THAT tab, read by its id (`chrome.tabs.get(tabId)`). The user's click opened
  it, so it is the page the user opened. The header's Scan Page still reads the active tab. A
  click never moves on to another suggestion or to Seek's page 2 (pinned by
  tests/test_scan_policy.py).

**The hard line — no second hop:**
- Never follow links found on the pages the scan itself visited. Links come only from a
  page the user opened (`parseSearchPage()` / `COLLECT_LINKS`), never from the detail pages
  the scan navigates to. No recursion, no crawler. One hop, full stop.
- No backend/agent-side requests to Seek. Do NOT use WebFetch or any server-side fetch
  against Seek; if you ever need a Seek page agent-side (e.g. au.seek.com/robots.txt),
  STOP and ask the user first.
- Keep it paced and capped. If the scan ever draws Cloudflare / anti-bot attention, pull it
  back to user-initiated opens only.

**Availability re-checks** (owner-accepted 2026-10-13): the side panel may re-visit the
`/job/{id}` page of a listing already stored in the database, to see whether it is still
advertised. It opens only that listing's own page, rebuilt from its id on Seek's own host
(`checkUrlFor`), reads only whether the description is there and the page title, and never
reads or follows a link on it. Caps, all in code and pinned by tests/test_scan_policy.py:
- Side panel only (never the backend, never background.js); one background tab at a time
  (`active: false`); one Seek page at a time across scans and checks together (`seekPace`).
- Background mode: ≥ `CHECK_DELAY_MS` (15s, never below `SCAN_DELAY_MS`) apart;
  ≤ `CHECK_SESSION_CAP` (8) pages per session; one session per 30 min while the panel is open.
- Fast mode (docs/availability-fast-mode-plan.md): automatic, the backend decides it
  (`/availability/next` `mode`) and holds its caps; only the top jobs (letter / scored 60+)
  not checked or seen live in 24 h; ≥ `CHECK_FAST_DELAY_MS` (5s, = `SCAN_DELAY_MS`) apart;
  stops at 10 seen live or `FAST_RUN_PAGE_CAP` (15) pages; at most one run per
  `FAST_RUN_EVERY` (1 h).
- Always: ≤ `CHECK_SESSION_HARD_LIMIT` (25) pages a session in the panel whatever the backend
  says; ≤ `DAILY_CAP` (100, app/availability.py) per rolling 24 hours, all checks together;
  never while a scan runs (a scan cancels the check in flight); a toggle in ⚙ Settings
  (`availability_checks_enabled`).
- Hard stop: a challenge page, or `CHECK_MISS_STREAK_LIMIT` (5) pages in a row that were not
  live (missing or unknown), pauses checks for 6 hours (fast mode too).
- A closed ad is never concluded from one empty page: it takes two separate visits (≥1 day
  apart) and a live page seen in the same session each time, so a blocked or logged-out
  browser cannot flag anything. Applied jobs are never re-checked or flagged.
- The same freshness (`FRESH_FOR`, 24h) gates all LLM work: no matching, ranking or letter
  for a job not seen live recently.

**Extension permissions:** the manifest has `<all_urls>` ONLY so `captureVisibleTab` can
screenshot Seek's Quick Apply "application sent" page automatically. It changes no Seek
traffic: content scripts still match Seek only and the extension makes no other request.
Don't use the grant for anything else; tests/test_extension_permissions.py pins this.

**Why this is defensible at this volume:** it runs in the user's real browser/session for
their own personal job search, opens a handful of listings the user could have clicked
themselves, spaced like normal browsing. It is low-volume, non-commercial, and the data is
used only for the user's own applications. Seek's ToS does prohibit automated access in
general (clause 9b), so this is a deliberate, owner-accepted personal-use trade-off — kept
small on purpose. The line that keeps it bounded is the no-second-hop rule; do not cross it.


## Tech stack

- Language: Python 3.11+
- ORM: SQLAlchemy 2.0 (typed, declarative mapped style). Migrations: Alembic.
- API: FastAPI + uvicorn (local backend for the extension)
- LLM: Gemini on Vertex AI (small/mid/strong tiers) — see the LLM layer section below
- Deps/CI: exact versions in requirements-lock.txt (frozen from .venv; regenerate with
  pip freeze from Git Bash); GitHub Actions (.github/workflows/tests.yml) runs pytest and
  node --check on windows-latest, with no secrets.
- Config: python-dotenv — app/db.py loads .env so DATABASE_URL (and secrets) can live in a
  gitignored .env file.
- DB: SQLite for local dev, Postgres-ready for hosting. The database URL comes from a
  DATABASE_URL env var, defaulting to sqlite:///app.db, so the SQLite → Postgres move is a
  config change, not a code change. Do not write SQLite-only SQL or rely on SQLite-specific
  behaviour. Keep everything portable so a Postgres DATABASE_URL works with no code changes.


## LLM layer

Active provider: **Gemini on Vertex AI**, authenticating with Application Default
Credentials (no API key — the trial project's org disallows keys). Trial ends 2026-12-30;
Flash intro prices double 2027-01-01, so re-check prices (and the plan's cost tables)
before running past the trial. OpenAI (gpt-5-nano / gpt-5-mini) and Groq remain as dormant
fallbacks in client.py. Model-choice reasoning: docs/cover-letter-loop-plan.md.

Callers name a **tier**, never a model:
  small  (GEMINI_MODEL_SMALL,  default gemini-3.1-flash-lite) — quick-screen,
         extract, match, search_refine. `complete_json` defaults to this.
  mid    (GEMINI_MODEL_MID,    default gemini-3.8-flash) — letter analysis and
         checking: analyze_job, match_profile, check_claims (small passed real
         overclaims), the agent's orchestrator turns.
  strong (GEMINI_MODEL_STRONG, default gemini-3.1-pro-preview) — cover letters.
         `complete_text` defaults to this. A *preview* model: may change; falling
         back is one env var.

Provider abstraction: ALL LLM calls go through app/llm/client.py —
`complete_json` (structured), `complete_text` (prose), `complete_tools` (one
tool-calling turn; provider-neutral `ToolSpec`, returns a `ToolStep`). Provider,
models, prices and thinking levels live in exactly ONE place. Never call a
provider SDK from extract.py / match.py / cover-letter code. Pass `task=` (and
`job_id` / `match_id` / `run_id` where known) so the usage row is labelled.

Cost control (all in client.py + app/llm/usage.py):
  * Every call writes an `llm_usage` row (tokens, thinking tokens, estimated USD).
    `cost_usd` is an estimate from the PRICES dict; Cloud Billing is the authority.
  * Budget guard: `llm_daily_budget_usd` ($5) / `llm_total_budget_usd` ($200) in
    `profiles.preferences`. At the cap, mid/strong calls raise
    `BudgetExceededError` BEFORE any request; `small` is never blocked. The idle
    loop pauses cover letters for 10 min and the sidebar shows a banner
    (`GET /llm/usage`, SSE `llm_budget_blocked`). The guard fails open if the
    usage table is unreadable. Don't add FKs from llm_usage (spend log must
    outlive the rows it describes) or purge it without keeping a running total.
  * Per-run limits (`letter_max_drafts` 3, `letter_max_tool_calls` 15,
    `llm_run_budget_usd` $0.50) are code defaults with no sidebar input; the API still
    takes them, and a run copies them into its own `state.budget`.
  * Thinking tokens bill as output and dominate small calls: GEMINI_THINKING_*
    sets the level per tier (default low/medium/high).
  * Gemini 3 is called WITHOUT `temperature` (Google: leave it at 1.0, lowering
    risks looping); the `temperature` argument is ignored there, like GPT-5.

Rate limits and workers: on HTTP 429, client.py backs off and retries (max 3);
delays > 300s are treated as daily exhaustion → DailyQuotaError. The idle loop
(`_processing_idle_loop` in app/api/idle_loop.py) runs two loops (docs/second-worker-plan.md):
letters one at a time on `worker._bg_executor` (also /regenerate, band letters, Write a letter),
and scoring (quick screen, extract, match) one job at a time on `worker._score_executor`, so new
jobs are scored while a letter is written. Each has its own pauses: the budget pause stops only
letters; the scorer backs off 3 minutes when extraction fails. A job both reach is guarded by
app/job_claims.py (the scorer skips it, /regenerate waits up to 120 s then goes ahead with a
WARNING). SQLite waits 30 s for the write lock (`busy_timeout`, app/db.py). Both loops share
the local throttle: LLM_RPM=8. Exactly two pools, both `max_workers=1` (a test guards this).

Letter engines: `letter_engine` defaults to `agent` (owner's decision, for the agent
experience); `workflow` is the fallback and must keep working through the same gap-policy
hook. Production entry points: app/llm/letter/production.py and engines.py. A run's best
draft lands in `generated_content`, clean or flagged. Pipeline settings: `letter_loop_enabled`,
`letter_loop_min_score`; the effective bar is the HIGHER of it and `auto_cover_letter_min_score`.

Env vars (gitignored .env): LLM_PROVIDER=gemini, GOOGLE_GENAI_USE_VERTEXAI=true,
GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION=global, GEMINI_MODEL_SMALL/_MID/
_STRONG, GEMINI_THINKING_SMALL/_MID/_STRONG (optional), LLM_RPM. The old
GEMINI_MODEL is still read as the small model. Verify with
`python scripts/check_llm.py` (every tier + a tool call + usage logging).
Test-only: `LLM_PROVIDER=stub` (LLM_STUB_RESPONSES / LLM_STUB_LOG) answers without a model.

Data privacy: profile data goes to Google. Verify the paid-tier / Vertex data-use
terms if it matters (not used for training at the time of writing).


## Repo map

- `app/` — backend. db.py (engine; reads DATABASE_URL), models.py, preferences, retention,
  availability, obligation, letter_bands, skill_gaps, gaps, job_claims, evidence_pdf,
  screenshots, search_suggest.
  - `app/api/` — main.py wires the app (lifespan, Origin guard, ordered routers, static
    mounts); deps.py (`get_db`); events.py (SSE); worker.py (the two worker threads +
    `_process_listing`); idle_loop.py; one router module per area.
  - `app/llm/` — client.py, usage.py, quickscreen, extract, prefilter, match, cover_letter
    (one-shot), search_refine; `letter/` (the cover-letter agent and workflow: state,
    guardrails, registry, agent, workflow, production, tools/); `skills/` (style skill).
  - `app/screening/` — Quick Apply question bank, sorting, assist, drafts.
  - `app/scraper/` — RETAINED, not in the active pipeline (selector logic reused).
- `extension/` — Manifest V3 Chrome extension: content_script.js, background.js,
  apply_flow.js (Quick Apply "application sent"), screening_assist.js, config.js,
  selectors.js (mirrors app/scraper/selectors.py), and the side panel (sidebar.html).
- `scripts/` — run_api.py, check_llm.py, letter_lab.py (letter eval harness),
  grading_panel.py (judges), eval_*.py (eval set), letter_metrics.py / letter_points.py,
  check_private_data.py + install_hooks.py, seed/backfill/report tools. `ls scripts`.
- `tests/` — pytest suite; `tests/e2e/` Playwright suites against a scratch DB.
- `evals/` — rubrics, grading standards, eval sets (real data is gitignored).
- `docs/` — plans (one per task, each with a Decision log), schema, architecture.
- `future_work/` — deferred tasks, one file each, indexed in its README.

Code gotchas:
- Patch the module that OWNS a name (`worker._bg_executor`, `idle_loop._letters_phase`, ...),
  never main; main re-exports only `app` and `get_db`.
- `GET /jobs/{job_id}` (jobs.py) must be matched after every other `GET /jobs/<literal>`
  route; tests/test_route_table.py guards the route table and OpenAPI schema.
- The side panel loads CLASSIC scripts in sidebar.html's order (config, selectors,
  screening_assist, sidebar_shared … sidebar_events, applied_record, sidebar, dashboard
  last). They share one global scope, so load order is the contract: no top-level name in
  two files, nothing used at load time from a later file. Check with
  `python scripts/js_globals.py --check` (also in pytest) and tests/e2e/sidebar_load_e2e.py.

Docs to read for each area:
| Area | Doc |
|---|---|
| Data model (wins over everything) | docs/database-schema.md |
| How the whole system fits together | docs/ARCHITECTURE.md |
| Cover-letter agent: design + Decision log | docs/cover-letter-loop-plan.md |
| Cover-letter agent in plain language | docs/cover-letter-agent-explained.md |
| Current stage: agent optimisation | docs/agent-optimisation-plan.md |
| Eval set v2 / letter metrics | docs/eval-set-plan.md, docs/letter-metrics-plan.md |
| Judge procedure and grading standard | evals/grading-panel.md, evals/grading-standard-v2.md |
| AI screeners' biases (lines we never cross) | docs/ai-recruitment-research.md |
| Quick Apply markup samples | docs/quick-apply-samples.md |
| Any finished feature | its docs/<name>-plan.md, and PROGRESS.md |


## Key schema decisions to respect (do not "improve" these away)

These were deliberate; the schema doc explains the reasoning:

- experience_skills is a pure junction (composite PK of the two FKs). It has
  no strength/relevance column — relevance depends on the job and is judged
  by the LLM at generation time, not stored.
- matches.score (0–100) is the single source of truth. There is no tier
  column — strong/medium/reach buckets are derived at display time.
- job_skills is NOT foreign-keyed to skills. Job skills are extracted from
  listings independently and matched to user skills by name at match time.
- CV lives at user level (user_cvs), never per-job. Cover letters are one
  row per match (cover_letters.match_id unique), holding both generated_content
  and edited_content with a status of draft/edited/final.
- Generation writes `generated_content` only; `edited_content` is the user's. The ONE
  exception: Rewrite (`/regenerate` with `discard_edits=true`) clears the edits once the
  new letter has landed. `cover_letters.generated_at` is never stamped by saving edits.
- Passwords are stored hashed (profiles.password_hash). Do not implement auth
  logic — just the column.
- Uniqueness constraints that must exist: profiles.email,
  skills (user_id, name), job_listings (source, source_job_id),
  matches (user_id, job_id), cover_letters.match_id.
- FK delete behaviour: ON DELETE CASCADE for user-owned and job-owned children;
  matches.cv_used_id is ON DELETE SET NULL. See schema doc per-table.
- Portability mechanics (don't "fix" these back): integer PKs use
  BigInteger().with_variant(Integer, "sqlite"); boolean server defaults use
  text("false") / text("true") (NOT 0/1, which break on a Postgres
  BOOLEAN, nor false()/true(), which don't exist in SQLite). ORM
  relationships set passive_deletes=True so deletes rely on DB-level cascade.
- "Applied" = `matches.applied_at` set (counted on its LOCAL date); `applied_via` is
  `auto` (Quick Apply sent page) or `manual` (Mark Applied). Applied matches are
  Centrelink evidence: never deleted, never hidden by bulk hide, never re-checked.
- Retention deletes by AGE, never by score (age is uncorrelated with score, so the
  suggestion miner's baseline survives). One `effective_cutoff` (122-day window +
  150-match floor) is shared by the sweep and the miner. Only `status='new'` with
  `applied_at IS NULL` is purgeable — a whitelist.
- Bulk hide is a SOFT delete (`matches.hidden_at`). Don't turn it into a real DELETE:
  hard-deleting the low scorers once moved the baseline 69.4 → 82.5 and flattened the
  search-suggestion ranking. Hidden matches still feed the baseline and yield stats.


## TWO ENVIRONMENTS — real vs test

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
sidebar's REAL/TEST pill flips it and reloads.

**CAUTION:** run_api.py runs uvicorn with reload=True, so a running REAL server picks up
every code edit and can start the letter pipeline (~20–25¢ a letter) for matches at the
bar. Keep the code importable at all times. run_api.py runs `alembic upgrade head` on
start; scripts that touch a DB need it run first.


## Testing rules

- The full `python -m pytest` must pass; no test makes a paid model call (use the stub).
- E2E suites (tests/e2e/) run on a scratch DB (`run_api.py test --db ...`) with the stub LLM.
- E2E: stub the panel's `chrome.tabs.create`. A real tab it opens can load before
  Playwright's routing attaches and reach Seek (it happened once).
- E2E: a context routed through a Python predicate pauses every request until the sync API
  is pumped, so wait with `side.wait_for_timeout`, never `time.sleep`.


## Quick Apply rules

- Each screening question is `user` (work rights, salary, notice, identity, how you heard,
  motivation: never sent to the model) or `assisted` (relates to the ad).
- Help only for jobs with a full-pipeline letter (agent/workflow); one-shot letters get no
  question help. `screening_question_help_enabled` off = no help at all, no model call.
- Choice questions show what the job wants and what the profile has; the user picks.
  Never recommend an option.
- Seek markup not covered by docs/quick-apply-samples.md needs a live check the owner
  drives — don't guess selectors. Auto-FILLING Seek's form is out of scope.


## Eval set and judge rules (docs/eval-set-plan.md)

- Set v2: 49 real ads frozen in evals/sets/v2/ (never rewrite a frozen file;
  `python scripts/eval_freeze.py --verify`). Split dev 17 / test 14 (`split.json`). A v2
  run needs `--split dev|test` or `--only`. Working files in gitignored evals/work/v2/,
  results in evals/results/v2/.
- **The test split stays closed**: it is graded once, at the end.
- **Judge v1 is frozen** (evals/results/v2/judge-v1.json). Always run
  `grading_panel.py v2-run <tag> --judge v1` with JUDGE_CLAUDE_BIN set to the pinned
  evals/work/v2/judge-bin/2.1.317/claude.exe; it refuses on any difference. Don't change the
  standard or the worked examples under v1 (rule questions wait in
  future_work/grading-standard-next-version.md), and never while a judge run is going: each
  judge reads them when its prompt is built.
- The owner's labels and answer key are the truth. The judge's run-to-run noise is up to
  1.5 points per letter: compare split means, never one letter.
- `sounds_ai` is a code count rule (`letter_metrics.stock_phrases` over `STOCK_LIMIT`); the
  phrase list and the limit never go into the standard the judge reads, and the judge
  never gets the repo.
- Writer rules must target a general failure, not particular dev letters.
- The real profile is frozen as of 2026-10-20. Strong fit = score 85+ and no must-have gap
  (`Requirement.needs_user`); partials are allowed. Main metric: the relevant score
  (Would apply = Yes ads).


## Environment note (this machine)

pip is behind a TLS-intercepting cert — installs need `--trusted-host pypi.org
--trusted-host files.pythonhosted.org`. Runtime HTTPS uses the Windows cert store via
`truststore` (client.py). Test the API with PowerShell Invoke-RestMethod, not curl.exe
(PS mangles embedded JSON quotes).
