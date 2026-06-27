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
(SCAN_DELAY_MS = 5000), and a sane per-scan cap (MAX_SCAN_PAGES, currently 3 —
raise deliberately if needed, don't remove the cap).


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

Active provider: Groq (console.groq.com — free tier).
Model: llama-3.3-70b-versatile
Free-tier quota: 30 RPM, ~14,400 RPD — far more headroom than Gemini.

Used for BOTH job extraction AND cover-letter generation.
Always read from GROQ_MODEL env var — never hardcode the model name.

Provider abstraction: ALL LLM calls go through app/llm/client.py
(complete_json for structured extraction; complete_text for prose). The provider
and model live in exactly ONE place. Do not call the Groq/Gemini SDKs directly
from extract.py / match.py / cover-letter code.

Temperature:
  Extraction → 0.1 (deterministic, consistent structure).
  Cover letters → higher (~0.7) for natural prose.

Rate-limit handling: on HTTP 429, client.py backs off and retries (max 3).
Groq provides a retry-after header; delays > 300s are treated as daily exhaustion
→ DailyQuotaError. The idle processing loop (_processing_idle_loop in main.py)
serialises all LLM work through a single-worker executor and backs off 3 minutes
when extraction fails. Local throttle: LLM_RPM=8 (~7.5s spacing).

TLS note (this machine): Groq uses httpx internally. The AV does TLS interception
with a local CA cert that certifi doesn't trust. truststore's inject_into_ssl()
doesn't affect httpcore's start_tls path, so the Groq httpx client is created
with verify=False. Acceptable on a local dev machine with a trusted AV proxy.

Env vars (gitignored .env):
  GROQ_API_KEY — from console.groq.com
  GROQ_MODEL=llama-3.3-70b-versatile
  LLM_PROVIDER=groq
  LLM_RPM=8

Fallback: Gemini (google-genai SDK) is still wired in client.py.
Switch: set LLM_PROVIDER=gemini + GEMINI_API_KEY + GEMINI_MODEL in .env.
Gemini TLS works via truststore.inject_into_ssl() (urllib3 path, unlike httpx).

⚠️ Gemini quota history: gemini-2.5-flash-lite = 20 RPD (NOT per-minute).
gemini-2.0-flash = 1,500 RPD but requires a proper AIza* AI Studio key, not
AQ.* Cloud trial keys. Switched to Groq to avoid all of this.

Data privacy: Groq free-tier prompts may be used for model training.
Fine for public job-ad text; be aware for personal profile data.


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



🔄 CURRENT TASK: TBD

Cover-letter generation is done (2026-06-27). Next task not yet defined — see
docs/job-application-assistant-plan.md for the planned feature list.


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