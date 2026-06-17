# Job Application Assistant

A personal tool that helps a job seeker find suitable roles on **Seek** (au.seek.com.au)
and drafts **tailored cover letters** from their own profile — qualifications,
experience, and skills. The human reviews and submits every application manually; the
tool **never auto-submits and never makes automated requests to Seek**.

It runs today as a **local, single-user app** and is deliberately built to grow into a
**multi-user hosted** app later (the database is Postgres-ready; the URL is the only
thing that changes).

> **New here? Read in this order:** this README (the whole system) →
> `docs/database-schema.md` (the authoritative data model) → `CLAUDE.md` (working rules
> + current task) → `PROGRESS.md` (chronological build log).

---

## Table of Contents

1. [The Big Idea](#1-the-big-idea)
2. [Why the Architecture Looks Like This (the Cloudflare pivot)](#2-why-the-architecture-looks-like-this-the-cloudflare-pivot)
3. [System Architecture at a Glance](#3-system-architecture-at-a-glance)
4. [End-to-End Data Flow](#4-end-to-end-data-flow)
5. [The Data Model (11 tables)](#5-the-data-model-11-tables)
6. [Component 1 — The FastAPI Backend (`app/api/`)](#6-component-1--the-fastapi-backend-appapi)
7. [Component 2 — The Chrome Extension (`extension/`)](#7-component-2--the-chrome-extension-extension)
8. [Component 3 — The LLM Layer (`app/llm/`, stubbed)](#8-component-3--the-llm-layer-appllm-stubbed)
9. [The Retired Scraper (`app/scraper/`)](#9-the-retired-scraper-appscraper)
10. [Repository Layout](#10-repository-layout)
11. [Setup & Running It](#11-setup--running-it)
12. [Networking & Safety Policy (non-negotiable)](#12-networking--safety-policy-non-negotiable)
13. [Key Design Decisions](#13-key-design-decisions)
14. [Project Status & Roadmap](#14-project-status--roadmap)
15. [Developer Environment Quirks](#15-developer-environment-quirks)
16. [Glossary](#16-glossary)

---

## 1. The Big Idea

Job hunting has two tedious halves: **finding** roles that genuinely fit, and **writing**
a fresh, honest cover letter for each one. This tool automates the busywork around both
while keeping the human in control:

- **Capture** job listings the user is browsing on Seek into a local database.
- **Understand** each listing (extract its required skills and qualifications).
- **Match** it against the user's stored profile and score it 0–100.
- **Draft** a tailored cover letter grounded in the user's real experience.
- **Review** — the user edits and submits manually on Seek. Always.

The product's value is the **matching + drafting** intelligence. Getting the job data in
is just plumbing — and that plumbing is the part this project had to rethink (next
section).

---

## 2. Why the Architecture Looks Like This (the Cloudflare pivot)

The project originally shipped a **Playwright scraper** (a headless Chromium driven by
code) that would log into Seek through a proxy and crawl search results + job pages. It
**was abandoned on 2026-06-17**, and understanding *why* explains every architectural
choice that followed:

- **Seek is behind Cloudflare.** Any automated/headless browser gets served a
  "Just a moment…" challenge. Verified live: even with a real proxy **and a human
  solving the CAPTCHA by hand**, Cloudflare never trusts an automation-driven client —
  it re-issues the challenge on every page, an infinite loop.
- **Seek's `robots.txt` disallows the target paths** for generic agents
  (`Disallow: *?` covers every search URL; `Disallow: */job/` covers every detail page).
- **There is no free, candidate-side Seek API.** `developer.seek.com` is a B2B
  recruiter-integration product (job posting / ATS), gated behind partner approval, with
  no "search jobs" endpoint.
- Pushing past Cloudflare would require **anti-bot evasion** (fingerprint spoofing,
  CAPTCHA-solver services) — which this project will not build, on principle and because
  it's a losing, high-maintenance arms race that risks the user's IP **and account**.

**The pivot:** stop automating Seek entirely. The user already browses Seek in their
**own real browser**, where they pass Cloudflare naturally (real human, real session,
real IP). A **Chrome extension** simply *reads the page the user is already looking at*
and sends the data to a **local backend**. No crawler, no proxy, no detection surface,
no ToS tug-of-war. It's both more honest and more robust — and it's a better product
because it captures exactly the jobs the user actually cares about.

---

## 3. System Architecture at a Glance

```
   ┌─────────────────────────────────────────────────────────────────────┐
   │  USER'S REAL CHROME BROWSER (passes Cloudflare as a genuine human)    │
   │                                                                       │
   │   Seek page (/jobs?... or /job/12345)                                 │
   │        │                                                              │
   │        │  content_script.js reads the already-rendered DOM           │
   │        ▼                                                              │
   │   ┌──────────────────────┐        ┌───────────────────────────────┐  │
   │   │  Extension (MV3)     │        │  Side panel / popup           │  │
   │   │  • selectors.js      │        │  • sidebar.js  → GET /jobs     │  │
   │   │  • content_script.js │        │  • popup.js    → GET /health   │  │
   │   │  • background.js     │        └───────────────┬───────────────┘  │
   │   └──────────┬───────────┘                        │                  │
   └──────────────┼────────────────────────────────────┼──────────────────┘
                  │ POST /ingest (JSON)                 │ GET reads
                  ▼                                     ▼
   ┌─────────────────────────────────────────────────────────────────────┐
   │  LOCAL FASTAPI BACKEND  (app/api/main.py @ 127.0.0.1:8000)            │
   │   /health  /ingest  /jobs  /jobs/{id}  /jobs/{id}/regenerate         │
   │   /profile/{id} (GET/PUT)                                            │
   │        │ upsert listings              │ fire BackgroundTasks         │
   │        ▼                              ▼                              │
   │   ┌──────────────────┐      ┌──────────────────────────────────┐    │
   │   │ SQLAlchemy ORM   │      │ app/llm/  (STUBS today)          │    │
   │   │ app/models.py    │◀─────│  extract_job() → job_skills      │    │
   │   └────────┬─────────┘      │  match_job()   → matches + CL    │    │
   │            ▼                └──────────────────────────────────┘    │
   │   ┌──────────────────┐                                              │
   │   │ SQLite (app.db)  │  ← Postgres-ready (DATABASE_URL swap)        │
   │   └──────────────────┘                                              │
   └─────────────────────────────────────────────────────────────────────┘
```

**Tech stack:** Python 3.11+, SQLAlchemy 2.0 (typed), Alembic (migrations), FastAPI +
uvicorn, SQLite (local) / Postgres (hosted), Chrome Manifest V3 extension (vanilla
JS/HTML — no build step).

---

## 4. End-to-End Data Flow

Follow one job from browser to cover letter:

1. **Browse.** The user opens a Seek search (`/jobs?keywords=…`) or a job
   (`/job/12345678`) in their normal Chrome. Cloudflare is satisfied — it's a real human.
2. **Capture.** The injected `content_script.js` waits for Seek's React app to render,
   then reads the DOM using the shared `data-automation` selectors:
   - On a **search page** it harvests every job *card* (id, url, title, company,
     location, work type, salary) — no description yet.
   - On a **detail page** it harvests the full ad body (`raw_description`).
3. **Ingest.** The script `POST`s the listings to `http://localhost:8000/ingest`.
4. **Upsert.** The backend inserts new listings (`new`), and for a listing already
   captured as a card it **backfills `raw_description`** when the detail page arrives
   (`updated`). Duplicates are ignored (`source` + `source_job_id` unique).
5. **Process (background).** For each new/updated listing the backend fires a
   `BackgroundTask`:
   - `extract_job(job_id)` → pull `job_skills` + `*_requirements` from the description
     *(stub today)*.
   - `match_job(job_id, profile_id)` → score the job against the profile, write a
     `matches` row + a draft `cover_letters` row *(stub today)*.
6. **Review.** The extension's **side panel** calls `GET /jobs` and lists matches ranked
   by score; clicking one shows the drafted cover letter (`GET /jobs/{id}`).
7. **Apply.** The user edits the letter and submits the application **manually** on Seek.

---

## 5. The Data Model (11 tables)

> **`docs/database-schema.md` is the source of truth.** `app/models.py` implements it as
> SQLAlchemy 2.0 typed models. If anything here disagrees with the schema doc, the doc
> wins.

The schema is two halves that **meet at `matches`**:

```
   PROFILE SIDE (one user → many …)            JOB SIDE (global, shared)
   ┌─────────────┐                             ┌──────────────┐
   │  profiles   │──┐                          │ job_listings │──┐
   └─────────────┘  │ 1─*                       └──────────────┘  │ 1─*
        │ 1─*       ├── saved_searches               │           └── job_skills
        │           ├── qualifications                │
        │           ├── experiences ──┐               │
        │           ├── skills ───────┤ M─N           │
        │           │   (experience_skills junction)  │
        │           └── user_cvs                       │
        │                                              │
        └──────────────►  matches  ◄───────────────────┘
                            │ 1─1
                            └── cover_letters
```

**Profile side**
- **`profiles`** — the user account. `email` unique; `password_hash` (auth not
  implemented yet, just the column); `visa_status`, `dob`.
- **`saved_searches`** — per-user search criteria (`keywords`, `location`, `work_type`,
  `salary_min`, `is_active`). A relic of the scraper era; still useful as saved filters.
- **`qualifications`** — formal credentials (degrees, certs, licenses) with type, title,
  institution, dates, status.
- **`experiences`** — everything the user has done (paid or not); `on_cv` flag; M-N to
  skills.
- **`skills`** — one row per distinct skill per user; unique `(user_id, name)`.
- **`experience_skills`** — **pure junction** (composite PK of the two FKs). Deliberately
  has **no `strength`/relevance column** — relevance to a job is judged by the LLM at
  generation time, never stored.
- **`user_cvs`** — master CV(s) at the **user** level (never per-job); `is_default`.

**Job side**
- **`job_listings`** — the **global pool** of captured jobs, stored once and shared
  across users. Holds card fields (`title`, `company`, `location`, `classification`,
  `subclassification`, `work_type`, `salary`) plus `raw_description` and the
  LLM-extracted `qualification_requirements` / `experience_requirements`. Unique
  `(source, source_job_id)`.
- **`job_skills`** — skills extracted from a listing. **Not** FK'd to `skills`: job
  skills are matched to user skills *by name* at match time, so the two vocabularies stay
  independent.

**The bridge**
- **`matches`** — per-user relevance linking a `profile` to a `job_listing`. **`score`
  (0–100) is the single source of truth** — there is intentionally **no tier column**;
  strong/medium/reach buckets are derived at display time. Carries `reasoning`, `gaps`,
  `status`, and `cv_used_id` (`ON DELETE SET NULL`). Unique `(user_id, job_id)`.
- **`cover_letters`** — exactly **one row per match** (`match_id` unique). Holds both
  `generated_content` and `edited_content` with a `status` of draft/edited/final.

**Portability mechanics (don't "fix" these back to SQLite-isms):** integer PKs use
`BigInteger().with_variant(Integer, "sqlite")`; boolean server defaults use
`text("false")`/`text("true")`; timestamps use `DateTime(timezone=True)` + `func.now()`;
ORM relationships set `passive_deletes=True` so deletes rely on DB-level
`ON DELETE CASCADE`. SQLite FK enforcement is turned on via a `PRAGMA foreign_keys=ON`
connect-listener in `app/db.py`.

---

## 6. Component 1 — The FastAPI Backend (`app/api/`)

A small local API the extension talks to. Entry point: `scripts/run_api.py`
(uvicorn on `127.0.0.1:8000`, `reload=True`). All of it lives in `app/api/main.py`.

**Cross-cutting**
- **CORS:** `allow_origins=["*"]`, `allow_credentials=False` (open for local dev; the
  extension calls from a `chrome-extension://` origin). Lock down to the extension id
  before any hosted deployment.
- **DB session:** a `get_db()` dependency yields a `SessionLocal()` and closes it per
  request — the standard FastAPI pattern.
- **Background work:** uses FastAPI `BackgroundTasks`. Because the request session is
  closed by the time a task runs, the LLM functions must open their own session when
  implemented.

**Endpoints**

| Method & path | Purpose |
|---|---|
| `GET /health` | Liveness probe → `{"status":"ok","profile_id":<id\|null>}`. The popup uses it. |
| `POST /ingest` | Upsert listings; **backfill** `raw_description` on a card-only row when its detail page arrives; fire extraction+matching tasks. Returns `{"received","new","updated"}`. |
| `GET /jobs` | Matched jobs for a profile, ranked by `score` desc. Params: `profile_id=1`, `min_score=0`, `limit=50`, `offset=0`. Each item carries `job_id,title,company,location,url,score,reasoning,gaps,status,has_cover_letter`. |
| `GET /jobs/{id}` | Full job detail + this profile's `match` + `cover_letter` (or `null`). `404` if the job is unknown. |
| `POST /jobs/{id}/regenerate` | Queue re-extraction + re-match for one job → `{"status":"queued"}`. |
| `GET /profile/{id}` | Profile with nested `qualifications`, `experiences`, `skills`, `cvs`. `404` if absent. |
| `PUT /profile/{id}` | Partial update of `name` / `email`. |

**The ingest upsert rule (the heart of it):** insert when `(source='seek',
source_job_id)` is new; if the row exists but has **no** description and the new payload
carries one, set it; otherwise skip. This lets the cheap search-card capture and the
richer detail capture **compose** — you get breadth from search pages and depth on the
jobs you actually open.

---

## 7. Component 2 — The Chrome Extension (`extension/`)

A **Manifest V3** extension, vanilla JS/HTML, **no build step** — load it unpacked.

| File | Role |
|---|---|
| `manifest.json` | MV3 config. Content scripts on `https://www.seek.com.au/jobs*` and `/job/*`; host permissions for Seek + `localhost:8000`; declares the side panel, popup, and service worker. |
| `selectors.js` | The `SELECTORS` map of Seek `data-automation` attributes + `extractJobId()`. **Mirror of `app/scraper/selectors.py` — keep the two in sync by hand.** |
| `content_script.js` | Injected into Seek pages. Detects search vs detail, **polls** for the React-rendered content (up to ~8 s), parses it, and `POST`s to `/ingest`. Reads only the page the user already opened. |
| `background.js` | Service worker. Keeps a session badge count and relays `INGEST_DONE` messages. |
| `popup.html` / `popup.js` | Toolbar popup: shows backend health (green/red) + session count, and an "Open Sidebar" button. |
| `sidebar.html` / `sidebar.js` | Side panel: fetches `GET /jobs`, renders the ranked match list with score badges; clicking a job expands its cover letter via `GET /jobs/{id}`. |

**Capture is passive by design.** The extension reads pages the user chooses to open;
it does **not** auto-navigate, paginate, or open job pages on its own. That boundary is
deliberate — programmatic navigation inside the user's authenticated session could get
their **Seek account** flagged, which is far worse than an IP block. (See §12.)

---

## 8. Component 3 — The LLM Layer (`app/llm/`, stubbed)

Two functions define the contract the backend's background tasks call. Both are
**stubs** today (they log `TODO: implement` and return), which lets the entire
ingest → process pipeline run end-to-end before the AI work lands.

- **`extract.py` → `extract_job(job_id)`** — will read `job_listings.raw_description`,
  ask an LLM to pull out skills + qualification/experience requirements, and populate
  `job_skills` and the `*_requirements` columns.
- **`match.py` → `match_job(job_id, profile_id)`** — will score the job against the
  profile (0–100), write `reasoning`/`gaps`, upsert the `matches` row, and generate a
  draft `cover_letters` row grounded in the user's real `experiences`/`skills`.

This is the **next task** for the project.

---

## 9. The Retired Scraper (`app/scraper/`)

Kept in the repo on purpose — **not** part of the active pipeline, and **must not be
re-activated** (it makes automated requests to Seek; see §2 and §12). Its lasting value:

- **`selectors.py`** — the canonical Seek selector definitions + search-URL builder.
  `extension/selectors.js` mirrors it; treat the Python file as the reference.
- `browser.py` — the (dormant) proxy-enforced Playwright launcher.
- `search.py` / `detail.py` — card/description parsing logic (now reimplemented in JS).
- `run.py` — the old orchestration (search → cap → detail → upsert).
- `interactive.py` + `scripts/scrape_interactive.py` — the human-in-the-loop attempt
  that also dead-ended on Cloudflare.
- `scripts/explore_seek.py` — a one-shot page-cacher; still handy for **offline** selector
  inspection if you ever save a page by hand.

---

## 10. Repository Layout

```
Job_Apply_Agent/
├── README.md                 ← you are here (the whole system)
├── CLAUDE.md                 ← agent rules, networking policy, current task
├── PROGRESS.md               ← chronological build log (newest on top)
├── requirements.txt
├── alembic.ini  /  alembic/  ← migrations (initial schema + classification cols)
├── app.db                    ← local SQLite (gitignored)
├── docs/
│   └── database-schema.md     ← AUTHORITATIVE data model
├── app/
│   ├── db.py                  ← engine + SessionLocal + Base + SQLite FK pragma
│   ├── models.py              ← the 11 tables (SQLAlchemy 2.0 typed)
│   ├── api/
│   │   └── main.py            ← FastAPI app + all endpoints
│   ├── llm/
│   │   ├── extract.py         ← STUB: job-skill/requirement extraction
│   │   └── match.py           ← STUB: scoring + cover-letter generation
│   └── scraper/               ← RETIRED (selector logic retained; do not re-run)
├── extension/                 ← Manifest V3 Chrome extension (load unpacked)
│   ├── manifest.json
│   ├── selectors.js           ← mirror of app/scraper/selectors.py
│   ├── content_script.js
│   ├── background.js
│   ├── popup.html / popup.js
│   └── sidebar.html / sidebar.js
└── scripts/
    ├── run_api.py             ← start the backend (uvicorn :8000)
    ├── seed_saved_search.py   ← create test profile (id 1) + a saved search
    ├── smoke_test.py          ← DB-layer end-to-end check
    └── (retired scraper CLIs: run_scrape.py, explore_seek.py, scrape_interactive.py)
```

---

## 11. Setup & Running It

**Prerequisites:** Python 3.11+, Google Chrome.

```powershell
# 1. Virtualenv + dependencies
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
#   ↑ On THIS machine, antivirus TLS interception breaks pip's cert check. If you see
#     "unable to get local issuer certificate", add:
#       --trusted-host pypi.org --trusted-host files.pythonhosted.org

# 2. Build / migrate the database
.venv\Scripts\python.exe -m alembic upgrade head

# 3. Seed a test profile (id 1) + a saved search
.venv\Scripts\python.exe scripts\seed_saved_search.py

# 4. Run the backend (leave it running)
.venv\Scripts\python.exe scripts\run_api.py      # → http://127.0.0.1:8000
```

**Load the extension:** Chrome → `chrome://extensions` → enable **Developer mode** →
**Load unpacked** → select the `extension/` folder.

**Use it:** browse Seek (logged into your normal account). Open a search page, then a
job. Open DevTools → Console; you should see `[SeekAssistant] Captured N cards…`. The
side panel (extension toolbar icon → Open Sidebar) lists what's been matched.

**Smoke-testing the API** (use PowerShell's `Invoke-RestMethod`, **not** `curl.exe` —
PowerShell mangles embedded JSON quotes):

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
$body = @{ listings=@(@{ source_job_id="1"; url="https://www.seek.com.au/job/1"; title="Test" }); profile_id=1 } | ConvertTo-Json -Depth 6
Invoke-RestMethod http://127.0.0.1:8000/ingest -Method Post -ContentType application/json -Body $body
```

---

## 12. Networking & Safety Policy (non-negotiable)

The full policy lives in `CLAUDE.md`; the essence:

- **No automated requests to Seek. Ever.** All Seek data must come from the user's own
  browser actions via the extension. Do **not** add code (Playwright, `WebFetch`,
  `requests`, etc.) that fetches a Seek URL programmatically.
- **No proxy needed anymore.** The app touches no scrape target directly. (The old
  Windscribe proxy was uninstalled; the dormant `PROXY_SERVER` plumbing is harmless.)
- **Passive capture only.** The extension reads pages the user opens; it must not
  auto-navigate or harvest in the background — that risks the user's **Seek account**.
- **The human submits.** The tool drafts; the user reviews, edits, and applies manually.
- **Respect `robots.txt`/ToS.** Seek asks bots not to crawl `*?` and `*/job/`; the
  passive-capture design sidesteps this by never crawling.

---

## 13. Key Design Decisions

- **Capture from the real browser, not a crawler.** The core architectural bet (§2):
  more honest, more robust, zero detection surface, and captures exactly the jobs the
  user cares about.
- **`matches.score` is the only ranking truth.** No tier column; buckets are derived at
  display time so the threshold can change without a migration.
- **Job skills are name-matched, not FK'd.** `job_skills` and user `skills` are separate
  vocabularies reconciled by the LLM at match time.
- **Listings are global; matches are per-user.** One `job_listings` row is shared by all
  users; relevance lives in `matches`. This is what makes multi-user cheap later.
- **Search-card + detail capture compose via backfill.** Breadth from search pages, depth
  on opened jobs, deduped on `(source, source_job_id)`.
- **DB portability is load-bearing.** Everything is dialect-neutral so a Postgres
  `DATABASE_URL` works with no code change.
- **Selectors have one source of truth** (`app/scraper/selectors.py`) mirrored into the
  extension — fix both together when Seek's DOM shifts.

---

## 14. Project Status & Roadmap

**Done & verified**
- ✅ **Database layer** — all 11 tables, Alembic migrations, smoke test (2026-06-15).
- ✅ **FastAPI backend** — all endpoints built and acceptance-tested against live uvicorn
  (health, ingest + dedup + description backfill, jobs, profile, 404s) (2026-06-17).
- ✅ **Chrome extension** — built (MV3). **Pending its first live capture**, which is also
  the first real check of the `data-automation` selectors against Seek's live DOM.

**Stubbed (the next task)**
- ⏳ `app/llm/extract.py` — real job-skill / requirement extraction.
- ⏳ `app/llm/match.py` — real 0–100 scoring + cover-letter drafting.

**Not built yet**
- Profile-management UI (creating/editing qualifications, experiences, skills, CVs).
- Authentication logic (column exists; no login flow).
- Hosted multi-user deployment + Postgres migration.

**Immediate next step for the user:** do the first live capture and confirm the
selectors. If a search page yields `Captured 0 cards`, the `data-automation` selectors
need updating in **both** `extension/selectors.js` and `app/scraper/selectors.py`.

---

## 15. Developer Environment Quirks

- **pip TLS interception (this machine).** Antivirus HTTPS scanning presents a cert pip's
  bundle doesn't trust → `CERTIFICATE_VERIFY_FAILED: unable to get local issuer
  certificate`. Work around with `--trusted-host pypi.org --trusted-host
  files.pythonhosted.org`. (Independent of the now-removed Windscribe.)
- **PowerShell + `curl.exe` + JSON.** Windows PowerShell strips/mangles embedded
  double-quotes when passing a JSON `-d` body to native `curl.exe`. Use
  `Invoke-RestMethod` with a hashtable + `ConvertTo-Json` instead.
- **Windows line endings / paths.** Dev happens on Windows (`.venv\Scripts\python.exe`).
  Keep code OS-neutral; the app itself has no Windows-only dependencies.

---

## 16. Glossary

- **Listing** — a captured Seek job (`job_listings` row). Global, shared across users.
- **Card** — the summary of a job on a search results page (no description).
- **Detail page** — `/job/{id}`, carrying the full ad body (`raw_description`).
- **Match** — a per-user relevance record (`matches` row) with a 0–100 `score`.
- **Ingest** — the `POST /ingest` call by which the extension hands listings to the API.
- **Backfill** — filling a card-only listing's `raw_description` when its detail page is
  later captured.
- **`data-automation`** — the stable attribute Seek puts on elements; the basis of all
  selectors.
- **Source of truth** — `docs/database-schema.md` for the data model;
  `app/scraper/selectors.py` for selectors; `matches.score` for ranking.
