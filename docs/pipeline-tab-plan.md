# Plan: turn the Jobs tab into "Pipeline", and move settings behind ⚙

**Status:** BUILT 2026-10-16 (Steps 1–5 committed); the manual checks below are the user's. APPROVED 2026-10-16 (the user accepted D1–D14 as written). Written for the chat that
implements it. Branch `centrelink-ease-apply` (tip `e00a13b`, clean). Read CLAUDE.md first,
mainly the side-panel load-order contract (`python scripts/js_globals.py --check`) and the e2e
rule about stubbing `chrome.tabs.create`.

## Context

The side panel's tabs follow the app's loop (find → score → write → apply → record → improve):

| Tab | Part of the loop | What it holds today |
|---|---|---|
| Overview | act now | progress card, evidence PDFs, Keep applying, No letter yet bands, Scan more |
| **Jobs** | leftovers | a list that mostly repeats Keep applying, question cards, each job's detail, the Personalise settings, a search-suggestion banner |
| Applied | record | monthly periods, the read-only record, CSV |
| Profile | facts about you | the editor, plus To work on at the bottom |

The user wants the Jobs tab to become useful in its own right (chat, 2026-10-16):

1. **Questions for the user** (letter runs waiting on an answer) and **suggested learning**.
2. **A compact list of every job scraped but not applied for**, including the ones below the
   cover-letter bar. The Overview's bands show only counts, so those jobs can't be seen one by one.
3. The list must **stop at a fixed number of rows and scroll**, so it doesn't take over the panel.
4. **Remove the "💡 Try searching" banner**: the Overview's Scan more does that job now.

They agreed with Claude's proposal: rename the tab **Pipeline**, move To work on into it, and
move the settings panel behind a ⚙ button in the header.

### What the code does today (checked 2026-10-16)

- `loadJobs()` (extension/sidebar_jobs.js:40) fetches `GET /jobs?profile_id=1` and splits the
  jobs into Needs Your Answer / Ready to Apply / Waiting on Cover Letter, plus a "N other roles"
  fold for scores under 60. Each card is 1–4 lines tall (taller for the gold and blue tiers, with
  animations), and a click expands `fillDetail()` in place: the letter editor, notes, side
  outputs, Quick Apply questions.
- **`GET /jobs` has `limit=50` by default** (app/api/jobs.py:158), so the tab silently stops at
  50 jobs. It **includes applied jobs** (they show "✓ Applied"), because only `status` /
  `ready` filter on `applied_at`.
- Question cards appear only inside an expanded job (`fillDetail` → `renderQuestionCard`). The
  Overview's "❓ N letters need your answer" (dashboard.js:156) just calls `showTab('jobs')`,
  and the user then has to find and open the right card.
- Personalise (sidebar.html, `#personalise`) has about 20 controls, wired by
  sidebar_personalise.js through element ids. Its **"Hide below score" acts at once on one click**,
  with no confirm (sidebar_personalise.js:333).
- `#budget-banner` sits inside `#jobs-section`, so a budget stop shows only on this tab.
- `#scanlog` (sidebar_scan.js:27) and the "⏳ N more scanned — analysing" row are the scan's
  progress display. The header's Scan Page switches to this tab (dashboard.js:742).
- The suggestion banner is `maybeShowSuggestions()` (sidebar_suggest.js:25). **`seekSearchUrl`
  in the same file is used by the Overview's Scan more** and must stay.
- To work on (`loadToWorkOn` / `renderToWorkOn`, sidebar_profile.js:499) lists only the skills
  the user said **No** to, from `GET /gaps/to-work-on`. On real.db that is 2 skills.
- Learning suggestions (`suggest_learning`) run only inside a full letter run, and only for
  gaps the user confirmed, so they almost never appear.

### Finding: skill names imported from Seek make most "gaps" false

The real profile's skills came from Seek: "Typescript Programming", "Docker Software",
"React Front End Development", "Microsoft Azure", "Atlassian Jira". `prefilter._partition`
compares **exact** normalised names, so an ad asking for "TypeScript" counts as a gap. A
read-only count over the last 90 days of scored jobs:

| | real.db | app.db |
|---|---|---|
| hard-skill asks | 725 | 259 |
| gaps by exact name (prefilter today) | 565 | 218 |
| gaps by whole-phrase match (`gaps.skill_matches`, both directions) | **395** | 209 |
| top "gaps", exact | TypeScript 24, JavaScript 21, React 18, Java 13, Azure 11, Docker 10 (all in the profile) | — |
| top gaps, whole-phrase | Kubernetes 7, Angular 6, Spring Boot 6, Confluence 6, AI-assisted dev tools 5, Claude Code 5, Next.js 5 | TypeScript 6, PHP 5, React 5, AWS 4, C# 4 |

So the Skills section (D10) **must not use prefilter's gaps**. It uses whole-phrase matching in
both directions (`gaps.skill_matches(normalise_skill(job_skill), profile_skill)` or the
reverse). The same problem feeds wrong "Gaps:" lines into the **match prompt**. Fixing that
would change scores, so it is a separate follow-up and not part of this plan.

## Decisions (proposed; the user approves or changes them)

- **D1. Label and ids.** The tab button reads **Pipeline**. Internal names stay `jobs`
  (`data-tab="jobs"`, `#jobs-section`, `loadJobs`, `showTab('jobs')`), so the e2e tests and the
  other scripts don't churn. Tab order stays the same: Overview · Pipeline · Applied · Profile.
- **D2. What the page lists:** jobs with a score that are **not applied**, not hidden and not
  flagged closed. Applied jobs leave (the Applied tab owns them). Jobs still waiting on analysis
  stay a count line under the list, as now.
- **D3. Page order, top to bottom:** scan notice → **Needs your answer** (hidden when empty) →
  **Jobs** (filters, then the scroll box) → **Skills ads ask for** (with To work on). Urgent
  first, then the main content, then the slower-moving insight.
- **D4. Compact rows in a capped scroll box.** One job per row, two lines (title; company ·
  location · age), with a score pill on the left and one status chip on the right. Each row is
  about 44 px. The box shows **7½ rows** (`max-height: min(calc(var(--row-h) * 7.5), 55vh)`): the
  half row shows there is more to scroll, and the 55vh cap keeps the Skills section on screen in
  a short panel. With fewer jobs the box shrinks to fit, so there's no empty space. The filter
  bar and the count sit **above** the box and don't scroll with it. A soft shadow at the top and
  bottom edges appears only when there is more to scroll that way.
- **D5. Filter, sort, search.** Single-select filter chips with counts: **All · Letter ready ·
  Writing · No letter · Check due** (a job not seen live in 24 h, which can't get a letter until
  a check). Sort: **Score** (default) or **Newest**. A search box (title or company) appears once
  there are more than 10 jobs. All of this is client-side on one fetch. The filter and sort are
  remembered in `chrome.storage.local` (a per-viewer convenience; wrapped in try/catch). The
  count line reads "Showing 12 of 47".
- **D6. A row click opens a detail view (drill-in), not an inline expansion.** The detail view
  replaces the Pipeline page, with "← All jobs" at the top (Esc does the same). It holds what
  `fillDetail` shows today, plus the actions in D7. Back restores the filter, search, scroll
  position and a brief highlight on the row just viewed. **Why:** expanding a row inside a fixed-height
  scroll box puts a scrolling letter editor inside a scrolling list (nested scroll), and the
  panel is too narrow to show both. Drill-in is the usual pattern for narrow screens (mail apps
  on a phone). *Alternative:* open the detail below the scroll box, which keeps the list in view
  but pushes the Skills section far down.
- **D7. Actions live in the detail view, not on the rows.** Rows stay one clean click target.
  The detail view has: Open ad on Seek ↗, Copy letter (when there is one), Mark applied
  (two-click, as on the Overview), **Write a letter for this job** (below the auto-letter bar
  only: the same one-shot writer and freshness gate as the Overview's "+ letters", with a
  two-click confirm showing the `letter_cost_estimate`) and a two-click 🗑. Regenerate stays for
  jobs that already have a letter. Today's "Generate cover letter anyway" goes: it calls
  `/regenerate`, which forces the full pipeline (≈20¢) where the user's settings say one-shot.
- **D8. Needs your answer.** One question card per waiting run (`GET /letter-runs/waiting`),
  each headed by the job's title and score, with "View job ›" opening its detail view. It uses
  the existing `renderQuestionCard`. The Overview's ❓ link opens Pipeline and scrolls to this
  section.
- **D9. Tidy-up moves here from settings.** "Hide jobs below [60]" becomes a line under the
  scroll box. The first click shows "Hide 14 jobs?" (counted from the loaded list), and a second
  click within 4 s does it. Same endpoint (`DELETE /jobs?below_score=`, which already skips
  applied jobs).
- **D10. Skills ads ask for (code only, no model call).** Hard `job_skills` from every job scored
  in the last 90 days (`gaps.WINDOW_DAYS`), including hidden and applied ones, since those are
  still market data. A skill counts as a gap when no profile skill matches it by whole phrase in
  either direction (see the Finding). Each row shows: the skill, "asked in N of M ads", the
  average score of those ads, and three actions:
  - **I have it** adds the skill to the profile (`origin='ask_user'`, through the same
    `save_confirmed` as a Quick Apply Yes, skill only, no model call) and drops the row. A note
    says to add where they used it in Profile.
  - **Not yet** saves a "no" (`gaps.save_no` + `seed_sightings`), which moves it into To work on.
  - **Ignore** hides it (`gap_ignored` list in `profiles.preferences`; no DDL), for noise like
    "APIs" or "database design".

  The top 8 show, with "Show all N". Skills with an active "no" or ignored skills are left out.
- **D11. To work on moves here** from Profile, below the Skills rows, unchanged
  (`renderToWorkOn`). Profile keeps a one-line pointer: "To work on is on the Pipeline tab ›".
  Profile then holds only facts you can edit.
- **D12. Settings go behind ⚙.** A ⚙ button in the header (left of the zoom controls) opens a
  full-panel Settings view with "← Back" (and Esc), not a fifth tab. The Personalise controls
  move there **keeping their element ids** (sidebar_personalise.js keeps working), regrouped
  under headings: Cover letters · Letter limits · Extras beside a letter · Quick Apply ·
  Scanning · Availability checks · Search suggestions (with Search performance). The
  `#personalise-toggle` collapse goes, since the view is the panel now.
- **D13. Global banners.** `#budget-banner` moves out of the tab to just under the tab bar, so
  a budget stop shows on every tab.
- **D14. The suggestion banner goes.** Delete `#suggestion-banner`, its CSS,
  `maybeShowSuggestions` and the cooldown. Move `seekSearchUrl` to sidebar_shared.js and delete
  sidebar_suggest.js (and its `<script>` tag). The "Use AI to refine search suggestions"
  setting stays, because it feeds the Overview's suggestions.

### UI/UX conventions this plan follows

- **Stable layout:** the capped box keeps the page's shape the same whether there are 5 or 500
  jobs. Grey placeholder rows (skeleton) while loading, not "Loading…", so nothing jumps when
  data arrives.
- **Live updates never steal your place:** SSE reloads redraw the rows in place and keep the
  filter, search text, scroll position and an open detail view. Nothing redraws while you type
  (`typingIn`).
- **Status is never colour alone:** every chip has an icon and a word (❓ Answer, ✍ Writing,
  ✅ Letter, ⚠ Failed, ⏳ Queued, 🕓 Check due, "No letter"). The score pill keeps its tier
  colour and shows the number.
- **Destructive or paid actions take two clicks** with the consequence in the button text
  ("Hide 14 jobs?", "Write letter? ≈ 12¢", "Delete?"), and go back after 4 s. This is the same
  pattern as Mark applied and the Keep Applying bin.
- **Empty states say what to do next:** e.g. "No jobs with a letter ready. Letters are written
  for scores of 80+, or use 'Write a letter' on a job." For each section and each filter.
- **Keyboard and screen readers:** each row is a `<button>` inside a list item (Tab/Enter work).
  The chips are `aria-pressed` toggle buttons. The scroll box has `tabindex="0"` and an
  `aria-label` so the keyboard can scroll it. The count line is `aria-live="polite"`. Focus
  returns to the row after "← All jobs". Esc closes the detail and Settings views.
- **Calm motion:** the gold/blue tier animations don't carry into the compact rows. Any
  remaining motion respects `prefers-reduced-motion`.
- **One click target per row:** no buttons on rows (D7), so a click can't hit the wrong thing in
  a narrow panel.

## Layout

Pipeline page:

```
│ Overview  [Pipeline]  Applied  Profile             │
│ ⚠ Budget reached: cover letters paused…  (if any) │  ← D13, every tab
│                                                    │
│ ❓ NEEDS YOUR ANSWER (1)                           │
│ ┌────────────────────────────────────────────────┐ │
│ │ Graduate Developer · Acme · 86    View job ›   │ │
│ │ Have you used Kubernetes?  [Yes…] [No]         │ │
│ └────────────────────────────────────────────────┘ │
│                                                    │
│ JOBS NOT APPLIED                  Showing 47 of 47 │
│ [All 47] [Letter 6] [Writing 1] [No letter 37] [🕓 3]│
│ 🔍 Search title or company…        Sort: Score ▾   │
│ ┌────────────────────────────────────────────────┐ │
│ │ 91  Graduate Software Engineer      ✅ Letter  │ │
│ │     Acme Pty · Brisbane · 2d                   │ │
│ │ 78  Data Analyst                    ✍ Writing  │ │
│ │     Foo Ltd · Remote · 1d                      │ │
│ │ 72  Junior Developer                No letter  │ │
│ │ …                    (7½ rows, then scrolls)   │ │
│ │ 58  IT Support Officer           🕓 Check due  │▒│
│ └────────────────────────────────────────────────┘ │
│ ⏳ 3 more scanned — analysing…                     │
│ Tidy up: hide jobs below [60]  [Hide]              │  ← D9
│                                                    │
│ SKILLS ADS ASK FOR THAT YOU DON'T LIST   (90 days) │
│ Kubernetes   7 of 57 ads · avg 55  [Have] [Not yet] [✕] │
│ Angular      6 of 57 ads · avg 64  [Have] [Not yet] [✕] │
│ …                                  Show all 31 ›   │
│ TO WORK ON (you said not yet)                      │
│ Spring Boot  6 ads in 90 days     [I've learned it] │
```

Detail view (replaces the page; D6, D7):

```
│ ← All jobs                                         │
│ Junior Developer                               72  │
│ Bar Pty · Brisbane · Full time · scored 14/10      │
│ [Open ad on Seek ↗] [Write a letter] [Mark applied] [🗑] │
│ WHY 72   <reasoning>                               │
│ GAPS     Kubernetes · Angular                      │
│ ⚠ Check: Australian work rights                    │
│ COVER LETTER / notes / side outputs / QA questions │  ← today's fillDetail
```

Settings view (D12): "← Back" then the groups in order. Each keeps its own Save buttons and
notes as today.

## Steps (one commit each; get the user's go-ahead between steps)

### Step 1 — Backend: the list, one-job letters, the skills view

- `GET /jobs`: add `applied: bool | None = None` (`false` → `applied_at IS NULL`). Allow `limit`
  up to 500 (`Query(50, ge=1, le=500)`). Add `date_scraped` and `fresh` (the job passes
  `availability.llm_gate` for this profile) to each row. Only add fields: the Overview and the
  overlay read the existing ones.
- `letter_bands.claim_job(db, profile_id, job_id)`: the same `_eligible` filter and gate as
  `claim_band`, for one job, plus the `queued` set. `POST /jobs/{job_id}/write-letter` queues
  `worker._write_band_letters([job_id], profile_id)` and returns
  `{"queued": 0|1, "reason": "has_letter|at_or_above_bar|waiting_check|queued|not_found"}`. Put it
  next to the band routes. Patch `worker._bg_executor` at call time (worker.py docstring rule).
- New `app/skill_gaps.py` (code only): `market_gaps(db, user_id, days=90, now=None)` →
  `{"window_days", "ads": M, "items": [{"skill", "key", "ads", "avg_score"}]}`. It reads
  hard `job_skills` of matches scored in the window (`Match.scored_at`, falling back to
  `created_at`), drops skills a profile skill matches (whole phrase, both directions, using
  `gaps.skill_matches`), drops active "no"s (`gaps.find_decision`) and `gap_ignored` keys,
  groups by `normalise_skill`, labels each group with its most common spelling, and sorts by
  ads, then avg score, then name. Routes in app/api/letters.py next to `/gaps/to-work-on`:
  - `GET /gaps/market`
  - `POST /gaps/market/have {skill}` → `answers.save_confirmed(db, pid, ProposedRows(skills=[skill]))`
    (sets `origin='ask_user'` and auto-clears a matching "no", as a Quick Apply Yes does)
  - `POST /gaps/market/not-yet {skill}` → `gaps.save_no` + `gaps.seed_sightings`
  - `POST /gaps/market/ignore {skill}` / `DELETE` the same to undo → `gap_ignored` in preferences
- Tests (no paid call): the `applied` filter and the limit cap; `fresh` with checks on and off;
  write-letter for each refusal reason and a queued job; `market_gaps` with Seek-style names
  ("Typescript Programming" covers "TypeScript"; "Java Programming" does **not** cover
  "JavaScript"), the window, hidden and applied jobs counted, "no" and ignored left out,
  grouping of spellings. Run tests/test_route_table.py.

### Step 2 — Settings view and global banner (D12, D13, D14)

- sidebar.html: a ⚙ button in `.hdr-right`; a new `#settings-section` (hidden) holding the
  Personalise controls, moved with their ids, under the D12 group headings. Remove
  `#personalise-toggle` and its panel wrapper, but keep `#personalise-panel` as the inner
  container id if sidebar_personalise.js needs it. Check what reads `personaliseToggle` first.
- Showing Settings hides every tab section and clears the active tab. "← Back" and Esc return
  to the tab that was open. Put this in sidebar_shared.js beside `showTab`, and make
  `showTab()` close Settings too.
- Move `#budget-banner` under `nav#tabs`. Delete the suggestion banner (D14), move
  `seekSearchUrl` to sidebar_shared.js, delete sidebar_suggest.js and its `<script>` tag, and
  update the CLAUDE.md repo layout. Run `python scripts/js_globals.py --check`.
- Leave "Hide below score" in Settings for now. Step 4 moves it.
- e2e: update `sidebar_load_e2e.py` (lines ~168–184), `availability_e2e.py:348` and
  `quick_apply_e2e.py:845`, which click `#personalise-toggle`, to open ⚙ instead. Add checks:
  ⚙ opens Settings, Back and Esc return to the previous tab, a saved setting still saves, the
  budget banner shows on the Overview.

### Step 3 — Pipeline list: compact rows, capped scroll box, detail view (D1–D7)

- Tab label "Pipeline". `loadJobs()` fetches `/jobs?applied=false&limit=500` and keeps the jobs
  in a module-level array. A new `renderPipeline()` draws chips, count and rows from it with the
  current filter, search and sort, so a filter change never refetches.
- State that outlives a reload: `{filter, sort, query, scrollTop, openJobId}`. Filter and sort
  go in `chrome.storage.local`. On an SSE reload, redraw the rows, restore `scrollTop`, and leave
  an open detail view alone unless its job left the list (then show "This job is no longer in
  your list" with Back).
- Status chip per row: one, in priority order: question → writing → letter → failed → queued
  (score ≥ bar, no letter yet) → check due (`!fresh`) → no letter. This reuses
  `coverLetterState` plus `fresh`.
- Detail view: new `#job-detail-view` in `#jobs-section`. It hides the page sections and shows
  "← All jobs", the header line, the action bar (D7) and the existing `fillDetail` body. Remove
  `makeGenerateButton`'s "anyway" use. The "Write a letter" button posts to
  `/jobs/{id}/write-letter`. It is two-click with the estimate from `GET /jobs/letter-bands`
  (`estimate_usd`; check the field name), and the `reason` decides what text it shows.
- Keyboard: Enter/Space on a row opens it, Esc goes back, focus returns to the row.
- CSS: new `.pl-*` classes for the compact rows. Remove the `ul#job-list li[data-tier=…]` card
  styles and animations that nothing uses any more, and `renderFold` / `renderSectionHeader`.
- Keep `#scanlog` at the top and the analysing row under the box.
- e2e `dashboard_e2e.py` (~1067–1124 select `#job-list li` by text): change them to the new row
  selector. Add a new `tests/e2e/pipeline_e2e.py` (scratch DB, stub LLM, `chrome.tabs.create`
  stubbed). Seed 30 jobs and check: applied jobs are absent; the box is capped (scrollHeight >
  clientHeight; about 7½ rows visible); chip counts and filtering; search; sort; filter kept
  after a panel reload; detail open/back with scroll restored; Esc; a `job_processed` SSE
  reload keeps scroll and the open detail; "Write a letter" two-click posts once; an empty
  filter shows its message.

### Step 4 — Needs your answer, tidy-up, skills and To work on (D8–D11)

- Needs your answer: fetch `GET /letter-runs/waiting`. Draw one card per run (the job's title,
  score, "View job ›") with `renderQuestionCard`. Hide the section when empty. The Overview's ❓
  link (dashboard.js:161) calls `showTab('jobs')` and then scrolls `#pl-needs` into view.
- Tidy-up line (D9): move `#bulk-score` / `#bulk-delete-btn` here with a two-click confirm that
  counts the loaded unapplied jobs below the number. Delete the old handler's one-click path.
- Skills section: `GET /gaps/market` → rows with Have / Not yet / ✕ (each disables while its
  POST runs, then the row leaves with a short "Added to your profile" / "Moved to To work on" /
  "Hidden · Undo" line). Top 8, plus "Show all N". Empty state: "Every skill these ads ask for is
  in your profile."
- Move To work on: move `#towork-list` and its heading and refresh button from the Profile
  section into Pipeline. `loadToWorkOn` runs when Pipeline opens (it now runs when Profile
  opens: sidebar_shared.js:141) and after Not yet. In Profile, leave the pointer line. Have /
  Not yet also refresh the Profile editor's data the next time it opens (`profileLoaded =
  false`).
- e2e: add to `pipeline_e2e.py`: a waiting run shows its card, and the Overview link scrolls to
  it; tidy-up two-click; Have adds the skill (check `/profile-ui/data`) and the row leaves;
  Not yet moves it to To work on; Ignore and Undo; To work on renders here and no longer in
  Profile.

### Step 5 — Docs

CLAUDE.md: a CURRENT TASK pointer to this file while the work runs, then a ✅ COMPLETED entry;
the repo layout (sidebar_suggest.js gone; new `app/skill_gaps.py`); the "Personalise" mentions
that now mean Settings (the Seek Access Policy names "the sidebar's Personalise panel" for
`scan_max_pages` and `availability_checks_enabled`: say Settings). PROGRESS.md slim block.
docs/database-schema.md: note `gap_ignored` under `profiles.preferences` (no DDL).

## Verification

- `python -m pytest` (no paid calls), `node --check` on every edited JS file,
  `python scripts/js_globals.py --check`.
- e2e: `sidebar_load_e2e.py`, `dashboard_e2e.py`, `availability_e2e.py`, `quick_apply_e2e.py`,
  and the new `pipeline_e2e.py`, all against a scratch DB with `chrome.tabs.create` stubbed.

## Manual checks to do at the end (the user drives; kept up to date per step)

- [ ] Step 2: ⚙ opens Settings from every tab; Back and Esc return to that tab; changing the
  auto-letter score still moves the Overview's bands; the suggestion banner is gone; a budget
  stop banner (if one happens) shows on the Overview.
- [ ] Step 3, REAL: Pipeline lists every unapplied job (compare with the Overview's counts); no
  applied job appears; the box shows about 7½ rows and scrolls; the chips filter and count
  correctly; the panel reopened remembers the filter and sort.
- [ ] Step 3: a row opens the detail view; Back returns to the same scroll spot with the row
  highlighted; Esc works; during a scan the list updates without jumping or closing the detail.
- [ ] Step 3, TEST: "Write a letter" on a job below the bar asks "≈ N¢?", writes a one-shot, and
  the row's chip goes ✍ Writing → ✅ Letter; on a stale job it says it's waiting for a check.
- [ ] Step 4: a waiting question appears at the top; the Overview's ❓ link lands on it.
- [ ] Step 4, REAL: the Skills list looks right for your profile (TypeScript, React, Docker
  should **not** be there); Have adds to Profile; Not yet moves to To work on; ✕ hides and Undo
  restores.
- [ ] Step 4: Tidy-up asks "Hide N jobs?" before hiding.
- [ ] Step 2: Settings → Search suggestions → "Refresh now" prints the AI's picks under the button
  (one small model call; the banner it used to fill is gone).

## Follow-ups (not in this plan)

- **Prefilter's exact-name matching** (the Finding): the match prompt gets false "Gaps:" lines
  for Seek-style names. Fixing it changes scores, so measure it (`scripts/check_matching.py`)
  before and after.
- An on-demand learning plan for one skill (small model, about 1¢) from the Skills row.
- "Re-score against the updated profile" after Have adds skills (retention plan's item 10).
- Bulk select in the list (hide or write several at once).

## Decision log

- 2026-10-16: plan drafted after the user agreed to the Pipeline direction in chat. Found the
  Seek-name gap inflation (565 → 395 false-gap asks on real.db) and that `GET /jobs` caps at 50
  and includes applied jobs. Waiting for the user's approval of D1–D14.
- 2026-10-16: USER approved the plan and D1–D14 as written ("go do it"), so the steps run
  back to back, one commit each, with the manual checks batched at the end.
- 2026-10-16: Step 1 done. `GET /jobs` has `applied`, `limit` 1–500 (default still 50),
  `offset` ≥ 0, and per row `date_scraped` (aware UTC) and `fresh`. `POST /jobs/{id}/write-letter`
  uses `letter_bands.claim_job` (reasons: queued, not_found → 404, has_letter, not_eligible,
  at_or_above_bar, writing, waiting_check); `_eligible(bar=None)` drops only the score cap.
  `app/skill_gaps.py` + `GET /gaps/market` and `POST /gaps/market/{have,not-yet,ignore,unignore}`
  (`not_yet` relies on `gaps.save_no`, which already seeds the sightings). Read-only on real.db:
  57 ads, 282 gap skills, top Kubernetes 7, Angular 6, Spring Boot 6, Confluence 6, Next.js 5;
  no TypeScript/React/Docker. tests/test_pipeline_api.py (41).
- 2026-10-16: Step 2 done. ⚙ (`#settings-btn`) opens `#settings-section`, holding the old
  Personalise controls with their ids under 8 headings (the 7 planned + "Job list" for bulk hide
  until Step 4). `openSettings` / `closeSettings` live in sidebar_shared.js; Back, Esc, ⚙ again or
  any tab close it. The budget banner sits under the tab bar. sidebar_suggest.js is deleted and
  `seekSearchUrl` is in sidebar_shared.js. "Refresh now" (AI suggestions) had no banner left to
  show its result, so it now prints the picks under the button. The tab reads "Pipeline". e2e:
  sidebar_load 65/65, availability 48/48, quick_apply 124/124, dashboard 197/197.
- 2026-10-16: Step 3 done. sidebar_jobs.js rewritten: `loadJobs` fetches
  `/jobs?applied=false&limit=500` once; `renderPipeline` draws chips, count and rows from it;
  `rowStatus` gives one status per row. Rows are 46 px (`--pl-row-h`) in `#pl-box`
  (`max-height: min(7.5 rows, 55vh)`), with edge shadows on `#pl-box-wrap`. Small changes from
  the plan: the "❓ Answer" chip shows only when a job needs an answer; the detail's action bar
  has no Copy (the letter editor below already has Copy and Save); the detail adds "Why N" and
  the scorer's gaps (useful when tuning the agent); an open detail redraws itself when its job's
  letter state changes (a letter lands) unless the user is typing in it. "Generate cover letter
  anyway" (→ full pipeline) is gone; a failed run's "Try again now" stays. The reload guard is
  `plBusy()` (typing in the tab, the search box excepted). The old card CSS (tiers, sheen
  animations, fold, section headers, two-column grid) is removed. e2e: new pipeline_e2e.py
  54/54; dashboard 199/199 (its delete checks now use the detail view); sidebar_load 65/65;
  quick_apply 124/124.
- 2026-10-16: Step 4 done. Needs your answer (`maybeLoadNeeds` / `loadNeeds` / `showNeeds` in
  sidebar_jobs.js) redraws only when the set of waiting runs changes, never while an answer is
  typed; the Overview's ❓ link calls `showNeeds()`. Tidy-up (`#bulk-score`, default 60) is two
  clicks with the count from the loaded list ("None below N" when there are none); it left
  Settings. New `extension/sidebar_skills.js` (loaded after sidebar_jobs.js): the skills list
  (top 8 + Show all; each answer leaves a one-line result in place rather than moving the list)
  and To work on, moved from sidebar_profile.js; Profile keeps a pointer button (`#towork-goto`).
  Opening the Pipeline tab refreshes the list, the skills and To work on. e2e pipeline 86/86
  (never answers the seeded question: an answered run would resume and call the model);
  sidebar_load 66/66, dashboard 199/199, availability 48/48, apply_sent 51/51, quick_apply
  124/124; pytest 1794.

