# Cleanup plan: ARCHITECTURE.md 5.4 items #11 to #14

Written 2026-10-12 on branch `fix/applied-evidence-safety` (HEAD `bb4e87d`). PLAN ONLY:
no code was changed to write it. Line numbers are from that commit. Re-check them
before editing, because earlier steps move them.

**Scope.** Items #1-#8 are done (see `docs/cleanup-3-8-plan.md`). This plan covers the
rest of the 5.4 list **except** #9 (split `main.py`), #10 (split `sidebar.js`) and #15
(hardcoded profile id). Those are deliberately left for later. Don't start them, and
don't do "while I'm here" moves toward them.

**Who executes this:** a cheaper model, one step at a time, with the owner reviewing each
commit. Every step names its files, its tests, and how to check it without a paid AI
call. **All work happens on the current branch, `fix/applied-evidence-safety`.** Don't
create branches, push, or open PRs. The owner does those.

---

## 0. Ground rules for every step

- **No requests to Seek, ever.** No WebFetch, no server-side fetch, no browser automation
  pointed at Seek. The owner does every hand check that involves a Seek page, in their
  own browser.
- **real.db is off limits.** No step reads or writes it.
- **No migrations.** None of these items needs one. If you find yourself writing one,
  stop and ask.
- **No new paid AI calls.** Every new test replaces the AI call or never reaches it.
  This machine's `.env` holds working Vertex credentials, so a test that forgets to do
  this would spend real money. When you review, check that it doesn't.
- **The code stays importable after every save.** `scripts/run_api.py` runs uvicorn with
  `reload=True`, so a running real server reloads on every edit to `app/`. Make each
  edit complete before saving.
- **The Seek access policy stays as it is** (`CLAUDE.md`, "SEEK ACCESS POLICY"): links
  come only from the page the user opened, there's no second hop, `SCAN_DELAY_MS` stays
  >= 5000, and `SCAN_PAGES_CEILING` stays 25. Nothing in this plan changes which Seek
  pages are opened. Step 2 only cuts **localhost** requests.
- **Don't touch `app/scraper/` logic.** It's the dormant scraper, and `CLAUDE.md` says to
  keep it. Only its selector *values* are read by a new test (step 1).

### How to run the checks

- Python tests: **`.venv\Scripts\python -m pytest -q`**. Baseline at `bb4e87d`:
  **1540 passed**. Don't set `LLM_PROVIDER=stub` for the whole run (see step 5).
- JavaScript: `node --check extension/<file>.js` (syntax only).
- Browser checks (each builds a scratch DB, runs the server with the stub provider, loads
  the real unpacked extension, and aborts every request that leaves the machine):
  `.venv\Scripts\python tests/e2e/dashboard_e2e.py` and
  `.venv\Scripts\python tests/e2e/quick_apply_e2e.py`. Both must stay fully green.
- **Don't write an automated test that runs Scan Page.** The scan opens tabs with
  `chrome.tabs.create`, and those tabs can load before Playwright's routing attaches and
  reach real Seek (this has happened once). Scan behaviour is checked by the owner by hand.

---

## 1. Findings

### #12 Seek selectors in three places: **still real**

- `extension/sidebar.js:1174-1191` (`pageScrapeDetail`) hardcodes
  `[data-automation="jobAdDetails"]` (line 1180) and `[data-automation="job-detail-title"]`
  (line 1184). The same values are `SELECTORS.DETAIL_DESCRIPTION` / `DETAIL_TITLE` in
  `extension/selectors.js:17-18` and `DETAIL_DESCRIPTION` / `DETAIL_TITLE` in
  `app/scraper/selectors.py`.
- Why the copy exists: `pageScrapeDetail` runs inside the Seek tab through
  `chrome.scripting.executeScript({ func })` (`injectFn`, `sidebar.js:1205-1208`). An
  injected function can't see the side panel's globals. But `executeScript` takes an
  `args` array (JSON values), so the selectors can be passed in.
- The side panel doesn't load `selectors.js` today (`extension/sidebar.html:972-975`
  loads `config.js`, `screening_assist.js`, `sidebar.js` and `dashboard.js`). The content
  script does (`manifest.json`, `content_scripts.js`).
- **Name clash check (done):** `selectors.js` declares three top-level names:
  `SELECTORS`, `extractJobId` and `extractApplyJobId`. None of `config.js`,
  `screening_assist.js`, `sidebar.js` or `dashboard.js` declares these at top level.
  `sidebar.js` has a *nested* `function extractJobId` inside `scanPage` (line ~1250),
  which only shadows the global and is harmless.
- `pageCollectJobLinks` (`sidebar.js:1161`) uses the generic `a[href*="/job/"]`, which
  isn't a Seek-specific selector and isn't in either selectors file. Leave it.
- `seekProfileExtract` (`sidebar.js:~2288-2440`) has its own profile-page selectors.
  They exist in one place only, so they're **out of scope**.
- `app/scraper/selectors.py` is the dormant scraper's copy. It can't import from JS, so
  the realistic fix is a **test that fails when the two files disagree**. That turns
  "three places, silently" into "two places, checked".

### #13 Probable double capture during a scan: **real by reading the code; unverified at runtime**

- The scan opens each job in a background tab (`sidebar.js:~1280`,
  `chrome.tabs.create({ url, active: false })`), injects `pageScrapeDetail`, ingests the
  result itself (`ingestListing`, `sidebar.js:1213`), and closes the tab.
- That tab is a Seek `/job/{id}` page, so the manifest also runs `content_script.js` in
  it. `main()` (`content_script.js:919-981`) waits up to 8 s for the description, then
  POSTs `/ingest` (line 937), resolves the internal id (`GET /jobs/by-source-id/...`)
  and fetches `GET /jobs/{id}` for the Quick-Apply overlay. Whether these run depends on
  whether they finish before the scan closes the tab, which is a race.
- Cost: only extra **localhost** requests and an upsert that rewrites the same row. No
  extra Seek traffic (the tab loads once either way) and no AI call (`/ingest` never
  calls the AI).
- Fix shape: the background worker keeps the set of tab ids the scan opened. The
  content script asks "am I a scan tab?" first and returns at once if so. If the worker
  forgot the set (for example, after a restart), the answer is "no", which is exactly
  today's behaviour. So the fix **fails safe**. The side panel keeps a port open to the
  worker (`sidebar.js:15`), which keeps the worker alive during a scan.

### #11 Broad `except Exception`: **audited; one real gap, the rest are deliberate**

27 handlers in `app/` outside the dormant `app/scraper/` (the "37" in 5.4 included the
scraper's 10). No bare `except:` and no `except BaseException`. Grouped:

| Kind | Where | Verdict |
|---|---|---|
| Roll back, then re-raise | `cover_letter.py:264`, `extract.py:146`, `extract.py:252`, `match.py:334`, `quickscreen.py:258` | Fine: nothing is hidden. |
| Record the run as failed, then re-raise | `letter/agent.py:274`, `letter/workflow.py:170` | Fine. Budget/quota are caught *before* these. |
| Retry loops that end in `raise` | `client.py:378`, `client.py:589` | Fine: only 429/5xx retry; the rest re-raise. |
| Deliberate fail-open, logged | `client.py:263` (usage log), `client.py:281` (budget read), `client.py:363` (truststore), `extract.py:161` (gap sightings), `quickscreen.py:210` (screen fails open), `search_refine.py:143` (suggestions), `screenshots.py:51` (downscale), `production.py:217` (SSE emit), `production.py:286` (run already recorded), `runner.py:130` (a failed tool is data; budget/quota caught first) | Fine, as documented. |
| Loop/background guards, logged | `main.py:107` (counted retry), `main.py:150` (retention), `main.py:160` (orphan recovery), `main.py:352` (idle loop) | Fine. |
| **Background task `_process_listing`** | `main.py:560`, `main.py:565`, `main.py:575` | **Gap.** See below. |
| Re-scoring failure | `match.py:303` | Fixed by #8 (budget/quota re-raised first). |

**The gap:** `_process_listing` (`main.py:539-588`, used by `POST /jobs/{id}/regenerate`)
catches everything from `extract_job` and `match_job`, **including `DailyQuotaError` and
`BudgetExceededError`**, logs them as ordinary failures, and carries on to the next
step. After a daily-quota stop in extraction, it still calls `match_job` and then the
letter pipeline. Those make more doomed requests, and the sidebar never sees the
`llm_budget_blocked` event that the letter step and the idle loop send. The fix: catch
the two account-level stops first, broadcast `llm_budget_blocked`, and return.

Everything else stays as it is. To stop new silent catches creeping in, add a small
guard test (step 3.2): every `except Exception` in `app/` (outside `app/scraper/`) must
either end in a bare `raise` or carry a `# noqa: BLE001 — <reason>` comment.

### #14 Documentation drift: **all still true, except the last item**

| Item | Current text | Location |
|---|---|---|
| Table count | "Eleven tables" | `app/models.py:4` (ARCHITECTURE says 18; count `__tablename__`) |
| LLM package | "Stub package for now" | `app/llm/__init__.py:1-6` |
| API docstring | "LLM extraction + matching (stubbed for now)"; "uvicorn on 127.0.0.1:8000" | `app/api/main.py:1-9` |
| Claims judge tier | "**Small model**, independently" | `app/llm/letter/tools/check_claims.py:12` (`CLAIMS_TIER = "mid"`, line 55) |
| Providers | "OpenAI — active provider", "Gemini ... dormant fallback"; Playwright comment doesn't say the scraper is dormant | `requirements.txt` (LLM block; Playwright block) |
| Proxy | "Scraper proxy (REQUIRED for any scraping) ... MANDATORY", with a live `PROXY_SERVER=` line | `.env.example` (last block) |
| Scan wording | "Auto-navigate the user's active tab" (the code opens **background** tabs, `active: false`) | `CLAUDE.md:39` |
| `.claude/settings.json` tracked | ARCHITECTURE says it "holds one machine's approval history" | **No longer true.** The owner rewrote it on purpose in `1b1e4cb` as a short, reviewed project policy (allow/deny rules, including deny rules for Seek WebFetch and `git push`). Recommendation E1: keep it tracked and close the item. |

---

## 2. Order

All on `fix/applied-evidence-safety`. **Each step is one commit.** After each commit,
stop and report to the owner (what changed, the check results, anything surprising),
then wait for "next".

| Step | Item | Why here |
|---:|---|---|
| 0 | - | Commit this plan file first: "Docs: cleanup plan for ARCHITECTURE 5.4 #11-#14". |
| 1 | #12 | Small, self-contained, and step 2 edits the same scan code. |
| 2 | #13 | Builds on step 1's `injectFn` change; needs the owner's hand check. |
| 3 | #11 | Backend only; unit-testable. |
| 4 | #14 | Comments and docs only. |
| 5 | extra (optional, E4) | A one-line test quirk left over from the last plan. |
| 6 | docs | Status updates for everything above. |

**How to review each step:** read the test diff first (what is promised), then the code
diff, then run the checks it lists.

---

### Step 1: #12, one source for the scan's selectors (~40 lines)

**1.1 The side panel loads `selectors.js`**
- `extension/sidebar.html:972-975`: add `<script src="selectors.js"></script>` right
  after `config.js`, so the order matches the manifest's content-script order:
  `config.js`, `selectors.js`, `screening_assist.js`, `sidebar.js`, `dashboard.js`.
- Re-run the name-clash check from finding #12 before saving. Grep the four other
  scripts for top-level `SELECTORS`, `extractJobId` and `extractApplyJobId`. A duplicate
  top-level `const` across classic scripts is a SyntaxError that breaks the whole panel.
- `extension/selectors.js:1-6` header: say it's loaded by the content script **and** the
  side panel, and that the side panel passes the values into injected functions as
  `args`.

**1.2 Pass the selectors into the injected scrape**
- `injectFn` (`sidebar.js:1205`): add an `args = []` parameter and pass it through:
  `chrome.scripting.executeScript({ target: { tabId }, func, args })`.
- `pageScrapeDetail` (`sidebar.js:1174`): take one parameter `sel` and use
  `sel.description` and `sel.title` instead of the two literals. Add a comment saying
  why: an injected function can't see the panel's globals, so the selectors arrive as
  `args`.
- The call site (`sidebar.js:~1292`):
  `injectFn(bgTab.id, pageScrapeDetail, [{ description: SELECTORS.DETAIL_DESCRIPTION, title: SELECTORS.DETAIL_TITLE }])`.
- `pageCollectJobLinks` keeps its no-argument call.

**1.3 A test that keeps JS and Python in step**: new `tests/test_selectors_sync.py`
(no AI, no network)
- Read `extension/selectors.js` as text and pull `KEY: 'value'` pairs with a regex like
  `^\s*([A-Z_]+):\s*'([^']*)'` (multiline).
- Read `app/scraper/selectors.py` with **`ast`**, not `import`:
  `app/scraper/__init__.py` imports the scraper runner, and the test shouldn't load it.
  Collect module-level `NAME = "string"` assignments.
- Assert every key present in both has the same value.
- Assert this shared set is non-empty and includes at least `JOB_CARD`,
  `CARD_TITLE_LINK`, `CARD_COMPANY`, `CARD_LOCATION`, `CARD_WORK_TYPE`, `CARD_SALARY`,
  `DETAIL_DESCRIPTION`, `DETAIL_TITLE`, `DETAIL_COMPANY`, `DETAIL_LOCATION` and
  `DETAIL_WORK_TYPE`, so the test can't pass by matching nothing.
- Assert `extension/sidebar.js` no longer contains the strings `jobAdDetails` or
  `job-detail-title` (the hardcoded copies are gone).

**Checks:** `node --check extension/sidebar.js extension/selectors.js`; the new test; the
full suite; **`dashboard_e2e.py`** (it loads `sidebar.html`, so it proves the extra script
didn't break the panel).

**Hand check (owner):** TEST backend, reload the extension, open a Seek search page
yourself, and press Scan Page with the cap at 2-3. Each job should log
"captured ✓ (N chars)" with a real character count, not "NO DESCRIPTION".

---

### Step 2: #13, scan tabs don't capture themselves (~40 lines)

**2.0 Confirm first (owner, no code). Do this before writing step 2.1.**
- TEST backend (`run_api.py test`), extension reloaded. Set the scan cap to 3 in
  Personalise. Open a Seek search page with new jobs and press Scan Page.
- In the server console, count the lines for one scanned job: `POST /ingest`,
  `GET /jobs/by-source-id/...` and `GET /jobs/<id>?profile_id=1`. The search page itself
  makes one `POST /ingest` for its cards. Ignore that one.
- **Two `POST /ingest` per scanned job (or any `by-source-id` lookups from the scan):**
  confirmed, so do 2.1. **One each and no lookups:** #13 doesn't happen in your Chrome.
  Skip 2.1, and record "checked, not observed" in step 6.

**2.1 The fix**
- `extension/background.js`:
  - add `const scanTabs = new Set();` with a comment (tabs the side panel's scan opened;
    lost on a worker restart, which only brings back the harmless double capture);
  - in the existing `onMessage` listener, add three message types:
    - `SCAN_TAB_ADD {tabId}` adds the id;
    - `SCAN_TAB_REMOVE {tabId}` deletes it;
    - `IS_SCAN_TAB` replies `sendResponse({ scan: scanTabs.has(sender.tab?.id) })`;
  - add `chrome.tabs.onRemoved.addListener((id) => scanTabs.delete(id));` as a backstop.
  - Leave the `BACKEND_FETCH` allow-list exactly as it is.
- `extension/sidebar.js`, `produce()` (`~1280-1290`): right after `chrome.tabs.create`
  returns, `await chrome.runtime.sendMessage({ type: 'SCAN_TAB_ADD', tabId: bgTab.id }).catch(() => {});`.
  When the tab is closed (the `finally` and the error path), also send `SCAN_TAB_REMOVE`
  with `.catch(() => {})`. The content script runs at `document_idle`, well after this
  message, so the registration wins the race. If it ever loses, the result is today's
  behaviour.
- `extension/content_script.js`, `main()` (line 919): first thing, before
  `startApplyWatcher()`:
  ```js
  // A tab the side panel's scan opened: the scan reads and ingests it itself
  // (sidebar.js pageScrapeDetail), so capturing here too would double every request.
  if (await isScanTab()) return;
  ```
  with a small `isScanTab()` that sends `{ type: 'IS_SCAN_TAB' }` and returns
  `reply?.scan === true`, or `false` on any error. **Fail to "not a scan tab"**, never the
  other way, so a normal Seek page always gets captured.
- **Don't** change which URLs are opened, the delay, the cap, or the tab's `active: false`.

**Checks:** `node --check` on all three files. Full suite (unchanged).
**`quick_apply_e2e.py` and `dashboard_e2e.py` both green.** The Quick Apply e2e runs the
content script in a normal tab, so it proves `isScanTab()` answers "no" there.
**Don't** add an automated scan test (section 0).

**Hand check (owner):** repeat 2.0. Expect **one** `POST /ingest` per scanned job and no
`by-source-id` lookups from the scan tabs. The scan log should still show
"captured ✓" and screen results. Then open one Seek job page yourself, in a normal tab,
and check it's still captured and the Quick-Apply overlay still appears for a job that
has a letter.

---

### Step 3: #11, account stops in `_process_listing` and a guard test (~80 lines)

**3.1 `_process_listing` stops on an account-level stop**
- `app/api/main.py:557-581`. Before each of the three `except Exception` blocks (extract,
  match, letter), add `except (DailyQuotaError, BudgetExceededError) as exc:` that:
  - logs a warning (no traceback): "job %s: %s — stopping this job's processing";
  - calls `broadcast_from_thread("llm_budget_blocked", {"reason": str(exc)})` (the same
    event and payload shape as `main.py:~578`);
  - `return`s, so the later steps don't run.
  Both names are already imported (`main.py:48`). An ordinary `Exception` from
  `extract_job` keeps today's behaviour (log, then still try `match_job`), because a bad
  extraction shouldn't stop the score.
- Tests: new `tests/test_process_listing_stops.py`. Patch `api.extract_job`,
  `api.match_job`, `api.letter_production.generate_for` and `api.broadcast_from_thread`
  on the `app.api.main` module with fakes that record calls. Check how `main.py` imports
  each name first, and patch the name `main.py` actually looks up. Nothing here reaches
  `client.py`, so no AI call is possible. Cases:
  1. extract raises `DailyQuotaError`: match and letter are **not** called, and one
     `llm_budget_blocked` broadcast is sent;
  2. extract OK, match raises `BudgetExceededError` with `with_cover_letter=True`: the
     letter is **not** called, and one broadcast is sent;
  3. extract raises `ValueError`: match **is** still called (today's behaviour kept);
  4. `has_description=False`: nothing is called (regression guard).

**3.2 Guard test: no new silent catch-alls**
- New `tests/test_exception_audit.py`. Walk every `.py` under `app/` except
  `app/scraper/` with `ast`. For each `ExceptHandler` whose type is the name `Exception`
  (alone or inside a tuple), it passes if **either**:
  - the handler body's last statement is a bare `raise` (`ast.Raise` with `exc is None`),
    **or**
  - the `except` line's source text matches `noqa: BLE001\s*[—-]+\s*\S`, meaning the
    noqa comment has a reason after it.
  On failure, list every offending `file:line` in the assertion message.
- Then make it pass by adding short reasons, not by changing behaviour. The table in
  finding #11 gives the reason for each. Today these will fail:
  - no comment: `main.py:150`, `main.py:160`, `main.py:352`, `client.py:363`,
    `quickscreen.py:210`;
  - noqa with no reason: `main.py:565`, `main.py:575`, `client.py:263`, `client.py:281`,
    `extract.py:161`, `production.py:217`.
  Trust the test's output over this list.
- Comments only in this half of the step. If a handler looks wrong while you're adding
  its reason, **stop and report it** instead of changing it.

**Checks:** the two new tests, then the full suite (1540 + new).

---

### Step 4: #14, docs drift (comments and docs only, no logic)

1. `app/models.py:1-12` docstring: replace "Eleven tables" with the real count
   (`grep -c "__tablename__" app/models.py`) and put every table under its half
   (profile side / job side / letter and usage tables). Check the names against the
   models; don't copy them from this plan.
2. `app/llm/__init__.py`: replace the "stub package" docstring with two or three lines:
   the LLM layer; every provider call goes through `client.py` (tiers, usage log, budget
   guard); the main modules are extract, match, quickscreen, cover_letter and `letter/`.
3. `app/api/main.py:1-9` docstring: drop "(stubbed for now)". Read `/ingest` and the idle
   loop first, then describe what actually happens (the idle loop does screen, extract,
   match and letters on the single worker). Replace the run line with
   `python scripts/run_api.py real|test` (ports 8000 / 8001). **Keep the file importable**:
   it's a docstring edit, so save once, complete.
4. `app/llm/letter/tools/check_claims.py:12`: "Small model, independently" -> "A mid-tier
   model (`CLAIMS_TIER`), independently". Leave the history note at lines 31 and 51-55.
5. `requirements.txt`:
   - LLM block: Gemini on Vertex AI is the active provider (`google-genai`); OpenAI
     (`openai`) and Groq (`groq`) are dormant fallbacks.
   - Playwright block: say the Playwright scraper is dormant (kept, not in the active
     pipeline), and the browser binary is only needed to run it.
   Comments only. `requirements-lock.txt` is untouched.
6. `.env.example`, last block: retitle it "Dormant scraper proxy (optional)". Say only
   `app/scraper` uses it, the active pipeline (the Chrome extension) needs no proxy, and
   the scraper still refuses to launch without it. Comment out the `PROXY_SERVER=` line.
   First grep for `PROXY_SERVER` / `require_proxy` to confirm nothing outside
   `app/scraper/` reads it. If something does, stop and report.
7. `CLAUDE.md:39-41`: "Auto-navigate the user's active tab to job links..." becomes
   "Open job links that already appear on a page the user opened, one at a time in a
   background tab (`chrome.tabs.create({active: false})`), read it, then close it; i.e.
   links on the opened search-results page are fair game." Keep the rest of the sentence
   ("This is 1 hop ...") and **don't loosen any rule** in that section.
8. `.claude/settings.json`: per E1, no change.

**Checks:** full suite (proves the edited modules still import);
`git diff --stat` shows only comments and docs.

---

### Step 5 (optional, E4): `model_for` handles the stub provider (~10 lines)

- Leftover from `docs/cleanup-3-8-plan.md` section 4: `client.model_for`
  (`app/llm/client.py:127-137`) has no `stub` branch. So with `LLM_PROVIDER=stub` set
  for the whole run, one test fails
  (`tests/test_ask_user.py::TestAnalyzeJobSkill::test_skill_kept_and_overlong_dropped_and_cached_v3`).
- Read how the stub provider is dispatched in `client.py` first. Then add
  `if provider == "stub": return "stub"` (or whatever model label the stub path already
  logs into `llm_usage`; match it).
- Test: in `tests/test_llm_client.py`, `model_for("small", provider="stub")` returns that
  label for every tier.
- Check: the full suite twice, normally and with `$env:LLM_PROVIDER='stub'` set in that
  PowerShell session only (`Remove-Item Env:LLM_PROVIDER` afterwards). Both must be all
  green.

---

### Step 6: Docs and status

- `docs/ARCHITECTURE.md`:
  - 4.9 items 5, 6 and 7: add a "**FIXED in `<sha>`**" line, as items 1-3 have.
  - 5.2, row "Seek selectors": selectors now live in `selectors.js` (the panel passes them
    as `args`) and `app/scraper/selectors.py`, checked by `tests/test_selectors_sync.py`.
  - 5.2, row `extension/background.js`: also mention the scan-tab set (step 2).
  - 5.4 status column for #11, #12, #13 and #14, with commit shas and what was checked by
    hand. #13 says "confirmed then fixed" or "checked, not observed", whichever 2.0 found.
  - 5.4 #11 "Why it matters": 27 outside the scraper, not 37.
  - The #14 drift list: mark each item done; `.claude/settings.json` closed per E1.
- `PROGRESS.md`: one block for #11-#14 (what changed, test counts, which hand checks
  passed and which are still pending).
- `CLAUDE.md`: no change beyond step 4.7, unless the owner asks.

---

## 3. Decisions (recommendations; the owner can override before starting)

| # | Decision | Recommendation |
|---|---|---|
| E1 | #14: untrack `.claude/settings.json`? | **No.** Since `1b1e4cb` it's a deliberate, reviewed project policy (it includes the Seek WebFetch and `git push` deny rules). Close the item. |
| E2 | #13: how does a tab know it's a scan tab? | **Background worker set** (step 2.1). It fails safe to today's behaviour. The alternative, a `#marker` on the URL, depends on Seek's single-page app not rewriting the URL before `document_idle`, which can't be tested without a live page. |
| E3 | #11: add the guard test (step 3.2)? | **Yes.** It's cheap, and it stops the "silent `except`" pattern that caused #8 from coming back unnoticed. |
| E4 | Do step 5 (stub provider)? | **Yes.** It's tiny and makes `LLM_PROVIDER=stub` safe for the whole run. |
| E5 | Who does the hand checks in 1 and 2? | **The owner.** Without 2.0, #13 stays "unverified" and step 2.1 should be skipped rather than guessed. |

## 4. Out of scope (noticed, not planned)

- #9, #10 and #15 (owner's decision: later or never).
- GET endpoints with side effects (left over from #5; e.g. the screening-assist view can
  make one small classify call). A website can trigger it with an `<img>` tag but can't
  read the result.
- `seekProfileExtract`'s profile-page selectors (one copy only, so no drift).
- The first CI run on GitHub (`.github/workflows/tests.yml`) hasn't been watched yet.
  Needs a push, which the owner does.
