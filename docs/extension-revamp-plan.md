# Extension Revamp Plan — Centrelink-Ready Job Application Assistant

**Status: planning only, nothing in this doc has been implemented yet.**
Written 2026-09-11 for a future session to execute. Read this whole file before
touching code — the sections build on each other and several decisions
(idle-loop reordering, score tiers) are deliberately chosen to interact.

## 0. Why this revamp (context for whoever implements it)

The user is using this tool as a real job seeker who needs to (a) find and
apply to good-fit roles efficiently, and (b) keep a clean record of
applications for Centrelink mutual-obligation / job-plan reporting ("get easy
credit on job seeker"). That second goal is new and changes priorities:
volume of applications matters less than having an accurate, exportable log
of what was actually applied to and when. Everything below is designed around
that, not just "make the UI prettier."

Hard constraints that do **not** change (restated from CLAUDE.md — do not
relax these while doing this work):
- The tool **never auto-submits** anything, to Seek or to Centrelink. Every
  "mark applied" / "copy cover letter" action is a manual click.
- The 1-hop scan policy (`extension/sidebar.js` `MAX_SCAN_PAGES`/`SCAN_DELAY_MS`,
  `extension/content_script.js` `collectJobLinks`) is untouched — no second
  hop, no crawling.
- `matches.score` stays the single source of truth. Score **tiers** (blue/
  green/amber/hidden) introduced below are a *display-time* bucketing done in
  JS, never a stored column — this matches the existing schema decision
  ("no tier column — buckets are derived at display time," docs/database-schema.md).
- All LLM calls still go through `app/llm/client.py`. The one new feature
  that might tempt an LLM call (search-phrase suggestions, §5) is designed to
  need **zero** extra LLM calls — keep it that way.

---

## 1. Score tiers — the vocabulary used everywhere below

| Tier | Score range | Color | Card treatment |
|---|---|---|---|
| **Blue** | ≥ 90 | neon blue | Largest card: full title, company/location, score badge, first line of `match.reasoning`, top 2–3 `job_skills`, cover-letter status inline. This is the "apply now" tier. |
| **Green** | ≥ 75 (reuse `cover_letter.THRESHOLD`) | green | Medium card: title, company/location, score, cover-letter status. No reasoning preview. |
| **Amber** | 60–74 | amber/orange | Compact single-line card: title + score only, meta on hover/expand as today. |
| **Long tail** | < 60 | grey, no color | Hidden by default behind a collapsed **"N other roles"** fold at the bottom of the list. Not deleted — bulk-delete stays a separate, explicit user action. |

Reusing 75 as both the green cutoff *and* the existing cover-letter
`THRESHOLD` is deliberate: it means "green or better" and "eligible for an
auto-generated cover letter" are always the same set of jobs. Don't invent a
second magic number here.

All bucketing happens client-side in `sidebar.js` after the existing
`GET /jobs` call — no backend change needed for this part. `/jobs` already
returns everything ≥ `min_score` (default 0), so fetch as today and bucket in
`renderJob`/`loadJobs`.

---

## 2. UI layout changes (`extension/sidebar.html`, `sidebar.js`)

### 2.1 Panel width
Chrome side panels are user-resizable by dragging the edge; there is no
manifest/API setting for a wider *default* width (Manifest V3 `side_panel`
has no width key). So "make it bigger" is a **fluid-layout** problem, not a
fixed-width one:
- Audit `sidebar.html`'s CSS for any fixed `px` widths on containers (currently
  none of consequence — good, it's already fluid). Keep it that way.
- Add a width breakpoint (e.g. `@media (min-width: 420px)`) so that once the
  user *does* drag it wider, blue-tier cards switch to a two-column meta
  layout (score/company/location side-by-side instead of stacked) rather than
  just leaving whitespace. This is the concrete, buildable version of "assign
  more space to each good opportunity."
- Mention to the user once, in the panel itself (small hint text under the
  header, dismissible via `localStorage`), that dragging the left edge of the
  panel resizes it — most users don't know Chrome side panels do this.

### 2.2 Card markup/CSS (`renderJob` in sidebar.js, styles in sidebar.html)
- Add a `data-tier="blue|green|amber|hidden"` attribute on each `<li>`,
  computed from `job.score` at render time.
- New CSS per tier: background tint + left border accent color + font-size/
  padding scale (blue largest, amber smallest). Use CSS custom properties
  (`--tier-color`, `--tier-bg`) set inline per card rather than four near-
  duplicate class blocks, to keep the stylesheet short.
- Blue-tier cards render a one-line reasoning snippet (`truncate(job.reasoning, 90)`)
  and up to 3 skill chips directly in the collapsed state — everything else
  stays click-to-expand as today. This needs `reasoning` and skills in the
  `/jobs` response; `reasoning` is already returned, skills are not — see §2.3.
- Long-tail (<60) cards are not rendered individually into `#job-list` at all;
  instead accumulate them into a count and render one collapsed `<li class="fold">N other roles — click to show</li>` at the end. Clicking it renders
  the rest in the existing compact style. Keep them in the DOM lazily (only
  build on expand) so a backlog of 50 low scorers doesn't cost render time
  up front.

### 2.3 Backend: add `job_skills` (top hard skills) to `GET /jobs`
Small addition to `app/api/main.py::list_jobs` — eager-load `JobListing.job_skills`
and include e.g. the first 3 `hard`-type skill names per row, so blue cards
can show them without a second round trip per card. This is the only backend
change needed for §2.

---

## 3. Idle-loop reprioritization + "stop drowning in mediocre jobs"

This is the literal ask: *"if we are getting below a certain score we don't
load the applications anymore, and instead start making cover letters for the
ones we have scored well already."*

Current order in `_processing_idle_loop` (`app/api/main.py:84-184`):
**Phase 1 extract → Phase 1b match → Phase 2 cover letters.** New extraction/
matching work always preempts cover letters, every iteration.

**Change: flip the priority.** Check Phase 2 (cover letters for existing
score ≥ 75 matches with no letter yet) **first**, every iteration; only fall
through to extraction/matching when there is no cover-letter backlog. This
directly implements "when we're not producing more great matches, spend the
idle time finishing letters for the ones we already know are great" — it's a
~10-line reorder of the existing three blocks, no new state needed. Given
`MAX_SCAN_PAGES=4` per scan and `_IDLE_INTERVAL_S=20s`, the worst-case delay
before a brand-new blue-tier job gets extracted/matched is a handful of
20-second slots — acceptable, and correct for a tool where the point is
finishing high-value letters, not raw throughput.

Do not add a hard "stop extracting below score X" rule — you can't know the
score before extracting + matching, so that phase must always run on new
listings eventually. The reprioritization above is what actually delivers the
requested behavior without breaking that.

No other idle-loop logic changes (backoffs, DailyQuotaError handling, RPM
throttle all stay as-is).

---

## 4. Application tracking for Centrelink reporting (biggest schema change)

This is new functionality, not a UI tweak — it's the actual point of the
revamp per the user's framing ("monitor all the jobs I apply to... get easy
credit").

### 4.1 Schema change
- Add `matches.applied_at: DateTime(timezone=True) | None` (nullable). One
  Alembic migration.
- Start actually using `matches.status` (it exists, defaults to `"new"`, but
  nothing ever sets it to anything else today). Document the vocabulary in
  **docs/database-schema.md** (source of truth per CLAUDE.md — update it
  alongside the migration, don't let it drift):
  `new → shortlisted → applied → interviewing → rejected / withdrawn`.
  Only `applied` needs new backend support in this pass; the others are free
  bookkeeping for later.

### 4.2 Backend
- `PATCH /jobs/{job_id}/status` — body `{status: str}`, sets `Match.status`
  and, when transitioning to `"applied"`, stamps `applied_at = now()`.
  Idempotent: re-marking applied doesn't reset the timestamp.

### 4.3 Frontend — "Applied" tracking
- Each card gets a **"Mark Applied"** action (button or checkbox icon,
  distinct from the existing ✚ generate / 🗑 delete icons).
- Add a lightweight **Applied** view: either a third tab next to Jobs/Profile,
  or a toggle at the top of the Jobs tab ("Showing: Active / Applied"). Prefer
  a third tab — keeps the Jobs list's tiering logic (§1) uncluttered by mixing
  in a totally different sort order (applied_at desc, not score desc).
- **CSV export**, client-side only (mirror the existing
  `export-profile-btn` → JSON blob pattern, just format as CSV instead of
  JSON — no backend endpoint needed since `/jobs?status=applied` already has
  everything required). Columns: `Date Applied, Job Title, Employer,
  Location, Source URL`. This is the artifact the user pastes into
  Centrelink's online reporting — keep it dead simple, no extra columns that
  need explaining.

---

## 5. "Cover letter ready" UX + Quick-Apply overlay on Seek itself

### 5.1 Card-level cleanup (`sidebar.js`)
Replace the current always-there ✚ button + inline-blurb-on-expand with a
state machine per card:
- **No letter, score < 75:** no cover-letter affordance in the collapsed
  card. (A manual "force generate anyway" option can live in the expanded
  detail view only, calling `/regenerate` which already supports
  `bypass_threshold=True` — don't remove that capability, just don't surface
  it as a first-class button.)
- **No letter yet, score ≥ 75, idle loop hasn't reached it:** small "Cover
  letter pending…" label (it'll arrive via the existing `cover_letter_ready`
  SSE event — no polling needed for this state, the current `pollForCoverLetter`
  can be dropped once SSE reliably drives this).
- **Letter ready:** a clear badge — `✅ Cover letter ready` — replacing the
  ✚ icon. Clicking it expands the letter with a **Copy** button (`navigator.clipboard.writeText`) and an editable `<textarea>` that saves back via the
  new endpoint in §5.3.

### 5.2 Quick-Apply floating panel on Seek (`extension/content_script.js`)
`content_script.js` already runs on every `au.seek.com/*` and
`www.seek.com.au/*` page at `document_idle` (see `manifest.json` — no new
`host_permissions` needed). Extend it:
- On any page where `path` matches `/job/(\d+)` (the existing detail-page
  regex `extractJobId` already handles this), after the existing capture
  logic runs, also `fetch(`${BACKEND}/jobs/${job_id}?profile_id=1`)`.
- If a cover letter exists for that job, inject a small floating panel
  (fixed position, bottom-right, **Shadow DOM** to avoid any CSS bleed
  to/from Seek's own styles) showing: job title, the cover letter in an
  editable textarea, **Copy**, **Save edits**, and **Mark Applied** buttons.
  Collapsible to a small tab so it doesn't obstruct the page.
- **Important — do not guess Seek's Quick Apply URL/DOM structure.** The
  implementing session must have the user open a real Seek job and click
  through the actual Apply/Quick Apply flow once, then inspect the resulting
  URL and DOM (still just reading pages the user opened themselves — no
  policy change) to add the right selector(s) to `extension/selectors.js`
  following its existing pattern. Ship the panel keyed off the detail page
  first (`/job/{id}`) since that's confirmed to work today, and treat
  apply-flow detection as a fast-follow once verified live.

### 5.3 New backend endpoint
- `PATCH /jobs/{job_id}/cover-letter` — body `{edited_content: str}` → sets
  `CoverLetter.edited_content` and `status='edited'`. Both the sidebar's
  expanded card view (§5.1) and the Quick-Apply overlay (§5.2) call this same
  endpoint, so edits made from either surface stay in sync.

---

## 6. Cover-letter personalization — mostly already works, tighten what's left

Checked `app/llm/cover_letter.py::_build_prompt` — it **already** includes
`profile.name`, every qualification (title, institution, field, status), and
every experience with an evidence map back to job-required skills. So "give
context about my name and where I went" is largely a **data-completeness**
issue (has the Profile tab actually been filled in / imported from Seek?),
not a missing code path. Two small prompt improvements are still worth
making:
- Add one line to `_SYSTEM_PROMPT` or the end of `_build_prompt`'s
  instructions: *"If the candidate is a recent or current graduate, foreground
  their degree and university by name in the opening paragraph."* — ties into
  the same new-grad-fairness philosophy already used in `match.py`.
- The current system prompt explicitly says "no date headers — just the body
  of the letter," which as written also suppresses a sign-off. Add: *"End
  with a one-line sign-off: 'Sincerely, {name}'"* — pass the name in cleanly
  rather than relying on the model to infer it belongs at the end.

Action item that isn't a code change: confirm the Profile tab actually has
name + qualifications (institution) + experiences filled in (via the
existing "Import from Seek Profile" button or manual entry) — the LLM can
only use what's stored in `profiles`/`qualifications`/`experiences`.

---

## 7. Search-phrase suggestions from good matches

Goal: mine titles of green/blue-tier jobs to suggest new search terms,
surfaced "somewhere clean" — occasionally, not naggy.

- **No LLM call for this** — keep it pure Python, it's a frequency-count
  problem, not a reasoning problem, and the point of the split-model
  strategy (CLAUDE.md LLM Layer) is not spending model calls where simple
  logic suffices.
- New backend endpoint `GET /jobs/suggested-searches?profile_id=1`: pull
  `JobListing.title` for all matches with `score >= 75`, tokenize, count
  bigrams/trigrams after stripping stopwords and seniority filler ("Graduate",
  "Senior", company-specific junk), rank by frequency, exclude phrases that
  already match an active `SavedSearch.keywords` (table exists, currently
  unused — this is its first real consumer). Return top 3.
- Frontend: a small dismissible banner under the header, above `#bulk-bar`
  — *"💡 Try searching: 'Graduate Software Engineer'"* with an `×` to dismiss
  (store dismissal + a re-show cooldown in `chrome.storage.local`, e.g. don't
  reshow for 7 days or until N more good matches accumulate). Clicking the
  suggestion opens a fresh Seek search results page in a new tab for that
  phrase — this is a normal user-initiated page open, not a fetch, so it's
  still 0-hop and doesn't touch the scan policy.

---

## 8. Implementation order for the next session

Do these roughly in order — each is independently shippable and testable:

1. **Migration**: add `matches.applied_at`; update docs/database-schema.md's
   status vocabulary note.
2. **Backend**: `PATCH /jobs/{id}/status`, `PATCH /jobs/{id}/cover-letter`,
   `GET /jobs/suggested-searches`; add top-3 hard skills to `GET /jobs`.
3. **Idle loop reorder** (`app/api/main.py`) — cover letters before extract/match.
4. **sidebar.html/css**: tier colors + card sizing + collapsed long-tail fold
   + Applied tab + suggestion banner markup.
5. **sidebar.js**: client-side tiering/render, mark-applied + CSV export,
   cover-letter state machine, suggestion fetch/dismiss, drop the old
   `pollForCoverLetter` in favor of SSE once confirmed reliable.
6. **content_script.js + selectors.js**: detail-page Quick-Apply floating
   panel (Shadow DOM). Apply-flow-specific detection is a fast-follow once
   the real Seek apply DOM/URL has been inspected live (see §5.2 warning —
   don't guess selectors).
7. **cover_letter.py**: the two prompt additions in §6.
8. Update **CLAUDE.md**: replace the `CURRENT TASK: TBD` section with a
   pointer to this plan and demote finished pieces to `✅ COMPLETED` entries
   as they land, per the file's own existing convention.

## 9. Guardrails to re-read before touching code

- No auto-submission of anything, ever — this revamp adds "Mark Applied" as
  a **manual** log entry, not a submission action.
- No change to the 1-hop scan policy or `MAX_SCAN_PAGES`/`SCAN_DELAY_MS`.
- No new `host_permissions` — everything here fits inside `au.seek.com/*`
  already granted.
- `matches.score` stays the only stored source of truth; tiers are computed,
  never persisted.
- Don't add an LLM call for the search-suggestion feature — keep it free.
- Any schema change here must be mirrored in docs/database-schema.md in the
  same commit — CLAUDE.md is explicit that the schema doc wins on conflicts.
