# Cleanup plan: ARCHITECTURE.md 5.4 items #3 to #8

Written 2026-10-12 on branch `fix/applied-evidence-safety` (HEAD `fabf381`). PLAN ONLY:
no code or data was changed to write it. Line numbers are from that commit. Re-check
them before editing, because earlier steps in this plan move them.

**Who executes this:** a cheaper model, one step at a time, with the owner reviewing each
commit. Every step names its files, its tests and how to check it without a paid AI call.
Don't start a step until the decisions it depends on (end of this file) are answered.

---

## 0. Ground rules for every step

- **No requests to Seek, ever.** No WebFetch, no server-side fetch. The hand checks in
  this plan that involve a Seek page are done by the owner in their own browser.
- **real.db is read-only.** Look at it only with `mode=ro` (as
  `scripts/check_applied_consistency.py` does). Anything that needs writable data uses
  `app.db` or a scratch copy.
- **Applied matches are never deleted, hidden or altered** by any step here. (Step 1.5,
  the backfill, is the owner's decision and the owner runs it.)
- **The code stays importable after every save.** `scripts/run_api.py` runs uvicorn with
  `reload=True`, so a running real server reloads on every edit. Make each edit
  complete before saving, or stop the real server while working.
- **No migrations are needed for any of the six items.** If an executor finds itself
  writing one, stop and ask.
- **AI calls only through `app/llm/client.py`.** No step adds a paid call.

### How to run the tests without spending money

- Run the suite with the project's environment: **`.venv\Scripts\python -m pytest -q`**
  (D6: `.venv` is what the real server runs on). Tests replace the AI call themselves
  (they monkeypatch `complete_json`, or set the provider to `stub` inside the test).
  Baseline on 2026-10-12: **1508 passed** on both `.venv` and the system Python.
- **Do not** set `LLM_PROVIDER=stub` for the whole run. It breaks one test,
  `tests/test_ask_user.py::TestAnalyzeJobSkill::test_skill_kept_and_overlong_dropped_and_cached_v3`
  (1507 passed, 1 failed), because `client.model_for` (`app/llm/client.py:127-137`) has
  no `stub` branch. That's a small existing quirk, not part of this plan.
- **Caution:** this machine's `.env` holds working Vertex credentials. A *new* test that
  forgets to replace `complete_json` would make a real (small) call. Every new test
  below says how it replaces the AI call; when you review, check that it does.
- JavaScript: `node --check extension/<file>.js` (catches syntax errors only).
- Browser checks: `python tests/e2e/dashboard_e2e.py` and
  `python tests/e2e/quick_apply_e2e.py`. They build a scratch DB, run the server with
  `LLM_PROVIDER=stub`, load the real unpacked extension, and abort every request that
  leaves the machine.

---

## 1. Findings

### #3 Old applications may be missing from the export: **real in principle, probably zero rows**

- The evidence export and the dashboard count an application by `applied_at IS NOT NULL`
  (`app/api/main.py:955-957`; `test_retention.py::test_applied_at_decides_not_status`
  locks this in). A row with `status='applied'` and `applied_at` NULL is in neither.
- **Can such rows exist?** `git log -S` shows the `PATCH /jobs/{id}/status` endpoint and
  the code that stamps `applied_at` arrived **in the same commit** (`e632e39`,
  2026-09-21; the migration `60d65ffb16df` was created 2026-09-11 but committed then).
  The endpoint always stamps `applied_at` when it sets `applied` and the date is empty
  (`main.py:1269-1271`). So the app has never had a path that sets `applied` without
  the date. Such a row would need a manual SQL edit, a script, or a pre-commit working copy.
- **Checked:** `app.db` (test) has 0 rows of either kind (I ran the read-only script).
  **Not checked:** `real.db`. The tool permission system blocked my read-only run, and
  that is your run to do anyway (section 3, step 1.4).

### #4 "Applied" has a second definition in the UI: **still real**

- `extension/sidebar.js:337-342`: the card's button text, style and "already applied"
  early return all read `job.status === 'applied'`.
- `extension/content_script.js:441`: the Seek overlay reads `data.match?.status === 'applied'`.
- `app/api/main.py:777-778` (`list_jobs`): `?status=applied` filters `Match.status == status`.
  **No extension code calls it any more.** I searched `extension/*.js`: the Applied tab uses
  `GET /obligation`. Only tests call it (`tests/test_expired_jobs.py:134`,
  `tests/test_retention.py:549`, `tests/test_search_endpoints.py:287`).
- `app/api/main.py:438-439`: `StatusUpdate.status: str` has no allowed list, and
  `update_status` writes it straight to the column (`main.py:1269`). Nothing sends
  anything but `"applied"` today (`sidebar.js:159`, `content_script.js:451`).
- **There are no tests for `PATCH /jobs/{id}/status` at all** (searched `tests/`).
- Good news: `applied_at` is **already returned** by `GET /jobs` (`main.py:798`) and
  `GET /jobs/{id}` (`main.py:1233`, inside `match`). The UI fix needs no backend change.
- Today a drifted row (status `new`, `applied_at` set; possible before `49f54ee`) shows
  "Mark Applied" on its card. Clicking it is harmless (the date is never reset), but the
  card says the wrong thing.

### #5 CORS allows any origin: **still real; a lockdown alone doesn't fully fix it**

- `app/api/main.py:336-342`: `allow_origins=["*"]`, all methods, all headers.
- **Who calls the backend:**
  - the side panel, popup and dashboard: extension pages, `chrome-extension://` origin;
  - the content script: always through the background worker's `BACKEND_FETCH`
    (`extension/background.js:38-54`, allow-listed to localhost:8000/8001). There is no
    direct `fetch(` in `content_script.js` or `screening_assist.js`;
  - the profile editor `app/static/profile_ui.html`: served by the backend itself, so
    `API = location.origin` (line 240). Same origin, needs no CORS.
  Extension contexts that have the localhost `host_permissions` (`manifest.json`) are
  normally exempt from CORS. So the extension probably needs **no** CORS headers at all.
  This is **unverified in a live Chrome** (see the hand check in step 5.3).
- **What CORS can't stop:** CORS controls whether a website can *read* the response,
  and it blocks *preflighted* calls (JSON bodies, PATCH/PUT/DELETE). It does not stop a
  "simple" request, such as a POST with no body. The backend has three of those:
  `POST /jobs/{id}/quick-screen` (a paid small call, `main.py:700`),
  `POST /jobs/{id}/regenerate` (a full letter run, ~$0.2, `main.py:1381`) and
  `POST /gaps/{id}/clear` (`app/api/letters.py:156`). JSON-body endpoints are safer:
  the installed FastAPI 0.138.1 only parses JSON when the content type is
  `application/json`, so a `text/plain` POST gets a 422 error.
- Chrome's Local Network Access (already mentioned at `background.js:39-40`) may block or
  prompt for a public site calling localhost. How much it blocks in your Chrome version
  is **unverified**.

### #6 No pinned dependencies and no CI: **still real, plus one new finding**

- `requirements.txt` uses only `>=` ranges, and there is no lock file. There is no
  `.github/` folder.
- **New finding:** there are two Python environments with different versions:
  - `.venv`: FastAPI 0.137.1, SQLAlchemy 2.0.50, google-genai 2.8.0, plus pytest-asyncio;
  - the system Python 3.11.9 (`WindowsApps\python`, which ran the tests above):
    FastAPI 0.138.1, SQLAlchemy 2.0.51, google-genai 2.10.0.
  **Resolved (D6, 2026-10-12):** the running real server is
  `.venv\Scripts\python.exe scripts/run_api.py real` (process list; on Windows the venv's
  `python.exe` is a launcher that starts the base interpreter as a child, so two
  processes show up, but `sys.prefix` is `.venv`). `.venv` has 61 packages, `pip check`
  is clean, and the full suite passes on it (1508). The system Python has 204 packages,
  mostly unrelated. So the code was being tested on different versions from the ones
  it runs on. The lock file is frozen from `.venv`.
- **CI is safe without secrets.** With no `.env`, `LLM_PROVIDER` defaults to `gemini`
  (`client.py:57`). With no `GOOGLE_CLOUD_PROJECT`, any call that slips through raises
  `LLMError` (`client.py:546`) instead of spending money.

### #7 Two AI chains can overlap: **still real, but the concrete risk is a race, not "database is locked"**

- `run_quick_screen` (`main.py:700-726`) is a plain `def` endpoint. FastAPI runs it on
  its own thread pool, not on `_bg_executor` (`main.py:74`). The sidebar's Scan Page
  calls it for each scraped job (`sidebar.js:1320`).
- **"database is locked" looks unlikely.** It happens when one writer holds a write
  transaction for longer than the 5-second default wait (`app/db.py` sets no timeout and
  no WAL). `quick_screen` only *reads* before its AI call, and writes and commits after
  it (`app/llm/quickscreen.py:138-215`). pysqlite starts a write transaction only at the
  first write, so no lock is held during the AI call. I spot-checked the other
  `.flush()` callers (`answers.py`, `gaps.py`, `bank.py`, `profile_ui.py`) and found no
  flush followed by an AI call. That isn't a full audit.
- **The concrete bug is a check-then-act race.** The idle loop's Phase 0
  (`main.py:211-242`) picks the *oldest unscreened* job. When there's no backlog, that's
  exactly the job the scan just ingested. Both threads pass the
  `quick_screen_at is not None` check (`quickscreen.py:147`) before either commits, and
  both make the AI call (**paid twice**). On a skip, both see no `Match` and both
  `db.add(Match(...))` (`quickscreen.py:204`). The second commit breaks
  `UNIQUE(user_id, job_id)`: an `IntegrityError` means a 500 error for the scan, or a
  logged loop error and a 60 s pause. **Reasoned from the code, not reproduced.** Step
  4.1 reproduces it in a test first.
- The endpoint docstring (`main.py:702-715`) gives the reason it skips the worker: a
  letter run takes minutes, and the scan needs an answer within its 5 s rhythm. Its
  claim that "quick_screen()'s own idempotency check makes this a harmless no-op" is
  only true when the two calls don't overlap.

### #8 A failed score becomes a permanent 0: **still real, and worse than listed**

- `app/llm/match.py:294-298`: `except Exception` turns *any* failure into
  `score=0, reasoning="Parse error"`. That includes network failure after retries,
  `DailyQuotaError` and a missing API key. The idle loop only picks jobs with **no**
  match row (`main.py:291-301`, Phase 1b), so the job is never retried.
- **Worse than listed:** with `force=True` (Regenerate, `main.py:1381-1407`) the same
  catch **overwrites an existing real score** with 0 (`match.py:301-305`).
- Why one `except` can't just be narrowed: `LLMError` means both "failed after
  retries" (`client.py:613`, transient) and "returned non-JSON" (`client.py:707`,
  usually a one-off but possibly repeatable for one ad). So the fix needs a **retry
  limit** as well as a narrower catch. Without a limit, one bad ad would be retried
  forever and, because Phase 1b always takes the *oldest* unmatched job, would block
  every job behind it.
- `app.db` has 0 "Parse error" rows. `real.db` was not checked (section 3, step 6.0).

---

## 2. Branches and order

| Order | Branch | Items | Why this position |
|---:|---|---|---|
| 1 | `fix/applied-evidence-safety` (exists) | #4, then #3 | Already holds #1/#2 (`49f54ee`, `046d2f9`) and isn't merged. #3 waits on your read-only run. |
| 2 | `chore/pin-deps-ci` | #6 | Small. Once merged, CI checks every later branch. |
| 3 | `fix/match-failure-retry` | #8 | Backend only, unit-testable, no browser needed. |
| 4 | `fix/scan-worker` | #7 | Backend only; the reproduction is a unit test. (ARCHITECTURE's #12/#13 also belong here but are **out of scope** for this plan.) |
| 5 | `chore/cors-lockdown` | #5 | Last, because only a live Chrome can confirm it and it can break the extension in ways tests miss. |

**Merge each branch to `main` (via a PR, as with PR #2) before starting the next.**
Branches 3 and 4 edit `match.py` / `quickscreen.py`, which `49f54ee` also changed.
Starting them from a `main` that lacks branch 1 would make merge conflicts.

**Before step 1.1:** the pending `PROGRESS.md` edit is committed (`05ebe09`, D0).
`docs/cleanup-3-8-plan.md` itself is still untracked: commit it as the first commit on
this branch ("Docs: cleanup plan for ARCHITECTURE 5.4 #3-#8").

Each step below is **one commit**. Sizes are rough diff sizes for review.
**How to review each step:** read the test diff first (what is promised), then the code
diff, then run the checks it lists.

---

### Branch 1: `fix/applied-evidence-safety` (#4, #3)

**Step 1.1: Validate `PATCH /jobs/{id}/status` and add its first tests** (~40 lines)
- `app/api/main.py:438-439`: make the field a `Literal` of the documented vocabulary
  (`docs/database-schema.md:443`): `new, shortlisted, applied, interviewing, rejected,
  withdrawn`. Anything else becomes FastAPI's automatic 422 error. Add `Literal` to the
  `typing` import if it's missing.
- D2 (decided): **once applied, the status can't change.** In `update_status`
  (`main.py:1251-1278`), if the match has `applied_at` set and `body.status !=
  "applied"`, return 409 ("This job is marked applied (Centrelink evidence); its status
  can't change.") before writing anything. Re-sending `"applied"` stays a harmless
  no-op. Mention the rule in `docs/database-schema.md:443-449`.
  - This also covers a drifted row (`applied_at` set, `status` not `applied`): it can't
    be moved anywhere except to `applied`, which repairs it.
- Tests: create **`tests/test_status_update.py`**, copying the `client` / `db` fixture
  pattern from `tests/test_expired_jobs.py` (its `_seed_job` helper takes `status=`). No
  AI involved. Cases:
  1. `applied` stamps `applied_at` and returns it;
  2. a second `applied` keeps the first timestamp;
  3. `"banana"` -> 422 and the row is unchanged;
  4. unknown job -> 404;
  5. an applied match sent `"withdrawn"` (and `"new"`) -> 409, and both `status` and
     `applied_at` are unchanged;
  6. a drifted row (`status="new"`, `applied_at` set) sent `"new"` -> 409; sent
     `"applied"` -> 200, status becomes `applied`, and `applied_at` keeps its old value.
- Check: `python -m pytest -q tests/test_status_update.py`, then the full suite.

**Step 1.2: `GET /jobs?status=applied` means `applied_at IS NOT NULL`** (~30 lines)
- `main.py:777-778`: when `status == "applied"`, filter `Match.applied_at.is_not(None)`.
  Other status values keep filtering the column. Keep `order_by(applied_at desc)`.
  Update the docstring (`main.py:743-746`).
- Update the comments that describe the old rule: `app/models.py:479` and
  `docs/database-schema.md:457-461` (interview paragraph). Also add one sentence to the
  `status` paragraph at `:449-455` saying that the `?status=applied` filter uses
  `applied_at` too.
- Tests to **change**, because they seed status without a date and would stop meaning
  anything:
  - `tests/test_expired_jobs.py:131`: `_seed_job(... status="applied")` -> also set
    `applied_at` (give `_seed_job` an `applied_at=None` parameter);
  - `tests/test_search_endpoints.py:284`: `update({"status": "applied"})` -> also set
    `applied_at`, otherwise the "hidden stays out" test passes for the wrong reason.
- Test to **add** (next to `test_applied_at_decides_not_status` in
  `tests/test_retention.py`, around line 528): a drifted row (`status="new"`,
  `applied_at` set) **is** listed by `?status=applied`, and a status-only row is **not**.
- Check: full suite.

**Step 1.3: The Jobs card and the Seek overlay read `applied_at`** (~15 lines)
- `extension/sidebar.js:337-342`: use `const applied = !!job.applied_at;` for the class,
  title, text and the early return. The click handler already sets `job.applied_at`
  (line 347).
- `extension/content_script.js:441`: `const isApplied = !!data.match?.applied_at;`.
- e2e: in `tests/e2e/dashboard_e2e.py`, seed one more job with `status="new"` and
  `applied_at` set (seeding is around lines 105-115). Check that its Jobs card shows
  "✓ Applied" and has no Delete. Update the header docstring's list of checks.
- Check: `node --check extension/sidebar.js extension/content_script.js`;
  `python tests/e2e/dashboard_e2e.py` (should be 90 + the new checks).
- **Hand check (owner):** the overlay isn't covered by an e2e test. Run the TEST
  backend, open a Seek job page yourself for a job that has a letter, and check that the
  overlay button says "✓ Applied" for an applied job and "Mark Applied" otherwise.
  Optional: `node --check` plus reading the one-line diff is a reasonable minimum.

**Step 1.4: #3, your read-only run (no commit)**
In PowerShell, from the repo root (the script opens the file with `mode=ro` and can't write):
```
python scripts/check_applied_consistency.py real.db
```
- Exit code 0 with both counts at 0: #3 is closed. Note it in step 1.6 and skip 1.5.
- Rows under "status = 'applied', applied_at NULL": run this read-only query to see the
  candidate dates for each row (paste it whole into PowerShell):
```
@'
import sqlite3, pathlib
c = sqlite3.connect(pathlib.Path("real.db").resolve().as_uri() + "?mode=ro", uri=True)
sql = """
SELECT m.id, j.title, j.company, j.date_scraped, m.created_at, m.scored_at,
       m.screenshot_taken_at, cl.updated_at AS letter_updated, m.updated_at AS match_updated
FROM matches m JOIN job_listings j ON j.id = m.job_id
LEFT JOIN cover_letters cl ON cl.match_id = m.id
WHERE m.status = 'applied' AND m.applied_at IS NULL ORDER BY m.id"""
for row in c.execute(sql):
    print(row)
'@ | python -
```
**Candidate dates, most to least trustworthy:**
1. **Your own record**: Seek's "Applied jobs" page in your account (open it yourself), or
   the confirmation emails. This is the only source that is actually evidence.
2. `screenshot_taken_at`: the overlay captures the screenshot when you click Mark
   Applied (`content_script.js:458`). A later recapture overwrites it, so it can be too late.
3. `cover_letters.updated_at`: your last edit to the letter, usually just *before* applying.
4. `matches.updated_at`: changes on **any** later write (re-score, interview), so it's
   only an upper bound.
5. `matches.created_at` / `job_listings.date_scraped`: lower bounds (you can't have
   applied before the job was seen).

**Step 1.5: #3 backfill (only if D1 says so, and you run it)**
Not planned in detail until the rows are known. If needed: a one-off
`scripts/backfill_applied_at.py` that takes explicit `match_id=YYYY-MM-DD` pairs from
you (no guessing), refuses any row whose `applied_at` is already set, defaults to
`--dry-run`, and is run first on a copy of `real.db`, then on `real.db` with the server
stopped and after a backup. A test against a scratch DB comes with it. **The executor
writes the script; only you run it.**

**Step 1.6: Docs** (~15 lines)
- `PROGRESS.md`: one block for #3/#4 (what changed, test counts, what was checked by hand).
- `docs/ARCHITECTURE.md` 5.4: set the status column for #3 and #4.

---

### Branch 2: `chore/pin-deps-ci` (#6)

**Step 2.1: Lock file** (1 new file, ~50 lines)
- Freeze `.venv` (D6), the environment the real server runs on, and the one the suite
  passes on:
```
.venv\Scripts\python -m pip check
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m pip freeze > requirements-lock.txt
```
  Add a three-line header comment to `requirements.txt`: "ranges here; exact versions
  in requirements-lock.txt (frozen from .venv); regenerate with the commands in
  docs/cleanup-3-8-plan.md step 2.1".
- PowerShell's `>` writes UTF-16 on Windows PowerShell 5.1, and pip can't read that.
  Run the freeze line in Git Bash, or use
  `... pip freeze | Out-File -Encoding ascii requirements-lock.txt`.
- The freeze will include `pytest-asyncio`, which is in `.venv` but not in
  `requirements.txt`. The tests don't use it, so either keep it or remove that one line.
- Check: the lock file has about 60 lines, all `name==version`, with no local paths or
  `-e` lines. To prove it installs, run
  `python -m venv .venv-check` and
  `.venv-check\Scripts\python -m pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org -r requirements-lock.txt`,
  then run the suite there and delete `.venv-check`.
- **From now on, run everything with `.venv`** (activate it with
  `.venv\Scripts\Activate.ps1`, or prefix commands with `.venv\Scripts\python`), so
  tests and server use the same versions.
- Don't fix the stale "OpenAI active / Gemini dormant" comments in `requirements.txt`
  here. That's docs drift (#14), a different branch.

**Step 2.2: GitHub Actions** (1 new file, ~30 lines)
- `.github/workflows/tests.yml`: on `push` and `pull_request`; `runs-on` from D7
  (recommended `windows-latest`, to match this machine); `actions/setup-python` with
  3.11; `pip install -r requirements-lock.txt`; `python -m pytest -q`. A second step uses
  `actions/setup-node` and runs `node --check` on each `extension/*.js`.
- **No secrets, and don't set `LLM_PROVIDER`** (the "How to run the tests" note above
  explains why). The e2e scripts are **not** run in CI.
- Check: push the branch (an outward action, so the owner does it or approves it) and
  watch the Actions tab go green. For a private repo, Windows runner minutes count double
  against the free allowance.

**Step 2.3: Docs**: `PROGRESS.md`, ARCHITECTURE 5.4 #6 status, and one line in
`CLAUDE.md`'s tech stack about the lock file and CI.

---

### Branch 3: `fix/match-failure-retry` (#8)

**Step 3.1: Tests that describe the new behaviour** (new file, ~120 lines, fails on
current code; commit it together with 3.2, or commit it first if you want to *see* the
red run)
- Create **`tests/test_match_failures.py`**. Copy the in-memory DB fixture from
  `tests/test_rescore_keeps_status.py:28-45` and add an extracted job. Replace the AI
  call with `monkeypatch.setattr(match_module, "complete_json", fake)`, the same way that
  file does. Cases:
  1. `fake` raises `LLMError("call failed after 3 retries")` on a **new** job: `match_job`
     raises, and **no** `matches` row exists.
  2. `fake` raises `DailyQuotaError`: raises, no row.
  3. **Existing** score 88 with `force=True`, `fake` raises: still 88, reasoning and
     `scored_at` unchanged.
  4. `fake` returns `{"score": "high"}` (fails `MatchScore` validation) with
     `record_failure=True`: a row with `score=0`, `reasoning="Parse error"`, i.e.
     today's terminal row.
  5. A good response still writes the row (regression guard).

**Step 3.2: `match_job` stops writing a 0 on failure by default** (~25 lines)
- `app/llm/match.py:220`: add the keyword `record_failure: bool = False`.
- `match.py:294-298`: `except (DailyQuotaError, BudgetExceededError): raise`. Then
  `except Exception`: if not `record_failure`, log and `raise`; otherwise build today's
  0 / "Parse error" row (only when there's no `existing` row; **never overwrite a real
  score**).
- `_process_listing` (`main.py:524-527`) already catches and logs a `match_job`
  exception, so Regenerate and Phase 1 just log. The job stays unmatched and Phase 1b
  picks it up.

**Step 3.3: The idle loop retries a few times, then records the failure** (~40 lines)
- `app/api/main.py`, Phase 1b (`main.py:290-310`):
  - add `_match_failures: dict[int, int] = {}` and `_MATCH_MAX_ATTEMPTS = 3` (D8) next
    to `_BUDGET_PAUSE_S`;
  - pull the work into one small sync function, `_match_attempt(job_id) -> str`
    (`"ok" | "failed" | "gave_up" | "quota"`), run on `_bg_executor` as today. On the
    final attempt it calls `match_job(..., record_failure=True)`, so the job ends as
    today's terminal row and can't block the queue;
  - after `"failed"` or `"quota"`, sleep 180 s (as Phase 0 and Phase 1 already do), not 20.
  - The counter is in memory: a server restart gives a job 3 more tries, which is cheap
    (one small call each).
- Tests: extend `tests/test_match_failures.py` to call `_match_attempt` directly
  (`import app.api.main as api`; point `api.SessionLocal` at the test engine, as other
  API tests do, and replace `match_module.complete_json`). Check: fail -> "failed" with
  no row, ×3 -> "gave_up" with the 0 row, and a quota error -> "quota" that doesn't count.
- Don't unit-test the `while True` loop itself.
- Check: full suite.

**What this changes for existing 0-score rows: nothing, by itself.** The idle loop only
picks jobs with no match row, so existing "Parse error" rows stay at 0. See D9 for the
options. Step 6.0 below is the read-only count to run first.

**Step 3.4: Docs**: `PROGRESS.md`, ARCHITECTURE 4.9 item 2 / 5.4 #8 status, and the
`match_job` docstring.

---

### Branch 4: `fix/scan-worker` (#7)

**Step 4.1: Reproduce the race in a test** (new file, ~80 lines; fails on current code)
- Create **`tests/test_quickscreen_race.py`** with a **file** SQLite DB in `tmp_path`.
  Not `:memory:` with `StaticPool`, which shares one connection across threads and
  would hide the bug. Use two `sessionmaker` sessions and seed one job with a
  description and a profile.
- Replace `quickscreen_module.complete_json` with a fake that counts calls, then waits
  on a `threading.Event` and returns `{"score": 10, "reason": "x"}` (a skip).
- Start `quick_screen(job, 1, session=s1)` in a thread. Once the fake has been entered,
  call `quick_screen(job, 1, session=s2)` on the main thread, then set the event.
- Expect after the fix: the fake was called **once**, the second call returned
  `"not-run"`, there is exactly **one** `matches` row and no exception. Today's code
  should fail with 2 calls and/or an `IntegrityError`, **which is the reproduction**.
  Paste the failing output into the commit message or the PR.
- Add `timeout=` to every `Event.wait` / `Thread.join` so a bug can't hang the suite.

**Step 4.2: Claim the job before the AI call** (~25 lines, `app/llm/quickscreen.py`)
- If D5 is **B (recommended)**: add a module-level `_in_flight: set[int]` with a
  `threading.Lock`. After the existing early checks, re-read the job
  (`db.refresh(job)`) so a commit from another thread is seen. Then, under the lock: if
  `job_id` is in flight or `quick_screen_at` is now set, return `"not-run"`; otherwise
  add the id. Remove it in a `finally`. The lock is held only for the set operations,
  never during the AI call.
  - Effect: the scan keeps its fast answer. If the idle loop is screening the same job
    at that moment, the scan gets `score: null`, which the sidebar already handles
    ("no score (already processed or errored)", `sidebar.js:1323`) and which doesn't
    count toward the weak streak.
  - Update the `run_quick_screen` docstring (`main.py:702-715`) to say the claim is what
    makes overlapping calls safe.
- If D5 is **A**: make `run_quick_screen` `async def` and
  `await loop.run_in_executor(_bg_executor, ...)`. It's simpler and fully serial, but
  while a letter run is going (minutes), each scan screen waits. The scan's early stop
  then won't happen, and the scan simply runs to its page cap.
- Check: `python -m pytest -q tests/test_quickscreen_race.py`, then the full suite.
- **Hand check (owner), for "database is locked" only, which no test covers:** on the
  TEST backend, with letters enabled and a letter run in progress, run Scan Page on a
  search page you opened yourself. Then search the server console for
  `database is locked` and `IntegrityError`. If neither appears, close that part of #7
  as "not observed". If one appears, report it before changing anything else (the next
  options would be `PRAGMA busy_timeout` / WAL, which need a portability review).

**Step 4.3: Docs**: `PROGRESS.md`, ARCHITECTURE 4.9 item 8 / 5.4 #7 status.

---

### Branch 5: `chore/cors-lockdown` (#5)

**Step 5.1: Remove the CORS middleware** (~10 lines; or allow-list per D3)
- **Recommended (D3 = remove):** delete the `CORSMiddleware` block and its import
  (`main.py:33`, `332-342`). With no CORS headers, a website can't read any response or
  send any preflighted call. The extension is exempt through `host_permissions`, and the
  profile editor is same-origin.
- Alternative (D3 = allow-list): `allow_origin_regex=r"^chrome-extension://<id>$"`. The
  id of an unpacked extension depends on its folder path, so pinning it needs a `key`
  in `manifest.json`. That's more moving parts for no gain if removal works.
- Tests: add `tests/test_origin_guard.py` (or a class in an existing API test). A request
  with `Origin: https://example.com` gets **no** `access-control-allow-origin` header,
  and an `OPTIONS` preflight from that origin isn't allowed.

**Step 5.2: Refuse cross-site writes the browser would still send** (~30 lines; only if D4 = yes)
- A small middleware: for `POST/PUT/PATCH/DELETE`, if an `Origin` header is present and
  is neither `chrome-extension://...` nor the server's own origin
  (`http://localhost:<port>` / `http://127.0.0.1:<port>`), return 403. Browsers send
  `Origin` on cross-site POSTs, so this closes the body-less `POST /regenerate`,
  `/quick-screen` and `/gaps/{id}/clear` gap. Requests with no `Origin` (TestClient,
  scripts) pass.
- Tests (same file): a POST to `/jobs/1/regenerate` with `Origin: https://example.com`
  gets 403 and nothing is queued (patch `_bg_executor.submit` to record calls). The same
  POST with `Origin: chrome-extension://abc` or no `Origin` is accepted. A `PATCH` with
  `Origin: http://localhost:8001` is accepted.
- Not covered: GET endpoints with side effects (e.g. the screening-assist view can make
  one small classify call). A website can still trigger those with an `<img>` tag but
  can't read the result. Note it in the PR as a remaining risk.

**Step 5.3: Verify**
- Automated: `python tests/e2e/dashboard_e2e.py` and `python tests/e2e/quick_apply_e2e.py`.
  Both load the real unpacked extension from `chrome-extension://`, so they show whether
  the side panel and the content script (via the background worker) still work without
  CORS headers. Both must stay fully green.
- **Hand checks (owner, in your own Chrome, TEST backend on 8001, extension reloaded):**
  1. Side panel: the Jobs list loads, and a live update arrives (SSE / `EventSource`;
     e.g. press Refresh, or mark a job applied and watch the Overview count change).
  2. Popup: shows the backend as OK (`GET /health`; the e2e doesn't cover the popup).
  3. Profile editor: open `http://localhost:8001/profile-ui`; it loads and saves one change.
  4. Seek overlay: on a Seek job page **you** open, the overlay loads and "Save edits" works.
  5. Negative check: on `https://example.com` (not Seek), open DevTools -> Console and run
     `fetch('http://localhost:8001/health').then(r => r.text()).then(console.log)`.
     Expect a CORS error (or a Local Network Access prompt, which you should **deny**).
     Then run
     `fetch('http://localhost:8001/jobs/1/regenerate', {method: 'POST'})` and check the
     server log shows a 403 (with step 5.2) and no queued work.
  Report what each one showed. If 1-4 fail, revert to D3 = allow-list rather than guessing.

**Step 5.4: Docs**: `PROGRESS.md`, ARCHITECTURE 5.4 #5 status, and ARCHITECTURE 5.2's
`app/api/main.py` row ("no CORS headers; Origin guard on writes").

---

### Step 6.0 (any time, read-only, owner): how many "Parse error" rows exist
```
@'
import sqlite3, pathlib
c = sqlite3.connect(pathlib.Path("real.db").resolve().as_uri() + "?mode=ro", uri=True)
print("parse-error rows, applied, hidden:", c.execute(
    "SELECT COUNT(*), SUM(applied_at IS NOT NULL), SUM(hidden_at IS NOT NULL) "
    "FROM matches WHERE reasoning = 'Parse error'").fetchone())
'@ | python -
```

---

## 3. Decisions needed before starting

| # | Decision | Recommendation |
|---|---|---|
| D0 | Commit or discard the pending `PROGRESS.md` edit on `fix/applied-evidence-safety`? | **DONE:** committed as `05ebe09`. |
| D1 | #3: after you run step 1.4, backfill any rows? With which date per row? | Only from your own record (candidate 1). If none exists, leave `applied_at` NULL and keep a note rather than invent a date. |
| D2 | #4: may `PATCH /status` move an applied job to another status (e.g. `withdrawn`)? | **DECIDED: no.** Once applied, the status can't change (409; step 1.1). |
| D3 | #5: remove CORS entirely, or allow-list the extension id? | Remove, and fall back to the allow-list if any hand check in 5.3 fails. |
| D4 | #5: add the Origin guard on writes (step 5.2)? | Yes. It's the part that actually stops a website triggering a paid `/regenerate`. |
| D5 | #7: (B) claim the job in `quick_screen`, or (A) move the endpoint onto the single worker? | **DECIDED: B** (step 4.2). |
| D6 | #6: which environment is the reference? | **RESOLVED: `.venv`**, the one the real server runs on (finding #6). The lock file is frozen from it (step 2.1). |
| D7 | #6: CI on `windows-latest` or `ubuntu-latest`? Is the GitHub repo private (minutes)? Add Dependabot? | `windows-latest`, no Dependabot for now (bump by hand when the trial ends). |
| D8 | #8: retry limit and pause | 3 attempts, 180 s pause, then today's 0 row. |
| D9 | #8: what to do with existing "Parse error" rows (count them first with step 6.0) | If there are a few: leave them. **Don't** use Regenerate on them, because it also runs a full cover letter (~$0.2 each, `bypass_threshold=True`). If there are many: a later one-off script that re-scores only rows with `reasoning='Parse error' AND applied_at IS NULL`, with the server stopped, on a copy first. |
| D10 | Merge each branch to `main` through a PR before starting the next? | Yes (section 2 explains why). |
| D11 | Do you want to do the hand checks in 1.3, 4.2 and 5.3 yourself? Without them, #5 and the "database is locked" part of #7 stay **unverified**. | Yes, at least 5.3. |

## 4. Out of scope (noticed, not planned)

- `client.model_for` has no `stub` branch, so running with `LLM_PROVIDER=stub` set for the
  whole process fails one test. Small, separate fix.
- ARCHITECTURE #12 / #13 (the other `fix/scan-worker` items), #14 (docs drift, including
  `requirements.txt` comments) and #11 (exception audit).
- GET endpoints with side effects (see step 5.2).
