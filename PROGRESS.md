# Progress Log — Job Application Assistant

Running record of what's been built. Newest entries on top. Keep entries slim —
one block per milestone.

---

## 2026-10-16 — Pipeline tab (was Jobs) + ⚙ Settings view — BUILT, manual checks pending

- Branch `centrelink-ease-apply`. Plan `docs/pipeline-tab-plan.md` (D1–D14 approved by the owner; built in one go, manual checks batched at the end of the plan).
- `5e9c3d1` backend: `GET /jobs` `applied=` / `limit` 1–500 / `fresh` / `date_scraped`; `POST /jobs/{id}/write-letter` (one-shot, band rules); `app/skill_gaps.py` + `GET /gaps/market` and have / not-yet / ignore / unignore. Finding: Seek-style profile skill names make prefilter's exact match report false gaps (real.db 565 → 395 asks with whole-phrase matching); the skills view uses whole-phrase matching, the scorer is a logged follow-up.
- `ac8625f` Settings behind ⚙ (same ids, grouped), budget banner on every tab, suggestion banner and sidebar_suggest.js removed. `39c08e8` compact rows in a 7.5-row scroll box, chips/search/sort, drill-in detail. `6a3e164` Needs your answer, two-click tidy-up, skills ads ask for, To work on moved from Profile (`extension/sidebar_skills.js`).
- Tests: pytest 1753 → 1794 (`tests/test_pipeline_api.py`); new `tests/e2e/pipeline_e2e.py` 86/86; dashboard 199, sidebar_load 66, availability 48, apply_sent 51, quick_apply 124. No paid call (stub LLM; Seek-touching Chrome calls stubbed).

---

## 2026-10-15 — Overview/Applied rework: Applied records, "no letter yet" bands, Scan more — DONE ✅

- Branch `centrelink-ease-apply`. Plan `docs/overview-applied-rework-plan.md` (D1–D8, the owner's same-day updates and Step 0 in its Decision log). One commit per step, owner's go-ahead between.
- `b8213fb` bulk hide skips applied jobs (it nulled their ad text; 2 TEST rows already lost theirs) + record fields on `GET /jobs/{id}`. `d7b3549` Applied row opens a read-only record in place (`extension/applied_record.js`). `a8b200f` `app/letter_bands.py` + `GET /jobs/letter-bands` / `POST .../write` + `worker._write_band_letters` (one-shot, ≤10 a click, freshness gate, budget stop; `Work.below_bar` because the one-shot refused every score below the bar). `e42f214` the band cards on the Overview.
- Step 0 (owner in the browser): Scan Page said "All jobs on this page already captured". The owner doubted it; cause: `GET /jobs/known-ids` counted card-only rows the content script stores on page load. `a9cd877` fixes it (real.db: 17 jobs the scan could never open). `b4b3e0b` one-click Scan more (status on the Overview, link polling, CLAUDE.md policy line + policy test).
- Owner's updates: `78de669` Scan more per suggested search (top 3), the scan reads its own tab by id; band cards in one sideways-scrolling row. `36637f8` estimate from `llm_usage` (one-pass letters, else the pipeline's draft call; ≈12¢ on REAL, the old 3¢ was 4× low; a pipeline letter is 22.5¢) and queued jobs move to Keep Applying as "✍ Writing…" cards. `05f169f` "+ letters" on the highest band it can write. `993a35b` two-click bin on Keep Applying cards.
- Tests: pytest 1663 -> 1753 (new `tests/test_job_detail.py`, `test_letter_bands.py`, `test_known_ids.py`; scan policy test updated). `dashboard_e2e.py` 92 -> 197 checks, `sidebar_load_e2e.py` 59/59. No paid call in any test (stub LLM; every Seek-touching Chrome call stubbed in the e2e).
- Owner's manual checks (2026-10-15, own Chrome, TEST and REAL): all passed. The two 64–60 jobs on REAL hold the button back because their ads look closed (one empty availability check each).

---

## 2026-10-13 — `extension/sidebar.js` split into classic scripts (ARCHITECTURE 5.4 #10) — DONE ✅

- Same branch `fix/applied-evidence-safety`. Plan `docs/refactor-9-10-plan.md`, steps 10.0-10.12; pure moves, one commit each. Classic `<script>` files, not ES modules (D4): one shared global scope, so the order in `sidebar.html` is the contract.
- `729caf8` safety nets: `scripts/js_globals.py` (shared-name map; `--check` fails on a duplicate top-level name, an undeclared name, a name `dashboard.js` needs from a later script, or an injected Seek-tab function using a panel global; `tests/test_js_globals.py` runs it in pytest/CI), `tests/e2e/sidebar_load_e2e.py` (real side panel, no backend, every off-extension request aborted, `chrome.tabs.create` and `EventSource` stubbed; every top-level name defined, every tab, Personalise, zoom, every SSE event), `test_selectors_sync` scans every `extension/*.js`, `quick_apply_e2e` fails on side-panel page errors, `dashboard.js` header lists `TRASH_SVG` and `scanBtn`.
- Moves, top-down, each tag inserted just before `sidebar.js`: `ebb1e96` `sidebar_shared.js`, `6f81cf0` `sidebar_jobs.js`, `35b6f6c` `sidebar_letter_notes.js`, `91fafe4` `sidebar_letter_card.js`, `203ead8` `sidebar_evidence.js`, `8332355` `sidebar_suggest.js`, `127a045` `sidebar_scan.js` (scan constants and injected functions), `f565551` `sidebar_personalise.js`, `6e5590d` `sidebar_profile.js` (largest, 580 lines), `ca3e619` `sidebar_seek_import.js`, `bc9968b` `sidebar_events.js`. `sidebar.js`: 2,752 -> 83 lines (env pill, zoom, startup). Each moved block was checked byte-identical by rebuilding the old file from the new ones.
- Tests: pytest 1569 passed (1567 + 2). `sidebar_load_e2e.py` 44/44 at baseline, 55/55 at the end (one check per script); `dashboard_e2e.py` 92/92 every step; `quick_apply_e2e.py` 124/124 (123 + the new page-error check). No backend file changed.
- Hand checks (owner, own Chrome, TEST backend, 2026-10-13): H1 (panel opens, every tab draws, no console errors) passed after 10.1. H2 (Scan Page, cap 2) passed after 10.7: the panel's Console showed `[scan] (1/2) opening job page…` / `captured ✓ (4732 chars)` and `(2/2) … captured ✓ (4015 chars)` from `sidebar_scan.js:34`. H3 (Personalise: score save, side-output checkbox persists, cap 26 snaps back) passed after 10.8. Final manual check after 10.12 (owner, TEST backend, same day): H1 again (panel opens, every tab draws, no script errors; the only red Console lines are `ERR_CONNECTION_REFUSED` when the backend is stopped, which is expected), H4 (Profile) and H6 (live updates) all passed. H5 (Seek profile import) was optional and not run.

---

## 2026-10-13 — `app/api/main.py` split into routers and modules (ARCHITECTURE 5.4 #9) — DONE ✅

- Same branch `fix/applied-evidence-safety`. Plan `docs/refactor-9-10-plan.md`, steps 9.0-9.16; pure moves, one commit each.
- `f7f561d` safety nets: `scripts/dump_routes.py`, `tests/fixtures/route_table.txt` + `openapi.json`, `tests/test_route_table.py` (table, OpenAPI, shadowing of `GET /jobs/<literal>`), `tests/test_scan_policy.py` (Seek policy read from every `extension/*.js`).
- `d38b433` one `get_db` (`deps.py`); `e62290c` `paths.py`; `995c00e` `events.py`; `5fdcbd7` `worker.py`; `392b502` + `fb66b41` `idle_loop.py`; `bf7c21c` `profile.py`; `24d7b28` `health.py`; `6d29fed` `evidence.py`; `046e23a` `search.py`; `9745b7b` `ingest.py`; `36e9a1f` `job_actions.py`; `a41bc43` `jobs.py`; `3f0af65` tidy (`main.py` 1,658 -> 95 lines); `e42c098` routers import `get_db` from `deps`.
- Rule M1: every patched or shared name has one owner module and is reached as `owner.name` at call time; `main` re-exports only `app` and `get_db`; tests patch the owner. `tests/test_route_table.py` also checks one `ThreadPoolExecutor`, one `get_db`, and that nothing under `app/` imports `app.api.main`.
- Tests: 1567 passed at the end (1549 at the start). `dashboard_e2e.py` 92/92, `quick_apply_e2e.py` 123/123. Route table and OpenAPI unchanged except the `get_db` dependency column (step 9.1).
- Hand check H-B1 (owner, TEST backend): server starts, no traceback, `/health` 200. Log lines now come from `app.api.worker` / `app.api.idle_loop` (a change of logger name only).
- Noticed, not fixed: `tests/test_screening_assist.py::TestConcurrentClassify::test_two_requests_one_call_both_sorted` failed intermittently in 3 full runs during step 9.10 (the second concurrent request came back unsorted); it passes alone and on later full runs, and nothing it uses was touched. `Counter` was an unused import in `main.py` from before the split (removed in the tidy-up).

---

## 2026-10-12 — Cleanup of ARCHITECTURE 5.4 #11-#14 and the stub provider — DONE ✅

- Same branch `fix/applied-evidence-safety`. Plan `docs/cleanup-11-14-plan.md`.
- `3c974de` (#12): the scan's detail-page selectors come from `selectors.js` (passed to the injected function as `args`); `tests/test_selectors_sync.py` keeps `app/scraper/selectors.py` in step.
- `e9b8a53` (#13): the background worker tracks scan tabs (`SCAN_TAB_ADD` / `SCAN_TAB_REMOVE` / `IS_SCAN_TAB`) and the content script skips them, so a scanned job is ingested once, not twice. It fails to "not a scan tab". Confirmed before the fix: a 3-job scan logged 6 ingests.
- `6e95050` (#11): `_process_listing` stops on `DailyQuotaError` / `BudgetExceededError` and broadcasts `llm_budget_blocked`. `tests/test_exception_audit.py` requires every `except Exception` in `app/` (outside the dormant scraper) to end in a bare `raise` or carry `# noqa: BLE001 - <reason>`.
- `5eeef4e` (#14): comments and docs that had drifted (comments only, no logic). `.claude/settings.json` stays tracked (deliberate policy).
- `fb7fb98` (E4): `model_for` returns `"stub"` for the stub provider, so `LLM_PROVIDER=stub` can run the whole suite. `complete_text` / `complete_tools` still have no stub support.
- Tests: 1549 passed, normally and with `LLM_PROVIDER=stub` set for the session. `node --check` on the edited JS files.
- Hand checks (owner, TEST backend, own Chrome, 2026-10-12): all passed. Scan Page logged "captured ✓" with real character counts; one `POST /ingest` per scanned job and no scan-tab lookups; a `/job/` page opened in a normal tab is captured. The cap of 4 pages is honoured.
- Not built (noticed): job pages shown in the search page's side pane (`?jobId=...`) are not captured, because the path is a search path. Needs live selectors from the user.

---

## 2026-10-12 — Local server locked down: no CORS, Origin guard on writes (ARCHITECTURE 5.4 #5) — DONE ✅

- Same branch `fix/applied-evidence-safety`. Plan `docs/cleanup-3-8-plan.md`, Branch 5 (D3 = remove CORS, D4 = Origin guard).
- `3c2a974`: the `CORSMiddleware` is gone. A page on another site gets no `Access-Control-*` headers, so it can't read responses or send preflighted calls.
- `52c46f9`: `app/api/origin_guard.py` (plain ASGI middleware) returns 403 for POST/PUT/PATCH/DELETE with an `Origin` that is not `chrome-extension://...` or `http://<Host>` on localhost/127.0.0.1. This closes the body-less `POST /jobs/{id}/regenerate` (about $0.2), `/quick-screen` and `/gaps/{id}/clear`. No `Origin` (scripts, tests) passes.
- Tests: `tests/test_origin_guard.py` (12; the worker is replaced by a recorder, no AI). `tests/e2e/dashboard_e2e.py` 92/92 and `tests/e2e/quick_apply_e2e.py` 123/123 with the real extension loaded.
- Hand checks (owner, own Chrome, TEST backend, 2026-10-12): all passed. Side panel, popup, profile editor and the Seek overlay's Save edits work without CORS headers. On example.com both fetches were blocked by Chrome's Local Network Access prompt (denied), so the Origin guard's 403 was not seen live; it is covered by `tests/test_origin_guard.py`.

---

## 2026-10-12 — Overlapping quick screens make one model call (ARCHITECTURE 5.4 #7) — DONE ✅

- Same branch `fix/applied-evidence-safety`. Plan `docs/cleanup-3-8-plan.md`, Branch 4.
- `bb0b094`: `quick_screen` claims the job id (in-process set + lock, never held during the model call), re-reads the job, and the loser returns `"not-run"`. The Scan Page request thread and the idle loop's Phase 0 could screen the same fresh job at once and pay twice. `run_quick_screen`'s docstring now says so.
- Reproduced first: `tests/test_quickscreen_race.py` (file SQLite DB, fake model, timeouts) failed on the old code with two model calls. It did NOT hit the `IntegrityError` the plan predicted.
- Not verified: "database is locked" (never reproduced; owner hand check from plan step 4.2: Scan Page during a letter run on the TEST backend, then search the server console for `database is locked` / `IntegrityError`).

---

## 2026-10-12 — A failed score no longer becomes a permanent 0 (ARCHITECTURE 5.4 #8) — DONE ✅

- Same branch `fix/applied-evidence-safety`. Plan `docs/cleanup-3-8-plan.md`, Branch 3.
- `065e1a4`: `match_job` no longer catches every error as `score=0 / "Parse error"`. A failure raises and writes nothing; quota/budget stops always raise; a forced re-score (Regenerate) can no longer replace a real score with 0. New keyword `record_failure=True` writes the terminal 0 row, only when no row exists.
- `87d8390`: the idle loop's Phase 1b runs `_match_attempt`: up to 3 attempts per job (counted in memory, so a restart allows 3 more), 180 s pause after a failure or quota stop, and the third attempt records today's 0 row so one bad ad can't block the queue.
- Tests: `tests/test_match_failures.py` (10, all with `complete_json` replaced). Existing "Parse error" rows are left alone (D9). Note: `scripts/letter_lab.py` now stops on a failed score instead of recording a 0.

---

## 2026-10-12 — Dependencies pinned and CI added (ARCHITECTURE 5.4 #6) — DONE ✅

- Same branch, `fix/applied-evidence-safety` (owner decision: all of plan items #3-#8 go on this one branch, merged once at the end). Plan `docs/cleanup-3-8-plan.md`, Branch 2.
- `4b70bf9`: `requirements-lock.txt`, frozen from `.venv` (56 packages, `pip check` clean). A fresh venv built only from it passes the suite (1517 passed). `requirements.txt` keeps its ranges and has a header pointing at the lock.
- `f55a9fc`: `.github/workflows/tests.yml` runs `pytest -q` and `node --check` on every `extension/*.js`, on `windows-latest` with Python 3.11. No secrets, no `LLM_PROVIDER`, no e2e.
- Not verified: the workflow has not run on GitHub yet (push is the owner's step).

---

## 2026-10-12 — Applied-evidence safety: one definition of "applied" (ARCHITECTURE 5.4 #4, #3) — DONE ✅

- Branch `fix/applied-evidence-safety`, not pushed. Plan `docs/cleanup-3-8-plan.md`, Branch 1.
- `ed7435a`: `PATCH /jobs/{id}/status` accepts only the six documented statuses (else 422), and once `applied_at` is set any status but `applied` is refused (409). Re-sending `applied` stays a no-op and repairs a drifted row. First tests for this endpoint: `tests/test_status_update.py`.
- `37e0452`: `GET /jobs?status=applied` now means `applied_at IS NOT NULL`, like the dashboard and the export. Two older tests that seeded a status with no date now set `applied_at`.
- `463c3a5`: the Jobs-card "Mark Applied" button and the Seek overlay read `applied_at`, not `status`.
- `.venv\Scripts\python -m pytest -q`: 1517 passed (9 new; baseline 1508). E2E `tests/e2e/dashboard_e2e.py`: 92/92 (2 new: a row with `applied_at` set and status `new` shows "✓ Applied", no Delete; it failed 91/92 before the change). `tests/e2e/quick_apply_e2e.py`: 123/123. `node --check` clean on both edited JS files.
- #3 (old rows with `status='applied'` but no `applied_at`): CLOSED, no backfill needed. The owner ran `python scripts/check_applied_consistency.py real.db` (read-only) on 2026-10-12: 3 matches have `applied_at` set, 0 with `applied_at` set but status not `applied`, 0 with status `applied` but no `applied_at`.
- Not verified: the Seek overlay's "✓ Applied" in a real Chrome (no e2e covers the overlay).

---

## 2026-10-05 — Applied-evidence safety: two bug fixes — DONE ✅

- Branch `fix/applied-evidence-safety` (from `centerlink-preperation`), not pushed. From docs/ARCHITECTURE.md §4.9 items 1 and 3.
- `49f54ee`: re-scoring (`match_job` / `quick_screen` with force, e.g. a queued Regenerate) no longer resets `matches.status` to `'new'`; the evidence export now selects on `applied_at IS NOT NULL`, the dashboard's definition. `scripts/check_applied_consistency.py` counts status/applied_at disagreements read-only (`mode=ro`).
- `046d2f9`: `DELETE /jobs/{id}` refuses an applied job (409) unless `?allow_applied=true`, and now removes the screenshot file. Jobs-tab cards for applied jobs have no Delete; the Applied tab has its own Delete with an explicit confirm.
- `python -m pytest -q`: 1508 passed (11 new). E2E `tests/e2e/dashboard_e2e.py`: 90/90 (13 new). `tests/e2e/quick_apply_e2e.py` now completely passes as well
- Verified through manual testing on the real user profile. Clicked applied to a job, it moved to the applies tab, Then deleted the job and confirmed. THe job was removed.

---

## 2026-10-05 — Centrelink dashboard: Overview tab + monthly Applied periods — DONE ✅

- Plan `docs/centrelink-dashboard-plan.md` (decision log at the end). Branch `centerlink-preperation`, 3 code commits.
- Backend: prefs `obligation_target` / `obligation_cycle_start`; `matches.interview_at` (migration `d5f1b8c3e9a2`); `app/obligation.py` (monthly periods from the start date, day clamped); `GET /obligation`; `PATCH /jobs/{id}/interview`; `GET /jobs?ready=true`; `retention.screenshot_fields` shared with `/jobs`. Preferences are now dumped in JSON mode so a date stores.
- Sidebar: Overview tab first (progress, waiting letters, Keep Applying card row, Scan More Jobs); Applied tab as `<details>` periods with cost, + Interview and per-period CSV; `markApplied()` shared; live reloads refresh the visible tab.
- `python -m pytest -q`: 1497 passed (29 new in `tests/test_obligation.py`). E2E `tests/e2e/dashboard_e2e.py`: 77/77 (scratch DB, stub LLM, no model call, nothing off the machine). Migration up/down/up on a scratch copy of app.db.
- Incident: an early e2e run let two tabs opened by `chrome.tabs.create` load real Seek pages (an ad URL and one search) before Playwright's routing attached. Fixed by stubbing `chrome.tabs.create` in the harness and failing on any off-machine request.
- Not verified: the user's own Chrome; what + Interview should trigger (later).

---

## 2026-10-05 — Phase 9c: Quick Apply drafts, help toggle, question eval — DONE ✅

- Branch `centerlink-preperation`. Toggle `screening_question_help_enabled` (Personalise; off = no help on any question, no model call). One-click drafts for open-ended assisted questions (`POST /jobs/{id}/screening-drafts`, mid), code-checked, stored on `job_screening_questions.draft` (migration `a9e3c5d7f142`) and marked "redraft" when the profile, the question's sorting or the letter changes. Draft / Redraft / Copy in the overlay and sidebar.
- `python -m pytest -q`: 1465 passed. E2E `tests/e2e/quick_apply_e2e.py`: 120/120 (stub LLM, scratch DB).
- Eval `screening-v1` ($0.196): sorting 45/45; 3 `user` wordings would reach the model (logged); labels 0 problems over 420 pointers; drafts 7/7 answered passed, planted 4/4 clean; 4 answers talked to the candidate ("my profile...") -> code check + prompt line.
- Fixed: a test that made a paid Gemini call on every full test run.
- Live check passed on the TEST backend (job 57, 7 questions; layer 5 live); fixed three defects it found (gap card "wanted" for a form-only skill; duplicate evidence rows; the panel re-fetching and jumping to the top when Seek re-renders the form). 1468 tests; e2e 123/123. Drafts not yet seen on a live page.

---

## 2026-10-04 — Loose ends after the 7/8 audit — DONE ✅

- The three per-run limits (`letter_max_drafts`, `letter_max_tool_calls`, `llm_run_budget_usd`) are wired from preferences into every new run, with bounds, validation on read and sidebar inputs (14/14 browser checks). `python -m pytest -q`: 1126 passed (118 new, Sonnet-written to a spec, reviewed).
- Live gap + screening run ($0.20, scratch copy of real.db): screening questions found and answered honestly; ask_user / suggest_learning not triggered (the must-have matched as partial). Real profile issues found: summary is "s", visa/work status empty.
- future_work/: `check-claims-leaks.md`, `gemini-prompt-caching.md` added; voice note updated.

---

## 2026-10-04 — Cover-letter Phase 7c: side outputs — DONE ✅

**Built**
- Tools `answer_screening` (mid), `suggest_learning` (small), `suggest_resume_tweaks` (mid); rules in `app/llm/letter/side_outputs.py` (toggle, once per run, readiness, due-before-finish, fixed-order `run_due`).
- Agent: side tools offered only when enabled, finish refused while one is due, failures non-fatal, code fallback at the letter's tool cap. Workflow: same tools after the letter. Side calls not charged to the 15-call cap.
- Prefs `screening_answers_enabled` / `learning_suggestions_enabled` / `resume_advice_enabled` (True); sidebar sections + Personalise checkboxes; `view.not_claimed` attitude fix; `letter_lab.py --side-outputs all` + a loop-report section.
- Schema doc first: no DDL (data in `letter_runs.state`).

**Verified**
- `python -m pytest -q`: 1008 passed (162 new). `node --check` on sidebar.js.
- Sidebar driven in Chromium against a scratch test DB, no LLM, port 8000 blocked: 27/27 checks.
- Eval `agent-v2-side` ($3.41): $0.227/letter, 229 s, 15/15 clean, 0 refusals; résumé notes 15/15 on the final draft, $0.012/letter.

**Not verified**: Chrome's real side panel; screening answers and learning suggestions on a real model (the eval set has neither questions nor confirmed gaps).

---

## 2026-10-04 — Cover-letter Phase 8: the pipeline runs in the app — DONE ✅ (side-output sections wait for 7c)

**Built**
- `app/llm/letter/production.py` + `view.py`; the idle loop's `_letters_phase`; `/regenerate` on the single worker.
- Prefs `letter_loop_enabled` / `letter_loop_min_score` / `letter_engine`; API `letter_run` on `/jobs`, `GET /jobs/{id}/letter-info`.
- Sidebar: question card, final letter + open issues, "left out" list, eligibility/instruction notes, Personalise controls, To work on, origin tags; same To work on + tags in the standalone editor.
- Schema doc first: no DDL; new `letter_runs.status` value `cancelled`.

**Verified**
- `python -m pytest -q`: 846 passed (147 new). `node --check` on sidebar.js.
- The real sidebar page driven in Chromium (stubbed `chrome.*`) against a scratch copy of the test DB with the idle loop off and every LLM function raising: 35 checks (question card Yes/edit/confirm and No, run -> answered, flagged and edited letters, failed/writing cards, Personalise round-trip, To work on + clear, profile save keeps ask_user rows). No Gemini call; real.db and app.db untouched.
- That run found and fixed a 7b bug: the editors lacked the `university_project` / `assignment` types, so a profile save retyped ask_user rows to `job`.

- Live ($0.1375, approved): the loaded extension (real `chrome.*`, port 8000 blocked) against a scratch test DB with the real idle loop and one crafted ad. ask_user fired (first time on a real model analysis), a Yes was parsed and confirmed, a No remembered, the loop resumed the answered run, the letter passed all checks on draft 1 and landed; every card change arrived over live SSE.

**Not verified:** Chrome's real side panel (the page ran as an extension tab), and the pipeline on a real Seek capture.

**Next up:** Phase 7c (side outputs), on the user's go-ahead.

## 2026-10-04 — Cover-letter writer v3: apply the evidence, no stock close — DONE ✅

**Goal:** stop letters failing would_send for reciting the profile and ending on a
stock close (panels opus-v2/v3).

**Built:** writer rules in `app/llm/letter/tools/generate.py` (apply each piece of
evidence to the employer's work from the ad, select instead of retelling, no new facts
in the connecting sentence, no stock bridges/closes, a specific close), one line in
`revise.py`, `SKILL.md` shape, and `style_lint` warnings `stock_close` / `stock_bridge`.
19 new tests by a Sonnet subagent to a spec (699 total); fixed a flaky 7b test.

**Result** (`workflow-v3`, 15 ads, $3.35): stock closes 13/15 -> 0/15, bridges 11 -> 0,
verbatim profile copying 36% -> 18%; 15/15 clean; $0.213/letter like for like (+16%).
Blind panel opus-v4 (57/60 consistent with opus-v3): musts 15 vs 15, claims 12 vs 11,
detail 13 vs 13, **would_send 0 vs 0**: the writer swapped the old habits for new
formulas ("prepares me to", "exactly the kind of"). Kept; wording polish deferred by
the user. `evals/results/grading-opus-v4.md`.

**Not done:** the agent on v3 (optional, not asked), and the follow-ups in the plan's
Decision log (attachment claims in `check_claims`, "rather than" gap-led pattern,
repeated-tail lint).

---

## 2026-10-03 — Cover-letter Phase 7b: ask_user, remembered "no"s, the to-work-on list — DONE ✅ (API only)

**Goal:** a must-have gap asks the user once instead of being silently left out; every
"No" is remembered and counted, so the skills ads keep asking for rise to the top.

**Built:** migration `f6a9c3d8e217` (`gap_decisions`, `gap_sightings`, `origin` on
profile rows); `app/gaps.py`; `app/llm/letter/gap_policy.py`, `answers.py` (Yes ->
proposed rows -> confirm), `engines.py`, resume paths for both engines (run status
`answered`); `analyze_job` `skill` field (v3); API `app/api/letters.py`;
`scripts/gap_report.py`. Scan sightings after extraction; origin carry-over and
auto-clear on profile save. 148 new tests (680 total), written by a Sonnet subagent to
a spec. Live prompt smoke $0.0135.

**Not verified:** the sidebar question card and the idle-loop resume (Phase 8), and an
ask_user on a real ad (no eval ad has a pending must-have gap).

---

## 2026-10-03 — Cover-letter Phase 7a: the agent, compared with the workflow — DONE ✅

**Goal:** answer the plan's main question: does a model choosing the steps beat the
fixed workflow, on the same tools, guardrails and writer?

**Built:** `app/llm/letter/registry.py` (tools + model-facing specs + guardrail
gates), `agent.py` (the §5.5 orchestrator loop, stateless turns, code-enforced
budget and finish), `outcome.py` (the run start/end shared with the workflow).
`letter_lab.py run --engine agent`, an agent section in `loop-report`, and
`cost-report` (where each letter's money and time go). 67 new tests (531 total),
written by a Sonnet subagent to a spec.

**Result (`agent-v1`, 15 ads):** $0.178/letter (orchestrator $0.013 of it), 182 s
(orchestrator 47 s), 15/15 clean, 0 refusals, and the same tool path as the
workflow on every job. Blind Opus panel opus-v3 against workflow-v2: musts 15 vs 15,
claims 12 vs 13, detail 12 vs 12, would_send 0 vs 0; the panel re-graded workflow-v2
at 57/60. Claude recommended the workflow; **the user chose the agent as the default
engine** (agent experience; ~$0.26/month extra at 20 letters), with the workflow as fallback.
`evals/results/grading-opus-v3.md`, `agent-v1-loop.md`, `cost-workflow-v2-vs-agent-v1.md`.

---

## 2026-10-03 — Cover-letter Phase 6: fixed workflow (the baseline) — DONE ✅

**Goal:** turn Phase 5's tools into a fixed loop, the baseline the Phase 7
agent has to beat, and measure whether revisions still regress.

**Built:** `app/llm/letter/workflow.py`: `run_workflow()` does analyze → match →
gap policy (`leave_out` until `ask_user` exists) → generate → 3 checks →
revise the latest draft ≤2 times → finish. When it stops short, it hands back
`guardrails.best_draft` with its open issues instead of crashing.
`letter_lab.py run --engine workflow` and `loop-report`. The eval set grew to
15 ads, and the one-shot baseline was extended to cover them.

**Result (`workflow-v1`):** $0.194/letter and 138 s; 14/15 clean. No revision
went over length or broke a passing check; 2/6 dropped a must-cover item.
Per-run detail is in `evals/results/workflow-v1*.md`; the decisions are in
docs/cover-letter-loop-plan.md's Decision log.

**Graded** by a blind Opus panel (user's request): a written standard
(`evals/grading-standard.md`), two graders plus an adjudicator, checked against
the user's held-out grades. Workflow beat one-shot on every judgement item
(musts 15 vs 4, claims 6 vs 2, detail 11 vs 4, would send 2 vs 0); details in
`evals/results/grading-opus-v1.md`. Found and fixed: a revision invented a video
link an ad asked for, so `check_claims` now blocks links and emails that aren't
in the profile.

**Follow-up (`workflow-v2`, the Phase 7 baseline):** skills the profile only
lists no longer count as must-cover or as experience. Unsupported claims fell
from 9 letters to 2, and gap-led sentences from 9 to 0; $0.184/letter, 15/15
clean. `would_send` is still 0/15. Each letter recites the profile without
applying it to the employer's work, which is the next thing to improve. The
grading panel is now repeatable: `scripts/grading_panel.py` and
`evals/grading-panel.md`.

**Verified:** `python -m pytest` (464 passed, 59 new).

---

## 2026-09-21 — Rolling retention + post-profile-update weighting — DONE ✅

**Goal:** hard-deleting low scorers had been fixed with soft deletes, which
leaves the history growing forever; and after a profile edit the miner kept
learning from matches scored against the *old* profile.

**Built:** `app/retention.py` (window, floor, sweep, weights) and
`app/screenshots.py` (downscale, expiry). Daily sweep at the top of the idle
loop deletes `status='new'` / not-applied matches older than 122 days
(newest 150 always kept); screenshot files expire at 30 days with
`screenshot_taken_at` retained; `GET /jobs/evidence-export` zips the CSV +
surviving screenshots; Applied cards show the expiry date. The miner and
`/jobs/search-performance` read the same window the sweep keeps and weight
post-profile-change matches 1.0 vs 0.35 for stale ones. Migration
`a7d2c9e15b48`. The LLM suggestion cache now also invalidates on a profile
change, and `should_refresh` uses `abs()` because the count can now shrink.

**Found on live data:** the dev profile was re-seeded today, so all 16
matches read as stale; uniform 0.35 weights flipped the top suggestion back
to `software engineer` (the original bug). Fixed with `relative_weights` —
discount only when a fresh match exists to prefer.

**Verified:** 175 tests pass. **Not verified:** the sweep on a genuinely aged
DB, and the sidebar changes in a loaded Chrome extension.

---

## 2026-09-21 — Search-suggestion overhaul: 4-layer pipeline — DONE ✅

**Goal:** the suggested-searches banner was offering "Administration Assistant
· Intermediate Software · Intermediate Software Engineer". Two of those are the
same idea and neither is a phrase anyone types into Seek. Replace frequency
ranking with something that measures payoff, and stop discarding the signals
that would make it measurable.

**Diagnosis** (run against the live dev DB, 25 matches, baseline avg 69.4):
frequency ranked `software engineer` top at 12 occurrences despite averaging
60.9 — *below* baseline. Frequency measures what the user already searched for,
not what worked. It also emitted nested n-grams and non-role fragments
(`experienced software`, `engineer front`, `systems automation`).

**Delivered**
- **Layer 1 — `app/search_suggest.py`** (new, pure Python, no LLM). Ranks by
  `(shrunk_mean - baseline) * log1p(support)`; Bayesian shrink K=3 stops a
  single 92-point job outranking a phrase with real support. Leading-segment-
  only mining, MODIFIER_TOKENS stripping (seniority words are Seek *filters*,
  not keywords), a ROLE_NOUNS head-noun gate, then nested + family dedup.
  Ranks over the whole score distribution — the low scores are what make the
  baseline meaningful.
- **Layer 2 — capture.** `job_listings.discovered_query` (migration
  `c5b21d7f4e3a`) from the search page; classification/subclassification now
  actually sent by the extension (columns existed since `aa057c74b513`, all 25
  rows were NULL) via the page's schema.org JSON-LD, with a selector fallback.
  `/ingest` backfills NULL fields without overwriting, so card and detail
  captures complete each other.
- **Layer 3 — `GET /jobs/search-performance`.** Volume/hits/yield per query;
  >=5 jobs and <15% yield halves the rank of any phrase that query contains.
  Demote, not delete. Surfaced as a table in the Personalise panel.
- **Layer 4 — `app/llm/search_refine.py`**, OFF by default behind a checkbox.
  One `complete_json` on OPENAI_MODEL_SMALL that picks/generalises among mined
  candidates rather than generating freely; debounced at 10 new matches and
  cached in `profiles.preferences`. Failure falls back to the mined list.

- **Location scoping.** A clicked suggestion opens the search scoped to
  `target_location`, falling back to `location`, else nationwide. Slug path +
  `?where=` deliberately, so `currentSearchQuery()` still attributes the job to
  the bare phrase and the same role in two cities aggregates under one query.
- **Bulk hide became a soft delete** (`matches.hidden_at`, migration
  `d3f8a1c60b92`). Found this mid-session: the dev DB had gone 25 jobs → 16
  and the baseline 69.4 → 82.5 because everything under 70 had been deleted,
  which is exactly the contrast Layer 1 measures against. `DELETE /jobs` now
  stamps `hidden_at` and nulls `raw_description` (the bulk of the storage)
  rather than removing the row. Hidden matches leave every `/jobs` view and
  are skipped by the cover-letter loop, but still feed the baseline and the
  yield stats.

**Result on live data:** `["Administration Assistant", "Data Analyst",
"Software Engineering"]`. Both "Intermediate" fragments gone. "Administration
Assistant" survives on merit — avg 86 vs a 69.4 baseline.

**Verified:** `python -m pytest` — 132 passed (44 new). `node --check` on the
three edited JS files. Endpoint exercised against the live dev DB. Attribution
round-trip (`seekSearchUrl` → `currentSearchQuery`) checked directly, including
the `/in-Brisbane-QLD-4000` form Seek redirects `?where=` into.
**Not verified:** sidebar UI in a loaded Chrome extension; the classification
selectors against a real Seek page (JSON-LD is primary, selectors are a
fallback guess — see CLAUDE.md CURRENT TASK).

---

## 2026-09-11 — Extension revamp: Centrelink-ready application tracking — DONE ✅

**Goal:** implement docs/extension-revamp-plan.md — score-tiered card UI,
idle-loop reprioritization (cover letters before new extraction), application
tracking for Centrelink mutual-obligation reporting, a cleaner cover-letter
card state, a Quick-Apply overlay on Seek, and free search-phrase suggestions.

**Delivered**
- Migration `60d65ffb16df`: `matches.applied_at`. Status vocabulary
  (`new -> shortlisted -> applied -> interviewing -> rejected/withdrawn`)
  documented in docs/database-schema.md.
- `app/api/main.py`: `PATCH /jobs/{id}/status` (idempotent `applied_at`
  stamping), `PATCH /jobs/{id}/cover-letter`, `GET /jobs/suggested-searches`
  (pure-Python bigram/trigram mining, no LLM call, registered before
  `/jobs/{job_id}` to avoid the route-match collision — same pattern as the
  existing `/jobs/known-ids`), `GET /jobs` gained `top_skills` + a `status`
  filter (sorts by `applied_at desc` when set). Idle loop reordered — cover
  letters (score>=75, no letter yet) checked first every iteration.
- `extension/sidebar.html` + `sidebar.js`: client-side tiering
  (blue>=90/green>=75/amber 60-74/hidden<60 behind a lazy fold), Applied tab +
  CSV export, "Mark Applied" per card, cover-letter ready/pending/none state
  machine with an editable Copy/Save textarea, dismissible resize hint,
  dismissible suggested-searches banner (`chrome.storage.local`, 7-day
  cooldown). Dropped `pollForCoverLetter` in favor of SSE-driven reload.
- `extension/content_script.js`: Shadow-DOM Quick-Apply panel on `/job/{id}`
  pages when a cover letter exists — title, editable textarea, Copy/Save
  edits/Mark Applied, all through the existing PATCH endpoints. Apply-flow
  auto-detection deliberately deferred (needs live inspection of Seek's
  actual Apply DOM — see CLAUDE.md CURRENT TASK).
- `app/llm/cover_letter.py`: system prompt now requests a `Sincerely, {name}`
  sign-off and foregrounds degree/university for recent grads.

**Verified:** `python -m pytest` (72 passed); manual TestClient round-trips of
both new PATCH endpoints against the live dev DB, including idempotency of
`applied_at` on repeat "mark applied" (then reverted the test writes);
`node --check` on all three edited extension JS files. **Not verified:** the
sidebar/Quick-Apply UI in an actual loaded Chrome extension — no browser
harness available this session.

---

## 2026-06-23 — LLM matching + scoring (match.py) — DONE, verified live ✅

**Goal:** score each extracted job against profile id=1 into a `matches` row
(0–100 + reasoning + gaps). Followed the matching plan, which scoped **cover
letters OUT** of this pass (separate next task) — so nothing writes `cover_letters`.

**Delivered**
- `app/llm/prefilter.py` — pure-Python, zero-LLM `prefilter(job, profile) ->
  PrefilterResult`: hard/soft skill match/gap lists, overlap %, `seniority_flag`
  (senior/lead/etc.), `qual_match` (user qual words vs job qual-requirement text).
  Context for the LLM, **not** a gate — every extracted job still gets scored.
- `app/llm/match.py` — `match_job(job_id, profile_id, session=None, force=False)`.
  Eager-loads profile (skills, quals, experiences+linked skills) + job (job_skills),
  builds plain-text candidate/job summaries + prefilter signals, calls
  `complete_json` (Pydantic `MatchScore`, temp 0.1) with a new-grad-fair prompt
  ("X years" = soft gap, weight quals/projects). Clamps score 0–100, upserts on
  UNIQUE(user_id, job_id), status `new`, `gaps` stored as a JSON string. Parse
  failure records a 0-score row rather than vanishing. Skips unextracted jobs and
  already-scored pairs unless `force`.
- `scripts/seed_profile.py` — idempotent richer seed for profile 1 (16 skills,
  1 qualification, 3 experiences with linked skills via `experience_skills`).
- `scripts/run_matching.py` — `--profile-id/--job-id/--limit/--force/-v`; default =
  extracted jobs with no match row for the profile; per-job try/except;
  `DailyQuotaError` stops the batch.
- API: `GET /jobs` + `/jobs/{id}` now return `gaps` as a list (`_gaps_to_list`,
  `json.loads`); `/jobs` already joined `matches` and ordered by score desc.

**Verified live 2026-06-23** (all acceptance criteria PASS):
- All 3 extracted jobs scored: Graduate SWE **85**, AI Engineer **65**, AI Lead **65**
  — relevant grad role clearly out-scores the senior/lead roles, and the lead role
  was NOT auto-zeroed (new-grad fairness holds). Reasoning + specific gaps populated.
- Re-run without `--force` → "no jobs to match", row count stays 3 (idempotent;
  UNIQUE user_id+job_id holds).
- `GET /jobs` (TestClient) → 3 rows ordered by score desc, `gaps` as JSON arrays.

**Next task:** cover-letter generation — `complete_text` (temp ~0.7) for
above-threshold matches → one `cover_letters` draft per match (UNIQUE match_id),
pulling real evidence by walking `experience_skills`. No new schema needed.

---

## 2026-06-23 — LLM extraction (extract.py) — DONE, verified live ✅

**Goal:** replace the `extract.py` stub with real Gemini extraction of structured
fields from `job_listings.raw_description` (CLAUDE.md "Current Task").

**Delivered**
- `app/llm/client.py` — provider abstraction (the ONE place model/provider live).
  `complete_json(system, user, schema, temp)` + `complete_text(...)`. Reads
  `LLM_PROVIDER`/`GEMINI_MODEL`/`GEMINI_API_KEY`(or `GOOGLE_API_KEY`)/`LLM_RPM`
  from env; in-process RPM throttle; 429 backoff (2/4/8, max 3); a *daily*-quota
  429 raises `DailyQuotaError` to stop a batch. Loads `.env` itself (no import-order
  dependency). Calls `truststore.inject_into_ssl()` so TLS uses the Windows cert
  store (AV TLS-interception breaks certifi — same root cause as the pip quirk).
- `app/llm/extract.py` — `extract_job(job_id, session=None, force=False)`. Pydantic
  `JobExtraction` schema (hard/soft skills, qualifications[], experience[], seniority
  enum, key_responsibilities[], summary) drives Gemini structured output. Idempotent:
  clears prior `job_skills` + extracted fields, dedupes skills, sets `extracted_at`.
  Skips null-`raw_description` and already-extracted (unless `force`).
- `scripts/run_extraction.py` — `--limit/--job-id/--force/-v`; per-job try/except;
  stops cleanly on `DailyQuotaError`; prints processed/succeeded/failed summary.
- API: `_process_listing` now wraps `extract_job`/`match_job` in try/except so a bad
  job can't kill the background task.
- Schema: added `seniority`, `key_responsibilities`, `summary`, `extracted_at` to
  `job_listings` (migration `f42bcd31e385`, batch-mode; `alembic check` clean).
  `*_requirements` now hold JSON. `docs/database-schema.md` updated.
- `requirements.txt` += `google-genai`, `truststore`.

**Self-test tool:** `scripts/check_llm.py` — key present → `list()` → one tiny
generate; prints PASS or a diagnosed FAIL. Use to validate any key before a batch.

**Verified live 2026-06-23** (all acceptance criteria PASS):
- `run_extraction.py --job-id 4` extracted the Graduate SWE ad → 5 hard + 11 soft
  `job_skills`, qualifications/experience JSON (must-have vs nice-to-have split
  correctly, e.g. Elixir → required:false), `seniority=graduate`, summary,
  key_responsibilities; `extracted_at` set.
- Re-run without `--force` → **skipped** (logged, no LLM call); `--force` →
  re-extracted with **no duplicate rows** (16→17, not 32 — clear-before-insert works).
- Null-`raw_description` row → **skipped with a warning, no crash**.
- All 3 real captures extracted: job 2 `lead`, job 3 `mid`, job 4 `graduate`.

**The key blocker, resolved:** the `AQ.Ab8…` keys are short-lived Google AI Studio
*ephemeral tokens*, and the user's personal Google account had no free-tier Gemini
quota ("project quota tier unavailable / contact your project administrator"). Fix
that worked: signed up for the **Google Cloud $300 free-trial** (billing account with
promo credit) → the project got paid-tier access drawn from the $300 credit. At
~$0.0003/job this is effectively free. Key lives in `.env` as `GOOGLE_API_KEY`.

**Next task:** real `app/llm/match.py` — 0–100 scoring of job vs profile (reasoning,
gaps) into `matches`, + cover-letter draft into `cover_letters`.

---

## 2026-06-17 — Extension: limited "Scan Page" (1-hop, 3-page) detail capture 🧪

**Goal:** confirm the detail-page → `/ingest` → extractor pipeline end to end on a
few real jobs, without a crawler. Owner-approved exception to the no-automated-Seek
policy, scoped tight (see CLAUDE.md "1-hop rule").

**What it does:** a **Scan Page** button in the side panel. On a Seek search results
page the user opened, it reads the job links already on that page, then navigates the
**active tab** to the first 3 of them — one at a time, **5s apart** — letting the
existing content script capture each detail page (full description) via `/ingest`.
It then returns to the search page and refreshes the matches list.

**Why the side panel (not the popup):** the panel survives the active tab navigating;
a popup is torn down on the first navigation, killing the loop.

**1-hop guarantee:** links come only from `parseSearchPage()` of the page the user
opened (`COLLECT_LINKS` message); the scan never collects links from the detail pages
it visits. Caps: `MAX_SCAN_PAGES = 3`, `SCAN_DELAY_MS = 5000` (`extension/sidebar.js`).

**Files touched:** `extension/sidebar.html` (+button/log), `extension/sidebar.js`
(self-contained scan via `chrome.scripting`), `extension/content_script.js`
(`collectJobLinks`, au.seek.com paths), `extension/manifest.json` (au.seek.com host +
broad match), `extension/selectors.js` + `app/scraper/selectors.py` (card selector).

**Live DOM fixes (real Seek differs from assumptions):**
- Host is **`au.seek.com`**, NOT `www.seek.com.au` — manifest/gate/base-URLs updated.
- Search URLs are SEO slugs (`/software-engineer-jobs/in-All-Brisbane-QLD`), not `/jobs`.
- Card container `[data-testid="job-card"]` (was `[data-automation="normalJob"]`, which
  missed premium/featured). `jobTitle`/`jobCompany`/`jobLocation`/`jobAdDetails` confirmed.
- The auto-injected content script wasn't injecting reliably, so the scan now injects its
  own collector/scraper via `chrome.scripting.executeScript` and POSTs to /ingest itself —
  no dependence on the declarative content script.

**VERIFIED LIVE 2026-06-17 ✅** — first successful real capture. Scan found 31 links,
visited 3 (5s apart), scraped full descriptions (4095/3343/3079 chars) → 3 rows in
`job_listings` with `raw_description`. Sidebar shows "No matched jobs" because `/jobs`
lists *scored* matches and `match.py` is still a stub — listings are stored fine.

---

## 2026-06-17 — Pivot: Playwright scraper ABANDONED → Chrome extension + API ✅

**Why:** Seek sits behind Cloudflare, which permanently loops the "Just a moment…"
challenge against any Playwright-driven browser — even with the mandatory proxy and
even when a human solves the challenge by hand (verified live 2026-06-16: the
automated client is never trusted, so clearance is re-challenged every page). Seek's
robots.txt also disallows the search (`*?`) and `*/job/` paths for generic agents,
and there is no free candidate-side Seek API. Pushing past Cloudflare would require
anti-bot evasion we won't build.

**New architecture (better product, zero detection surface):** the user browses Seek
in their *own real browser* (passes Cloudflare naturally, real IP — no proxy needed).
A **Chrome extension** reads the already-rendered DOM and POSTs listings to a **local
FastAPI backend**, which upserts them and (later) runs LLM extraction + matching.

**Delivered this session**
- `app/api/main.py` — FastAPI: `GET /health`, `POST /ingest` (upsert + backfill
  `raw_description` on existing rows; fires background tasks), `GET /jobs`,
  `GET /jobs/{id}`, `POST /jobs/{id}/regenerate`, `GET/PUT /profile/{id}`. CORS open
  for local dev; `get_db` session dependency.
- `app/llm/extract.py` + `match.py` — STUBS (log "TODO: implement") so the
  ingest → background-task pipeline runs end to end before the LLM work lands.
- `scripts/run_api.py` — uvicorn on 127.0.0.1:8000 (reload).
- `extension/` (Manifest V3): `manifest.json`, `selectors.js` (mirror of the Python
  selectors), `content_script.js` (parses /jobs + /job/ pages, polls for React
  render, POSTs to backend), `background.js` (badge + message relay), `popup.html/js`
  (backend health + open sidebar), `sidebar.html/js` (ranked matched-jobs list).
- `requirements.txt` += fastapi, uvicorn[standard], httpx.

**Kept (not deleted):** `app/scraper/` stays — selector logic is reused by the
extension (`extension/selectors.js` mirrors `app/scraper/selectors.py`), and
`explore_seek.py` is still a handy dev tool. The proxy infra (`require_proxy()` etc.)
is dormant but harmless; the new pipeline never makes automated requests to Seek.

**Out of scope this session:** real LLM extraction/matching, cover-letter generation,
profile UI, auth, Postgres. Stubs only.

**Verified this session**
- Deps installed (`fastapi 0.137`, `uvicorn 0.49`, `httpx 0.28`) — needed the
  `--trusted-host pypi.org --trusted-host files.pythonhosted.org` workaround (AV TLS
  interception on this machine; unrelated to the now-uninstalled Windscribe).
- `alembic upgrade head` + `scripts/seed_saved_search.py` → profile id=1 exists.
- All backend acceptance checks PASS against a live uvicorn:
  `/health` ok; `/ingest` new=1 then dedup new=0; detail re-ingest backfills
  `raw_description` (updated=1) then updated=0; `/jobs` `[]`; `/profile/1` ok;
  `/jobs/{id}` returns full detail / 404 when absent. Background tasks fire on ingest.
  (Note: send JSON to the API via PowerShell `Invoke-RestMethod`, not `curl.exe` —
  PS mangles embedded double-quotes.)

**Remaining (handed to the user):** load `extension/` unpacked in Chrome and do the
first live capture — this is also the first chance to verify the `data-automation`
selectors against a real Seek DOM (fix `extension/selectors.js` + its Python mirror
together if a capture returns 0 cards).

---

## 2026-06-15 — Seek scraper (Task 2) — code complete, NOT yet run ⏸️

Built the full scraping layer. **No scraping has been executed** (per the standing
constraint — selectors are unverified against live DOM). Code is structured so that
Step 0 exploration + a small `--limit 5` live run are the only remaining steps.

**Delivered**
- `app/scraper/selectors.py` — single source of truth for all Seek selectors
  (`data-automation` based) and the generic search-URL builder (`keywords`/`where`/
  `worktype`/`sortmode=ListedDate`). All selectors flagged "verify in Step 0".
- `app/scraper/browser.py` — `launch_browser()` context manager (headless Chromium,
  default realistic UA) + `build_proxy_from_env()`. Playwright imported lazily.
- `app/scraper/search.py` — **Component 1**: walks results pages, parses each card
  into a `ScrapedListing`, dedupes on `(source='seek', source_job_id)`, early-stops
  when a page has zero new listings. Does not cap (capping is orchestration's job).
- `app/scraper/detail.py` — **Component 2**: visits each detail page, extracts the
  ad body from the description container (noise sections excluded by being outside
  it), randomised 2–5 s delay *between* visits. Per-page errors logged, not fatal.
- `app/scraper/run.py` — `run_daily_scrape(max_new_per_search=20, limit=None)`:
  iterates active saved searches → Component 1 → cap → Component 2 → insert →
  logged `RunSummary` (new / processed / deferred / inserted / errors). Effective
  cap = `min(max_new_per_search, limit)`.
- `scripts/explore_seek.py` — one-shot dev tool: caches a search page + a detail
  page to `dev_data/` (gitignored) for offline selector work.
- `scripts/run_scrape.py` — CLI: `--max-new`, `--limit`, `--headed`, `-v`.
- `scripts/seed_saved_search.py` — idempotent seed: test profile + one saved
  search (keywords="software engineer", location="Brisbane QLD", work_type=NULL).
- `requirements.txt` (+`playwright`), `.env.example` (optional `PROXY_*` vars),
  `.gitignore` (+`dev_data/`).

**Verified (offline only)**
- All modules byte-compile and import via `.venv`; Playwright stays lazy (not
  imported until a browser is launched).
- URL builder produces correct generic URLs incl. worktype code + date sort.

**Schema change (agreed): classification / subclassification**
- Added nullable `classification` + `subclassification` `Text` columns to
  `job_listings` (Seek's own free categorisation — cheap pre-filter + UI badge,
  distinct from LLM-extracted `job_skills`). Now persisted in the upsert.
- Migration `aa057c74b513` (batch-mode, portable); `alembic upgrade head` +
  `alembic check` = no drift. `docs/database-schema.md` updated (table, note, and
  `idx_job_listings_classification` index).

**Remaining before this task is "done"**
1. `python -m playwright install chromium` (one-time).
2. Run `scripts/explore_seek.py` once; reconcile `app/scraper/selectors.py` against
   the cached HTML; check `au.seek.com/robots.txt`.
3. `python scripts/seed_saved_search.py`, then `python scripts/run_scrape.py
   --limit 5` to verify end-to-end (and a second run → 0 new = dedup confirmed).

**Next up:** verify selectors via Step 0, then the LLM extraction pass
(`job_skills` / `*_requirements`).

---

## 2026-06-15 — Database layer (Task 1) ✅

Built the full persistence layer per `docs/database-schema.md`. Nothing else yet
(no scraper, matching, generation, or UI).

**Delivered**
- Repo scaffold: `app/`, `alembic/`, `scripts/`, `docs/`, `requirements.txt`,
  `.env.example`, `.gitignore`.
- `app/db.py` — engine + `SessionLocal` + `Base` from `DATABASE_URL`
  (default `sqlite:///app.db`); SQLite `PRAGMA foreign_keys=ON` listener; loads
  `.env` via python-dotenv.
- `app/models.py` — all 11 tables as SQLAlchemy 2.0 typed models, portable
  across SQLite and Postgres.
- `alembic/` wired to the app's engine + metadata; initial migration
  `cf7f9fc0ffc2_initial_schema` creates all 11 tables.
- `scripts/smoke_test.py` — seeds a full graph, runs the "experiences that
  demonstrate skill X" evidence query, and verifies cascade delete.

**Verified**
- `alembic upgrade head` on a fresh `app.db` → 11 tables. Downgrade/upgrade
  round-trips. `alembic check` reports no drift (models = migration = DB).
- `python scripts/smoke_test.py` → PASS (evidence query + cascade + shared job
  survives).

**Decisions worth remembering**
- Boolean defaults use `text("false")` / `text("true")` (not `0/1` or
  `false()`), so the migration runs unchanged on both SQLite 3.23+ and Postgres.
- PKs use `BigInteger().with_variant(Integer, "sqlite")` to autoincrement on
  both dialects.
- Relationships use `passive_deletes=True`, so deletes rely on DB-level
  `ON DELETE CASCADE` (this is what the smoke test actually exercises).

**Environment notes**
- pip is behind a TLS-intercepting cert here; installs need
  `--trusted-host pypi.org --trusted-host files.pythonhosted.org`.
- `docs/job-application-assistant-plan.md` is referenced by CLAUDE.md but not yet
  present in the repo.

**Next up:** the Seek scraper (Feature 2) — not started.
