# Plan: Applied-tab records, "no letter yet" bands, one-click Scan More

**Status:** APPROVED 2026-10-15 (the user accepted D1–D8 as written). Written for the chat that implements it. Branch `centrelink-ease-apply`
(tip `524e24b`, clean). Read CLAUDE.md first, mainly the Seek Access Policy and the side-panel
load-order contract (`python scripts/js_globals.py --check`).

## Context

The Overview tab is going to take over most of the Jobs tab's work. The user wants three
changes now:

1. **Applied tab: keep the record.** Clicking an applied job opens its Seek ad, and the Quick
   Apply overlay there shows the letter. Once the ad is taken down, that page may show nothing.
   The user wants to see the cover letter **and a summary of the ad** for every past
   application, whether or not the ad still exists, so they can judge how the system wrote
   its applications.
2. **Overview: "scanned, no letter yet" bands.** Under Keep Applying, show the scored jobs that
   have no cover letter as **compact, thin cards, one per 5-point score band**, in a grid of
   **2 columns × up to 3 rows**. Each card shows the band, how many jobs are in it, and (top band
   only) a **+ cover letters** button. Bands with no jobs get no card.
3. **Overview: one-click Scan More.** Replace "click a suggested search, then press Scan Page"
   with one **Scan more** button next to the "Scan more jobs" heading. It opens the top
   suggested search in a new tab and scans it. The user also reports that Scan Page sometimes
   doesn't work on the page a suggestion opens (e.g.
   `https://au.seek.com/software-developer-jobs/in-All-Brisbane-QLD`), so find out why first.

Everything follows the user's Personalise settings: the auto-letter score
(`auto_cover_letter_min_score`), the letter-loop settings, Job pages per Scan
(`scan_max_pages`), availability checks (`availability_checks_enabled`), the budget guards and
the search location (`target_location`).

### What the code does today (checked 2026-10-15)

- Applied rows: `renderPeriodJob` (extension/dashboard.js:641) does
  `li.addEventListener('click', () => chrome.tabs.create({ url: job.url }))`. Nothing in the
  panel shows the letter or the ad.
- On Seek, `main()` (content_script.js:885) shows the letter overlay (`maybeInjectQuickApply`) on
  any `/job/{id}` page whose job has a letter, even when the description is missing. If Seek
  keeps the URL for a closed ad, the overlay probably still appears. If it redirects, nothing
  appears. Nobody has seen a closed ad yet (CLAUDE.md, availability checks: "NOT verified").
- The database keeps everything the record needs: `job_listings` (title, company, location,
  work_type, salary, classification, `summary`, `key_responsibilities` as a JSON list,
  `raw_description`, `expired_detected_at`, `last_seen_live_at`), `job_skills`, `matches`
  (score, reasoning, gaps, applied_at/via, interview_at), `cover_letters`
  (generated/edited/status/created_at/updated_at) and `GET /jobs/{id}/letter-info` (open issues,
  side outputs). Retention never purges an applied match (`app/retention.py`, `applied_at IS
  NULL` whitelist).
- **A data-loss bug:** `DELETE /jobs?below_score=` (`bulk_hide_jobs`, app/api/job_actions.py:256)
  does **not** skip applied matches. It stamps `hidden_at` and **nulls `raw_description`** on
  applied jobs too. A read-only check found **2 of the 3 applied jobs in app.db (TEST) hidden
  with the ad text gone**. real.db is intact (6 applied, all with description, summary and
  letter). Fix this first: it is exactly the data item 1 shows.
- `GET /jobs/{id}` (app/api/jobs.py:208) returns the listing, match and letter, but not
  `summary`, `key_responsibilities`, skills, `expired_detected_at`, `last_seen_live_at` or
  `interview_at`.
- Letters: `production.next_work` writes letters for matches with `score >= auto_min_score`
  only. `kind_for` picks the full pipeline at or above `pipeline_min_score` (the higher of the
  two bars), otherwise the one-shot. So every job below the auto bar gets a **one-shot** (about 3¢)
  under the user's own settings. `generate_for(bypass_threshold=True)` (Regenerate) would force
  the **full pipeline** (about 20¢). Don't use it for the bands.
- Scan: `scanPage()` (extension/sidebar_scan.js:126) scans the **active tab**, checks it against
  `/^https:\/\/(au\.seek\.com|www\.seek\.com\.au)\/[^?#]*jobs/i`, injects `pageCollectJobLinks`
  **once with no wait**, drops known ids, and opens up to `scanMaxPages` new links. Its notices
  go to `#scanlog`, which is on the **Jobs** tab, and the Overview's handler switches to Jobs
  (dashboard.js:535). `tests/test_scan_policy.py` requires that `pageCollectJobLinks` is injected
  only into `tab` from `chrome.tabs.query({ active: true, ... })`.
- Suggestions: `seekSearchUrl` (sidebar_suggest.js:15) builds `/software-developer-jobs?where=Brisbane`,
  and Seek redirects that to `/software-developer-jobs/in-All-Brisbane-QLD`. That URL **passes**
  the scan's URL regex, so the regex is probably not the cause (see Step 0).
- real.db preferences: `auto_cover_letter_min_score` 75, `letter_loop_min_score` 75,
  **`scan_max_pages` 1**. A scan on REAL opens only one job page, which can look like "it didn't
  work". Tell the user.

### Decisions (confirmed by the user 2026-10-15)

- **D1. Applied row click → the record opens in the panel; "Open ad on Seek ↗" is its first
  button.** The row expands in place (accordion, like the Jobs cards' detail) instead of opening
  a tab straight away. This works for dead ads, and the user can review 20 past applications
  without opening 20 Seek tabs. The alternative is to keep the click opening the ad *and* expand
  the record, which costs one less click to reach a live ad but opens a tab every time.
- **D2. The record is read-only.** It shows the letter as last saved (edited, else generated,
  labelled which), with Copy. Editing a sent letter would rewrite the record being judged.
- **D3. Bands are anchored on the auto-letter bar**: bar 80 → 79–75, 74–70, 69–65…; bar 77 →
  76–72, 71–67… Empty bands are skipped, and the **6 highest non-empty bands** are shown,
  row-major (left to right, then down). "Top range" means the **highest band that has jobs**.
  On real.db today (bar 75) the 74–70 and 69–65 bands are empty, so the cards are 64–60 (3),
  59–55 (4), 49–45 (1), 44–40 (1), 39–35 (1), and the button sits on 64–60.
- **D4. Which jobs a band counts:** a match with a score, no cover letter, `applied_at` null,
  `hidden_at` null, `expired_detected_at` null, `extracted_at` set (a full score; quick-screen
  skips aren't extracted and can't get a letter), and `score < bar`. Band by `floor(score)`
  (scores are whole numbers in practice).
- **D5. "+ cover letters" writes one-shot letters through `kind_for`**, so the user's settings
  decide the kind. It goes on the single worker, with the budget guard as usual, **at most 10 per
  click**, after a two-click confirm that shows the estimate ("Write 3 letters? ≈ 9¢"), the same
  pattern as Mark applied.
- **D6. The freshness gate applies to the band button** (`availability.llm_gate`). A job not seen
  live within 24 hours (with checks on) is counted but not written. The card says "1 waiting
  for an availability check". The reason: this is batch work like the idle loop's, not a
  one-job Regenerate.
- **D7. Scan more opens the search in a new foreground tab** (`active: true`), then scans that
  tab. A foreground tab keeps it "a page the user opened" (they clicked, and they see it), and
  it keeps the pinned active-tab test true. It opens **one** search per click (the top
  suggestion). It never chains to the next suggestion or to page 2 on its own.
- **D8. The Overview's 3 suggestion rows go**, replaced by the button plus one line saying what
  it will search. The Jobs tab's suggestion banner stays as it is.

## Layout

```
│  KEEP APPLYING · 2 letters ready              ‹ › │
│  [mini-card][mini-card] …                         │
│                                                   │
│  NO LETTER YET · 10 scored below 75               │
│  ┌──────────────────────┐ ┌──────────────────────┐│
│  │ 64–60   3  [+ letters]│ │ 59–55   4            ││  ← ~32px tall
│  ├──────────────────────┤ ├──────────────────────┤│
│  │ 49–45   1            │ │ 44–40   1            ││
│  ├──────────────────────┤ └──────────────────────┘│
│  │ 39–35   1            │                         │
│  └──────────────────────┘                         │
│                                                   │
│  SCAN MORE JOBS                       [Scan more] │
│  Searches "software developer" in Brisbane, up to │
│  10 new jobs.   · Scanning 3/10…  (while running) │
```

The band cards use the same tier colours as the score pills (`tierOf(lo)`), as a 3px left
border, not a top border. While a band's letters are being written, the button reads
"Writing 3…" and is disabled.

Applied record (row expanded):

```
│ ▼ Graduate Software Engineer           85  ✓ Interview 🗑 │
│   Acme Pty · Applied 08/10/2026 · recorded from Seek      │
│   [Open ad on Seek ↗]  Ad closed (noticed 12/10) | Last seen live 14/10 │
│   THE AD  Brisbane · Full time · $80–90k · ICT / Software  │
│     <summary>                                              │
│     • responsibility 1  • responsibility 2 …              │
│     Skills: Python, SQL, React                             │
│     ▸ Full ad text (as captured 07/10/2026)               │
│   THE MATCH  85 — <reasoning>   Gaps: …                    │
│   COVER LETTER (your edited version)            [⧉ Copy]  │
│     <letter, read-only, scrolls past ~12 lines>           │
│     <letter notes: open issues / left out, via renderLetterNotes> │
```

## Steps (one commit each; get the user's go-ahead between steps)

### Step 0 — Find out why Scan Page fails on a suggestion's page (no code; the user drives)

The agent must **not** fetch Seek (CLAUDE.md). Ask the user to do this on TEST (each quick
screen is a small, cheap model call):

1. Right-click inside the side panel → Inspect → Console. Optionally, Personalise → Job pages
   per Scan → 3.
2. Overview → click the top suggestion. Wait until the results show, and note the final URL.
3. Press **Scan Page**. Copy the `[scan]` console lines and whatever `#scanlog` says on the Jobs
   tab. Also open the Seek tab's own console and look for `[SeekAssistant] Captured N cards`
   (the content script's card capture on that URL form).
4. Do it once more, pressing Scan Page **immediately** while the results are still loading.

| What the log says | Cause | Fix |
|---|---|---|
| `No job links found on this page.` | Pressed before Seek rendered the cards (`pageCollectJobLinks` reads once, no wait) | Step 5: make it poll |
| `All jobs on this page already captured.` | Suggestions come from jobs already scored, so page 1 of that search is often already in the DB | Clear message in Step 5; page 2 / next suggestion is a follow-up that needs the user's OK |
| `3 weak results in a row …` | Early exit worked as designed | Nothing; show the notice on the Overview (Step 5) |
| `Open a Seek search results page …` | URL regex failed on the real final URL | Widen the regex to that URL form |
| `Could not read the page: …` | `executeScript` refused | Look at the error text |
| Only 1 job opened | `scan_max_pages` is 1 on REAL | No bug; tell the user |
| Nothing visible on the Overview | The notice went to the Jobs tab's `#scanlog` | Step 5: show progress on the Overview |

Record the answer in this file's Decision log before Step 5.

### Step 1 — Backend: keep applied jobs whole and return the record fields

- `bulk_hide_jobs`: add `.where(Match.applied_at.is_(None))`. Applied matches are never hidden
  and never lose `raw_description`. Update the docstring. Test it in
  tests/test_search_endpoints.py: an applied job scoring under the threshold keeps
  `hidden_at` null and its description, and the hidden count excludes it.
- Already-damaged TEST rows can't be recovered (the text is gone). The record must still render
  without it (Step 2).
- `GET /jobs/{id}`: add `summary`, `key_responsibilities` (parsed to a list, `[]` when bad JSON),
  `seniority`, `skills` (`{"hard": [...], "soft": [...]}` from `job_skills`), `discovered_query`,
  `expired_detected_at`, `last_seen_live_at`, `match.interview_at`, and
  `cover_letter.created_at` / `updated_at`. Only add fields. The overlay, `copyLetter` and
  `fillDetail` read the existing ones. Run tests/test_route_table.py. Add an endpoint test for a
  job with every field and one with a null description and no letter.

### Step 2 — Applied record in the panel (D1, D2)

- `renderPeriodJob` (dashboard.js): the row click toggles an inline record below the row
  instead of `chrome.tabs.create`. Keep `stopPropagation` on + Interview and Delete. Keep open
  rows in a Set across live reloads (as `apOpen` does for periods), so `refreshDashboard()` during
  a scan doesn't collapse the one being read.
- The record loads `GET /jobs/{id}` and `fetchLetterInfo(jobId)` (sidebar_jobs.js:305), then
  renders the layout above. Reuse `renderLetterNotes` (sidebar_letter_notes.js:56) for the letter
  notes and `evidenceNote(job)` for the screenshot. Every piece is optional: no description →
  "The ad text wasn't kept"; no letter → "No cover letter was saved for this application"
  (manual applications can have none); no summary → show the first ~600 characters of
  `raw_description`.
- Ad status line: `expired_detected_at` → "Ad closed (noticed dd/mm)", else `last_seen_live_at` →
  "Last seen live dd/mm". This is a label only. It makes no request to Seek.
- Put the new code in a new classic script `extension/applied_record.js`, loaded just before
  `dashboard.js` (or in dashboard.js if it stays small). Add it to the CLAUDE.md repo layout and
  run `python scripts/js_globals.py --check`. Nothing it uses at load time may come from
  dashboard.js. `fmtDay` and `localDay` are only called at run time, which is fine.
- CSS in sidebar.html next to the Applied styles.
- Optional, small: when the overlay on Seek finds no description for an applied job, its header
  could say "This ad may have closed: the saved copy is in the side panel's Applied tab." Skip
  it if it adds risk.

### Step 3 — Backend: letter bands (D3–D6)

New module `app/letter_bands.py` (pure query + band maths, no LLM), plus routes in
`app/api/jobs.py`. **Register them before `GET /jobs/{job_id}`** (the route-order rule;
tests/test_route_table.py).

- `GET /jobs/letter-bands?profile_id=1` →
  `{"bar": 75, "width": 5, "total": 10, "bands": [{"lo": 60, "hi": 64, "count": 3, "ready": 3,
  "waiting_check": 0, "queued": 0}, ...]}`. `bar` = `letter_settings(...)["auto_min_score"]` (the
  one the idle loop uses). Bands follow D3 and D4, at most 6, highest first, empty ones left out.
  `ready` = passes `availability.llm_gate`. `queued` = in the in-process queue below.
- `POST /jobs/letter-bands/write {lo, hi}`: the server works out the eligible jobs itself (same
  filter + gate; never trusts a client list), `ORDER BY score DESC LIMIT 10`, adds their ids to
  a module-level `queued` set, and submits **one** function to `worker._bg_executor`. For each
  job, that function re-checks it is still eligible (no letter, not applied/hidden/expired,
  still gated in), then runs
  `production.run_work(Work(kind_for(settings, score), job_id), profile_id, events.broadcast_from_thread)`.
  On `landed.account_limit`, it broadcasts `llm_budget_blocked` and stops. The id leaves `queued`
  in a `finally`. Returns `{"queued": n, "waiting_check": m, "skipped": k}`. 400 if `lo`/`hi`
  isn't a band below the bar.
- Use `worker._bg_executor` / `worker` as module attributes at call time (plan rule M1, worker.py
  docstring) so tests can patch them.
- Tests (stub LLM, no paid call): band maths (bar 80 and 77, empty bands skipped, cap 6, the
  floor at the band edge), each D4 exclusion, the gate (checks on/off), the write cap of 10, the
  one-shot kind below the bar even with the loop enabled, a budget stop ends the batch, and no
  double-queue on a second click.

### Step 4 — Overview: the bands UI

- sidebar.html: a new `<section class="ov-block" id="ov-bands">` between Keep Applying and Scan
  more jobs. Heading "No letter yet", sub "N scored below {bar}". A 2-column CSS grid of
  `.band-card` (about 32px tall, one line: range, count, optional button). Hidden when there are
  no bands.
- dashboard.js `loadOverview`: add `getJson(`${BACKEND}/jobs/letter-bands?...`).catch(() => null)`
  to the `Promise.all` (optional, like suggestions), then `renderBands(data)`.
- Only the first band gets the button: "+ letters" → first click "Write 3? ≈ 9¢" (3¢ each, the
  figure already used in sidebar_personalise.js:91) → second click POSTs. While `queued > 0`:
  "Writing 3…", disabled. If `waiting_check` > 0, a small note under the grid. Reloads come from
  the existing `cover_letter_ready` SSE: `scheduleReload` (sidebar_events.js:36) already calls
  `refreshDashboard()`, so no new event is needed. Also call `loadOverview()` once the POST
  returns, so "Writing 3…" shows at once.
- Saving the auto-letter score in Personalise must refresh the Overview
  (`refreshDashboard()` after the save in sidebar_personalise.js) so the bands move with it.

### Step 5 — Overview: one-click Scan More (D7, D8), plus the Step 0 fix

- `pageCollectJobLinks` (sidebar_scan.js:55): make it `async` and poll every 400 ms for up to 8 s
  until at least one `/job/` link exists (the same pattern as `pageScrapeDetail`). It is still
  the same single function with one injection, so the policy test holds. This also fixes a
  Scan Page pressed too early.
- `scanPage({ expectTabId } = {})`: keep `const [tab] = await chrome.tabs.query({ active: true,
  currentWindow: true })` exactly as it is. If `expectTabId` is set and `tab.id !== expectTabId`,
  stop with "You switched tabs: open the search tab and press Scan Page". Let a notice hook
  write to an Overview status element as well as `#scanlog`. Also post brief progress there
  ("Scanning 3/10…").
- dashboard.js: `renderSuggestions` becomes a heading button **Scan more** plus one line
  `Searches "<top>" in <where>, up to <scanMaxPages> new jobs.` On click: disable the button and
  `scanBtn` at once (guard double clicks before `scanning` is set), then
  `const t = await chrome.tabs.create({ url: seekSearchUrl(top, where), active: true })`,
  `await waitForTabComplete(t.id, 15000)`, and `await scanPage({ expectTabId: t.id })`. Stay on
  the Overview: the header Scan Page handler stops calling `showTab('jobs')` when the scan came
  from Scan more. Leave the header button's own behaviour alone.
- No suggestion: the button is disabled, with the current empty message.
- Seek Access Policy: add a line to CLAUDE.md that Scan more opens **one** Seek search page (the
  top suggestion) in a **foreground** tab per click, and that this is the page the user opened.
  The 1-hop rule, `SCAN_DELAY_MS`, the cap and the no-second-hop line are unchanged. Add a
  policy test: the Scan more `chrome.tabs.create` uses `seekSearchUrl(...)` and `active: true`,
  it appears once, and nothing loops over the suggestions.
- If Step 0 found a different cause (the regex, say), fix that here too.

### Step 6 — Docs

CLAUDE.md: a CURRENT TASK pointer to this file while the work runs, then a ✅ COMPLETED entry, plus
the new file in the repo layout. PROGRESS.md slim block. docs/database-schema.md needs no change
(no DDL in this plan).

## Verification

- `python -m pytest` (no paid calls; the stub LLM covers the band writer), `node --check` on
  every edited JS file, `python scripts/js_globals.py --check`.
- e2e (scratch DB, `run_api.py test --db`, stub LLM). **Stub `chrome.tabs.create`**: a real tab
  can load before Playwright routing attaches and reach Seek.
  - `tests/e2e/dashboard_e2e.py`: replace the "a suggestion opens its Seek search" check
    (line ~369) with Scan more. Assert the recorded `tabs.create` URL and `active: true`, and
    that `scanPage` ran with that tab id (stub `tabs.query` / `scripting.executeScript`). Add
    bands: card count/order/labels for seeded scores, the button only on the top band, the
    two-click confirm, POST body, "Writing…" state. Add the Applied record: expand/collapse,
    kept across a reload, a dead-ad job (null description, `expired_detected_at` set) still
    shows its letter and summary, a job with no letter.
  - `tests/e2e/sidebar_load_e2e.py` still passes (load order).
- Live (the user drives, not verifiable here): Scan more on TEST from the Overview, the Applied
  record for a real applied job on REAL, and, when one exists, an applied job whose ad has
  closed.

## Manual checks to do at the end (the user drives; kept up to date per step)

The user chose (2026-10-15) to do every manual check together after the last step.

- [ ] Step 2, REAL: Applied tab -> click an application: the record opens in place (no Seek tab);
  ad facts, summary, responsibilities, skills, "Full ad text", the match, the letter labelled
  edited/as generated, Copy works; "Open ad on Seek ↗" opens the ad; a second click closes it;
  an open record stays open while a scan refreshes the tab.
- [ ] Step 2, TEST: the 2 applied jobs whose ad text bulk-hide deleted show "The ad text wasn't
  kept" and still show their letter.
- [ ] Step 2, when one exists: an applied job whose ad has closed shows "Ad closed (noticed dd/mm)".
- [ ] Step 3/4, REAL or TEST: the Overview's "No letter yet" cards match the bands; "+ letters" asks
  to confirm with the estimate, writes one-shot letters (≈3¢ each), shows "Writing N…", and new
  letters appear in Keep Applying; jobs not seen live in 24h are counted as waiting for a check.
  (real.db on 2026-10-15: 60–64 has 3 jobs but only 1 fresh, so a click writes 1.)
- [ ] Step 4: change the auto-letter score in Personalise, open the Overview: the bands move to
  the new bar; a band's "+ letters" left alone for 4 s goes back to "+ letters".
- [ ] Step 5 (known-ids fix): Scan Page on the same "software developer" search no longer says
  "All jobs on this page already captured"; it opens new job pages (1 per scan on REAL, where
  Job pages per Scan is 1). real.db had 17 card-only jobs the old rule hid from the scan.
- [ ] Step 5, TEST (pill on TEST, port 8001): Overview → "Scan more jobs" lists the top 3 searches,
  each with its own Scan more. Click one: that search opens in a tab in front, the panel stays on the
  Overview, "<search>: Scanning 1/N…" then "Done: N new jobs captured…" (or "All N jobs on this
  page are already in your list."). Switching to another tab straight after the click does NOT
  stop it. The header Scan Page still works and still jumps to the Jobs tab.
- [ ] Step 5b: the "No letter yet" cards sit in one row, one card tall, and scroll sideways.
- [ ] Step 5c: "+ letters" shows a realistic estimate (≈12¢ a letter on REAL today, from the
  pipeline's draft calls until one-pass letters exist). After the second click, the band's jobs
  leave "No letter yet" (an emptied band disappears) and appear in Keep Applying as "✍ Writing…"
  cards (Open ad, no Copy), with "· N being written" in the count; each turns into a normal card
  when its letter lands.
- [ ] Step 5d: after "+ letters", the button stays on that card while its range still has jobs to
  write, and moves to the next card when the range is used up (or only has jobs waiting for an
  availability check).
- [ ] Keep Applying bin: the 🗑 on a card turns into "Delete?" on the first click, goes back after
  4 s, and the second click removes the job (also from the Jobs tab). No bin on a "✍ Writing…" card.

## Follow-ups (not in this plan)

- When the top suggestion's page 1 is all captured: offer the next suggestion, or Seek's page 2
  link from the page the user opened. Needs the user's policy OK first.
- Clicking a band card body could list its jobs (on the Overview, or the Jobs tab filtered).
- Moving the rest of the Jobs tab's work onto the Overview.

## Decision log

- 2026-10-15: plan drafted. Found and scheduled the bulk-hide data-loss bug (Step 1).
- 2026-10-15: USER approved the plan and D1–D8 as written. Implement in a new chat, one
  commit per step, with the user's go-ahead between steps. Step 0 needs the user at the browser;
  Steps 1–4 don't depend on it, so they can go first, and Step 5 waits for Step 0's answer.
- 2026-10-15: Step 1 done. `bulk_hide_jobs` skips applied matches; `GET /jobs/{id}` gained the
  record fields (tests/test_job_detail.py). `test_hidden_jobs_stay_out_of_the_applied_tab` relied
  on the bug (it hid an applied job through bulk-hide), so it now stamps `hidden_at` directly.
  The 2 damaged applied rows in app.db (TEST) stay hidden with no ad text; Step 2 must render them.
- 2026-10-15: Step 2 done. `extension/applied_record.js` loads before `sidebar.js` (not right
  before `dashboard.js`: tests/test_js_globals.py and sidebar_load_e2e pin those two as the last
  scripts). The record goes inside the row's `<li>` (flex-wrap), with open ids and the fetched
  record cached so a reload redraws it without a fetch. The screenshot note stays on the row
  (`evidenceNote` already draws it there), so the record doesn't repeat it. `GET /jobs/{id}` now
  returns its datetimes as aware UTC (`_iso`), since SQLite drops the zone and the panel would
  read them as local time. The optional overlay hint on Seek is skipped.
- 2026-10-15: Step 3 done. CODE CONTRADICTED THE PLAN (small): `run_work(Work(ONESHOT, id))` reaches
  `generate_cover_letter(bypass_threshold=False)`, which refuses every score below the auto bar, so
  the planned call would write nothing. Fix: `Work.below_bar` (default False), passed by `run_work`
  to the one-shot as `bypass_threshold` only (`force` stays off: an existing letter is never
  replaced). D5 unchanged: `kind_for` still picks the kind. The writer is
  `worker._write_band_letters` (next to `_process_listing`, so tests patch `worker.SessionLocal`);
  it also skips a job with a live letter run (a Regenerate in flight), which the cards still count.
  `skipped` in the POST response = ready jobs already queued or over the cap of 10. Read-only
  check on real.db reproduces D3's bands exactly (60–64: 3, 59–55: 4, 49–45, 44–40, 39–35: 1).
- 2026-10-15: Step 4 done. The bands render from `loadOverview` (optional fetch, like the
  suggestions). Two additions to the plan: while any band has `queued > 0` the Overview reloads
  every 15 s (a job the worker skips sends no SSE event, so "Writing N…" could otherwise stick), and
  the armed "Write N? ≈ X¢" state survives a live reload (as `bandArmed`), so a scan's refreshes
  can't eat the first click. A write that queues nothing says "Nothing to write in that band right now."
- 2026-10-15: Step 0 RESULT (the user ran it; logs show localhost:8000, so REAL, not TEST; `new: 0`,
  so no model call). Both runs, including Scan Page pressed while the results were loading:
  `[scan] Skipped 30 already-captured job(s).` then `[scan] All jobs on this page already captured.`;
  the content script's card capture worked on the redirected URL form (`Ingest done … received: 32`).
  Not the URL regex, not timing, not executeScript. First read as table row 2 (page 1 already
  scored); the USER doubted the scan saw the jobs, and was right. REAL CAUSE: `GET /jobs/known-ids`
  returns every `source_job_id`, including CARD-ONLY rows (no description, never matched) that the
  content script's card capture writes as soon as a search page loads. So by the time Scan Page runs,
  every job on the page is "known" and none is opened. Read-only counts: real.db 17 card-only jobs
  never matched (22 card-only rows with discovered_query "software developer"); app.db 66. FIX (Step
  5, as the plan allows for a different cause): known-ids returns only jobs the scan has nothing to
  add to: a description, OR any match (bulk-hidden rows lost their text but are scored), OR flagged
  closed. Only the scan reads known-ids. Still 1 hop: the jobs opened are links on the page the user
  opened. The `pageCollectJobLinks` polling stays (cheap insurance for a slow page).
- 2026-10-15: Step 5 done (two commits: the known-ids fix first, then Scan more). `scanPage` takes
  `{expectTabId, onStatus}` and keeps its active-tab query as is; the header button calls it with
  no options. `pageCollectJobLinks` is async and polls 8 s. Scan more is a named `scanMore()` in
  dashboard.js; a `scanMoreBusy` flag (beyond the plan) keeps the button off from the click until
  the scan ends, since a live reload while the search page loads would otherwise re-enable it before
  `scanning` is set. The header handler needed no change: Scan more calls `scanPage` directly, so
  the handler's `showTab('jobs')` never runs for it. CLAUDE.md has the Scan more policy line;
  tests/test_scan_policy.py pins one `seekSearchUrl` tab, `active: true`, no loop.
- 2026-10-15: USER UPDATED D7 and D8 after Step 5, plus the band layout (commit "Step 5b"):
  (1) Scan more reads the search tab its click opened by id (`chrome.tabs.get(tabId)`), so the user
  need not stay on it; the header's Scan Page still reads the active tab. The "You switched tabs"
  stop is gone; a closed tab or one no longer on a Seek search stops with its own notice. CLAUDE.md's
  policy line and tests/test_scan_policy.py now say so (`tabId` only from Scan more's own new tab).
  (2) The top 3 suggested searches are back as rows, each with its own Scan more (one search per
  click, still no loop); the status line names the search. (3) The "No letter yet" cards are one
  row, one card tall, scrolling sideways (still the 6 highest non-empty bands).
- 2026-10-15: USER: the "≈ 9¢ for 3" estimate was misleading (their letters cost ~20¢). Checked
  read-only: no one-pass letter has run yet on either DB; a full pipeline letter averages 22.5¢ on
  real.db and its draft call (the same strong model as the one-pass writer) ~12¢. The estimate now
  comes from `llm_usage` (`letter_bands.letter_cost_estimate`: latest one-pass `cover_letter` calls,
  else `generate_letter` calls, else 10¢); the Personalise note's "about 3¢" is now "about 10–15¢".
  USER: a job being written belongs in Keep Applying with the writing notice, and leaves "No letter
  yet". So: bands leave out band-queued jobs (the per-band `queued` and the "Writing N…" button are
  gone; top-level `writing` counts them); GET /jobs gains `letter_queued`, and `ready=true` also
  returns jobs being written (band-queued, or a pipeline run running/answered); Keep Applying shows
  them as "✍ Writing…" cards and reloads every 15 s while any is writing.
- 2026-10-15: USER: after a "+ letters" click the button must stay available: on the same card while
  that range has more to write, else on the next. So "+ letters" now sits on the highest band with
  `ready > 0` (not always the first card): a band left with only jobs waiting for an availability
  check keeps its card and passes the button down. Updates D3's "button on the top band".
- 2026-10-15: USER asked for a two-click bin on the Keep Applying cards. It uses the Jobs tab's own
  Delete (DELETE /jobs/{id}, a hard delete; the backend refuses applied jobs, and these never are),
  with the Mark applied pattern: first click arms "Delete?", 4 s to confirm. No bin on a card whose
  letter is still being written, so a job is never deleted under the writer.
