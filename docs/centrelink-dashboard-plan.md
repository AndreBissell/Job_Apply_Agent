# Plan: Centrelink dashboard (Overview tab + monthly Applied periods)

## Context

The app's short-term job is Centrelink JobSeeker: the user must apply to **20 jobs in each
1-month period** to keep getting paid. Right now the sidebar has Jobs | Applied | Profile.
Jobs is one long list, and Applied is a flat list with no idea of the monthly period. This
plan adds:

1. An **Overview** tab (first tab, opens by default): progress this period (`x/20`), the
   jobs with a letter ready to apply to (horizontal cards), and 3 suggested searches.
2. A rebuilt **Applied** tab: one collapsible section per monthly period with the count,
   the AI cost for those applications, and each job with a **+ Interview** button.

What the interview button triggers later is out of scope; for now it only records the
interview.

**Timing:** 9c is done and committed on `centerlink-preperation` (tip `e8b8ba7`,
re-checked 2026-10-05: no code this plan references changed). Build on that branch. First step: copy this plan
to `docs/centrelink-dashboard-plan.md` (repo convention for plans) and point CLAUDE.md's
CURRENT TASK at it.

### Decisions (user, 2026-10-05)
- **Cost per job = all AI spend tagged to that job** (`llm_usage.job_id`): scoring, the
  letter (agent/workflow/one-shot) and Quick Apply help.
- The **⚙ Personalise metrics** settings panel **stays on Jobs**. Overview shows only
  progress.
- Applied periods are **newest first, with the current period expanded**.

### Defaults chosen (change if wrong)
- The target is a setting (default 20) and the start date is set by the user. Periods run
  monthly from the start date in both directions, so changing the date re-buckets all of
  history consistently. Start date 31st → clamps to the month's last day (30 Apr, 31 May).
- Period end = next start − 1 day, shown as `07/10/2026 – 06/11/2026` (dd/mm/yyyy, as in
  the user's example).
- "Applied" means `applied_at IS NOT NULL`, counted on the **local** calendar date (server
  local time, the same rule as `app/llm/usage.py::local_day_start_utc`). Applied jobs count
  even if later hidden by the bulk-hide action, because they are Centrelink evidence.
- +Interview stores a timestamp and does **not** change `status`. Existing
  `status == 'applied'` queries (Applied CSV `main.py:762`, evidence export `main.py:951`,
  retention) keep working untouched. Clicking it again undoes it.

## Layout

```
┌ Seek Job Assistant [REAL]      [Scan Page][Refresh] − + ┐
│ Overview │  Jobs  │ Applied │ Profile                   │
├─────────────────────────────────────────────────────────┤
│  7 / 20  applied this period                            │
│  ███████░░░░░░░░░░░░                                    │
│  07/10/2026 – 06/11/2026 · 12 days left · 13 to go  ✎  │
│  ❓ 2 letters need your answer ›        (only if any)   │
│                                                         │
│  KEEP APPLYING  · 6 letters ready                ‹ ›    │
│  ┌──────────┐┌──────────┐┌──────────┐┌───              │
│  │ 92       ││ 88       ││ 86       ││                  │
│  │Data      ││Junior BI ││Graduate  ││  ← scrolls       │
│  │Analyst   ││Developer ││Engineer  ││                  │
│  │Acme Pty  ││Contoso   ││Initech   ││                  │
│  │[Open][⧉] ││[Open][⧉] ││[Open][⧉] ││                  │
│  │[✓ Applied]│[✓ Applied]│[✓ Applied]│                  │
│  └──────────┘└──────────┘└──────────┘└───              │
│                                                         │
│  SCAN MORE JOBS                                         │
│  🔍 Data Analyst · Brisbane                          ›  │
│  🔍 Software Engineering · Brisbane                  ›  │
│  🔍 Administration Assistant · Brisbane              ›  │
│  Open one, then press Scan Page.                        │
└─────────────────────────────────────────────────────────┘

Applied tab
│ [Export CSV] [Export evidence (zip)]                    │
│ ▾ 07/10/2026 – 06/11/2026  (current)   7/20   US$1.48   │
│     Data Analyst · Acme          09/10/2026  92  [+ Interview]
│     Junior BI Dev · Contoso      08/10/2026  88  [✓ Interview]
│     …                            Export this period (CSV)│
│ ▸ 07/09/2026 – 06/10/2026          21/20 ✓  US$4.54     │
│ ▸ 07/08/2026 – 06/09/2026          18/20    US$3.90     │
```

- No start date set yet → the progress block is a single prompt: "When does your
  Centrelink month start? [date] [Save]". Until it's set, the Applied tab groups by
  calendar month with a note pointing to the prompt.
- ✎ opens an inline editor for the start date and the target.
- Count turns green with ✓ at or above the target.
- Mini-cards: ~200px wide, `scroll-snap`, and ‹ › buttons because a mouse wheel doesn't
  scroll sideways. **Open** opens the Seek ad, where the existing Quick-Apply overlay shows
  the letter. **⧉** copies the letter. **✓ Applied** marks the job applied, removes the
  card and bumps the counter. Score pill uses the existing `tierOf()` colours.
- Periods use native `<details>/<summary>`, so they're keyboard accessible and need no
  custom toggle code. Clicking a job row opens the ad, as today, and the screenshot-expiry
  note (`evidenceNote`) stays on each row.
- The header's Scan Page / Refresh buttons show on Overview as well as Jobs.

## Backend

**1. Preferences** (`app/preferences.py`, `PreferencesUpdate` in `app/api/main.py:396`)
- `obligation_target` (default 20, 1–100), `obligation_cycle_start` (`"YYYY-MM-DD"` or
  `None`).
- In `PreferencesUpdate`, add `obligation_cycle_start: datetime.date | None`. The update
  handler (`main.py:1559`) must use `body.model_dump(mode="json", exclude_none=True)` so
  the date is stored as a string. Today's call would hand a `date` to `json.dumps`.

**2. Migration**: `matches.interview_at` (DateTime tz, nullable), chained off the current
head `a9e3c5d7f142` (9c's `add_draft_to_job_screening_questions`; re-check the head first). Add it to the model and
to `docs/database-schema.md` (matches section: an interview recorded, status unchanged).

**3. `app/obligation.py`** (new, pure functions, no DB)
- `add_months(anchor, k)` with day clamping; `period_for(local_date, anchor) -> (start,
  end)`: k = month difference, minus 1 if the date falls before that month's start.
- `calendar_period_for(d)` fallback when no anchor is set.
- `to_local_date(dt_utc)` uses `.astimezone()`, matching `app/llm/usage.py`.

**4. `app/api/obligation.py`** (new router, registered like `app/api/letters.py` /
`screening.py`)
- `GET /obligation?profile_id=1` → one payload for both tabs:
  `{target, cycle_start, current: {start, end, applied, days_left}, waiting_count,
  periods: [{start, end, applied, cost_usd, uncosted, jobs: [{job_id, title, company,
  url, score, applied_at, cost_usd, interview_at, screenshot_*}]}]}`
  - Applied matches (`applied_at IS NOT NULL`, hidden ones included) joined to
    `JobListing`, bucketed by `period_for`. Newest first. The current period is always
    present, even at 0.
  - Cost: **one** grouped query, `SUM(llm_usage.cost_usd) GROUP BY job_id WHERE job_id IN
    (…)`. A job with no usage rows (applied before logging began on 2026-10-01) gets
    `cost_usd: null` and adds to the period's `uncosted` count, shown as "n not costed".
    Cost is an estimate, so it's labelled "≈ US$".
  - Screenshot fields reuse the same expression `list_jobs` uses (factor a small helper
    out of `main.py:766-790` rather than copying it).
  - `waiting_count` = runs in `waiting_user`. Reuse `letter_view.latest_runs` / the
    `/letter-runs/waiting` logic in `app/api/letters.py`; don't write a new query.
- `PATCH /jobs/{job_id}/interview` body `{interview: bool}` sets or clears
  `interview_at`. 404 if there's no match, 409 if the job isn't applied.

**5. `GET /jobs?ready=true`** (`list_jobs`, `main.py:722`): only matches with a cover
letter and `applied_at IS NULL`, still excluding expired and hidden ones, ranked by score.
This feeds Keep Applying, and stops the default `limit=50` from dropping ready jobs.

Suggested searches: reuse `GET /jobs/suggested-searches` as is (it already returns ≤3 and
the location). Overview ignores the banner's 7-day dismiss cooldown, which stays a
Jobs-tab thing. Opening a search is a normal user-initiated tab, so the Seek access policy
is unchanged.

## Extension

- **`extension/sidebar.html`**: add `<button data-tab="overview">` first and make it
  `active`, plus a `#overview-section`. Replace `#applied-list` with `#applied-periods`
  (keep the two export buttons). Add CSS for the progress bar, mini-cards, scroller,
  suggestion rows and `<details>` periods, reusing the existing palette (`#2557a7`,
  `#059669`, tier colours). Load the new `dashboard.js` after `sidebar.js`.
- **`extension/dashboard.js`** (new, so `sidebar.js` at 2.8k lines doesn't grow). Classic
  script sharing the sidebar's globals (`BACKEND`, `PROFILE_ID`, `tierOf`,
  `seekSearchUrl`, `csvEscape`, `evidenceNote`):
  `loadOverview()`, `renderProgress()`, `renderStartDateEditor()`, `renderMiniCard()`,
  `renderSuggestions()`, `loadAppliedPeriods()` (replaces `loadApplied` /
  `renderAppliedRow`), `renderPeriod()`, `toggleInterview()`, `exportPeriodCsv()`. The
  last one reuses the existing CSV row builder, factored out of the export-button handler
  in `sidebar.js:1069`.
- **`extension/sidebar.js`**
  - Tab switching (`:123`): handle `overview`, show `hdrJobsBtns` on overview and jobs, and
    open on Overview at startup.
  - Factor the inline "Mark Applied" PATCH in `renderJob` (`:313`) into
    `markApplied(jobId)`, used by both Jobs cards and mini-cards.
  - Have `scheduleReload` (`:2648`) and the `job_processed` handler reload whichever tab
    is visible. `loadOverview` must not wipe a focused input, same as the existing guard.
  - The "Export CSV" button reads from the `/obligation` payload (all periods) rather than
    the old flat list.
  - Remove the old Applied-list code that `dashboard.js` replaces.
- Copy-letter on a mini-card: fetch `GET /jobs/{id}` and copy `edited_content ||
  generated_content`, the same source the existing editor's Copy button uses
  (`renderCoverLetterEditor`, `sidebar.js:913`).

## Steps (one commit each, user's go-ahead between)

1. Backend: preferences, migration, `app/obligation.py`, `/obligation`, interview PATCH,
   `/jobs?ready=true`, tests.
2. Overview tab.
3. Applied periods + +Interview + per-period CSV.
4. Docs: CLAUDE.md (CURRENT TASK → ✅ line), PROGRESS.md, `database-schema.md`, the
   plan's decision log.

## Verification

- `python -m pytest`, with a new `tests/test_obligation.py` covering:
  - period math: anchor on the 7th; anchor on the 31st (Feb/Apr clamp); dates before the
    anchor; Dec→Jan; an application at 22:00 UTC landing on the next local day.
  - `/obligation`: counts, newest first, current period present at 0, hidden-but-applied
    counted, cost sum, `null` cost for unlogged jobs.
  - interview PATCH: set, clear, 404, 409.
  - preferences: the date round-trips as a string, and the bounds hold.
  - `/jobs?ready=true` excludes applied, letter-less, expired and hidden jobs.
- `alembic upgrade head` on a scratch copy; never real.db.
- `node --check extension/dashboard.js extension/sidebar.js`.
- UI: `python scripts/run_api.py test --db <scratch>` seeded with applied matches across
  3 periods plus some `llm_usage` rows, then load the extension in TEST mode and check:
  - Overview counter, set-date prompt, edit, scroller arrows, Copy / Open / ✓ Applied
    (counter bumps, card leaves).
  - Suggestion rows open the right Seek URL.
  - Applied periods: totals, cost, current period expanded, +Interview toggle, CSV
    exports.
  - Narrow and wide panel widths.
  - No LLM calls needed.
