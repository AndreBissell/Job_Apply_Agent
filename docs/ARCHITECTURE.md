# Architecture guide: Job Application Assistant

> Written as a learning guide for a new engineer reading this codebase for the first time.
> Every claim cites a file (and function/class where it helps) so you can check it yourself.
> Anything I did not confirm by reading code is marked **unverified**.
> Snapshot: branch `centerlink-preperation`, 2026-10-05, 75 commits (first commit 2026-06-15).

**Status of this document:** all six steps written. Steps 1-4 describe `centerlink-preperation` as of `b45346b`. Step 5 was written later, on branch `fix/applied-evidence-safety` after its two fixes (`49f54ee`, `046d2f9`), and records which issues are already fixed.

## Contents

- [The whole system on one page](#the-whole-system-on-one-page): start here
- [First, a correction to the mental model](#first-a-correction-to-the-mental-model)
- [Step 1: Signal vs noise](#step-1-signal-vs-noise): what is code, what is clutter
- [Step 2: High-level map](#step-2-high-level-map-layer-1): folders, components, tables, diagram
- [Step 3: Component details](#step-3-component-details-layer-2): every key file in a sentence; the most central files
- [Step 4: One job, traced end to end](#step-4-one-job-traced-end-to-end): function by function, with every DB and AI touch
- [Step 5: Review guide and cleanup list](#step-5-review-guide-and-cleanup-list): what to check when a diff touches each area; issues ranked for future branches
- [Step 6: Glossary and reading order](#step-6-glossary-and-reading-order): every project term in one place; which files to read, in what order

---

## The whole system on one page

**What it is.** A personal job-search assistant for Seek (an Australian job site) with two halves that talk over HTTP on your own machine:

| Half | Language | Lives in | Job |
|---|---|---|---|
| Chrome extension | JavaScript | `extension/` | Reads Seek pages **you** open, sends them to the server, shows results in a side panel |
| Local server | Python (FastAPI) | `app/`, started by `scripts/run_api.py` | Stores jobs, scores them with AI, writes cover letters, tracks applications |

It **never submits applications**. You do that on Seek, then press Mark Applied.

**The life of a job, in five stages** (full trace in Step 4):

```
A. START     run_api.py -> migrations -> FastAPI app -> background idle loop
B. CAPTURE   you open Seek -> content_script.js reads the page -> POST /ingest -> job_listings
C. SCORE     idle loop: quick screen (small AI) -> extract (small AI) -> match/score (small AI) -> matches
D. WRITE     score >= 85: agent loop picks tools (analyze, match, write, 3 checks, revise) -> cover_letters
             score 75-84: one-shot letter writer
E. APPLY     you edit/copy the letter, submit on Seek yourself, press Mark Applied -> matches.applied_at
```

**The ten ideas that explain most of the code**

1. **The backend never contacts Seek.** All Seek data comes from pages open in your browser (`CLAUDE.md`, "SEEK ACCESS POLICY"). The "Scan Page" button follows only links on a page you opened (1 hop), 5 s apart, capped at 25.
2. **Saving is fast; AI is slow and happens later.** `/ingest` only writes the row. A single background loop (`app/api/main.py::_processing_idle_loop`) does AI work one item at a time.
3. **One gateway to every AI model:** `app/llm/client.py`. Code asks for a *tier* (`small`, `mid`, `strong`), never a model name. The gateway handles rate limiting, retries, the dollar budget, and logs every call's cost to `llm_usage`.
4. **Cheap filters before expensive ones:** quick screen (300 characters) -> extract -> score -> letter. Each step only runs if the earlier one passed.
5. **The "agent" is the cover-letter writer.** `app/llm/letter/agent.py`: a mid-tier model chooses the next tool each turn; the tools do the work. A fixed-order twin (`workflow.py`) does the same with code choosing the order.
6. **Rules live in code, not prompts.** `app/llm/letter/guardrails.py` decides what the agent may do next (e.g. can't finish until three checks pass on the latest draft). Refused moves cost nothing and are explained back to the model.
7. **One shared state object.** `LetterState` (`state.py`) holds everything a run knows and is saved as JSON on `letter_runs`, so a run can pause (to ask you a question) and resume later.
8. **Claims must point at your profile.** The writer lists its factual claims with a pointer to a profile row; `check_claims` verifies the pointers in code and has a judge model re-read the letter.
9. **Applications are evidence.** Applied jobs feed Centrelink reporting (the Overview/Applied tabs, CSV and screenshot export), so several parts of the code avoid deleting data (soft "hide", age-only retention). Step 4.9 found two places that broke this rule; Step 5.4 tracks their fixes.
10. **Two isolated environments.** `real` (your profile, `real.db`, port 8000) and `test` (fake profile, `app.db`, port 8001). Each database has exactly one profile, id 1, and `1` is hardcoded in many places.

**Where to look first when reviewing a change** (from Step 3's measurements)

| If a diff touches… | Why it matters |
|---|---|
| `app/models.py` + `alembic/versions/` | Imported by 52 files; schema changes ripple everywhere |
| `app/llm/client.py` | Every AI call, all spending and retries |
| `app/llm/letter/state.py`, `guardrails.py`, `runner.py` | The contract and the rules of the letter pipeline |
| `app/api/main.py` | Most endpoints and the background loop in one 1,564-line file |
| `extension/sidebar.js` | The most-changed file; all UI |

**Size, honestly:** about 28k lines of real code, 17.7k of tests, 8k of docs. The rest of the "85k" is mostly gitignored experiment output in `evals/`.

---

## First, a correction to the mental model

The project is described as "an agent that automatically applies to jobs". **It does not submit applications.** I searched `extension/` for anything that submits a form or clicks Seek's Apply button and found nothing. The only `.click()` calls are for downloading CSV files and switching tabs (`extension/dashboard.js:568`, `extension/sidebar.js:143`, `:1040`, `:2555`, `extension/content_script.js:321`).

What it actually is: a **job-search assistant** made of two programs.

1. A **Chrome extension** that reads Seek pages you open yourself and sends them to a local server.
2. A **local Python server** that scores each job against your profile, writes a tailored cover letter, and helps with Quick Apply questions.

You read, edit and submit every application yourself, then press "Mark Applied" to log it. This is a deliberate rule in `CLAUDE.md` ("the tool never auto-submits"), and the "Applied" log exists as evidence for Centrelink (Australian unemployment benefit) job-search obligations.

This matters for Step 4: there is no "submit" step to trace. The end of the flow is the letter landing in the sidebar and you pressing Mark Applied (`PATCH /jobs/{id}/status`, `app/api/main.py::update_status`).

---

# Step 1: Signal vs noise

## 1.1 Line counts

Counted with my own script over every file that is **tracked by git or untracked-but-not-ignored** (215 files). Lines = newline characters, so blank lines and comments are included.

| Category | Lines | Files | What is in it |
|---|---:|---:|---|
| **Tests** | 17,689 | 33 | `tests/test_*.py` (31 files) plus `tests/e2e/` (2 browser-driven scripts) |
| **Source: backend** (`app/`) | 15,473 | 66 | Python, plus one HTML page (`app/static/profile_ui.html`, 802 lines) |
| **Source: extension** | 6,061 | 10 | JavaScript and HTML for the Chrome extension |
| **Source: scripts** | 5,391 | 22 | Command-line tools: seeds, batch runners, eval harness |
| **Migrations** | 990 | 19 | `alembic/versions/*.py`: one file per schema change |
| **Alembic plumbing** | 94 | 2 | `alembic/env.py`, `script.py.mako` |
| **Docs / planning** | 8,196 | 51 | `docs/`, `future_work/`, root `*.md`, `evals/*.md`, the style `SKILL.md` |
| **Data / JSON** | 2,513 | 5 | `evals/screening/set.json` (1,241), `tests/fixtures/quick_apply_samples.json` (1,033), `data/test_profile.json`, `extension/manifest.json`, `.claude/settings.json` |
| **Config** | 360 | 6 | `alembic.ini`, `requirements.txt`, `.env.example`, `.gitignore`, two style word lists |
| **Total (non-ignored)** | **56,767** | **215** | |

**Hand-written code that runs (source + migrations + plumbing): about 28,000 lines.** Tests are another 17,700, so roughly 0.63 lines of test per line of source. That is a healthy ratio. There are no lock files (no `package-lock.json`, no `poetry.lock`); dependencies are listed in `requirements.txt` only.

## 1.2 Where "85k" probably comes from (unverified)

I can't know how you counted, but there is a large pile of text the git repo mostly ignores:

| Local-only (gitignored) | Lines | Files | Notes |
|---|---:|---:|---|
| `evals/runs/` | 64,458 | 275 | Output of cover-letter experiments (letters, graded results) |
| `evals/panels/` | 19,258 | 24 | Blind grading packets |
| `evals/jobs/` | 756 | 54 | Saved job-ad text used by the evals |
| **Subtotal** | **~84,500** | 353 | Very close to 85k. Likely the source of the number. |

`.gitignore` lines for these: `evals/jobs/`, `evals/runs/`, `evals/panels/`, `evals/eval.db`, `evals/set.json`. Only the rubric docs and the short result summaries in `evals/results/` are committed.

Other things on disk that are not code:

- **Databases (binary, not line-countable):** `app.db` 416 KB (the fake "test" profile), `real.db` 692 KB (the real profile, contains personal data), `evals/eval.db` 2.2 MB (scratch copy for experiments). All gitignored.
- **`.venv/`**: 10,908 files of installed third-party packages. Not yours.
- **`backups/`**: 17 files, old copies of databases and eval folders. Gitignored.
- **Caches:** `.pytest_cache/`, `.ruff_cache/`, `__pycache__/` folders. Generated by tools.

## 1.3 What I'm excluding from the deeper analysis (and why)

| Excluded | Why |
|---|---|
| `.venv/`, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/` | Generated or third-party. Not your code. |
| `*.db` files, `backups/` | Data, not code. |
| `evals/runs/`, `evals/panels/`, `evals/jobs/`, `evals/results/` | Experiment output (the biggest "noise" source). Explains *how decisions were made*, not how the program works. |
| `docs/*-plan.md`, `PROGRESS.md`, `future_work/` | Planning history. I use them only to confirm intent. Where they disagree with code, I trust the code. |
| `tests/fixtures/`, `evals/screening/set.json`, `data/test_profile.json` | Static sample data. |
| `app/scraper/` (~900 lines) and `scripts/run_scrape.py`, `scripts/scrape_interactive.py`, `scripts/explore_seek.py` | **Dormant.** An older Playwright scraper that Cloudflare blocked. Kept in the repo but not part of the running system (`CLAUDE.md`, "SEEK ACCESS POLICY"). I'll mention it but not trace it. |
| `alembic/versions/*` | Included as a list of schema history, not read line by line. |

## 1.4 The largest files (where complexity lives)

| Lines | File | Role |
|---:|---|---|
| 2,723 | `extension/sidebar.js` | The whole side-panel UI in one file |
| 1,761 | `scripts/letter_lab.py` | Cover-letter experiment harness (dev tool, not runtime) |
| 1,564 | `app/api/main.py` | The server: most endpoints plus the background loop |
| 980 | `extension/content_script.js` | Code injected into Seek pages |
| 922 | `app/llm/client.py` | The one gateway to the AI models |
| 785 | `app/models.py` | All database tables |
| 711 | `app/screening/assist.py` | Quick Apply question help |

---

# Step 2: High-level map (Layer 1)

## 2.1 Plain-English definitions (first use)

- **Backend / API / endpoint**: a program that waits for requests from another program and answers them. An *endpoint* is one address it answers, like `GET /jobs`. Here the backend is FastAPI (a Python web framework), run by uvicorn (the web server).
- **Chrome extension (Manifest V3)**: a small add-on to Chrome. This one has a *content script* (JavaScript injected into Seek pages), a *service worker* (`background.js`, a background helper), and a *side panel* (the sidebar UI).
- **ORM**: a library (here SQLAlchemy) that lets you work with database rows as Python objects. `app/models.py` defines them.
- **Migration**: a versioned script that changes the database structure (add a column, etc.). Tool: Alembic.
- **LLM**: large language model (the AI). Here Google's Gemini, called through Google Cloud's Vertex AI.
- **Tier**: this project never names a model in code; it asks for `small`, `mid` or `strong` (cheap to expensive) and `app/llm/client.py` maps the tier to a model.
- **SSE (server-sent events)**: a one-way live channel from server to browser. The server uses it to tell the sidebar "a letter is ready".

## 2.2 Folder structure

```
Job_Apply_Agent/
  app/                  Backend (Python). The part that thinks.
    api/                  HTTP endpoints (FastAPI)
    llm/                  Everything that calls an AI model
      letter/               The cover-letter pipeline (agent, workflow, tools, guardrails)
        tools/                One file per tool the pipeline can use
      skills/               Writing-style rule files fed to the letter writer
    screening/            Quick Apply question bank, sorting, help, drafts
    scraper/              DORMANT old Playwright scraper
    static/               One HTML page: the profile editor
    models.py             Database tables (18)
    db.py                 Database connection + environment switch
    (gaps, obligation, preferences, retention, screenshots, search_suggest .py)
                          Smaller single-purpose modules (see 2.3)
  extension/            Chrome extension (JavaScript, no build step)
  alembic/              Database migrations (versions/ = 19 files)
  scripts/              Command-line tools (run server, seed data, batch jobs, evals)
  tests/                pytest tests; e2e/ = browser-driven checks
  docs/                 Plans and design docs (database-schema.md is the data-model reference)
  evals/                Cover-letter experiments (mostly gitignored output)
  future_work/          Parked ideas, one file each
  backups/ dev_data/ data/   Local scratch (gitignored, except data/test_profile.json)
  CLAUDE.md             Instructions for the AI assistant that built this (see note below)
  PROGRESS.md, README.md   History and usage notes
  real.db / app.db      SQLite database files (gitignored)
  requirements.txt, alembic.ini, .env.example, .env
```

> **About `CLAUDE.md`:** it is both project documentation and the instruction file the AI coding assistant reads each session. It is long (711 lines) and half of it is a changelog. When it conflicts with the code, believe the code. I found at least two stale spots (listed in 2.7).

## 2.3 Major components

### Entry points (ways the system starts)
| Entry point | What starts | File |
|---|---|---|
| `python scripts/run_api.py real` or `test` | The backend server. **The main entry point.** Applies migrations, then starts uvicorn on port 8000 (real) or 8001 (test). | `scripts/run_api.py::main` |
| Opening a Seek page in Chrome | The content script runs on every Seek page | `extension/manifest.json` (`content_scripts`), `extension/content_script.js` |
| Clicking the extension icon | Popup, then the side panel | `extension/popup.js`, `extension/sidebar.html` |
| Server startup (`lifespan`) | The **background idle loop** that processes jobs with the AI | `app/api/main.py::lifespan` starts `_processing_idle_loop` |
| Other scripts | One-off tools | `scripts/*.py` (see 2.3, Scripts) |

### The "agent loop" and where it sits
There are really two layers of automation; do not confuse them.

1. **The idle loop** (`app/api/main.py::_processing_idle_loop`): a plain `while True` that wakes every ~20 seconds and does the next piece of AI work. Order: retention cleanup, then cover letters, then quick-screen, then extract+match. This is a **job queue**, not an AI agent.
2. **The cover-letter agent** (`app/llm/letter/agent.py::run_agent`): the actual "agent". An AI model is asked repeatedly "which tool next?" until the letter is finished. Its fixed-order twin is `app/llm/letter/workflow.py::run_workflow`. `app/llm/letter/engines.py` picks which one; `letter_engine` in preferences defaults to `agent`. The idle loop reaches it via `app/llm/letter/production.py::next_work` / `run_work`.

### Tools (what the agent can call)
Plain Python functions in `app/llm/letter/tools/`, registered in `app/llm/letter/registry.py`:

| Tool | Does |
|---|---|
| `analyze_job` | Turns a job ad into a requirements checklist |
| `match_profile` | Finds profile evidence for each requirement |
| `generate` (`generate_letter`) | Writes draft 1 (the "strong" model) |
| `check_claims`, `check_requirements`, `style_lint` | Three checks on a draft (claims true? requirements covered? sounds human?) |
| `revise` | Fixes a draft where checks failed |
| `answer_screening`, `suggest_learning`, `suggest_resume_tweaks` | Extras beside the letter |
| ask-the-user flow | Not a file in `tools/`: `app/llm/letter/gap_policy.py` + `answers.py` pause the run and ask you |

Rules the tools must obey live in code, not in prompts: `app/llm/letter/guardrails.py`. State shared between tools: `app/llm/letter/state.py::LetterState`. Every tool call is logged by `app/llm/letter/runner.py::execute_tool`.

### Database and schema
Defined in `app/models.py`; connection in `app/db.py`; changes in `alembic/versions/`. SQLite locally, written to be Postgres-portable. **18 tables** (the docstring at the top of `models.py` still says "Eleven", which is stale):

*Profile side (who you are)*
| Table | Stores |
|---|---|
| `profiles` | The user: name, contact, summary, target role, `preferences` (a JSON blob of settings), `writing_sample` |
| `saved_searches` | Search criteria (used by the dormant scraper) |
| `qualifications` | Degrees, certificates |
| `experiences` | Jobs, projects, volunteering |
| `skills` | One row per skill name per user |
| `experience_skills` | Junction: which skills belong to which experience (no extra columns on purpose) |
| `user_cvs` | CV text/file path |

*Job side (what Seek showed you)*
| Table | Stores |
|---|---|
| `job_listings` | Every captured job, shared pool: title, company, `raw_description`, AI-extracted fields, the cached `requirements_checklist` |
| `job_skills` | Skills the AI extracted from a listing (not linked to `skills` by key) |

*Bridge (you x a job)*
| Table | Stores |
|---|---|
| `matches` | One per profile+job: `score` (0-100), reasoning, gaps, `status`, `applied_at`, `interview_at`, `hidden_at`, screenshot evidence |
| `cover_letters` | One per match: `generated_content`, `edited_content`, status |

*Cover-letter pipeline bookkeeping*
| Table | Stores |
|---|---|
| `letter_runs` | One execution of the pipeline: engine, status, saved `LetterState` JSON, cost |
| `letter_run_steps` | One row per tool call within a run |
| `gap_decisions` | Skills you told the app you don't have (remembered "No"s = your to-work-on list) |
| `gap_sightings` | Each ad that asked for one of those skills |

*Quick Apply questions*
| Table | Stores |
|---|---|
| `screening_questions` | Global bank of employer questions (shared across jobs), with how each was classified |
| `job_screening_questions` | Which questions appeared on which job, in order; also any saved draft answer |

*Cost tracking*
| Table | Stores |
|---|---|
| `llm_usage` | One row per AI call: tokens, estimated dollars. Feeds the budget limit. Deliberately has no foreign keys so spend history survives deletions. |

Non-obvious design choices documented in `CLAUDE.md` and `docs/database-schema.md`: `matches.score` is the single source of truth (no stored "tier"); bulk "hide" is a soft delete (`matches.hidden_at`) to keep statistics stable; applied matches are never purged (Centrelink evidence).

### External APIs and integrations
| What | How | Where | Status |
|---|---|---|---|
| **Gemini on Google Vertex AI** | `google-genai` SDK, authenticated with Google "Application Default Credentials" (no API key) | `app/llm/client.py` (`_get_gemini_client`, `_generate_gemini`) | **Active** |
| OpenAI, Groq | Alternative providers behind the same functions | `app/llm/client.py` | Dormant fallbacks |
| **Seek** (au.seek.com) | **Never called by the backend.** Only the extension reads pages *you* opened in your own browser. | `extension/` | Active, by policy (`CLAUDE.md`, "SEEK ACCESS POLICY") |
| Playwright (headless browser) | Old scraper | `app/scraper/` | Dormant (blocked by Cloudflare) |
| Chrome APIs | `chrome.runtime`, `chrome.storage`, `chrome.tabs`, `sidePanel` | `extension/` | Active |

### Config and secrets
- **Environment variables** loaded from a `.env` file by `python-dotenv`: `app/db.py` calls `load_dotenv()` at import. Key variables are listed in `.env.example` (`DATABASE_URL`, `LLM_PROVIDER`, `GOOGLE_CLOUD_PROJECT`, model names per tier, `LLM_RPM`).
- `.env` is gitignored and **not tracked** (verified: `git ls-files .env` prints nothing). My check read variable *names* only; the file exists locally and contains entries for Google, Groq and OpenAI keys. I did not print any values.
- **Real vs test environment:** `scripts/run_api.py` sets `DATABASE_URL` and `APP_ENV` *before* importing app code, so it overrides `.env`. `app/db.py::app_env()` defaults to `"test"` so tests and ad-hoc runs can never touch the real database.
- **User-tunable settings** (letter engine, budgets, thresholds, feature toggles) live as JSON in `profiles.preferences`, read/written through `app/preferences.py` (`get_preferences`, `set_preferences`, `LIMIT_BOUNDS`).
- **Spend limits:** $5/day and $200 total by default, enforced in `app/llm/client.py::_check_budget` with totals from `app/llm/usage.py`.
- **Who may talk to the server:** it binds to `127.0.0.1` only (`scripts/run_api.py`). CORS is wide open (`app/api/main.py`, `allow_origins=["*"]`), with a code comment saying that is a local-dev choice.

### UI / API layer
- **Server**: `app/api/main.py` (core endpoints: `/ingest`, `/jobs`, `/jobs/{id}/...`, `/profile/{id}`, `/events` for SSE, `/health`) plus four routers: `letters.py` (questions/answers, to-work-on list), `screening.py` (Quick Apply), `obligation.py` (Centrelink dashboard data), `profile_ui.py` (serves the profile editor page and its data).
- **Extension UI**: `sidebar.html` + `sidebar.js` (tabs Overview / Jobs / Applied / Profile), `dashboard.js` (Overview + Applied tabs), `screening_assist.js` (Quick Apply help, drawn both on the Seek page and in the sidebar), `popup.html/js`.
- **Profile editor**: `app/static/profile_ui.html` served at `/profile-ui`.
- No build step, no framework: plain JavaScript files.

### Scripts (`scripts/`)
- **Run:** `run_api.py` (start server).
- **Seed / load data:** `seed_profile.py`, `seed_saved_search.py`, `seed_test_jobs.py`, `load_test_profile.py`.
- **Batch AI runners:** `run_extraction.py`, `run_matching.py`, `run_quickscreen.py`, `run_cover_letters.py`.
- **Checks:** `check_llm.py`, `check_matching.py`, `check_retention.py`, `smoke_test.py`.
- **Eval harnesses:** `letter_lab.py`, `grading_panel.py`, `screening_eval.py`.
- **Reports/repairs:** `gap_report.py`, `export_question_bank.py`, `backfill_company.py`.
- **Dormant scraper tools:** `run_scrape.py`, `scrape_interactive.py`, `explore_seek.py`.

### Tests
- `tests/test_*.py` (pytest): letter engine and tools, screening, retention, API endpoints, the LLM client, and so on.
- `tests/e2e/`: `quick_apply_e2e.py`, `dashboard_e2e.py` drive the real extension pages in a browser against a scratch database with a fake LLM (`LLM_PROVIDER=stub`).
- Documented count: 1,497 tests (`CLAUDE.md`). **Unverified**: I did not run the suite (read-only task).

## 2.4 How the pieces connect

```
 YOU, browsing Seek in Chrome
   |
   |  (1) you open a search page or a job page
   v
+--------------------------------------------------------------+
| CHROME EXTENSION  extension/                                 |
|  content_script.js  reads the page DOM (no request to Seek)  |
|        |                                                     |
|        | chrome.runtime message                              |
|        v                                                     |
|  background.js  (service worker; relays calls to localhost)  |
|                                                              |
|  sidebar.html/js + dashboard.js   <- side panel you read     |
|  screening_assist.js              <- Quick Apply help        |
+--------------+---------------------------^-------------------+
               | HTTP (JSON)               | HTTP + SSE live events
               | POST /ingest, PATCH ...   | GET /jobs, /events ...
               v                           |
+--------------------------------------------------------------+
| BACKEND  app/api/  (FastAPI on 127.0.0.1:8000 real / 8001    |
|          test)   main.py + letters/screening/obligation/     |
|          profile_ui routers                                  |
|                                                              |
|   request handlers          IDLE LOOP (_processing_idle_loop)|
|   (fast reads/writes)       one worker thread, ~20s pace:    |
|        |                      1. retention sweep (daily)     |
|        |                      2. cover letters  --------+    |
|        |                      3. quick-screen           |    |
|        |                      4. extract + match        |    |
+--------+---------------------------+--------------------|----+
         |                           |                    |
         |                           v                    v
         |                  app/llm/  quickscreen,   app/llm/letter/
         |                  extract, match, prefilter   production -> engines
         |                           |                  -> agent (or workflow)
         |                           |                  -> tools/* + guardrails
         |                           |                       |
         |                           +---------+-------------+
         |                                     v
         |                          app/llm/client.py  (ONE gateway)
         |                          tiers, retries, budget guard,
         |                          logs every call to llm_usage
         |                                     |
         |                                     v
         |                          Gemini on Vertex AI (Google)
         v
+--------------------------------------------------------------+
| DATABASE  SQLite: real.db (real) / app.db (test)             |
| app/models.py (18 tables), app/db.py, alembic/versions/      |
+--------------------------------------------------------------+
```

Key facts the diagram encodes:

- **Seek traffic only ever comes from your browser.** The backend makes no requests to Seek (`CLAUDE.md` policy; the scraper that did is dormant).
- **The extension never talks to the server directly from the Seek page.** `extension/content_script.js::backendFetch` sends a message to `background.js`, which makes the call (reason in the code comment: Chrome blocks a public web page from reaching localhost).
- **AI work is not done inside request handlers.** `/ingest` just saves the job (`app/api/main.py::ingest`). The idle loop picks it up later on a single background thread, so only one AI call chain runs at a time.
- **One gateway to the AI.** Everything goes through `app/llm/client.py`; no other file imports a provider SDK (a rule stated in `CLAUDE.md`; **unverified** that it holds everywhere, I will check in Step 5).
- **Two databases, never mixed.** One profile (id 1) per database; `profile_id=1` is hardcoded in many places (`app/api/main.py` idle loop, `extension/sidebar.js` `PROFILE_ID`).

## 2.5 The job's life, in one line

`open Seek page` -> `content_script` captures -> `POST /ingest` -> row in `job_listings` -> idle loop: `quickscreen` (cheap skip?) -> `extract_job` -> `match_job` (score) -> if score is high enough: letter pipeline -> `cover_letters` row -> SSE `cover_letter_ready` -> sidebar shows it -> you edit, copy, apply on Seek yourself -> **Mark Applied** -> `matches.applied_at`.

(Step 4 walks this file by file.)

## 2.6 Things to know before you read any code

- **Hardcoded `1`s:** profile id 1 is assumed throughout. Fine for single-user; a real problem if this ever becomes multi-user. The code says so (`CLAUDE.md`, "TWO ENVIRONMENTS").
- **Comments are very long and reference plan sections** like "plan §5.5" or "Phase 7c". Those point into `docs/cover-letter-loop-plan.md` (1,244 lines). You do not need to read it to follow the code.
- **Big files are the exception, not the rule:** most `app/llm/letter/` files are 100-300 lines. `app/api/main.py`, `extension/sidebar.js` and `app/llm/client.py` are the three that need patience.

## 2.7 Mismatches I found while mapping (full triage comes in Step 5)

- `app/models.py` docstring says "Eleven tables"; there are 18.
- `app/llm/__init__.py` docstring says it is a "stub package for now"; it is the whole LLM layer.
- `requirements.txt` comments call OpenAI the "active provider"; `CLAUDE.md` and `.env.example` say Gemini on Vertex is active, OpenAI dormant.
- `.env.example` documents a *mandatory* scraper proxy; `CLAUDE.md` says no proxy is needed and that code is dormant.
- The local `.env` has older variable names (e.g. `GEMINI_MODEL`, `OPENAI_MODEL_SMALL`) than `.env.example` (`GEMINI_MODEL_SMALL/_MID/_STRONG`). `CLAUDE.md` says the old name is still read; **unverified** for the others.
- `.claude/settings.json` is tracked by git and holds a long list of past permission approvals from one machine (it includes a local proxy IP). Not a secret, but it is machine-specific noise in the repo.
- The extension has **not** been verified in a loaded Chrome for several features, per `CLAUDE.md` ("NOT verified live on Seek").

---

# Step 3: Component details (Layer 2)

Descriptions come from reading each file's header docstring and, for the central files, their code. Where I only read the docstring and not the body, it says so under "Confidence" at the end of this step.

## 3.1 Database layer

| File | Responsibility |
|---|---|
| `app/db.py` | Reads `DATABASE_URL`, creates the engine and `SessionLocal` (a factory for database sessions), defines `Base` (parent of all tables) and `app_env()` (real vs test). Turns on SQLite foreign-key enforcement via an event listener. |
| `app/models.py` | All 18 tables as Python classes. Also encodes portability tricks (`BIG_INT_PK`, `text("false")` defaults) so the same code works on SQLite and Postgres. |
| `alembic/versions/*.py` | One migration per schema change; `cf7f9fc0ffc2_initial_schema.py` is the start. `scripts/run_api.py` runs `alembic upgrade head` on every launch. |
| `docs/database-schema.md` | Written reasoning for the schema. `CLAUDE.md` calls it the source of truth. |

## 3.2 Backend API (`app/api/`)

| File | Responsibility |
|---|---|
| `main.py` | Creates the FastAPI `app`; defines the idle loop, SSE broadcasting, `/ingest`, `/jobs*`, `/profile/{id}`, preferences, screenshot upload, evidence export, search suggestions; mounts the routers and static folders. **Does too much** (endpoints, background scheduling, helper logic in one 1,564-line file). |
| `letters.py` | Endpoints for the "needs your answer" questions from the letter agent, and the to-work-on list. |
| `screening.py` | Endpoints to store captured Quick Apply questions, fetch help for them, and request a draft answer. |
| `obligation.py` | `GET /obligation` (progress per monthly period) and `PATCH /jobs/{id}/interview`. |
| `profile_ui.py` | Serves the profile editor page and reads/saves the whole profile (`/profile-ui/data`). |

## 3.3 Job pipeline: capture, filter, score (`app/llm/`, non-letter)

| File | Responsibility |
|---|---|
| `client.py` | **The AI gateway.** `complete_json` (structured data), `complete_text` (prose), `complete_tools` (one tool-calling turn). Handles tiers to models, rate limiting (`_throttle`), retries (`_with_retry`), the budget check (`_check_budget`), cost logging (`_record_usage`) and the providers. Also contains a test-only `stub` provider. |
| `usage.py` | Reads `llm_usage` to compute spend vs budget. Split from `client.py` so the API can use it without loading AI SDKs. |
| `quickscreen.py` | Cheap first pass on a short excerpt; skips jobs in obviously wrong fields before the costly steps. |
| `extract.py` | Pulls structured fields (skills, seniority, summary) out of a job ad into `job_skills` and `job_listings`. |
| `prefilter.py` | Pure-Python signals (skill overlap etc.) handed to the scorer as context. No AI, no DB writes. |
| `match.py` | Scores a job against the profile (0-100), writes `matches`. |
| `cover_letter.py` | The **old one-shot** letter writer. Still used for lower-scored matches and as the fallback when the new pipeline is switched off. |
| `search_refine.py` | Optional AI re-rank of suggested searches. Off by default. |

## 3.4 Cover-letter pipeline (`app/llm/letter/`)

*Control flow*
| File | Responsibility |
|---|---|
| `production.py` | What the idle loop and `/regenerate` call: `next_work` (what to do next), `run_work`, `generate_for`, `land_letter`, `recover_orphaned_runs`. The glue to the real app. |
| `engines.py` | `run_letter` / `resume_letter`: choose agent vs workflow from preferences. |
| `agent.py` | `run_agent`: asks a mid-tier model which tool to call next; refusals from guardrails are fed back as the next turn's result. |
| `workflow.py` | `run_workflow`: the same tools in fixed order (the fallback and the baseline the agent is compared with). |
| `outcome.py` | How a run starts and ends (`open_run`, `conclude`, `LetterResult`), shared by both engines. |
| `registry.py` | Maps each tool name to its function, the description shown to the model, and a guardrail gate. |
| `runner.py` | `execute_tool`: runs one tool, times it, logs a `letter_run_steps` row, saves the run state. |

*Shared state and rules*
| File | Responsibility |
|---|---|
| `state.py` | `LetterState`: the one object every tool reads and writes, plus the resolver that checks "evidence pointers" (references from a claim back to a profile row). |
| `guardrails.py` | The rules, in code: what must be covered, what may be used, what must not be claimed; gates such as "no drafting with an undecided must-have gap" and the draft cap. |
| `rubric.py` | The checks that need no judgement; shared with the eval scripts. |
| `style.py` + `skills/cover_letter_style/` | Builds the style guide and your-voice text for the writer prompt; word lists in `.txt` files. |
| `side_outputs.py` | When the three extra tools may run. |
| `view.py` | Read-only summaries of runs for the sidebar. |

*Asking the user*
| File | Responsibility |
|---|---|
| `gap_policy.py` | Decides what to do with a must-have skill the profile doesn't show: use a remembered "no", or pause and ask. |
| `answers.py` | Handles your Yes/No answers, proposes profile rows to add, and creates them on confirm. |

*Tools (`tools/`)*: `analyze_job`, `match_profile`, `generate`, `revise`, `check_claims`, `check_requirements`, `style_lint`, `answer_screening`, `suggest_learning`, `suggest_resume_tweaks` (one file each; roles in the table in 2.3).

## 3.5 Quick Apply question help (`app/screening/`)

| File | Responsibility |
|---|---|
| `identity.py` | Works out which question this is (Seek ids or a text fingerprint). |
| `sort.py` | Classifies a question (your own info vs related to the ad) using fixed rules, no AI. |
| `bank.py` | Saves captured questions to the shared bank (`record_job_questions`). |
| `classify.py` | Last-resort AI classification for questions the rules couldn't sort; once per bank row. |
| `assist.py` | Builds the help view: what the employer wants beside what your profile backs. Only for jobs with a full-pipeline letter. |
| `drafts.py` | Drafts one answer on click (mid-tier model); code checks flag unbacked claims. |

## 3.6 Supporting backend modules (`app/*.py`)

| File | Responsibility |
|---|---|
| `preferences.py` | Settings stored as JSON in `profiles.preferences`: defaults, bounds, and typed getters (`letter_settings`, `obligation_settings`). |
| `retention.py` | Deletes old unapplied matches (age-based, never score-based) and shapes the data the search miner uses. Applied matches are never purged. |
| `screenshots.py` | Downscales and expires screenshot evidence files. |
| `search_suggest.py` | Mines "what to search next" from your score history, no AI. |
| `gaps.py` | Remembered "no" answers and the to-work-on list. |
| `obligation.py` | Centrelink period date maths. Pure functions, no DB. |

## 3.7 Chrome extension (`extension/`)

| File | Responsibility |
|---|---|
| `manifest.json` | Declares permissions, which sites the content scripts run on (`au.seek.com`, `www.seek.com.au`), and which localhost ports are reachable. |
| `config.js` | Picks backend URL (real:8000 or test:8001) from `chrome.storage.local`; loaded first everywhere. |
| `selectors.js` | The CSS selectors for Seek's page elements (must be kept in sync with `app/scraper/selectors.py`). |
| `content_script.js` | Runs on Seek pages. `parseSearchPage` / `parseDetailPage` read the DOM, `ingest` sends data, `maybeInjectQuickApply` shows the letter panel, `parseQuestionnaire` captures Quick Apply questions, `main` is the page-type router. |
| `background.js` | Service worker. Relays localhost calls for content scripts (`BACKEND_FETCH`, allow-listed to localhost ports 8000/8001 only), takes screenshots, counts captures for the icon badge. |
| `sidebar.html` / `sidebar.js` | The side panel UI: 83 top-level functions in one file. Job list, cards, tabs, profile, scanning pages. |
| `dashboard.js` | Overview and Applied tabs. Shares globals with `sidebar.js` (loaded after it; relies on its functions). |
| `screening_assist.js` | Draws Quick Apply help in two places (on-page overlay and sidebar card). |
| `popup.html` / `popup.js` | Small popup: backend health, session count, "open side panel". |

## 3.8 Scripts, tests, evals

- **`scripts/letter_lab.py`** (1,761 lines): the experiment harness for comparing letter engines. Developer tool, not part of the running app.
- **`scripts/run_api.py`**: the launcher. The only script a normal run needs.
- **`tests/`**: roughly one test file per backend module, named after it (`test_letter_agent.py` tests `agent.py`). The biggest are `test_screening_assist.py` (1,390 lines) and `test_letter_side_engines.py` (1,060).
- **`tests/e2e/`**: browser-driven scripts, run by hand, not by plain `pytest` (**unverified**: I did not check how they are invoked beyond their headers).

## 3.9 Which files are most central

Two measures. **Change frequency** = number of commits touching the file (only 75 commits exist, so treat as rough). **Import fan-in** = how many other `app/` and `scripts/` files import it, counted with a small script over Python `import` statements (JavaScript not measured).

| File | Commits | Imported by | Reading |
|---|---:|---:|---|
| `app/models.py` | 16 | **52** files (+30 test files) | The data model. Almost everything depends on it. A change here ripples everywhere. |
| `app/llm/client.py` | 9 | **31** | The AI gateway; money and reliability both flow through it. |
| `app/db.py` | n/a (stable) | **26** (+25 tests) | Connection setup. Rarely changes, breaks everything if wrong. |
| `app/llm/letter/state.py` | 6 | **26** (+18 tests) | `LetterState`: the contract between all the letter tools. |
| `app/llm/letter/runner.py` | 5 | **20** | Every tool call goes through it. |
| `app/preferences.py` | 9 | **13** | Settings; changed whenever a feature gets a toggle. |
| `app/llm/letter/guardrails.py` | 5 | **12** | The safety rules. |
| `app/api/main.py` | **18** | few (imported mainly by tests) | Nothing depends on it, but it is edited constantly because it holds the endpoints. A magnet for merge conflicts. |
| `extension/sidebar.js` | **22** | n/a | **The most-changed file in the repo.** Every UI feature touches it. |
| `extension/sidebar.html` | 21 | n/a | Changes together with `sidebar.js`. |
| `scripts/letter_lab.py` | 14 | n/a | Heavily edited dev tool. |
| `extension/content_script.js` | 10 | n/a | Page capture and Quick Apply. |
| `.claude/settings.json` | 11 | n/a | Changes a lot only because it logs tool approvals (noise). |

**Takeaway for review:** the files where a diff should get the most scrutiny are `models.py` (plus its migration), `client.py`, `state.py`/`guardrails.py`, and `main.py`. Files that change a lot but break little: `sidebar.*`, `letter_lab.py`.

Also: `PROGRESS.md`, `CLAUDE.md`, `README.md` change in nearly every commit (excluded from the table above); they are a running log rather than code.

## 3.10 Confidence for this step

- **Read in full or in substantial part:** `app/db.py`, `app/models.py`, `scripts/run_api.py`, `app/api/main.py` (lines 1-375 and the `/ingest` / `_process_listing` region), `extension/background.js`, `extension/manifest.json`, `.env.example`, `requirements.txt`.
- **Described from module docstrings and function lists only (not read line by line):** everything in `app/llm/letter/`, `app/screening/`, `app/retention.py`, `app/search_suggest.py`, `extension/sidebar.js`, `extension/dashboard.js`, `extension/screening_assist.js`. The docstrings in this repo are detailed, but a docstring can be out of date, so treat those rows as **unverified against the code body**. Step 4 reads the letter path properly.
- The import counts exclude JavaScript and exclude dynamic imports inside functions that my script could not resolve; the real numbers may be slightly higher.

---

# Step 4: One job, traced end to end

**What "end" means here.** The code never submits an application (see the correction at the top). So this traces one job from server start to the moment you press **Mark Applied** after submitting on Seek yourself.

**How to read the tags**
- `[DB read]` / `[DB write]`: the database is read or changed (table names given).
- `[AI small]` / `[AI mid]` / `[AI strong]`: an AI call at that tier. Every one goes through the same path, described once in 4.7.
- `[no AI]`: plain code, no model call.
- `file::function` is where to look. Line numbers are approximate and will drift as the code changes.

**The example job.** You search Seek for "graduate software engineer", one listing scores 88, it gets the full letter pipeline, and you apply.

## 4.1 Stage A: the server starts

1. **`scripts/run_api.py::main`**: you run `python scripts/run_api.py real`. It sets `DATABASE_URL=sqlite:///real.db` and `APP_ENV=real` *before* importing any app code, then runs `alembic upgrade head` `[DB write: schema only, if behind]`, then starts uvicorn on `127.0.0.1:8000` with `app.api.main:app`.
2. **`app/db.py`** (runs at import): `load_dotenv()` reads `.env`, then creates `engine` and `SessionLocal`. A listener turns on SQLite foreign keys for every connection.
3. **`app/api/main.py::lifespan`**: FastAPI's startup hook. Saves the event loop (needed for SSE from threads) and starts `_processing_idle_loop()` as a background task.
4. **`main.py::_processing_idle_loop`**: waits 15 s, then calls `_recover_orphaned_runs` -> **`app/llm/letter/production.py::recover_orphaned_runs`** `[DB write: letter_runs status running -> failed]`. A run left `running` by a crash would otherwise block its job for good.
5. The loop now repeats forever. Each pass does **at most one** piece of work, in this priority order: retention sweep, cover letters, quick screen, extract+match, match-only. The work runs on a single worker thread (`_bg_executor`, `max_workers=1`), so two AI jobs never run at the same time from the loop. There is one exception in 4.2, step 11.

## 4.2 Stage B: the job is captured (in your browser)

**B1: You open a Seek search results page.**

6. **`extension/manifest.json`** injects `config.js`, `selectors.js`, `screening_assist.js`, `content_script.js` into the page. `config.js` resolves `BACKEND` (real = `http://localhost:8000`) from `chrome.storage.local`. Then `backendReady.then(main)`.
7. **`content_script.js::main`**: the path contains `-jobs`, so it treats the page as a search page and calls `waitFor(parseSearchPage, SELECTORS.JOB_CARD, 8000)`. This polls every 400 ms for up to 8 s, because Seek renders with React and the cards may not exist yet.
8. **`content_script.js::parseSearchPage`** `[no AI]`: for every `[data-testid="job-card"]` it reads title, link, company, location, work type, salary, plus `discovered_query` from the URL (`currentSearchQuery`). No description yet (`raw_description: null`).
9. **`content_script.js::ingest`** -> **`backendFetch`** -> `chrome.runtime.sendMessage({type: 'BACKEND_FETCH'})` -> **`extension/background.js`** (the `onMessage` listener). That listener only allows `http://localhost|127.0.0.1` on ports 8000/8001, then performs the `fetch`. This detour exists because Chrome blocks a public page from calling localhost directly.
10. **`app/api/main.py::ingest`** (`POST /ingest`): the body is validated by the Pydantic models `IngestBody` / `IngestListing` (Pydantic = a library that checks incoming JSON matches declared field types). For each listing:
    - `[DB read: job_listings]` by the unique pair `(source='seek', source_job_id)`.
    - New: `[DB write: INSERT job_listings]`. Already present: fill only fields that are still `NULL` (never overwrite), `[DB write: UPDATE job_listings]`.
    - Returns `job_ids`. **No AI is called here.** The response is fast; AI work happens later in the loop.
    - Back in the browser, `content_script` sends `INGEST_DONE` so `background.js` updates the icon badge count.

**B2 (optional): you click "Scan Page" in the sidebar.** This is how most listings get their full description without you opening each one.

11. **`extension/sidebar.js::scanPage`**:
    - Checks the active tab is a Seek results page, then injects `pageCollectJobLinks` into it to collect every `/job/<id>` link **on the page you opened** (the "1 hop").
    - `GET /jobs/known-ids` (`main.py::known_job_ids`, `[DB read: job_listings]`) and drops jobs already captured. Keeps at most `scanMaxPages` (default 10, ceiling `SCAN_PAGES_CEILING = 25`).
    - **Producer** (`produce`): for each link, `chrome.tabs.create({active: false})` opens a **background tab**, waits for it to load, injects `pageScrapeDetail` to read the description, closes the tab, then `POST /ingest` (same as step 10). Then `sleep(SCAN_DELAY_MS)` = 5 s.
    - **Consumer** (`consume`), running at the same time: for each captured job, `POST /jobs/{id}/quick-screen` -> **`main.py::run_quick_screen`** -> **`app/llm/quickscreen.py::quick_screen`** `[AI small]` (details in step 14). If 3 jobs in a row score under 50, it sets `abort` and the producer stops opening tabs.
    - Note: this quick-screen runs on the **request's own thread**, not the idle loop's single worker (its docstring says so), so it can overlap a loop AI call. The shared rate limiter in `client.py` still spaces the calls.

**B3: you open the job's own page** (or the scan did it for you).

12. **`content_script.js::main`**, `/job/` branch: `waitFor(parseDetailPage, SELECTORS.DETAIL_DESCRIPTION, 8000)`.
    - **`parseDetailPage`** reads the description text, title, and company/location/work type/classification. It prefers Seek's embedded structured data (`readJsonLdJobPosting`, schema.org JSON-LD) and falls back to selectors.
    - Then `ingest` again (step 10). This time `raw_description` fills in on the existing row `[DB write: UPDATE job_listings]`.
    - Then `maybeInjectQuickApply(internalJobId)`: `GET /jobs/{id}`. No letter exists yet, so nothing is shown.
    - If no description appears within 8 s but one was captured on an earlier visit, it calls `PATCH /jobs/{id}/expired` (`main.py::mark_expired`, `[DB write: job_listings.expired_detected_at]`).

## 4.3 Stage C: the idle loop scores the job (on your machine)

Each numbered item is one pass of `_processing_idle_loop`, roughly 20 s apart (`_IDLE_INTERVAL_S`).

13. **Retention**, `main.py::_retention_tick` -> `app/retention.py::run_sweep_if_due`. A no-op except once per 24 h. Then **letters**, `_letters_phase` -> `production.next_work`: nothing to do yet for our job (no match row). Falls through.
14. **Phase 0, quick screen.** `[DB read: job_listings]` finds the oldest job with a description, not extracted, not screened. Then **`app/llm/quickscreen.py::quick_screen`**:
    - `[DB read: profiles, skills, qualifications]` for a short candidate summary. The job summary is title, company, category and the **first 300 characters** of the ad.
    - `[AI small]` `complete_json(..., schema=QuickScreenOutput, task="quickscreen")`. The prompt tells the model to score high when unsure.
    - Score < `SKIP_THRESHOLD` (20): `[DB write: INSERT matches]` with an "Auto-skipped" reason, and the job is done for good.
    - Otherwise: `[DB write: job_listings.quick_screen_at, quick_screen_score]`.
    - On an AI error (except daily quota), it **fails open**: it stamps `quick_screen_at` with a null score and lets the job continue.
    - If the scan in B2 already screened this job, this step skips it (`quick_screen_at` is set).
15. **Phase 1, extract + match.** `[DB read]` up to 50 screened, unextracted, unmatched jobs. Picks one with `main.py::_raw_relevance_score` (counts your skill names appearing in the ad text, `[no AI]`). Then `main.py::_process_listing(job_id, 1, has_description=True)` on the worker:
    - **`app/llm/extract.py::extract_job`** `[AI small]` `complete_json(schema=JobExtraction, task="extract")` on the full ad text. Then:
      - `[DB write: DELETE + INSERT job_skills]` (hard and soft skills, deduplicated).
      - `[DB write: UPDATE job_listings]` seniority, summary, requirement JSON, `extracted_at`. If company was missing, it is filled from the extracted employer name (`_fill_company`).
      - `_count_gap_sightings` -> `app/gaps.py::record_scan_sightings` `[DB write: gap_sightings]` when the ad asks for a skill you previously said you don't have.
    - **`app/llm/match.py::match_job`**:
      - `[DB read: profiles + skills + qualifications + experiences + experience_skills; job_skills]`.
      - `app/llm/prefilter.py::prefilter` `[no AI]` computes skill overlap, seniority and qualification signals, which go into the prompt as context.
      - `[AI small]` `complete_json(schema=MatchScore, task="match")` returns score, reasoning, gaps. The score is clamped to 0-100.
      - `[DB write: INSERT/UPDATE matches]` score, reasoning, gaps (as a JSON string), `status='new'`, `scored_at`.
    - `broadcast_from_thread("job_processed")` pushes an SSE event. The sidebar's listener (`sidebar.js::connectEvents`) calls `loadJobs()` -> `GET /jobs` (`main.py::list_jobs`, `[DB read: matches JOIN job_listings, job_skills, letter_runs]`). **The job now appears in your sidebar with score 88.**
    - If extraction failed (`extracted_at` still null), the loop waits 3 minutes before the next try.

## 4.4 Stage D: the cover letter is written

16. **Next loop pass, `_letters_phase`** -> **`production.py::next_work`**:
    - First looks for a run you have finished answering (`letter_runs.status='answered'`). None here.
    - `letter_settings` (**`app/preferences.py`**) `[DB read: profiles.preferences]`: `auto_min_score` 75, `loop_min_score` 85, `pipeline_min_score` = the higher of the two = 85, engine `agent`, all three side outputs on, limits of 3 drafts / 15 tool calls / $0.50.
    - `[DB read]` the top 25 matches with no `cover_letters` row, score >= 75, extracted, not hidden. Skips any that `_attempt_blocked` says has a live run, 2 failures, or a failure in the last 30 minutes.
    - `kind_for`: 88 >= 85, so **PIPELINE**. (75-84 would get **ONESHOT**: `app/llm/cover_letter.py::generate_cover_letter`, one `[AI strong]` `complete_text` call, `[DB write: cover_letters]`. That's the whole one-shot path.)
17. **`production.py::run_work`** -> `_run_pipeline`: emits SSE `letter_run_started` (the card shows "Writing…") -> **`app/llm/letter/engines.py::run_letter`** (engine `agent`, gap policy `ask_user_gaps`) -> **`app/llm/letter/agent.py::run_agent`**.
18. **`app/llm/letter/outcome.py::open_run`**: `[DB read: job_listings, matches]` (raises if the job has no match), builds an empty **`LetterState`** (`app/llm/letter/state.py`) with your limits, then **`runner.py::start_run`** `[DB write: INSERT letter_runs status='running', state=<JSON>]`.
19. **`agent.py::_drive`**, the agent loop. Each turn:
    1. `runner.persist` `[DB read: SUM llm_usage.cost_usd for this run]` `[DB write: letter_runs.state, tool_calls, cost_usd]`; then `state.budget_exceeded()` stops the run at a limit.
    2. `[AI mid]` **`client.complete_tools`** (`task="orchestrate"`). The model sees only a state summary, the list of steps so far and the last result (`turn_prompt`), never the letter or the conversation history. It returns one tool name.
    3. **`registry.py`** looks up the tool and runs its **gate** (a check from `guardrails.py`). A refused call runs nothing and costs no tool call. It is logged `[DB write: letter_run_steps, error='refused: ...']` and the reason becomes the next turn's "last result". More than 4 refusals ends the run.
    4. **`runner.py::execute_tool`** runs the tool function **with no arguments from the model** (by design, per the `registry.py` docstring: tools read everything from the state). It times it, `[DB write: INSERT letter_run_steps]`, and persists the state. A tool error is recorded and returned, not raised. Budget and quota errors are the exception: they stop the run.

    The usual sequence of tools. Per `CLAUDE.md`'s eval notes the agent picked the same path as the fixed workflow on 15/15 test jobs; that's unverified here, since I did not run it.

    | # | Tool (file) | AI | Reads / writes |
    |---|---|---|---|
    | a | `tools/analyze_job.py::analyze_job` | `[AI mid]`, or none if cached | Reuses `job_listings.requirements_checklist` if the ad's hash and `ANALYSIS_VERSION` match; otherwise calls the model and `[DB write: job_listings.requirements_checklist]`. Fills `state.requirements` (each has importance and a "letter role"). |
    | b | `tools/match_profile.py::match_profile` | `[AI mid]` | Maps each requirement to profile evidence; pointers are validated in code. Then `gap_policy.py::apply_remembered` `[DB read: gap_decisions]` `[DB write: gap_sightings]` leaves out skills you said "No" to before. |
    | c | `ask_user` (`gap_policy.py::ask_user_gaps`) | none | Only if a must-have requirement has no evidence and no decision. Adds questions to the state; the run stops as `waiting_user`. See 4.5. |
    | d | `tools/generate.py::generate_letter` | `[AI strong]` (`complete_json`, `WRITER_TIER`) | Gate `guardrails.can_generate` (draft 1 only, no undecided gaps). Writer prompt = style guide + your writing sample (`style.py`) + evidence. Returns the letter **and its own list of claims with sources**. Stored in `state.drafts`. |
    | e | `tools/check_claims.py::check_claims` | code + `[AI mid]` | Stage 1, code: every cited source must resolve to real profile text; links and emails not in the profile block. Stage 2: a judge model rates each claim supported / overstated / unsupported. |
    | f | `tools/check_requirements.py::check_requirements` | `[AI small]` | Does the draft still cover every must-cover item? Quotes are verified in code. |
    | g | `tools/style_lint.py::style_lint` | none | Banned phrases, US spellings, dashes, stock openings and closings. |
    | h | `tools/revise.py::revise_letter` | `[AI strong]` | Only if a check failed and all three ran (`guardrails.can_revise`). Fixes the failed items, then e-g run again. Capped at `max_drafts`. |
    | i | `tools/answer_screening.py`, `suggest_learning.py`, `suggest_resume_tweaks.py` | `[AI mid]` / `[AI small]` / `[AI mid]` | "Side outputs", each once, not charged to the tool-call cap. `finish` is refused while one is due. Results go into `state.side_outputs`. |
    | j | `finish` | none | Accepted only if `guardrails.can_finish` passes (all three checks passed on the **latest** draft), or the draft cap is reached with every check run. |

20. **`outcome.py::conclude`**: picks `guardrails.best_draft`, ranked by passed claims check, then fewest missing must-cover items, then passed style, then the later draft. `is_clean` = all three checks passed. Then **`runner.finish_run`** `[DB write: letter_runs.status='done' (or budget_stopped/failed), finished_at, final_draft_version]`.
21. Back in **`production.py::_run_pipeline`** -> **`land_letter`** `[DB write: INSERT/UPDATE cover_letters.generated_content]`. `edited_content` (your edits) is never overwritten. Emits SSE `cover_letter_ready` and `letter_run_done` (with `clean` and `open_issues`).
    - Important for reviewers: if no draft passed, **the best draft still lands**, with its open issues shown in the sidebar. This can include one that failed `check_claims`. The flag is the only guard, by design.
22. **Sidebar**: `connectEvents` hears the event -> `scheduleReload` -> `loadJobs` -> `GET /jobs`. Expanding the card calls `GET /jobs/{id}/letter-info` (`app/api/letters.py::letter_info` -> `app/llm/letter/view.py::letter_info`, `[DB read: letter_runs.state]`) to show open issues, what was left out and why, and eligibility notes.

## 4.5 Detour: the agent asks you a question

Only when a must-have requirement has no evidence in your profile and you have never answered it before.

23. Step 19c adds questions; `_drive` raises `Stop("waiting_user")`; `conclude` -> `finish_run` `[DB write: letter_runs.status='waiting_user']`. `_run_pipeline` emits `letter_run_waiting`. **The worker is now free**; a waiting run never blocks the loop.
24. Sidebar: `GET /letter-runs/waiting` (`app/api/letters.py::waiting_runs`) shows the question card.
25. You answer:
    - **No**: `POST /letter-runs/{id}/answers` -> **`answers.py::answer_no`**: requirement is left out. `[DB write: gap_decisions]` (added to your to-work-on list), `[DB write: gap_sightings]`.
    - **Yes + text**: **`answers.py::answer_yes`** -> `parse_answer` `[AI small]` turns your text into proposed skill/experience/qualification rows. Nothing is saved yet.
    - **Confirm**: `POST .../questions/{qid}/confirm` -> **`answers.py::confirm`** -> `save_confirmed` `[DB write: skills / experiences / qualifications with origin='ask_user'; profiles.profile_revised_at]`. The requirement resets to `unknown` for re-matching.
    - When no questions remain, `_save` sets `[DB write: letter_runs.status='answered']`.
26. Next loop pass: `next_work` sees the `answered` run **first** -> `RESUME` -> `engines.resume_letter` -> `agent.resume_agent` -> `outcome.reopen_run` (loads the state JSON, adds 2 extra tool calls, status back to `running`) -> `_drive` continues from step 19 (it typically calls `match_profile` again for the reset items, then drafts).

## 4.6 Stage E: you review, apply on Seek, and log it

27. **Edit the letter** in the sidebar or overlay -> `PATCH /jobs/{id}/cover-letter` -> `main.py::update_cover_letter` `[DB write: cover_letters.edited_content, status='edited']`. **Copy** uses `navigator.clipboard.writeText` `[no backend]`.
28. **Open the job on Seek** -> `content_script.js::main` (detail branch) -> `maybeInjectQuickApply` -> `GET /jobs/{id}`. A letter exists now, so **`buildQuickApplyPanel`** draws a floating panel (inside a Shadow DOM, so Seek's CSS can't affect it) with Copy, Save edits, Screenshot, and Mark Applied.
29. **Quick Apply questions** (if the employer has them). On `/job/{id}/apply/...`:
    - `startApplyWatcher` uses a `MutationObserver` (a browser API that fires when the page changes) to call `checkApplyStep` -> `parseQuestionnaire`, which reads question text, type and options only. It never reads your selected answers.
    - `resolveOrCreateJob` -> `postScreeningQuestions` -> `POST /jobs/{id}/screening-questions` -> `app/api/screening.py` -> **`app/screening/bank.py::record_job_questions`** `[DB write: screening_questions, job_screening_questions]` (sorted by rules in `sort.py`, `[no AI]`).
    - The overlay / sidebar card (`screening_assist.js`) calls `GET /jobs/{id}/screening-assist` (`app/screening/assist.py`; may call `classify.py` `[AI small]` once per new question). A "Draft" click calls `POST /jobs/{id}/screening-drafts` (`drafts.py`, `[AI mid]`).
    - **Nothing is typed into Seek's form.** You copy and paste.
30. **You submit the application on Seek.** No code from this project runs here.
31. **Mark Applied**:
    - From the Seek overlay: `content_script.js` `applyBtn` -> `PATCH /jobs/{id}/status {"status": "applied"}`. Or from the sidebar: `sidebar.js::markApplied` -> the same endpoint.
    - **`main.py::update_status`** `[DB write: matches.status='applied'; matches.applied_at = now, only if not already set]`.
    - Overlay only: `captureAndDownloadScreenshot` -> `background.js` `CAPTURE_SCREENSHOT` (`chrome.tabs.captureVisibleTab`) -> local download + `POST /jobs/{id}/screenshot` -> `main.py::upload_screenshot` (downscale, `[file write: app/screenshots_real/]`, `[DB write: matches.screenshot_path, screenshot_taken_at]`).
    - The Overview and Applied tabs (`dashboard.js`) then read `GET /obligation` (`app/api/obligation.py`) to count it towards this period's target.

**Done.** The job's lifetime footprint: one `job_listings` row, its `job_skills`, one `matches` row (now `applied`), one `cover_letters` row, one `letter_runs` row with ~10-20 `letter_run_steps`, and ~15-20 `llm_usage` rows.

## 4.7 What happens inside every AI call

Every `[AI …]` tag above goes through **`app/llm/client.py`**:

1. `complete_json` / `complete_text` / `complete_tools`. If `LLM_PROVIDER == "stub"` (tests only), it returns canned answers instead.
2. `model_for(tier)` maps the tier to a model from env vars (`GEMINI_MODEL_SMALL/_MID/_STRONG`).
3. **`_logged_call`**:
   - `_check_tier`.
   - **`_check_budget(tier)`**: `small` is never blocked. For `mid`/`strong`, `[DB read: llm_usage]` via `app/llm/usage.py::budget_status`; at the daily ($5) or total ($200) cap it raises `BudgetExceededError` **before** any request. If the table can't be read it fails open (allows the call).
4. **`_generate_gemini`**:
   - `_throttle()` enforces `LLM_RPM` (default 8, so at least 7.5 s between calls, across all threads; it holds a lock).
   - It retries 429 (rate limit), 5xx and dropped-connection errors with backoff, up to `_MAX_RETRIES`. A 429 asking to wait longer than `_DAILY_RETRY_SECS` raises `DailyQuotaError` (treated as "out for the day").
5. **`_record_usage`** `[DB write: llm_usage]` in its own session: tokens, thinking tokens, estimated USD from the `PRICES` table, labels `task`/`job_id`/`match_id`/`run_id`. It never raises.
6. The JSON text is parsed; the **caller** validates it against its Pydantic schema (`Schema.model_validate(data)`).

How stops travel back up: `BudgetExceededError` / `DailyQuotaError` -> `runner.execute_tool` re-raises -> `agent._drive` ends the run as `budget_stopped` with `account_limit` set -> `production._run_pipeline` -> `main._letters_phase` pauses all letters for 10 minutes and emits `llm_budget_blocked` (the sidebar shows a banner). Small-tier work (screen/extract/match) keeps going.

## 4.8 Cost of one job (from the docs, unverified)

Calls for one pipeline letter: about 3 small (screen, extract, match) + ~10-12 mid orchestrator turns + analyze, match_profile and check_claims (mid) + 1-3 strong writer calls + check_requirements (small) + side outputs. `CLAUDE.md` reports **about $0.23 per letter and about 4 minutes** for the agent engine on its eval set (run `agent-v2-side`). I did not run anything to confirm this. The `llm_usage` table is where to check real numbers (`scripts/letter_lab.py cost-report`).

## 4.9 Things I noticed while tracing (input for Step 5)

Not yet ranked; Step 5 will triage these with the rest.

1. **FIXED in `49f54ee`** (re-scoring keeps the status; the export uses `applied_at`). **Two definitions of "applied", and re-scoring breaks one.** The Overview/Applied tabs count a job as applied when `matches.applied_at` is set (`app/api/obligation.py`, line ~63). The evidence export (`GET /jobs/evidence-export`, called from `sidebar.js:1034`) instead filters on `Match.status == "applied"` (`main.py:955`). Meanwhile `match.py::match_job` with `force=True` resets `existing.status = "new"` (upsert branch near the end of `app/llm/match.py`), and `POST /jobs/{id}/regenerate` always passes `force=True` (`main.py::regenerate`). So pressing Regenerate on an applied job would likely keep it in the dashboard count but **drop it from the Centrelink evidence export**. **Plausible, not reproduced**: I didn't confirm the sidebar shows Regenerate on applied cards.
   **Fixed in `49f54ee`:** re-scoring no longer changes `status`, and the evidence export now uses `applied_at`. Follow-ups are in Step 5.4 (#3, #4).
2. **A failed match becomes a permanent score of 0.** `match_job` catches *every* exception from the AI call (`except Exception`), including network failure and `DailyQuotaError`, and writes `score=0, reasoning="Parse error"`. The idle loop only picks jobs with **no** match row, so that job is never retried automatically.
3. **FIXED in `046d2f9`** (409 without `allow_applied`; Delete only on the Applied tab). **Delete removes evidence.** Every Jobs-tab card has a Delete button (`sidebar.js`, `delBtn`) -> `DELETE /jobs/{id}` -> `main.py::delete_job`, which hard-deletes the job and, by cascade, its match and letter, **even if applied**. `CLAUDE.md` says applied matches are Centrelink evidence and never purged; this endpoint has no such guard.
   **Fixed in `046d2f9`:** `delete_job` now returns 409 (refused) for an applied job unless `?allow_applied=true` is passed, and it removes the screenshot file. Jobs-tab cards for applied jobs have no Delete button. Deleting an application is a separate, confirmed button on the Applied tab.
4. **`PATCH /jobs/{id}/status` accepts any string** (`StatusUpdate.status: str`, no allowed list).
5. **Duplicated selectors.** `sidebar.js::pageScrapeDetail` hardcodes `[data-automation="jobAdDetails"]` and `[data-automation="job-detail-title"]` instead of using `selectors.js`. There is a likely reason: a function injected with `chrome.scripting.executeScript` can't see the content script's globals. Still, a selector change must now be made in three places (`selectors.js`, `app/scraper/selectors.py`, `sidebar.js`).
6. **Probable double capture during a scan.** The background tab the scan opens is a Seek page, so `content_script.js` also runs there and ingests the same job. Harmless because `/ingest` is an upsert, but it doubles the requests. **Unverified at runtime.**
7. **Doc drift.**
   - `CLAUDE.md` says the scan "auto-navigates the user's active tab"; the code opens **background** tabs (`active: false`).
   - The `check_claims.py` docstring says stage 2 is a "Small model"; `CLAIMS_TIER = "mid"`.
   - `main.py`'s module docstring says extraction is "stubbed for now".
8. **Two AI chains can overlap.** The Scan Page quick-screen runs outside the single worker (step 11). The throttle prevents bursts, but two threads can write to SQLite at once. Whether that ever causes "database is locked" errors is **unverified**.

---

# Step 5: Review guide and cleanup list

Two parts:
- **5.1-5.3: how to review a change.** A routine, a lookup table, and one rule I checked.
- **5.4-5.6: what to clean up.** Ranked issues, things to leave alone, and how to group the fixes into branches.

## 5.1 The routine for any diff

1. **See what changed.** Run `git show --stat <commit>` (or look at the PR's file list). Find each file in the table in 5.2.
2. **Read the tests first.** They show what the author thinks the change does. Most modules have a test file with a similar name (`app/llm/letter/agent.py` -> `tests/test_letter_agent.py`, `app/obligation.py` -> `tests/test_obligation.py`). A behaviour change with no test change is the first question to ask.
3. **Then read in this order:** schema (`models.py` + migration), then backend logic, then the extension UI. Schema mistakes are the hardest to undo; UI mistakes the easiest.
4. **Run the checks:**
   - `python -m pytest` for the whole suite. `CLAUDE.md` says it makes no paid AI calls; tests use the `stub` provider in `client.py`.
   - `node --check extension/<file>.js` for each edited JavaScript file. This catches syntax errors only.
   - The browser checks in `tests/e2e/` are run by hand (see each file's header).
5. **Ask the four project-specific questions.** These are the rules a normal-looking diff can quietly break:

| Question | Why it matters here | Where the rule is written |
|---|---|---|
| Could this delete, hide or change an **applied** job? | Applied jobs are Centrelink evidence. "Applied" means `matches.applied_at IS NOT NULL`. | `CLAUDE.md` (retention entry); `app/retention.py` |
| Could this make the **backend contact Seek**? Could it make the extension go past **1 hop**, or fill in / submit a form? | Seek's terms; the owner's deliberate, limited trade-off | `CLAUDE.md`, "SEEK ACCESS POLICY" |
| Could this **spend money** outside `app/llm/client.py`, past the budget guard, or outside the single worker? | Every AI dollar is meant to be logged and capped | `CLAUDE.md`, "LLM LAYER" |
| Could this run against **`real.db`** by accident? | It holds the real profile and the evidence | `app/db.py::app_env` (defaults to `test`) |

## 5.2 "If a diff touches X, check Y"

**Database**

| If a diff touches… | Check… |
|---|---|
| `app/models.py` | There is a matching new file in `alembic/versions/`. The column types use the portable forms (`BIG_INT_PK`, `text("false")`, not `0/1`). New relationships use `passive_deletes=True`. `docs/database-schema.md` is updated (it is the stated source of truth). No foreign key was added to `llm_usage` (it must outlive the rows it describes). |
| `alembic/versions/*.py` | Its `down_revision` is the previous head, so there is still one head (`alembic heads` prints one line). It has no SQLite-only SQL. A new non-null column has a default, or the existing rows get a value. `scripts/run_api.py` applies migrations on every start, so a broken migration stops the real server. |
| Anything that deletes or updates `matches` | Applied matches survive. The purge whitelist in `app/retention.py` is still "`status='new'` and `applied_at IS NULL`". Retention deletes by **age**, never by score. The bulk "hide" (`DELETE /jobs?below_score=`) is still a soft delete (`hidden_at`). |
| `app/db.py`, `scripts/run_api.py` | `app_env()` still defaults to `"test"`. `run_api.py` still sets `DATABASE_URL` / `APP_ENV` before importing app code. |

**AI layer**

| If a diff touches… | Check… |
|---|---|
| `app/llm/client.py` | The budget check still runs **before** the request (`_logged_call` -> `_check_budget`). `_record_usage` still never raises. Retries still cover 429 / 5xx and still turn a long wait into `DailyQuotaError`. Gemini 3 calls still pass no `temperature`. Prices in `PRICES` match the provider's current price list. |
| Any new AI call, anywhere | It goes through `complete_json` / `complete_text` / `complete_tools`, names a **tier** (not a model), and passes `task=` (plus `job_id` / `run_id` when known) so the cost row is labelled. It runs on the idle loop's worker (`_bg_executor`), not inside a request handler, unless there is a stated reason. A new optional feature has an on/off preference in `app/preferences.py`. |
| Prompts in `app/llm/letter/tools/*.py` or `app/llm/skills/` | Letter quality and cost can move. The project measures that with `scripts/letter_lab.py` on the eval set (see `evals/rubric.md`). A prompt change with no eval result is unmeasured. |

**Cover-letter pipeline**

| If a diff touches… | Check… |
|---|---|
| `app/llm/letter/state.py` (`LetterState`) | New fields **have defaults**. Saved runs are reloaded from JSON (`LetterState.model_validate_json` in `outcome.py:118`, `answers.py:231`, `view.py`), so a run waiting for your answer since before the change must still load. |
| `app/llm/letter/guardrails.py`, `registry.py` | Each rule is still enforced **in code** (a gate in `registry.py`), not just asked for in a prompt. `must_cover` / `may_use` / `do_not_claim` are still defined only in `guardrails.py`. Refused moves are still free and still capped (4). |
| `app/llm/letter/tools/*.py` | The tool still takes no arguments from the model and reads what it needs from the state. Errors are recorded, not raised, **except** `BudgetExceededError` / `DailyQuotaError`, which must still stop the run (`runner.execute_tool`). |
| `production.py::land_letter`, `PATCH /jobs/{id}/cover-letter` | `edited_content` (your edits) is never overwritten by a new draft. |
| `agent.py` and `workflow.py` | The workflow still works through the same gap-policy hook. `CLAUDE.md` says it is the fallback engine, so it should not drift behind the agent. |

**Backend API**

| If a diff touches… | Check… |
|---|---|
| `app/api/main.py` | New endpoints might belong in a router file (`letters.py`, `screening.py`, `obligation.py` show the pattern). `profile_id` / `user_id == 1` assumptions don't spread. The file must stay importable after every edit: `run_api.py` runs uvicorn with `reload=True`, so a running real server reloads on save. |
| `_processing_idle_loop`, `_letters_phase` | The order (retention, letters, quick screen, extract + match) and the "one piece of work per pass" rule. Every LLM phase that `continue`s must not starve the retention sweep (the sweep is at the **top** for that reason, `CLAUDE.md`). |
| `GET /obligation`, `app/obligation.py`, `dashboard.js` | Dates are counted on the **local** date. "Applied" means `applied_at` set, hidden matches included. `tests/test_obligation.py` covers the period maths. |

**Extension**

| If a diff touches… | Check… |
|---|---|
| `extension/sidebar.js::scanPage`, `pageCollectJobLinks` | Links come only from the page **you** opened, never from pages the scan visits. `SCAN_DELAY_MS` is still at least 5000 and `SCAN_PAGES_CEILING` still 25 (`sidebar.js:1124-1126`). The API's matching bound (`PreferencesUpdate`, `le=25`) agrees. |
| `extension/content_script.js` | No code types into Seek's form, clicks Seek's buttons, or submits anything. Question capture reads question text only, never your answers. |
| `extension/background.js` | The `BACKEND_FETCH` allow-list is still localhost ports 8000/8001 only. Otherwise any Seek page could use the extension to make arbitrary requests. |
| `extension/manifest.json` | Any new `permissions` / `host_permissions` entry needs a reason. |
| Seek selectors | A change must be made in three places: `extension/selectors.js`, `app/scraper/selectors.py`, and the hardcoded copies in `sidebar.js::pageScrapeDetail` (4.9, item 5). |
| `tests/e2e/*` | `chrome.tabs.create` is stubbed. A real tab can escape Playwright's routing and load Seek (`CLAUDE.md`, Centrelink dashboard entry). |

**Quick Apply**

| If a diff touches… | Check… |
|---|---|
| `app/screening/*` | `user` questions (work rights, salary, identity, motivation) are never sent to the AI. Help is offered only for jobs with a full-pipeline letter. The `screening_question_help_enabled` toggle switches off **all** assisted help before any model call. |

## 5.3 Checked: the "one gateway" rule holds

Step 2.4 left this unverified. I searched all `.py` files (excluding `.venv/`) for imports of `google`, `openai`, `groq` and `anthropic`:
- In app code they appear only in `app/llm/client.py` (lines ~443, 507, 543, 568, 846), each imported inside a function.
- The only other match is `tests/test_llm_client.py:9`, which is a test.

So no other module talks to an AI provider directly.

## 5.4 Ranked cleanup list

**How I ranked:**
1. Could it lose Centrelink evidence?
2. Could it leak personal data?
3. Could it give wrong results or waste money?
4. Does it make the code harder to change?
5. Is it only wrong documentation?

Within a level, cheaper fixes come first. Size is rough: S = under an hour, M = a few hours, L = a day or more.

| # | Issue | Why it matters | Where | Fix sketch | Size | Status (2026-10-05) |
|---:|---|---|---|---|---|---|
| 1 | **Delete removes applied jobs** (4.9, item 3) | One click on a Jobs-tab card permanently deletes an application, its letter and (by cascade) its evidence. The screenshot file is also left orphaned. | `sidebar.js::renderJob` (`delBtn`); `main.py::delete_job` (line ~1455) | Refuse with 409 for applied jobs unless an explicit flag is passed. Remove the button from applied cards. Add a separate, clearly worded Delete on the Applied tab. Unlink the screenshot. | M | **Fixed in `046d2f9`** (pytest + dashboard e2e). |
| 2 | **Re-scoring un-applied a job** (4.9, item 1) | A Regenerate queued before Mark Applied could reset `status` to `new`, dropping the job from the evidence export. | `match.py::match_job`, `quickscreen.py` skip branch, `main.py::evidence_export` | Leave status alone on update; export by `applied_at`. | S | **Fixed in `49f54ee`** (with tests in `tests/test_rescore_keeps_status.py`). |
| 3 | **Old applications may now be missing from the export** | Rows applied before `applied_at` existed (migration `60d65ffb16df`, 2026-09-11, no backfill) have `status='applied'` but `applied_at` NULL. Since fix #2 they are in neither the dashboard nor the export. | Data in `real.db` | Run `python scripts/check_applied_consistency.py real.db` (read-only). If it finds rows, decide what date to backfill into `applied_at`. That is your call, since it is evidence. | S | **Open.** Unverified whether any such rows exist. |
| 4 | **"Applied" still has a second definition in the UI** | The backend now agrees on `applied_at`, and so does the Jobs-tab Delete since `046d2f9`. But the sidebar's "✓ Applied" button and the Seek overlay still read `status`. So does the `GET /jobs?status=applied` filter. Nothing sends any status except `applied` today, so the risk is low but real. | `sidebar.js:337-342`, `content_script.js:441`, `main.py::list_jobs` (`status` param, line ~733) | Expose `applied_at` on `/jobs` and use it for the button. Also validate `StatusUpdate.status` against the documented vocabulary (4.9, item 4; today `status: str`, `main.py:438`). | S | Open |
| 5 | **The local server accepts calls from any website** | `allow_origins=["*"]` (`main.py:338`) means a page you visit could, in principle, read `http://localhost:8000/profile-ui/data` (your profile) or call `DELETE` endpoints. Chrome's local-network protections may block much of this; **unverified** how much. | `app/api/main.py`, CORS middleware | Allow only the extension's origin. The profile editor is same-origin and needs no CORS. Test in a loaded Chrome: extension pages with `host_permissions` may not need CORS at all. | S-M | Open |
| 6 | **No pinned dependencies and no CI** | `requirements.txt` uses `>=` ranges with no lock file, so a fresh install can pick up a breaking SQLAlchemy / FastAPI / `google-genai` release. There is no `.github/` folder, so tests run only when someone remembers. | `requirements.txt`; repo root | Commit a `pip freeze` lock file. Optionally add a GitHub Action that runs `pytest` (it already uses the stub provider, so it needs no Google credentials). | S | Open |
| 7 | **Two AI chains can overlap** (4.9, item 8) | The Scan Page quick screen runs on the request thread, outside the single worker. Two threads writing SQLite at once can cause "database is locked". | `main.py::run_quick_screen` | Submit it to `_bg_executor` and wait for the result, or document why not. Reproduce first. | S | Open, **unverified** whether it ever fails |
| 8 | **A failed score becomes a permanent 0** (4.9, item 2) | A network error or daily-quota stop writes `score=0, "Parse error"`, and the loop never retries it. | `match.py::match_job` (`except Exception`) | Don't write a row on transport or quota errors. | S | **Accepted by owner** (rare; 2026-10-05). Listed for completeness. |
| 9 | **`app/api/main.py` does too much** | 1,564 lines: endpoints, the background loop and helpers. Edited in 18 of 75 commits, so most branches touch it and conflict. | `app/api/main.py` | Move groups into routers (jobs, events/SSE, evidence and screenshots) and the idle loop into its own module, following `letters.py` / `obligation.py`. Pure moves, no behaviour change. | L | Open. Do it **after** the branches above merge. |
| 10 | **`sidebar.js` is one 2,723-line file**, and `dashboard.js` relies on its globals | The most-changed file in the repo. Load order is the only contract between the two files. | `extension/sidebar.js`, `dashboard.js` | Split by tab (jobs list, scan, profile). No build step, so use more `<script>` files in a fixed order, or ES modules. | L | Open, low priority |
| 11 | **Broad `except Exception`** | 37 in `app/` (count by search). Many are deliberate fail-opens (usage logging, the budget guard's read, quick screen). #8 shows one that hides a real failure. | `app/**` | Audit each one: does it log, and does it let `BudgetExceededError` / `DailyQuotaError` through? | M | Open |
| 12 | **Seek selectors in three places** (4.9, item 5) | One Seek layout change needs three edits; missing one breaks the scan quietly. | `selectors.js`, `app/scraper/selectors.py`, `sidebar.js::pageScrapeDetail` | Pass the selectors into the injected function as `args` to `chrome.scripting.executeScript`, which can't see content-script globals. | S | Open |
| 13 | **Probable double capture during a scan** (4.9, item 6) | The scan's background tab also runs `content_script.js`, so each job is ingested twice. Harmless (upsert), just extra work. | `content_script.js::main`, `sidebar.js::produce` | Confirm first. If true, skip auto-ingest in tabs the scan opened. | S | Open, **unverified** |
| 14 | **Documentation drift** | Misleads the next reader (and the AI assistant that reads `CLAUDE.md`). | See list below | One docs-only branch | S | Open |
| 15 | **Hardcoded profile id 1** | Fine for one user per database. A blocker only if this becomes multi-user. | `main.py`, `sidebar.js` `PROFILE_ID`, others | Nothing now; remove before any hosted version. | L (later) | Deliberate for now |

**The drift list for #14:**
- `app/models.py` docstring says "Eleven tables" (there are 18).
- `app/llm/__init__.py` says "stub package".
- `app/api/main.py` docstring says extraction is "stubbed".
- `check_claims.py` docstring says stage 2 uses a "Small model" (`CLAIMS_TIER = "mid"`).
- `requirements.txt` calls OpenAI "active" and Gemini "dormant" (the reverse of `CLAUDE.md`).
- `.env.example` describes the scraper proxy as mandatory.
- `CLAUDE.md` says the scan navigates "the user's active tab" (it opens background tabs).
- `.claude/settings.json` is tracked by git and holds one machine's approval history.

## 5.5 Looks odd, but leave it alone

These are deliberate decisions written down in `CLAUDE.md` or `docs/database-schema.md`. A "cleanup" that removes them would be a regression.

- **The dormant scraper** (`app/scraper/`) and the proxy code. `CLAUDE.md`: "do not delete it".
- **Bulk hide is a soft delete** (`matches.hidden_at`). Hard-deleting low scores once moved the search baseline from 69.4 to 82.5 in one click.
- **`llm_usage` has no foreign keys.** The spend log must survive deletions.
- **The best draft lands even when a check failed**, with its open issues shown. Flagging rather than blocking was chosen on purpose (4.4, step 21).
- **`experience_skills` has no extra columns, and there is no stored "tier" on `matches`.** Both are documented schema decisions.
- **Tools take no arguments from the model** (`registry.py`). It looks restrictive; it is what stops the model from pointing a tool at the wrong data.

## 5.6 Suggested branches

| Branch | Issues | Notes |
|---|---|---|
| `fix/applied-evidence-safety` (exists) | 1 and 2 (done), 3, 4 | Same theme: one definition of "applied" and no way to lose it. Issue 3 needs your decision on dates. |
| `chore/cors-lockdown` | 5 | Needs a live Chrome check. |
| `chore/pin-deps-ci` | 6 | Smallest high-value change. |
| `fix/scan-worker` | 7, 12, 13 | All in the scan path; reproduce 7 and 13 before changing anything. |
| `chore/docs-drift` | 14 | Docs and comments only. |
| `refactor/split-main` | 9, then 10 | Last, so it doesn't conflict with the others. Moves only. |
| *(later)* `chore/exception-audit` | 11 (and 8 if you change your mind) | |

---

# Step 6: Glossary and reading order

## 6.1 Glossary

Terms from 2.1 (backend, endpoint, ORM, migration, LLM, tier, SSE, Manifest V3) are not repeated. Each entry points to where the idea lives.

**The domain**

| Term | Meaning | Where |
|---|---|---|
| Centrelink | The Australian government agency paying unemployment benefits. It requires a set number of job applications per period, so the app keeps applications as evidence. | `app/api/obligation.py`, `docs/centrelink-dashboard-plan.md` |
| Obligation period | One reporting period (monthly, from `obligation_cycle_start`) with a target number of applications (`obligation_target`, default 20). | `app/obligation.py` |
| Applied | A match with `applied_at` set (stamped once, the first time you press Mark Applied). The backend's one definition since `49f54ee`; two UI spots still read `status` (5.4, #4). | `main.py::update_status` |
| Evidence export | A zip of `applied-jobs.csv` plus surviving screenshots. | `main.py::evidence_export` |
| Quick Apply | Seek's in-site application form, sometimes with employer questions. | `content_script.js`, `app/screening/` |
| 1-hop rule | The extension may open links found on a page **you** opened, never links found on pages it opened itself. | `CLAUDE.md`, "SEEK ACCESS POLICY" |

**Architecture**

| Term | Meaning | Where |
|---|---|---|
| Real / test environment | Two separate databases, ports and screenshot folders. Each has exactly one profile, id 1. | `scripts/run_api.py`, `app/db.py::app_env` |
| Ingest | Saving a captured job. Fast, with no AI. An **upsert**: insert if new, otherwise fill only the empty fields. | `main.py::ingest` |
| Idle loop | The background `while True` that does one piece of AI work every ~20 s. | `main.py::_processing_idle_loop` |
| Single worker | The one-thread executor (`_bg_executor`) the idle loop runs AI work on, so loop calls never overlap. | `app/api/main.py` |
| Content script | JavaScript the extension injects into Seek pages. Reads the page; never submits. | `extension/content_script.js` |
| Service worker | The extension's background script; relays calls from Seek pages to localhost. | `extension/background.js` |
| Side panel | The extension's sidebar UI (Overview / Jobs / Applied / Profile). | `sidebar.html`, `sidebar.js`, `dashboard.js` |
| Shadow DOM | A sealed-off piece of a web page whose styles don't mix with the page's. Used for the on-Seek panels. | `content_script.js::buildQuickApplyPanel` |
| JSON-LD | Structured job data Seek embeds in its pages; read in preference to CSS selectors. | `content_script.js::readJsonLdJobPosting` |
| Selector | A CSS pattern that finds an element on a web page. Breaks when Seek changes its layout. | `extension/selectors.js` |
| Cascade (ON DELETE CASCADE) | Deleting a row automatically deletes the rows that belong to it. | `app/models.py`, `docs/database-schema.md` |
| Soft delete | Marking a row hidden (`hidden_at`) instead of deleting it. | `main.py::bulk_hide_jobs` |

**Scoring jobs**

| Term | Meaning | Where |
|---|---|---|
| Quick screen | A cheap first score from the first 300 characters of an ad; below 20, the job is skipped. | `app/llm/quickscreen.py` |
| Extract | AI pulls skills, seniority and a summary out of the full ad. | `app/llm/extract.py` |
| Prefilter | Plain-code signals (skill overlap etc.) given to the scorer as hints. Not a filter despite the name. | `app/llm/prefilter.py` |
| Match / score | The 0-100 fit between you and a job; one `matches` row per job. | `app/llm/match.py` |
| Retention | Deleting old, unapplied matches by age (122-day window, keeping at least 150). Only `status='new'` with no `applied_at` can be deleted. | `app/retention.py` |
| Baseline / suggestion miner | Your average score, and the code that finds search phrases that score above it. | `app/search_suggest.py` |

**The AI layer**

| Term | Meaning | Where |
|---|---|---|
| Gateway | `client.py`, the only code that talks to an AI provider. | `app/llm/client.py` |
| Budget guard | The check that refuses mid/strong calls once the daily ($5) or total ($200) estimate is reached. | `client.py::_check_budget`, `app/llm/usage.py` |
| `BudgetExceededError` / `DailyQuotaError` | The two "stop for now" errors: our own cap, and Google's daily limit. | `app/llm/client.py` |
| Thinking tokens | Hidden reasoning the model does before answering. Billed as output, and most of a letter's cost. | `client.py`, `CLAUDE.md` |
| Stub provider | A fake AI that returns canned answers, used by tests (`LLM_PROVIDER=stub`). | `app/llm/client.py` |
| `llm_usage` | One row per AI call: tokens and estimated cost. | `app/models.py`, `client.py::_record_usage` |

**The cover-letter pipeline**

| Term | Meaning | Where |
|---|---|---|
| One-shot | The old single-call letter writer, used for scores 75-84. | `app/llm/cover_letter.py` |
| Pipeline / run | The multi-step letter process for scores 85+; one execution is a `letter_runs` row. | `app/llm/letter/production.py` |
| Engine | Which pipeline drives the tools: `agent` (the model chooses) or `workflow` (fixed order). | `engines.py`, `agent.py`, `workflow.py` |
| Agent / orchestrator | The mid-tier model that picks the next tool each turn. | `agent.py::_drive` |
| Tool | One step the agent can choose: analyze, match, generate, check, revise, side outputs. | `app/llm/letter/tools/`, `registry.py` |
| `LetterState` | The one object every tool reads and writes, saved as JSON on the run. | `app/llm/letter/state.py` |
| Requirements checklist | The ad turned into a list of requirements, each with importance and letter role. Cached on the job. | `tools/analyze_job.py` |
| Evidence pointer | A reference from a requirement or claim to a specific profile row; checked in code. | `state.py` (resolver) |
| `must_cover` / `may_use` / `do_not_claim` | What the letter must mention, may mention, and must not claim. | `guardrails.py` |
| Guardrail / gate | A code rule that allows or refuses the agent's next move. | `guardrails.py`, `registry.py` |
| Refusal | A tool call a gate blocked. Free, explained back to the model, capped at 4. | `agent.py` |
| Draft / revise | Draft 1 comes from `generate`; later drafts come from `revise` after a failed check. | `tools/generate.py`, `tools/revise.py` |
| The three checks | `check_claims` (true and backed?), `check_requirements` (musts covered?), `style_lint` (reads human?). | `app/llm/letter/tools/` |
| Clean / open issues | Clean = all three checks passed on the chosen draft; open issues = what didn't, shown in the sidebar. | `outcome.py::conclude` |
| Best draft | The draft handed back when none is clean, ranked by claims, then musts, then style, then recency. | `guardrails.py::best_draft` |
| Side outputs | Extras beside the letter: screening answers, learning suggestions, résumé tweaks. | `side_outputs.py` |
| Gap | A must-have requirement your profile shows no evidence for. | `gap_policy.py` |
| Gap decision / sighting | Your remembered "No" for a skill / each later ad that asked for it. Together they form the to-work-on list. | `app/gaps.py` |
| `ask_user` / `waiting_user` / `answered` | The run pauses with a question (`waiting_user`); once you answer every question it is `answered` and the loop resumes it. | `gap_policy.py`, `answers.py` |
| Eval / eval set | Offline experiments on 15 saved ads, to compare letter engines and prompts. | `scripts/letter_lab.py`, `evals/rubric.md` |
| Grading panel | Blind grading of eval letters by another model, against a written standard. | `scripts/grading_panel.py`, `evals/grading-panel.md` |

**Quick Apply help**

| Term | Meaning | Where |
|---|---|---|
| Question bank | Every employer question ever captured, shared across jobs, so repeats need no AI. | `screening_questions` table, `app/screening/bank.py` |
| `user` vs `assisted` question | Personal facts (never sent to the AI) vs questions about the job (help offered). | `app/screening/sort.py` |
| Layer 5 | The last-resort AI classification for a question the rules couldn't sort. | `app/screening/classify.py` |
| Draft fingerprint | A stamp saved with a draft answer; if the profile or letter changes, the draft shows "redraft". | `app/screening/drafts.py` |

## 6.2 Reading order

The order follows the data: how it starts, what it stores, how a job arrives, how it is scored, how a letter is written. Each stop says what to look for, so you can stop reading a file once you have that.

| # | Read | Look for | Skip |
|---:|---|---|---|
| 0 | This doc's one-page summary and Step 4; then `CLAUDE.md` down to the end of "TWO ENVIRONMENTS" | The rules every change must respect | The rest of `CLAUDE.md` is a changelog |
| 1 | `scripts/run_api.py`, `app/db.py` (both short) | How real vs test is chosen; why app code is imported late | |
| 2 | `app/models.py`, with `docs/database-schema.md` open beside it | `JobListing`, `Match`, `CoverLetter`, `LetterRun` first; the columns Step 4 tagged | The profile tables' detail |
| 3 | `extension/manifest.json`, then `content_script.js` (`main`, `parseDetailPage`, `ingest`, `backendFetch`), then `background.js` | How a page becomes a `POST /ingest` | Quick Apply parts for now |
| 4 | `app/api/main.py`: only `lifespan`, `_processing_idle_loop`, `ingest`, `_process_listing`, `list_jobs`, `update_status` | The loop's priority order and the single worker | The other ~50 endpoints |
| 5 | `app/llm/client.py`: `complete_json`, `_logged_call`, `_check_budget`, `_record_usage` | One gateway: tier, budget, retry, log | The OpenAI / Groq functions (dormant) |
| 6 | `app/llm/quickscreen.py` -> `extract.py` -> `prefilter.py` -> `match.py` | The cheap-before-expensive chain; what each writes | |
| 7 | Letter pipeline: `state.py` -> `guardrails.py` -> `registry.py` -> `runner.py` -> `agent.py` -> `outcome.py` -> `production.py` | The state object, the gates, one turn of the loop, how a letter lands | |
| 8 | One tool end to end: `tools/generate.py`, then `tools/check_claims.py` | How a claim's source is written and then verified | Other tools until you need them |
| 9 | `workflow.py` | The same tools in fixed order; a good check that you understood step 7 | |
| 10 | `gap_policy.py`, `answers.py`, `app/api/letters.py` | Pause and resume (Step 4.5) | |
| 11 | `app/obligation.py`, `app/api/obligation.py`, `extension/dashboard.js` | Centrelink periods and the Applied tab | |
| 12 | `extension/sidebar.js` by search, not top to bottom: `loadJobs`, `renderJob`, `connectEvents`, `scanPage` | How the UI reacts to SSE events | The profile editor parts |
| 13 | Only if working on Quick Apply: `app/screening/` in the order of 3.5, then `extension/screening_assist.js` | | |
| 14 | Tests: for any file above, its test file | The examples in tests are the clearest statement of intended behaviour | |

**Skip entirely unless your task needs them:** `app/scraper/` (dormant), `scripts/letter_lab.py` and the other eval scripts, `evals/`, the `docs/*-plan.md` files, `PROGRESS.md`, and `future_work/`. They explain how decisions were made, not how the program runs.

**A good first hands-on check** once you reach stop 7:
1. Start the test environment (`python scripts/run_api.py test`).
2. Open `app.db` in any SQLite viewer.
3. Read one `letter_runs` row and its `letter_run_steps`. If the test database has none yet, `evals/eval.db` has many.
4. Find each step in the Step 4 table (19a-j).

If you can, the pipeline has clicked.
