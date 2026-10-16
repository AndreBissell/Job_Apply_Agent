# Plan: simplify the Settings view

Status: BUILT 2026-10-16, manual checks pending (list at the end). Side-panel UI only: no
backend, API or migration change. Every preference key stays as it is and keeps being read by
the backend.

## Why

The ⚙ Settings view (docs/pipeline-tab-plan.md D12) moved the old Personalise controls
unchanged: 7 headings, 15 controls, 6 Save buttons and 6 grey paragraphs. Three problems:

1. **Three controls for one decision.** "Auto-generate at or above", "Check and revise letters"
   and "Full pipeline at or above" all decide which letters get the fact-checked run. The
   backend uses `max(auto, pipeline)`, which the UI never shows, so a value can quietly do
   nothing. This is live on REAL today: auto = 80, pipeline = 75, so the 75 is ignored and
   every automatic letter is already fact-checked.
2. **Developer settings shown to the user.** Letter engine, max drafts, max tool calls and max
   spend per letter tune the pipeline, not the job search. Both engines produce the same
   letters (agent-v1 vs workflow-v2: the same tool path on 15/15). The daily and total budget
   caps already live only in code, and these four belong with them.
3. **Too much text, and one control that isn't a setting.** The notes are written as
   paragraphs. Search performance is a report, not a setting. The cost text is also out of
   date: it says a quick pass costs "about 10–15¢", but REAL's one-pass letters average 2.4¢
   (3 calls; `letter_bands.letter_cost_estimate` already measures this from `llm_usage`).
   Full runs average 22.8¢ (11 runs).

## Every current control and what happens to it

| Control (id) | Preference key | What it drives | Decision |
|---|---|---|---|
| Auto-generate at or above (`auto-letter-min`) | `auto_cover_letter_min_score` | idle-loop letters, card "pending" state, Overview bands, Pipeline status | **Keep**, auto-save |
| Check and revise (`letter-loop-enabled`) | `letter_loop_enabled` | off = every letter is one-pass | **Merge** into D1 |
| Full pipeline at or above (`letter-loop-min`) | `letter_loop_min_score` | pipeline bar (`max` with auto) | **Merge** into D1 |
| Letter engine (`letter-engine`) | `letter_engine` | agent or workflow | **Remove from UI** (D3) |
| Max drafts / tool calls / spend (`limit-*`) | `letter_max_drafts`, `letter_max_tool_calls`, `llm_run_budget_usd` | per-run stops | **Remove from UI** (D3) |
| Screening answers / gap ideas / résumé notes (`side-*`) | `screening_answers_enabled`, `learning_suggestions_enabled`, `resume_advice_enabled` | side-output calls in a full run | **Keep**, one row (D4) |
| Quick Apply question help (`qa-question-help`) | `screening_question_help_enabled` | overlay, layer 5, drafts | **Keep** |
| Job pages per Scan (`scan-pages`) | `scan_max_pages` (1–25, policy ceiling) | 1-hop scan cap | **Keep**, auto-save |
| Availability checks (`availability-checks`) | `availability_checks_enabled` | re-checks + 24h freshness gate | **Keep**, shorter label |
| AI search suggestions (`llm-suggest`) + Refresh now | `llm_search_suggestions` | layer-4 re-rank | **Keep** |
| Search performance (`search-perf-btn`) | none (report) | table | **Move** to the Overview (D6) |

## Decisions (for the user to approve)

- **D1. One "Fact-check & revise" dropdown replaces the checkbox and the second score.**
  Options: *Every letter* · *Score 85+* · *Score 90+* · *Score 95+* · *Never*. Options at or
  below the auto score are left out, so the `max()` trap can't be set. Saving writes existing
  keys in one PUT:
  - *Every letter* → `letter_loop_enabled: true, letter_loop_min_score: 0`. Writing 0 rather than
    the auto score keeps "every letter" true when the auto score changes later.
  - *Score N+* → `letter_loop_enabled: true, letter_loop_min_score: N`.
  - *Never* → `letter_loop_enabled: false` (the stored score is kept).
  On load: disabled → *Never*; `loop_min <= auto` → *Every letter* (REAL's 75/80 shows this,
  which is what it actually does today); a stored value that isn't a preset (e.g. 87) is added
  as its own option so it isn't lost. When the auto score changes, the dropdown redraws.
- **D2. Auto-save; no Save buttons.** Checkboxes and the dropdown save on `change`. Number inputs
  save on `change` (Enter or leaving the field), snap back if out of range, and show a short
  "Saved ✓" or "Not saved" beside the control for 1.5 s. One helper does all of it. The
  auto-score save still calls `loadJobs()` and `refreshDashboard()`.
- **D3. Engine and the three per-letter limits leave the UI.** The keys, defaults
  (`app/preferences.py`: agent, 3, 15, $0.50), API bounds and backend reads are unchanged, so a
  value set earlier still applies and the API can still change them. The engine stays `agent`
  (USER DECISION 2026-10-03). The 4N+2 tool-call warning goes with them.
- **D4. Extras as one row of three small checkboxes:** "Extras with fact-checked letters:
  ☑ Résumé notes ☑ Screening answers ☑ Gap ideas". Same ids and keys. (Alternative if the user
  prefers: one switch that writes all three keys.)
- **D5. Short labels; the explanations go behind ⓘ.** Each row gets one plain label. The long
  text (availability caps, extras costs, what "off" means) moves into a hidden note that an ⓘ
  button beside the row toggles: works by keyboard and keeps the wording. At most one short
  grey hint line stays visible per row (e.g. "max 25 · ~5 s each").
- **D6. Search performance moves to the Overview's suggested-searches card** as a closed
  "How past searches did" disclosure (same table and endpoint). It is the report for the
  searches listed there. Settings then holds only settings.
- **D7. Live cost hints, not fixed numbers.** The dropdown's hint reads the one-pass estimate
  from the existing `GET /jobs/letter-bands` (`est_usd_per_letter`), e.g. "Quick pass ≈ 2¢ ·
  fact-checked ≈ 20¢". The fact-checked figure stays a fixed "≈ 20¢" (measured 22.8¢); no new
  endpoint.
- **D8. Two groups instead of seven:** *Cover letters* and *Finding jobs*.

## What it looks like

```
← Back   Settings

COVER LETTERS
  Write letters for scores from       [ 80 ]  ✓
  Fact-check & revise                 [ Every letter ▾ ]      ⓘ
                                      quick pass ≈ 2¢ · fact-checked ≈ 20¢
  Extras   ☑ Résumé notes  ☑ Screening answers  ☑ Gap ideas  ⓘ
  ☑ Help with Quick Apply questions                           ⓘ

FINDING JOBS
  Job pages per scan                  [ 10 ]  max 25 · ~5 s each
  ☑ Hide closed ads automatically                             ⓘ
  ☐ AI-refined search suggestions     ↻                       ⓘ
```

From 15 controls, 6 Save buttons and 6 paragraphs to 9 controls, no Save buttons and 2 hint lines.

## Steps (one commit each; the user's go-ahead before Step 1)

### Step 1 — Settings view (sidebar.html, sidebar_personalise.js, CSS)
- Markup: the two groups and rows above. Remove `letter-loop-enabled`, `letter-loop-min`,
  `letter-loop-save`, `letter-engine`, `limit-*`, `limits-note`, `auto-letter-save`,
  `scan-pages-save`, the search-performance row. Add `letter-quality` (the D1 select) and the
  ⓘ buttons. Keep every id that stays (`auto-letter-min`, `side-*`, `qa-question-help`,
  `scan-pages`, `availability-checks`, `llm-suggest`, `llm-suggest-refresh`,
  `llm-suggest-result`), so the other scripts and e2e tests keep working.
- JS: one `bindPref` helper (D2); `letterQualityOptions()` / `readLetterQuality()` (D1);
  delete `letterLimits`, `limitValues`, `showLimit`, `updateLimitsNote`, the engine handler
  and the per-control Save handlers. `loadPreferences` keeps setting `autoLetterMin` and
  `scanMaxPages` (sidebar_shared.js, sidebar_scan.js and dashboard.js read them).
- `python scripts/js_globals.py --check` (globals removed; nothing else may read them).

### Step 2 — Search performance on the Overview (dashboard.js)
- Move the table code from sidebar_personalise.js into the suggested-searches card as a closed
  `<details>`, loaded when it is first opened.

### Step 3 — Docs
- CLAUDE.md: the Seek Access Policy says "Personalise" for `scan_max_pages` and
  `availability_checks_enabled` (say Settings); the limits paragraph says "sidebar inputs in
  Personalise" (say they are code defaults with no UI). PROGRESS.md slim block, this plan's
  Decision log.

## Tests
- `tests/e2e/sidebar_load_e2e.py`: the "`.set-group` count ≥ 7" check is now "2 groups, no Save
  buttons". The behaviour checks below live in a new `tests/e2e/settings_e2e.py` (scratch DB with
  REAL's 80/75 letter settings, stub LLM, `chrome.tabs.create` stubbed):
  - the dropdown writes the D1 keys for each option, and REAL's 75/80 case loads as *Every letter*;
  - raising the auto score above the selected preset redraws the dropdown as *Every letter*;
  - an out-of-range number snaps back and saves nothing; a valid one saves without a button;
  - each Extras box writes only its own key;
  - ⓘ shows and hides its note;
  - the removed controls are gone, and no Save buttons are left in `#settings-section`.
- `availability_e2e.py` and `quick_apply_e2e.py` use `#availability-checks` and
  `#qa-question-help`: ids kept, re-run unchanged.
- `dashboard_e2e.py`: the performance disclosure opens and shows the table (Step 2).
- `tests/test_scan_policy.py` (the 25 ceiling) and the full pytest suite: unchanged, re-run.

## Manual checks to do at the end (the user drives)
- [ ] Settings shows two groups; nothing needs a Save click; reopening the panel shows the saved values.
- [ ] REAL: the dropdown shows *Every letter* (auto 80, pipeline 75).
- [ ] Changing the auto score still moves the Overview's bands.
- [ ] The cost hint shows a sensible quick-pass figure.
- [ ] The ⓘ notes open and close.
- [ ] Overview → suggested searches → "How past searches did" shows the table.

## Decision log
- 2026-10-16: drafted after the Pipeline/Settings work landed (commits ac8625f..00aaa26). Bulk
  hide and the suggestion banner had already left Settings in that work. Waiting for the user's
  approval of D1–D8.
- 2026-10-16: USER approved D1–D8, with three Extras checkboxes (D4) and search performance on
  the Overview (D6). Built in one go:
  - sidebar.html: the Settings markup and CSS; the Overview's `<details id="ov-search-perf">`.
    New classes `.pref-label`, `.pref-extras`, `.pref-saved`, `.pref-info`, `.pref-icon`,
    `.pref-hint`, `.pref-more` (`.pref-note` is shared with the letter cards, so it is untouched).
  - sidebar_personalise.js rewritten: `savePref`, `flashSaved`, `bindNumberPref`, the
    `prefToggles` map (AI suggestions joined it; it no longer refreshes anything on change), the
    D1 dropdown (`letterQualityValue` / `renderLetterQuality`), `refreshLetterEstimate` on opening
    Settings. The engine, limits, per-control Save handlers and the 4N+2 warning are gone.
  - dashboard.js: the search-performance table, fetched each time the disclosure opens.
  - Small changes from the plan: the (i) buttons show an "i" (styled as an info dot); ↻ prints its
    result under the row as before; a number save shows "0–100 only" (or "1–25 only") when out of range.
  - The new e2e first failed 3 checks for a reason in the harness, not the app: the context routes
    through a Python predicate, so each request waits until the sync API is pumped, and the DB polls
    used `time.sleep`. They now wait with `side.wait_for_timeout` (`wait_db`).
