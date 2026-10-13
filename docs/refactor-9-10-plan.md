# Refactor plan: ARCHITECTURE.md 5.4 items #9 and #10

Written 2026-10-12 on branch `fix/applied-evidence-safety` (HEAD `d6dceb4`). PLAN ONLY:
no code was changed to write it. Line numbers are from that commit. Re-check them
before editing, because every step moves them.

**Scope.**
- **#9**: split `app/api/main.py` (1,658 lines, 24 endpoints, the idle loop, the SSE
  broadcaster, the single worker, helpers) into routers and modules.
- **#10**: split `extension/sidebar.js` (2,752 lines) into several classic `<script>`
  files.

Both are **pure moves**. Nothing a user, the extension or a test can observe may change.
#15 (hardcoded profile id 1) stays out of scope.

**Who executes this:** a cheaper model, one step at a time, with the owner reviewing
each commit. Every step names exactly what moves, where it goes, which tests change, how
to check it without a paid AI call, and how to roll it back.

---

## 0. Ground rules for every step

- **Pure moves.** A step that also changes behaviour is a bug in the plan. Allowed
  edits:
  - cut and paste code;
  - add the imports the new home needs;
  - turn a bare name into a `module.name` attribute access (rule M1 below);
  - add `<script>` tags;
  - update tests so they patch the new owner of a name.
  - Not allowed: renaming, reformatting, "small fixes" or docstring rewrites. If you see
    a bug, stop and report it.
- **One commit per step.** The message says what moved, where, and **why** (the owner is
  learning to review). Show `git diff --stat` in your report, and keep each diff
  reviewable in under 15 minutes.
- **The whole app works after every commit.** The suite is green, `app/` imports, the
  side panel loads, and `git revert <that commit>` alone restores the previous working
  state. No step depends on a later one to be correct.
- **`app/` stays importable after every save.** `scripts/run_api.py` runs uvicorn with
  `reload=True`, so a running server reloads on each save. Make a move complete (new file
  written, then old code removed and imports fixed) before you save `main.py`. Simplest:
  stop any running server first.
- **No requests to Seek, ever.** No WebFetch, no browser automation pointed at Seek, no
  automated Scan Page test. Every hand check involving a Seek page is done by the owner,
  in their own Chrome.
- **real.db is off limits.** No step reads or writes it.
  - `scripts/check_retention.py` copies `real.db` by default. Only ever run it as
    `--source app.db`, and only if the owner asks.
- **No migrations, no schema change, no paid AI calls.**
  - The suite already makes none: tests replace the model call or use the stub provider.
    Run it plainly, as the baseline was run.
  - Both e2e scripts run the server with `LLM_PROVIDER=stub`.
  - The new tests in this plan only import the app or read files; none can reach
    `app/llm/client.py`.
- **The Seek access policy is untouched** (`CLAUDE.md`, "SEEK ACCESS POLICY"):
  - links come only from the page the user opened, and there is no second hop;
  - `SCAN_DELAY_MS` stays >= 5000;
  - `SCAN_PAGES_CEILING` stays 25, and the API's `le=25` stays;
  - scan tabs stay `active: false`.
  The scan-policy test added in step 9.0 checks this mechanically after every move.
- **Deliberate oddities stay** (ARCHITECTURE 5.5, plus: the REAL/TEST switch,
  `PROFILE_ID = 1`, `user_id == 1` in the idle loop, the dormant scraper and proxy code).

### Rule M1: patch targets have exactly one owner (the #1 hazard for #9)

`monkeypatch.setattr(main, "X", fake)` replaces the name `X` **in `main`'s namespace
only**. Once the code that uses `X` lives in another module, that patch silently does
nothing: the test may still pass, while the real `SessionLocal`, executor or broadcaster
runs.

1. Every shared or patched name has **one owner module**. Other modules reach it as
   `owner.X` at call time (`from app.api import worker` ... `worker._bg_executor.submit(...)`),
   **never** `from app.api.worker import _bg_executor`. The `from` form copies the
   binding at import, so a patch on the owner would miss it.
2. **`main.py` does not re-export any patched or mutable name.** When a name leaves
   `main`, a stale `monkeypatch.setattr(main, "X", ...)` raises `AttributeError`
   (monkeypatch's default is `raising=True`). That turns a silent miss into a loud
   failure. Re-exports are allowed only for `app` and `get_db` (D2): those are used by
   identity (an import, or a `dependency_overrides` key), never patched.
3. Tests are updated **in the same commit** as the move, so the patch target follows the
   name.
4. Names keep their current spelling, including the leading underscore (D3). That way a
   test edit is a one-word module swap (`main.` -> `worker.`) that is easy to review.
   Optional renames come in a separate, final step.

### How to run the checks

- Python: `.venv\Scripts\python -m pytest -q`. **Baseline at `d6dceb4`: 1549 passed in
  ~130 s** (recorded 2026-10-12 while writing this plan).
- JavaScript syntax: `node --check extension/<file>.js` for every edited or new file. CI
  runs it on `extension/*.js`.
- Browser checks: run them **one at a time**.
  - Before and after each run:
    `Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue`.
    It must be empty before you start. If something is listening that you didn't start,
    stop and ask; never kill a process you didn't start.
  - `.venv\Scripts\python tests/e2e/dashboard_e2e.py`
  - `.venv\Scripts\python tests/e2e/quick_apply_e2e.py`
  - plus `tests/e2e/sidebar_load_e2e.py` (new, step 10.0, needs no server).
  - Each must end with every check passing.
- Route table (new, step 9.0):
  `.venv\Scripts\python -m pytest -q tests/test_route_table.py tests/test_scan_policy.py`.

---

## 1. Baseline (record BEFORE any move; steps 9.0 and 10.0 commit it)

### 1.1 Numbers

| What | Value at `d6dceb4` | How measured |
|---|---|---|
| pytest | 1549 passed, ~130 s | run while writing this plan |
| `app/api/main.py` | 1,658 lines; 24 endpoints; 2 static mounts | `wc -l`; route dump below |
| `extension/sidebar.js` | 2,752 lines; ~150 top-level names | `wc -l`; `jsmap` below |
| `dashboard_e2e.py` / `quick_apply_e2e.py` | **not re-run for this plan**. The last recorded counts are 77/77 and 123/123 (CLAUDE.md), and the earlier plan says both were green on 2026-10-12. | Step 9.0 records the exact counts before any move. |

### 1.2 Facts the plan relies on (all checked by reading the code)

**Backend (#9)**
- **There are two separate `get_db` functions** with identical bodies:
  - `main.get_db` (`main.py:402`) serves main's 24 endpoints;
  - `profile_ui.get_db` (`profile_ui.py:35`) serves `profile_ui`, and `letters.py`,
    `obligation.py` and `screening.py` import it (`from app.api.profile_ui import get_db`).
  - Tests key `app.dependency_overrides` on one or both:
    - 10 files override only `main.get_db`;
    - 3 only `profile_ui.get_db`;
    - 5 override both, with comments saying that missing one "sends that route to the
      default (test) app.db".
  - So moving an endpoint from main into a router that uses the other `get_db` would
    point a test at the real `app.db`. **Step 9.1 makes them one function before any
    endpoint moves.**
- **FastAPI is 0.137.1** (`requirements-lock.txt`), and it includes routers lazily:
  - `app.routes` holds `_IncludedRouter` objects, not a flat list;
  - flattening needs `_IncludedRouter.effective_candidates()` (private; verified on
    0.137.1, see step 9.0);
  - `app.openapi()` is public and produces the same output on every call (verified: 45
    operations, 54,922 bytes, two calls identical).
- **Route order today** (flattened):
  - the docs routes;
  - `profile_ui` (4), `letters` (7), `screening` (8), `obligation` (2). These are
    included at `main.py:382-385`, before main's own decorators run, so they come first;
  - mounts `/static`, `/screenshots`;
  - then main's 24 routes in file order.
  - The only same-method overlaps are the six GET literals `/jobs/known-ids`,
    `/jobs/pending-count`, `/jobs/by-source-id/{source_job_id}`,
    `/jobs/search-performance`, `/jobs/evidence-export` and `/jobs/suggested-searches`
    against `GET /jobs/{job_id}` (`job_id: int`).
  - If a literal comes after `{job_id}`, it gets a 422, not its data. Two docstrings
    (`evidence_export`, `suggested_searches`) already say so.
- **Shared module state in `main.py`, which must each stay a single instance:**

| Name | Kind | Used by | New owner |
|---|---|---|---|
| `_bg_executor` (`ThreadPoolExecutor(max_workers=1)`, line 77) | the one worker | idle loop (5 places), `_letters_phase`, `regenerate` | `app/api/worker.py` |
| `_sse_clients`, `_event_loop` | SSE client list; the loop for thread-safe sends | `_broadcast`, `broadcast_from_thread`, `/events`, `lifespan` (sets `_event_loop`) | `app/api/events.py` |
| `_letters_paused_until` | float, reassigned with `global` | `_letters_phase` only | `app/api/idle_loop.py` |
| `_match_failures` | dict | `_match_attempt` only | `app/api/idle_loop.py` |
| `_screenshots_dir` | Path, built at import from `app_env()`, `mkdir` at import | `_retention_tick`, `evidence_export`, `upload_screenshot`, `delete_job`, the `/screenshots` mount | `app/api/paths.py` |
| `logger` | `getLogger(__name__)` = `"app.api.main"` | everywhere in main | one per module (names change: see risk R9) |

- **Every name tests and scripts reach in `main`** (aliases `main`, `api`, `api_main`,
  `main_module`, and `from app.api.main import ...`):

| Name | Where | How used | Owner after the move | Test change |
|---|---|---|---|---|
| `app` | ~20 test files, `run_api.py` (`"app.api.main:app"`) | import | stays in `main` | none |
| `get_db` | 15 test files | `dependency_overrides` key | `deps` (main re-exports, D2) | none (same object) |
| `SessionLocal` | `test_letter_phase8_api` (4 fixtures), `test_match_failures`, `test_process_listing_stops` | patched | `worker` (for `_process_listing`), `idle_loop` (for `_letters_phase`, `_recover_orphaned_runs`, `_match_attempt`) | patch the module the function now lives in |
| `extract_job`, `match_job` | `test_letter_phase8_api::TestProcessListing`, `test_process_listing_stops` | patched | `worker` | `worker.` |
| `letter_production` | same, plus `TestLettersPhase` | its **attributes** are patched (`.generate_for`, `.next_work`, `.run_work`) | the module object itself; location-independent as long as moved code calls `letter_production.X(...)` | swap the alias, or patch `production` directly |
| `broadcast_from_thread` | phase8 (patched; `emit is main.broadcast_from_thread` twice), `test_process_listing_stops` | patched / identity | `events` | `events.` |
| `_broadcast` | phase8 `TestLettersPhase` | patched | `events` | `events.` |
| `_bg_executor` | phase8 (3), `test_origin_guard` | patched | `worker` | `worker.` |
| `_process_listing` | phase8 (7 calls, `fn is main._process_listing`), `test_process_listing_stops` (4) | called / identity | `worker` | `worker.` |
| `_letters_phase`, `_letters_paused_until`, `_IDLE_INTERVAL_S`, `_recover_orphaned_runs` | phase8 | called / patched / read and reset | `idle_loop` | `idle_loop.` |
| `_processing_idle_loop` | `test_letter_limits`, `test_letter_side_engines` | patched **so the lifespan doesn't start the real loop** | `idle_loop`; `lifespan` must call `idle_loop._processing_idle_loop()` | `idle_loop.` |
| `_match_attempt`, `_match_failures` | `test_match_failures` | called / cleared | `idle_loop` | `idle_loop.` |
| `_screenshots_dir` | `test_retention`, `test_screenshot_upload`, `scripts/check_retention.py` (assigns it) | patched | `paths` | `paths.` |
| `_retention_tick`, `logger` | `scripts/check_retention.py` (calls the tick; captures `api_main.logger.name`) | called / logger name | `idle_loop` | the script imports `idle_loop` |

- **No other app module imports `app.api.main`.** Only `scripts/run_api.py` (the string
  `"app.api.main:app"`) and `scripts/check_retention.py` do. No router imports `main`.
- **`from __future__ import annotations` is on in `main.py`**, so endpoint annotations
  are strings that FastAPI resolves against the endpoint's own module. A moved endpoint's
  module must import every name in its signature (`Session`, `Depends`, `Literal`, the
  body model). A missing one fails **at import**, which is loud, and the suite catches it.

**Extension (#10)**
- `sidebar.html:972-976` loads classic scripts: `config.js`, `selectors.js`,
  `screening_assist.js`, `sidebar.js`, `dashboard.js`. There are no inline scripts (MV3
  CSP), no `type="module"`, and no `web_accessible_resources` in the manifest.
- **`dashboard.js` uses 16 `sidebar.js` globals.** Its header lists 15 names; one,
  `BACKEND`, comes from `config.js`, and **`TRASH_SVG` and `scanBtn` are missing from
  it**. `scanBtn` is used **at load time** (`dashboard.js:375`,
  `scanBtn.addEventListener`). The full list: `PROFILE_ID, TRASH_SVG, appliedSection,
  autoLetterMin, coverLetterState, csvEscape, evidenceNote, loadJobs, markApplied, mk,
  overviewSection, scanBtn, seekSearchUrl, showTab, tierOf, typingIn`.
- **`sidebar.js` uses names from later or earlier files**, all at call time:
  - `dashboard.js`: `refreshDashboard`, `loadOverview`, `loadAppliedPeriods`;
  - `config.js`: `BACKEND`, `BACKEND_ENV`, `backendReady`;
  - `selectors.js`: `SELECTORS`;
  - `screening_assist.js`: `SCREENING_ASSIST_CSS`, `renderScreeningAssist`.
  - `screening_assist.js` uses nothing from `sidebar.js`; the panel passes its options
    in. `content_script.js` and `popup.js` share nothing with `sidebar.js` (they load
    `config.js`, and the content script also loads `selectors.js` and
    `screening_assist.js`).
- **Startup:**
  - `backendReady.then(...)` at `sidebar.js:2747` (not `await`) renders the env pill,
    loads preferences then jobs and the Overview, refreshes the budget banner and
    connects SSE;
  - `applyZoom(1)` and the saved-zoom IIFE run just before it, deliberately outside
    `backendReady`;
  - `announceSidebarOpen()` runs at line 19.
- **Functions injected into Seek tabs** (serialised by `chrome.scripting.executeScript`,
  so they must stay self-contained):
  - `pageCollectJobLinks` (1161): uses only `document` and `location`;
  - `pageScrapeDetail(sel)` (1176): gets the selectors as `args`;
  - `seekProfileExtract` (2307): all its helpers are nested inside it.
  - None references a panel global today.
- **Policy constants** live in one place: `SCAN_PAGES_CEILING = 25` (1128),
  `SCAN_DELAY_MS = 5000` (1130), `scanMaxPages` (1129), `WEAK_*` (1139-1140). The API
  bound is `PreferencesUpdate.scan_max_pages` (`main.py:462`, `le=25`).
- **Load-time references in `sidebar.js` all point backwards** (to names declared above
  them). Examples: `scanBtn.addEventListener('click', scanPage)`,
  `new MutationObserver(refreshProfileDirty)`, `applyZoom(1)`. Nothing relies on a
  function declared lower in the file being hoisted for a load-time call. This is what
  makes the top-down extraction in section 3 safe.
- **Two tests read `sidebar.js` by path or would miss moved code:**
  - `tests/test_selectors_sync.py::test_sidebar_has_no_hardcoded_detail_selectors` reads
    `extension/sidebar.js` only. After the split it would pass while checking nothing.
  - `quick_apply_e2e.py:819` only **prints** side-panel `pageerror`s; it doesn't fail on
    them. `dashboard_e2e.py:404` does fail.
- **CI** (`.github/workflows/tests.yml`) runs `node --check` on `extension/*.js`. Files
  in a subfolder would not be checked (D5).

### 1.3 Baseline artifacts (committed in 9.0 / 10.0)

- `scripts/dump_routes.py`: prints the flattened route table, one line per route:
  `METHOD path | endpoint function name | response_model | dependency qualnames`, plus
  `MOUNT path` lines, **sorted**.
  - It leaves out the endpoint's *module*, so moving a route doesn't change its line.
  - It includes the *dependency's* module, so step 9.1's unification shows up as an
    intended diff.
  - Verified approach (scratchpad prototype, FastAPI 0.137.1):
    ```python
    from fastapi.routing import _IncludedRouter
    def flat(routes, out):
        for r in routes:
            if isinstance(r, _IncludedRouter):
                flat(r.effective_candidates(), out)  # private API: pinned FastAPI
            else:
                out.append(r)
    ```
    Each flattened entry has `.path`, `.methods`, `.endpoint`, `.response_model` and
    `.dependant.dependencies[i].call`. If a FastAPI upgrade breaks this, the test
    **fails** with a message telling you to update the helper. It never skips.
- `tests/fixtures/route_table.txt`: the dump at baseline (51 lines: 4 docs routes, 45
  API routes, 2 mounts).
- `tests/fixtures/openapi.json`: `json.dumps(app.openapi(), sort_keys=True, indent=1)`.
  - operationIds are `<function name><path>_<method>`, so a pure move keeps them.
  - Any change to a path, a method, a parameter, a default, a body model, a response
    model or a docstring shows up as a diff.
- `scripts/js_globals.py` (step 10.0): the dependency map in section 3.1, generated, plus
  `--check`.

---

## 2. #9: split `app/api/main.py` (17 steps: 9.0-9.16)

### 2.1 Target layout

```
app/api/
  main.py        app = FastAPI(...), lifespan, OriginGuardMiddleware, include_router (ordered,
                 commented), the two static mounts. Re-exports app, get_db only.
  deps.py        get_db (the ONE session dependency)
  paths.py       STATIC dir, _screenshots_dir (+ mkdir at import, same as today)
  events.py      _sse_clients, _event_loop, _broadcast, broadcast_from_thread; router: GET /events
  worker.py      _bg_executor (the ONE ThreadPoolExecutor), _process_listing
  idle_loop.py   _IDLE_INTERVAL_S, _BUDGET_PAUSE_S, _letters_paused_until, _MATCH_*, _match_failures,
                 _match_attempt, _retention_tick, _recover_orphaned_runs, _letters_phase,
                 _raw_relevance_score, _processing_idle_loop
  health.py      GET /health, GET /llm/usage
  profile.py     GET/PUT /profile/{id}, GET/PUT /profile/{id}/preferences (+ProfileUpdate, PreferencesUpdate)
  evidence.py    GET /jobs/evidence-export, POST /jobs/{id}/screenshot (+_csv_safe, ScreenshotUpload)
  search.py      GET /jobs/search-performance, GET /jobs/suggested-searches (+_YIELD_*, _search_performance,
                 _underperforming_queries, _profile_phrase_candidates, _search_location, _refined_suggestions)
  ingest.py      POST /ingest (+SOURCE, IngestListing, IngestBody)
  job_actions.py POST quick-screen, PATCH status/expired/cover-letter, POST regenerate, DELETE /jobs,
                 DELETE /jobs/{id} (+StatusUpdate, CoverLetterUpdate)
  jobs.py        GET known-ids, pending-count, by-source-id, GET /jobs, GET /jobs/{job_id}
                 (+_score_to_number, _gaps_to_list, _eligibility_notes)  <- included LAST of the /jobs routers
  (unchanged) letters.py, obligation.py, profile_ui.py, screening.py, origin_guard.py
```

**Import graph (no cycles):**
- `deps`, `paths`, `events` import nothing from `app.api`;
- `worker` imports `events`;
- `idle_loop` imports `events`, `worker` and `paths`;
- the routers import `deps` plus whichever of `events`, `worker`, `paths` they need;
- `job_actions` and `jobs` import `ingest.SOURCE`, a constant, so plain
  `from ... import` is fine;
- `main` imports everything, and **nothing imports `main`**. Step 9.16 adds a test for
  this.

**Route order rule:**
- the existing routers stay first, as today;
- the new routers are included in this order: `health`, `events`, `ingest`, `evidence`,
  `search`, `job_actions`, `jobs`, `profile`.
- The only constraint is that every `/jobs/<literal>` GET comes before
  `GET /jobs/{job_id}`. Keeping `{job_id}` in `jobs.py`, last among the `/jobs` routers,
  and the literals above it inside `jobs.py`, satisfies that. A comment in `main.py`
  says so. Step 9.0's shadow test enforces it.
- While a step is half done, the not-yet-moved routes still sit on `app` in `main.py`,
  **after** every `include_router` call. So a literal moved into a router always comes
  before main's `{job_id}`.

### 2.2 Steps

Each step lists what moves, which tests change, the checks, the rollback and the risk.
**Every step also runs** the full suite and `tests/test_route_table.py` (route table and
OpenAPI snapshot unchanged unless the step says otherwise). Rollback for every step is
`git revert <sha>`, which restores the previous working state because each step is
self-contained.

#### 9.0 Safety nets (no move) (~200 lines, tests and scripts only)
- Add `scripts/dump_routes.py`, `tests/fixtures/route_table.txt` and
  `tests/fixtures/openapi.json` (section 1.3), and `tests/test_route_table.py`:
  1. `test_route_table_unchanged`: the dump equals the fixture. On failure, print a
     unified diff.
  2. `test_openapi_unchanged`: `app.openapi()` equals the fixture.
  3. `test_literal_job_paths_reach_their_own_endpoint`: a generic first-match check over
     the flattened table. For every route L, build a sample path (each `{param}`
     replaced by `1`), then find the first route with the same method whose
     `path_regex` matches. It must be L itself, or one with an identical path. This
     catches any shadowing, not just today's six.
  4. `test_literal_job_paths_answer`: `TestClient` on an in-memory SQLite DB (the
     `dependency_overrides` pattern from `tests/test_search_endpoints.py`). A GET to each
     of the six literal paths must not return 422. This is the behavioural version of
     test 3, and it uses no private API.
  - All four only import the app. No server, no model call.
- Add `tests/test_scan_policy.py` (cross-cutting; it protects #10 too). It reads every
  `extension/*.js` as text:
  - exactly one `const SCAN_DELAY_MS = <n>` with n >= 5000;
  - exactly one `const SCAN_PAGES_CEILING = 25`;
  - `pageCollectJobLinks` is injected exactly once, into the user's active tab
    (`injectFn(tab.id, pageCollectJobLinks)`);
  - the scan's `chrome.tabs.create` passes `active: false`;
  - `pageScrapeDetail` is injected only into `bgTab.id`.
  - It also imports `PreferencesUpdate` (from `app.api.main` today; step 9.7 updates the
    import) and asserts `scan_max_pages` has `le=25` (read from the field's metadata).
- Record the e2e baselines: run `dashboard_e2e.py`, then `quick_apply_e2e.py` (port rule
  in section 0). Put the exact pass counts in the commit message.
- Commit: "Tests: route-table, OpenAPI and scan-policy safety nets for the main.py /
  sidebar.js split".
- **Risk:** none to the app (tests only). Check that the new test file fails if you
  delete one route locally, then undo that.

#### 9.1 One `get_db` (~15 lines)
- New `app/api/deps.py` holding the `get_db` body (identical to both existing ones).
- In `main.py` and `profile_ui.py`, replace each local `def get_db` with
  `from app.api.deps import get_db`.
  - The name `profile_ui.get_db` keeps existing (letters, obligation, screening and
    tests import it), and is now **the same object** as `main.get_db`.
  - Drop `SessionLocal` from `profile_ui`'s imports **only if** nothing else in that
    file uses it (check with grep).
- Add to `test_route_table.py`:
  `assert main.get_db is profile_ui.get_db is deps.get_db`.
- **Intended fixture diff:** in `route_table.txt`, every dependency column becomes
  `app.api.deps.get_db`. Regenerate it with the script and review that this is the only
  change. OpenAPI is unchanged (`get_db` has no parameters).
- **Tests:** no edits. Overriding either key now overrides the one function, which is a
  superset of today. A test that overrode only one key now also covers the routes that
  used the other `get_db`. That's safer, and it changes nothing in production.
- **Risk:** low. Watch for a test that relied on *not* overriding a router's `get_db`
  (none found by reading).

#### 9.2 `paths.py`: the screenshots folder (~25 lines)
- Move `_static_dir` and `_screenshots_dir` (with its `mkdir`) into `app/api/paths.py`,
  unchanged. Keep the names (D3).
- `main.py` mounts with `paths._static_dir` / `paths._screenshots_dir`. All four readers
  (`_retention_tick`, `evidence_export`, `upload_screenshot`, `delete_job`) read
  `paths._screenshots_dir` at call time.
- Tests: `test_retention.py:72` and `test_screenshot_upload.py:61` patch `paths`; their
  later reads (`main_module._screenshots_dir`, lines 109 and 126) read `paths`.
  `scripts/check_retention.py:269` assigns `paths._screenshots_dir`.
- `main` keeps **no** `_screenshots_dir`, so any stale patch raises.
- **Check:** `.venv\Scripts\python -m py_compile scripts/check_retention.py`. Don't run
  the script itself (it defaults to real.db).
- **Risk:** the folder depends on `app_env()` at import, so `paths` must still be
  imported after `run_api.py` sets `APP_ENV`. It is: `paths` is imported from `main`,
  which uvicorn imports after `run_api.py` sets the variable. Same timing as today.

#### 9.3 `events.py`: the SSE broadcaster and `/events` (~70 lines)
- Move `_sse_clients`, `_event_loop`, `_broadcast`, `broadcast_from_thread` and the
  `sse_events` endpoint (as `@router.get("/events")`) into `app/api/events.py`.
- `lifespan` replaces `global _event_loop; _event_loop = ...` with
  `events._event_loop = asyncio.get_running_loop()`. Same effect, one owner.
- `main` calls `events.broadcast_from_thread(...)` and `await events._broadcast(...)` as
  attributes: in `_process_listing` (4 places), the idle loop (1) and `_letters_phase`
  (`partial(..., events.broadcast_from_thread)` and `await events._broadcast(...)`).
- Include `events.router` after `obligation_router` (it overlaps nothing).
- Tests:
  - `test_letter_phase8_api`: patch `events.broadcast_from_thread` and
    `events._broadcast`; `emit is events.broadcast_from_thread` (lines 317 and 387).
  - `test_process_listing_stops.py:46`: patch `events.broadcast_from_thread`.
- **Check:**
  - `grep -rn "_sse_clients\s*[:=]\|_event_loop\s*[:=]" app/` lists exactly one
    definition of each, in `events.py` (plus the one assignment in `lifespan`).
  - The route table and OpenAPI are unchanged.
  - **dashboard_e2e.py** (the panel connects to `/events` on load).
- **Risk: two client lists.** If any module did `from app.api.events import _sse_clients`
  and reassigned it, there would be two lists. Nothing reassigns it (only
  `.append`/`.remove`), but the grep above proves one definition.

#### 9.4 `worker.py`: the single executor and `_process_listing` (~70 lines)
- Move `_bg_executor` and `_process_listing` into `app/api/worker.py`, with the imports
  `_process_listing` uses: `extract_job`, `match_job`, `letter_production`,
  `SessionLocal`, `DailyQuotaError`, `BudgetExceededError`, `events`.
- `regenerate` calls `worker._bg_executor.submit(worker._process_listing, ...)`. The
  idle loop uses `worker._bg_executor` (6 places) and `worker._process_listing`.
- Tests:
  - `test_letter_phase8_api`: `TestRegenerate` patches `worker._bg_executor`
    (`fn is worker._process_listing`). `TestProcessListing` patches
    `worker.extract_job`, `worker.match_job`, `worker.SessionLocal`,
    `events.broadcast_from_thread`, and calls `worker._process_listing`.
    `TestLettersPhase` patches `worker._bg_executor`.
  - `test_process_listing_stops.py`: `api` becomes `worker`, and the broadcast patch
    goes to `events`.
  - `test_origin_guard.py:87`: `worker._bg_executor`.
- Add to `test_route_table.py`: across `app/api/*.py`, `ThreadPoolExecutor(` appears
  exactly once, in `worker.py`.
- **Risk: a second executor would let two AI chains run at once**, breaking the
  single-worker rule. The test above catches it.

#### 9.5 `idle_loop.py`, part 1: the helpers (~90 lines)
- Move `_match_attempt`, `_MATCH_MAX_ATTEMPTS`, `_MATCH_RETRY_PAUSE_S`, `_match_failures`,
  `_retention_tick`, `_recover_orphaned_runs` and `_raw_relevance_score` into
  `app/api/idle_loop.py`.
- The loop (still in `main`) calls them as `idle_loop._match_attempt(...)` etc. Its
  constants `_IDLE_INTERVAL_S` and `_BUDGET_PAUSE_S` stay in `main` until 9.6.
- Tests:
  - `test_match_failures.py:123-175`: `api` becomes `idle_loop` (`SessionLocal`,
    `_match_failures`, `_match_attempt`).
  - `test_letter_phase8_api::TestRecoverOrphanedRuns`: patch `idle_loop.SessionLocal`;
    call `idle_loop._recover_orphaned_runs`.
- `scripts/check_retention.py`: call `idle_loop._retention_tick()` and capture the
  `idle_loop.logger.name` logger. `py_compile` it.
- **Risk:** the retention tick's log lines now come from logger `app.api.idle_loop`
  (R9).

#### 9.6 `idle_loop.py`, part 2: the loop and the letters phase (~210 lines)
- Move `_IDLE_INTERVAL_S`, `_BUDGET_PAUSE_S`, `_letters_paused_until`, `_letters_phase`
  and `_processing_idle_loop` into `idle_loop.py`. `global _letters_paused_until` keeps
  working, because the function and the variable now live in the same module.
- `lifespan` does `asyncio.create_task(idle_loop._processing_idle_loop())`, **as an
  attribute lookup at lifespan time**, so the tests' patch takes effect.
- Tests:
  - `TestLettersPhase`: patch `idle_loop.SessionLocal`, `idle_loop._IDLE_INTERVAL_S` and
    `idle_loop._letters_paused_until`, and read and reset the same at lines 373, 389,
    398 and 412. Call `idle_loop._letters_phase()`. The `worker` and `events` patches
    come from 9.3 and 9.4.
  - `test_letter_limits.py:117` and `test_letter_side_engines.py:121`: patch
    `idle_loop._processing_idle_loop`.
- **Check:**
  - `grep -n "_letters_paused_until" app/` shows one module only.
  - Order inside the loop is unchanged: diff the moved function body against the old
    one with `git show HEAD~1:app/api/main.py`. It must be identical apart from
    `worker.` / `events.` / `idle_loop.` prefixes.
  - Run **both e2e scripts**: the server starts the idle loop for real, on the stub
    provider and a scratch DB.
- **Hand check (owner):** stop any running server. Then
  `python scripts/run_api.py test`; wait 30 s; the console shows no traceback and
  `GET http://127.0.0.1:8001/health` answers. Stop it.
  - Note: on `app.db` the loop does whatever AI work is pending, exactly as before the
    move. If you don't want that, run it with `--db` on a scratch copy.
- **Risk:** this is the riskiest backend step: the loop, shared state and the lifespan
  hook. It's isolated to its own commit so a revert is clean.

#### 9.7 `profile.py`: profile and preferences (~90 lines)
- Move `ProfileUpdate`, `PreferencesUpdate`, `get_profile`, `update_profile`,
  `read_preferences` and `update_preferences` into `app/api/profile.py` (router).
  Include it last.
- `tests/test_scan_policy.py` imports `PreferencesUpdate` from `app.api.profile`.
- **Check:** the `le=25` assertion still passes (the policy cap moved with the model).
  Route table and OpenAPI unchanged.

#### 9.8 `health.py`: `/health` and `/llm/usage` (~25 lines)
- The smallest router, and the pattern for the rest: `router = APIRouter()`,
  `db: Session = Depends(get_db)` with `get_db` from `app.api.deps`. Include it right
  after `obligation_router`.
- Popup and extension check: `/health` is what `popup.js` calls. Its JSON is unchanged
  (OpenAPI snapshot).

#### 9.9 `evidence.py`: evidence export and screenshot upload (~110 lines)
- Move `_csv_safe`, `ScreenshotUpload`, `evidence_export` and `upload_screenshot`.
  `delete_job` stays for now (it's a job action) and reads `paths._screenshots_dir`.
- **Order:** `/jobs/evidence-export` now comes from a router included before main's
  remaining `GET /jobs/{job_id}`, so the shadow test passes.
- Tests: `test_screenshot_upload.py` (already patches `paths`) needs no change.

#### 9.10 `search.py`: search performance and suggestions (~300 lines)
- Move the `_YIELD_*` constants, `_profile_phrase_candidates`, `_search_performance`,
  `_underperforming_queries`, `search_performance`, `suggested_searches`,
  `_search_location` and `_refined_suggestions` (with `search_suggest`, `search_refine`,
  `retention`, `COVER_LETTER_THRESHOLD`, `SavedSearch`).
- `tests/test_search_endpoints.py` uses the client only, so it needs no change.
- **Risk:** `suggested_searches` can make an AI call (layer 4, `search_refine`) when the
  preference is on. The tests already stub it. Don't change those tests.

#### 9.11 `ingest.py`: `POST /ingest` (~95 lines)
- Move `SOURCE`, `IngestListing`, `IngestBody` and `ingest`. `main` (for now) imports
  `SOURCE` from `ingest` for `job_by_source_id`.
- **Extension check:** `content_script.js` and `sidebar.js::ingestListing` post to it.
  The OpenAPI snapshot proves the body schema is unchanged.

#### 9.12 `job_actions.py`: the job write endpoints (~200 lines)
- Move `StatusUpdate`, `CoverLetterUpdate`, `run_quick_screen`, `update_status`,
  `mark_expired`, `update_cover_letter`, `regenerate`, `bulk_hide_jobs` and `delete_job`
  (with `quick_screen`, `worker`, `paths`, `unlink_screenshot`).
- `regenerate` keeps `worker._bg_executor.submit(worker._process_listing, ...)`
  verbatim.
- **Run both e2e scripts** (Mark applied, delete, the 409, the interview path).

#### 9.13 `jobs.py`: the job read endpoints (~200 lines)
- Move `_score_to_number`, `_gaps_to_list`, `_eligibility_notes`, `known_job_ids`,
  `pending_job_count`, `job_by_source_id`, `list_jobs` and `get_job`, **in that order,
  with `get_job` (`/jobs/{job_id}`) last in the file**.
- Include `jobs.router` after `job_actions`, before `profile`.
- After this step `main.py` has no endpoints left.
- **Run both e2e scripts.**

#### 9.14 `main.py` tidy-up (~-60 lines)
- Remove imports nothing uses (check each with grep).
- Keep `from app.api.deps import get_db` with the comment
  `# re-exported: tests key dependency_overrides on it`.
- Group the `include_router` calls with the order comment (2.1).
- Replace the module docstring's last paragraph with the module map, which describes
  the existing behaviour and changes nothing.
- **Target:** `main.py` under ~130 lines.

#### 9.15 (optional, D6) Routers take `get_db` from `deps`
- `letters.py`, `obligation.py` and `screening.py` change
  `from app.api.profile_ui import get_db` to `from app.api.deps import get_db`. It's the
  same object (9.1), so the route table is unchanged.

#### 9.16 Guard tests and docs
- Add a test that no module under `app/` imports `app.api.main` (scan with `ast`).
  Without it, a cycle could come back later.
- Docs updates are listed in section 6.

---

## 3. #10: split `extension/sidebar.js` (13 steps: 10.0-10.12)

### 3.1 Dependency map (deliverable; generated, then checked by hand)

Produced by a regex scan of top-level declarations. Strings aren't stripped, so a few
"users" are false positives (for example, `scanning` matches the word in
`refreshBudgetBanner`'s message text). `scripts/js_globals.py` (step 10.0) regenerates
this table; trust its output over this snapshot.

**Names used outside `sidebar.js`** (the cross-file contract):

| Name | Kind | Defined (sidebar.js) | Used by |
|---|---|---|---|
| `PROFILE_ID` | const | 8 | dashboard.js, almost every fetch in sidebar.js |
| `autoLetterMin` | let (written by `loadPreferences` and the auto-letter save) | 30 | dashboard.js, `coverLetterState`, `fillDetail`, `updateLetterLoopNote` |
| `tierOf` | function | 32 | dashboard.js, `loadJobs` |
| `TRASH_SVG` | const | 109 | dashboard.js (**not in its header**), `renderJob` |
| `mk` | function | 114 | dashboard.js, ~16 sidebar functions |
| `overviewSection`, `appliedSection` | const (DOM) | 125, 127 | dashboard.js, tab switching, `scheduleReload` |
| `showTab` | function | 149 | dashboard.js (load-time listener at 375) |
| `markApplied` | function | 155 | dashboard.js, `renderJob` |
| `loadJobs` | function | 201 | dashboard.js, `connectEvents`, `scanPage`, `scheduleReload`, refresh / auto-letter / bulk-hide handlers, startup |
| `coverLetterState` | function | 291 | dashboard.js, `loadJobs`, `renderJob` |
| `evidenceNote`, `csvEscape` | function | 1018, 1038 | dashboard.js only |
| `seekSearchUrl` | function | 1069 | dashboard.js, `maybeShowSuggestions` |
| `scanBtn` | const (DOM) | 1142 | dashboard.js **at load time** (375) (**not in its header**), `scanPage` |
| `typingIn` | function | 2615 | dashboard.js, `scheduleReload` |

**Names `sidebar.js` uses from other files:**
- `config.js`: `BACKEND`, `BACKEND_ENV`, `backendReady`;
- `selectors.js`: `SELECTORS`;
- `screening_assist.js`: `SCREENING_ASSIST_CSS`, `renderScreeningAssist`;
- `dashboard.js`: `refreshDashboard`, `loadOverview`, `loadAppliedPeriods`, all at call
  time (inside click handlers, `scanPage`, `connectEvents`, `scheduleReload` and the
  startup `.then`).

**Every top-level name, by section** (line = declaration at `d6dceb4`; each section
becomes one file):

| Section -> file | Lines | Top-level names |
|---|---|---|
| S1 -> `sidebar_shared.js` | 1-163 | `PROFILE_ID`, `announceSidebarOpen` (+ its call), `GOLD_MIN`, `BLUE_MIN`, `GREEN_MIN`, `AMBER_MIN`, `autoLetterMin`, `tierOf`, `QUAL_TYPE_LABELS`, `QUAL_STATUS_LABELS`, `EXP_TYPE_LABELS`, `fmtDate`, `monthInputHtml`, `normaliseMonth`, `monthValue`, two `document` listeners (month inputs), `truncate`, `TRASH_SVG`, `mk`, `tabBtns`, `overviewSection`, `jobsSection`, `appliedSection`, `profileSection`, `hdrJobsBtns`, `profileLoaded`, the tab-click listeners, `showTab`, `markApplied` |
| S2 -> `sidebar_jobs.js` | 165-525 | `jobStatusEl`, `jobListEl`, `renderSectionHeader`, `analysingEl`, `refreshPendingCount`, `loadJobs`, `renderFold`, `coverLetterState`, `CL_BADGES`, `renderJob`, `makeGenerateButton`, `fetchLetterInfo`, `fillDetail` |
| S3a -> `sidebar_letter_notes.js` | 527-726 | `screeningCssInjected`, `injectScreeningAssistCss`, `fetchScreeningAssist`, `renderQuickApplyQuestions`, `renderLetterNotes`, `sideSection`, `copyButton`, `renderSideOutputs` |
| S3b -> `sidebar_letter_card.js` | 728-1007 | `EXP_TYPES`, `QUAL_TYPES`, `QUAL_STATUSES`, `labelled`, `textInput`, `selectOf`, `splitList`, `proposalEditor`, `runRequest`, `errorText`, `renderQuestionCard`, `renderCoverLetterEditor` |
| S4 -> `sidebar_evidence.js` | 1009-1056 | `evidenceNote`, `csvEscape`, the `#export-evidence-btn` listener |
| S5 -> `sidebar_suggest.js` | 1058-1118 | `SUGGESTION_COOLDOWN_MS`, `seekSearchUrl`, `maybeShowSuggestions`, the `#sugg-close` listener |
| S6 -> `sidebar_scan.js` | 1120-1374 | `SCAN_PAGES_CEILING`, `scanMaxPages`, `SCAN_DELAY_MS`, `WEAK_SCORE_THRESHOLD`, `WEAK_STREAK_LIMIT`, `scanBtn`, `scanLogEl`, `scanning`, `scanLog`, `scanNotice`, `sleep`, **`pageCollectJobLinks` (injected)**, **`pageScrapeDetail` (injected)**, `waitForTabComplete`, `injectFn`, `ingestListing`, `scanPage`, the `#scan-btn` and `#refresh-btn` listeners |
| S7 -> `sidebar_personalise.js` | 1376-1721 | the 13 Personalise DOM consts, `letterLimits`, `limitValues`, `limitsNote`, `showLimit`, `updateLimitsNote`, the limits loop, `sideOutputToggles`, `letterLoopEnabled`, `letterLoopMin`, `letterEngine`, `updateLetterLoopNote`, `savePref`, the toggle listener, `loadPreferences`, the 7 save/change handlers, `searchPerfEl` + its listener, the bulk-hide listener |
| S8 -> `sidebar_profile.js` | 1723-2299 | `profileMsg`, `showMsg`, `dirtyNote`, `saveBar`, `savedSnapshot`, `setProfileDirty`, `refreshProfileDirty`, `markProfileClean`, `profileSectionEl` + the MutationObserver and 2 listeners, `addOriginBadge`, `makeQualCard`, `readQualCard`, `makeExpCard`, `readExpCard`, `skillsData`, `skillOrigins`, `renderSkillPills`, `addSkill`, `loadProfile`, `readProfileForm`, `populateForm`, `writingInput`, `profileMain`, `writingView`, `updateWritingStats`, `showWritingView` (+3 listeners), `saveProfile` (+3 listeners), `loadToWorkOn`, `renderToWorkOn` (+listener), `confirmSkill`, `cancelSkillInput` (+4 listeners) |
| S9 -> `sidebar_seek_import.js` | 2301-2589 | **`seekProfileExtract` (injected)**, `setImportStatus`, `importFromSeekProfile`, the `#seek-import-btn` and `#export-profile-btn` listeners |
| S10 -> `sidebar_events.js` | 2591-2673 | `refreshBudgetBanner`, `_eventsEverOpened`, `typingIn`, `reloadTimer`, `scheduleReload`, `connectEvents` |
| stays in `sidebar.js` | 2675-2752 | `envPill`, `renderEnvPill` (+listener), `ZOOM_STEPS`, `currentZoom`, `zoomInBtn`, `zoomOutBtn`, `updateWidthClasses`, `applyZoom`, `stepZoom` (+3 listeners), `applyZoom(1)`, the saved-zoom IIFE, **the `backendReady.then` startup** |

**Writes across file boundaries after the split** (they work in classic scripts, because
all top-level `let`s share one global lexical scope):
- `loadPreferences` (S7) writes `autoLetterMin` (S1) and `scanMaxPages` (S6);
- the auto-letter save handler (S7) writes `autoLetterMin`;
- `loadProfile` (S8) writes `profileLoaded` (S1).

### 3.2 Classic scripts, not ES modules (recommended, D4)

- **ES modules would not be a pure move.**
  - Each module gets its own scope, so every shared name needs `export`/`import`.
  - `dashboard.js` would have to become a module.
  - `screening_assist.js` can't, because the manifest loads it as a content script and
    MV3 content scripts can't be modules.
  - Imported bindings are read-only, so the cross-file writes above (`autoLetterMin`,
    `scanMaxPages`, `profileLoaded`) would need setter functions. That's new code.
  - Module scripts are deferred, which shifts when `applyZoom(1)` runs relative to
    parsing.
- **Classic scripts keep today's contract exactly:** one shared global scope, with load
  order as the contract. The split doesn't create that contract; it already exists
  between `sidebar.js` and `dashboard.js`.

### 3.3 The method that makes it safe: extract top-down, insert in place

- Each step cuts the **topmost remaining section** of `sidebar.js` into a new file.
- It adds that file's `<script>` tag **immediately before `sidebar.js`** (after the
  previously extracted files).
- So the browser runs every top-level statement in **exactly the same order** as today.
  The concatenation of the panel's scripts is the same text in the same order.

This removes the temporal dead zone (TDZ) and hoisting hazards:
- **TDZ:** a load-time statement can only reach names declared above it (section 1.2
  showed every load-time reference already points backwards), and those are all in
  earlier files.
- **Hoisting:** a function declared in a later classic script doesn't exist while an
  earlier one runs. That would only matter for a load-time call to a function declared
  further down, and there are none.
- **Call-time references in any direction are fine**, because nothing calls them until
  every script has run.
- **Startup stays in `sidebar.js`**, which stays the second-to-last script (before
  `dashboard.js`), so `backendReady.then(...)` is registered at the same point as today.

Final `sidebar.html` order:
```html
<script src="config.js"></script>
<script src="selectors.js"></script>
<script src="screening_assist.js"></script>
<script src="sidebar_shared.js"></script>
<script src="sidebar_jobs.js"></script>
<script src="sidebar_letter_notes.js"></script>
<script src="sidebar_letter_card.js"></script>
<script src="sidebar_evidence.js"></script>
<script src="sidebar_suggest.js"></script>
<script src="sidebar_scan.js"></script>
<script src="sidebar_personalise.js"></script>
<script src="sidebar_profile.js"></script>
<script src="sidebar_seek_import.js"></script>
<script src="sidebar_events.js"></script>
<script src="sidebar.js"></script>          <!-- zoom, env pill, startup -->
<script src="dashboard.js"></script>
```

**Manifest and CSP:** no change.
- Extension pages may load any file packaged with the extension, and the MV3 default
  `script-src 'self'` allows them.
- `web_accessible_resources` is only for exposing files to web pages.
- The content script's `js` list is untouched.
- The files stay flat in `extension/` so CI's `node --check extension/*.js` covers them
  (D5).

**What is and isn't machine-checkable**

| Hazard | Caught by |
|---|---|
| Syntax error in a new file | `node --check`; CI |
| Missing `<script>` tag, wrong order, duplicate top-level `const` across files (a SyntaxError that kills the whole later file), load-time ReferenceError/TDZ | **`tests/e2e/sidebar_load_e2e.py`** (new, 10.0): `pageerror` must be empty; plus `js_globals.py --check` for duplicates |
| A name `dashboard.js` uses that no longer exists | `js_globals.py --check` (every name `dashboard.js` uses is declared in an earlier script); `dashboard_e2e.py` |
| Call-time breakage on a specific tab or handler | the load e2e clicks every tab, opens Personalise, expands a card, fires each SSE event through a fake `EventSource`; `dashboard_e2e.py`; `quick_apply_e2e.py`; **not** the Scan Page or Seek import flows (owner hand checks) |
| An injected function that lost a closure | not machine-checkable without Seek. `js_globals.py --check` asserts the three injected functions reference no top-level panel name; owner hand check runs them |
| Policy constants changed or duplicated | `tests/test_scan_policy.py` (9.0) |

### 3.4 Steps

**Every move step's checks:**
- `node --check` on the new file and on `sidebar.js`;
- `python scripts/js_globals.py --check`;
- `tests/e2e/sidebar_load_e2e.py`;
- `dashboard_e2e.py`;
- `pytest -q tests/test_scan_policy.py tests/test_selectors_sync.py`;
- `git diff --stat`: the deleted lines in `sidebar.js` equal the lines in the new file
  (± the header comment).
- Add `wc -l` (or `Measure-Object -Line`) before and after to the report.

**The commit diff must be reviewable as a move.** Use `git diff --color-moved=zebra`.
The moved block shows as moved, not edited. The only new text is a 2-4 line header
comment in the new file (what it holds, and "classic script: loaded in this order by
sidebar.html; shares globals").

**Rollback:** `git revert` of the step. It restores both the file and the tag.

#### 10.0 Safety nets (no move)
- **`tests/e2e/sidebar_load_e2e.py`** (new). It needs no backend and can't reach Seek:
  - Playwright opens the real unpacked extension's `sidebar.html` (same launch code as
    `dashboard_e2e.py`).
  - Every request whose scheme isn't `chrome-extension` is **aborted**.
  - `chrome.tabs.create` is stubbed to record URLs (E2E RULE). Install the stub with
    `add_init_script` so it's in place before the panel's scripts run.
  - `EventSource` is replaced by a fake class that records instances and can `emit(name)`.
  - Then it checks: no `pageerror` on load; each tab clicked (Overview, Jobs, Applied,
    Profile); Personalise opened; zoom in and out; each SSE event name
    (`job_processed`, `cover_letter_ready`, `letter_run_started` / `_waiting` / `_done`
    / `_failed`, `llm_budget_blocked`) emitted, with no `pageerror` after each.
  - With the backend unreachable, the jobs list shows its "Backend not running" text.
    That is the expected state, and it proves `loadJobs` ran.
  - Exit 0 only if every check passed.
- **`scripts/js_globals.py`**: the generator from section 3.1. It reads `sidebar.html`'s
  script order. `--check` fails on:
  - a duplicate top-level name across the panel scripts;
  - a name `dashboard.js` uses that isn't declared in an earlier script (or `config.js`);
  - `pageCollectJobLinks`, `pageScrapeDetail` or `seekProfileExtract` referencing any
    top-level panel name.
- **`tests/test_selectors_sync.py`**: `test_sidebar_has_no_hardcoded_detail_selectors`
  scans every `extension/*.js` except `selectors.js` (and `content_script.js`, which
  uses `SELECTORS` too; check it has no literal first), not just `sidebar.js`.
  Otherwise it goes vacuous after the split.
- **`quick_apply_e2e.py:819`**: also record side-panel `pageerror`s and fail if there are
  any (today it only prints them).
- **`dashboard.js` header**: add the two missing names (`TRASH_SVG`, `scanBtn`) and say
  `BACKEND` comes from `config.js`. Comment only.
- **Checks:** all three e2e scripts green; full suite.

#### 10.1-10.11 One section per step, in this order
1. **10.1 S1 -> `sidebar_shared.js`.** Most of `dashboard.js`'s contract lives here.
   - After this step, `dashboard_e2e.py` proves `dashboard.js` still finds `mk`,
     `tierOf`, `showTab`, `markApplied`, `TRASH_SVG`, `overviewSection` and
     `appliedSection`.
   - `announceSidebarOpen()` still runs first, before any other panel code.
2. **10.2 S2 -> `sidebar_jobs.js`.** Run `quick_apply_e2e.py` too (it expands a job card
   and draws the Quick Apply questions inside `fillDetail`).
3. **10.3 S3a -> `sidebar_letter_notes.js`.** Run `quick_apply_e2e.py` (it uses
   `renderQuickApplyQuestions` and `SCREENING_ASSIST_CSS`).
4. **10.4 S3b -> `sidebar_letter_card.js`.** `dashboard_e2e.py` covers the letter editor
   (the "waiting" job with a question card).
5. **10.5 S4 -> `sidebar_evidence.js`.** `dashboard_e2e.py` checks `evidenceNote` and
   `csvEscape` through the Applied tab and the CSV.
6. **10.6 S5 -> `sidebar_suggest.js`.**
7. **10.7 S6 -> `sidebar_scan.js`.** Policy step.
   - `tests/test_scan_policy.py` must still pass unchanged. It finds the constants in
     any `extension/*.js`.
   - `js_globals.py --check` confirms the two injected functions are still
     self-contained.
   - `dashboard.js:375` uses `scanBtn` at load time, so `sidebar_scan.js` must load
     before `dashboard.js`. It does.
   - **Owner hand check required (H2).**
8. **10.8 S7 -> `sidebar_personalise.js`.** Run `quick_apply_e2e.py` (it toggles the
   Personalise question-help checkbox). **Owner hand check (H3).**
9. **10.9 S8 -> `sidebar_profile.js`.** The load e2e clicks the Profile tab (it calls
   `loadProfile` and `loadToWorkOn`; with no backend it shows their error text).
   **Owner hand check (H4).**
10. **10.10 S9 -> `sidebar_seek_import.js`.** `js_globals.py --check` confirms
    `seekProfileExtract` is self-contained. It uses `sleep` and `waitForTabComplete`
    from `sidebar_scan.js` at call time. **Owner hand check (H5), optional** (it opens
    the owner's Seek profile page).
11. **10.11 S10 -> `sidebar_events.js`.** The load e2e fires every SSE event. **Owner
    hand check (H6).** `sidebar.js` is now ~80 lines: zoom, env pill, startup.

#### 10.12 Docs (section 6).

### 3.5 Owner hand checks in your own Chrome (TEST backend unless stated)

Before each: `python scripts/run_api.py test`, then `chrome://extensions` -> reload the
extension -> open the side panel. If the pill shows REAL, click it to switch to TEST. Do
the Seek steps yourself; the executor never does.

- **H1 (after 10.1, and again at the end): the panel opens.**
  1. Open the panel. Expect the Overview tab, no "Backend not running", and the zoom you
     last used.
  2. Click Jobs, Applied, Profile, Overview. Each draws.
  3. Right-click the panel -> Inspect -> Console. Expect no red errors.
- **H2 (after 10.7): Scan Page.**
  1. Personalise -> set the scan cap to 2 -> Save.
  2. Open a Seek search results page yourself.
  3. Press Scan Page. Expect the Console to show `[scan] (1/2) opening job page…`, then
     `captured ✓ (N chars)` with a real number.
  4. Expect at least 5 s between the two "opening" lines.
  5. Expect the background tabs to open and close without stealing focus.
  6. On the Overview, press Scan Page: it switches to Jobs (the `dashboard.js:375`
     listener).
- **H3 (after 10.8): Personalise.**
  1. Open Personalise; the values match before the update.
  2. Change the auto-letter score by 1 -> Save -> "Saved ✓"; put it back.
  3. Untick and re-tick one side-output box; it stays.
  4. Type 26 into the scan cap -> Save. Expect it to snap back, without saving.
- **H4 (after 10.9): Profile.**
  1. The Profile tab loads your fake profile.
  2. Edit the phone field. Expect "● Unsaved changes"; undo the edit and the note goes
     away.
  3. Open "Your Writing" and go back.
  4. The To work on list loads.
- **H5 (optional, after 10.10): Seek profile import.** Press Import from Seek profile
  (it opens your own `au.seek.com/profile/me` in a background tab, as today). Expect the
  "Imported: …" status. **Don't save** unless you want the data.
- **H6 (after 10.11): live updates.**
  1. With the panel open on Jobs, stop the server (Ctrl+C) and start it again.
  2. Within ~10 s the panel reconnects and the list reloads (SSE `onopen`); the Console
     shows no errors.
  3. Click the REAL/TEST pill twice; the panel reloads each time.
  4. Close and reopen the panel; the zoom is remembered.

### 3.6 Backend hand checks for #9

Only H-B1 (after 9.6): the server starts and the idle loop survives 30 s (in step 9.6).
Every other backend step is fully covered by tests and the e2e scripts.

---

## 4. Cross-cutting: does a step change anything the extension relies on?

- **#9: no, at every step.**
  - URL paths, methods, parameters, request bodies and responses: the OpenAPI snapshot
    is identical after each step. The only planned route-table diff is 9.1's dependency
    column.
  - SSE event names and payloads: the same `broadcast_from_thread("…")` /
    `_broadcast("…")` calls move verbatim. Check:
    `git grep -ho "broadcast[_a-z]*(\"[a-z_]*\"" -- app/ | sort | uniq -c`
    gives the same counts before and after each step.
  - The Origin guard: still the only middleware, added to the same `app`. Its tests
    (`test_origin_guard.py`) run in every step.
- **#10: no backend change at all.** The diff touches only `extension/*.js`,
  `sidebar.html` (script tags only), `dashboard.js` (header comment only), and test or
  script files. Check: `git diff --stat main...HEAD -- app/` is empty on the #10 branch.
- **#10 steps that need a real Chrome:** 10.1 (H1), 10.7 (H2), 10.8 (H3), 10.9 (H4),
  10.10 (H5, optional), 10.11 (H6), and H1 again at the end. The e2e scripts use the
  real unpacked extension under Playwright, but not your profile, your Seek session or
  real Seek pages.

---

## 5. Order and branches

**Do #9 first, then #10. Don't interleave them.**
- #9 touches the file that conflicts with every backend branch (ARCHITECTURE 3.9), and
  it's fully machine-checkable.
- #10 needs owner hand checks.
- They share no files except `docs/` and the 9.0 scan-policy test (which #10 then uses).
  Finishing one before starting the other keeps each review about one thing.

**Git state now** (`git status` clean; `git branch -vv`):
- `fix/applied-evidence-safety` (HEAD `d6dceb4`) is **26 commits ahead of
  `origin/fix/applied-evidence-safety`** (not pushed) and 33 ahead of `origin/main`.
- `origin/main` has one commit HEAD lacks: `c7cd8ea`, the PR #3 merge of
  `centerlink-preperation`. Its content is already in HEAD, so merging it is
  conflict-free.
- Local `main` is 12 behind `origin/main`.
- `centerlink-preperation`, `feat/cover-letter-agent-phases-5-9` and
  `feat/search-suggestion-pipeline` are all already in both HEAD and `origin/main`.

**Owner decision (2026-10-12): all of #9 and #10 happen on `fix/applied-evidence-safety`**
(pushed at `2872a71`), and the branch is merged into `main` once both are done. This
replaces the two-branch recommendation (D7).
- Order on the branch is unchanged: 9.0-9.16, then 10.0-10.12. Don't interleave them.
- The executor doesn't create branches, push, merge or rebase. The owner pushes after
  reviewing steps.
- Because the branch now lives longer, don't start other work on `main.py` or
  `sidebar.js` elsewhere until it merges.
- The merge should be a plain merge (not squash), so each step stays a separately
  revertible commit on `main`.
- `origin/main`'s extra merge commit `c7cd8ea` has the same content as this branch, so
  the final merge is expected to be conflict-free. Check with
  `git merge-tree --write-tree origin/main HEAD` before opening the PR.

---

## 6. Done means

**#9**
- [ ] `app/api/main.py` under ~130 lines; no endpoint, helper or idle-loop code left in
  it.
- [ ] `tests/fixtures/route_table.txt` differs from baseline only by 9.1's dependency
  column; `openapi.json` byte-identical to baseline; the shadow and literal tests pass.
- [ ] Exactly one `ThreadPoolExecutor(`, one `_sse_clients`, one `_letters_paused_until`,
  one `get_db` definition in `app/api/`; nothing imports `app.api.main`.
- [ ] Suite = 1549 + the new tests, all green; both e2e scripts at baseline counts or
  more.
- [ ] H-B1 passed.
- [ ] Docs updated:
  - ARCHITECTURE 5.4 #9 status (with shas);
  - 3.2 (`main.py` row, plus a row per new module);
  - 2.2/2.3, 3.9, 4.1 steps 3-5, 4.2-4.6 (every `main.py::<function>` reference now
    names its module);
  - 5.2 rows for `app/api/main.py` and `_processing_idle_loop`, `_letters_phase` (now
    `idle_loop.py`; add "patch the owner module, not `main`" (rule M1));
  - the glossary (Idle loop, Single worker, Evidence export, Ingest, Soft delete);
  - reading order step 4. Use `grep -n "main\.py" docs/ARCHITECTURE.md` as the
    checklist.
  - `CLAUDE.md`: "Repo layout" tree (new `app/api/` files) and the LLM LAYER sentence
    "(_processing_idle_loop in main.py)" -> `app/api/idle_loop.py`.
  - `README.md` lines 225 and 326-327 (it says "All of it lives in `app/api/main.py`").
  - A `PROGRESS.md` block.

**#10**
- [ ] `sidebar.js` ~80 lines (zoom, env pill, startup); the largest panel file
  (`sidebar_profile.js`) under ~600 lines.
- [ ] `sidebar.html` script order exactly as in 3.3; `js_globals.py --check` clean; the
  `dashboard.js` header matches its real use.
- [ ] `node --check` clean on every file (CI); `sidebar_load_e2e.py`,
  `dashboard_e2e.py` and `quick_apply_e2e.py` green; `test_scan_policy.py` and
  `test_selectors_sync.py` green.
- [ ] H1-H4 and H6 passed (H5 optional), recorded in PROGRESS.md.
- [ ] Docs updated:
  - ARCHITECTURE 5.4 #10 status;
  - 3.7 (`sidebar.js` row -> one row per new file; the `dashboard.js` row's
    "relies on sidebar.js" -> "on the `sidebar_*.js` files");
  - 5.2 extension rows (the stale `sidebar.js:1124-1126` -> `sidebar_scan.js`;
    `scanPage` / `pageCollectJobLinks` now in `sidebar_scan.js`);
  - 4.2 step 11, 4.4 step 22, 4.6 step 31 (`sidebar.js::…` references);
  - reading order step 12;
  - 1.1/1.4 line counts.
  - `CLAUDE.md` repo layout (`extension/` files) and the SEEK ACCESS POLICY sentence
    naming where the scan constants live, with no rule change.
  - `PROGRESS.md` block.

---

## 7. Decisions for the owner (before work starts)

| # | Decision | Recommendation |
|---|---|---|
| D1 | Router names and layout | **Flat files in `app/api/`** as in 2.1 (`deps`, `paths`, `events`, `worker`, `idle_loop`, `health`, `profile`, `evidence`, `search`, `ingest`, `job_actions`, `jobs`). It matches `letters.py` / `obligation.py`; a sub-package (`app/api/routers/`, `app/worker/`) changes more import paths for no gain at this size. |
| D2 | Temporary re-exports in `main.py` | **Only `app` and `get_db`, permanently** (used by identity, never patched; 15 test files reach `get_db` through `main`). **No re-export of anything a test patches**, so stale patches fail loudly (rule M1). |
| D3 | Keep leading-underscore names when moving (`worker._bg_executor`, `idle_loop._letters_phase`) | **Yes, during the moves.** It's a smell to reach `_x` across modules, but it keeps each diff a pure move and each test edit a one-word swap. Renaming to public names is optional, after 9.16, as one mechanical commit. |
| D4 | Classic scripts vs ES modules | **Classic scripts** (3.2). Modules would need setters for cross-file writes and can't include `screening_assist.js` (a content script). |
| D5 | Slicing of the extension | **11 files, one per section, extracted top-down** (3.3). Flat in `extension/`, named `sidebar_*.js`, so CI's `node --check extension/*.js` covers them. Coarser slicing (for example 5 files) is possible, but each step's diff then exceeds the 15-minute review target. |
| D6 | Do optional step 9.15 (routers take `get_db` from `deps`) | **Yes.** Three one-line edits, and the profile editor stops being the session provider for unrelated routers. |
| D7 | Share a branch between #9 and #10 | Recommended two branches; **owner decided: both on `fix/applied-evidence-safety`, #9 then #10, merged once at the end** (section 5). |
| D8 | Keep the route-table, OpenAPI, scan-policy and load e2e permanently | **Yes for scan-policy, the shadow test and the load e2e** (cheap, and they guard the Seek policy and load order forever). **OpenAPI and route table: keep as well**, and regenerate deliberately when an endpoint changes on purpose (the fixture diff then documents the API change in review). If that feels like friction, drop them after both refactors merge. |
| D9 | Who runs the hand checks | **The owner** (H1-H6, H-B1). Without H2, step 10.7 stays "machine-checked only" in the docs. |

---

## 8. Out of scope: don't touch

- ARCHITECTURE #15 (hardcoded profile id 1, `PROFILE_ID`, `user_id == 1`).
- Any behaviour change, including "obvious" fixes noticed during a move. Report them
  instead. Noticed while planning, not to be fixed here:
  - GET endpoints with side effects;
  - `job_id` path params could use `{job_id:int}` converters so order stops mattering
    (that would change 422 into 404, so it's a behaviour change);
  - `dashboard.js` startup relying on the panel scripts having run first.
- Any schema change or migration; `app/models.py`; `app/llm/`.
- CORS / Origin-guard semantics (`origin_guard.py` and its middleware position).
- The dormant scraper (`app/scraper/`) and proxy code.
- Renaming public routes, query parameters, JSON fields or SSE event names.
- `content_script.js`, `background.js`, `popup.js`, `config.js`, `selectors.js`,
  `screening_assist.js`, `manifest.json` (no edits needed by #10).
- CSS in `sidebar.html` and any HTML other than the `<script>` tags.

---

## 9. Risk register: ways this could break silently, and what catches each

| # | Silent failure | Caught by |
|---|---|---|
| R1 | A test's `monkeypatch.setattr(main, "X", …)` stops taking effect after `X` moves; the real `SessionLocal`, executor or broadcaster runs (in the worst case, against `app.db`) | Rule M1: `main` keeps no copy of `X`, so the stale patch raises `AttributeError`. Every move updates the patch in the same commit. Reviewers grep the diff for `setattr(main` / `setattr(api`. |
| R2 | An endpoint moved into a router uses the other `get_db`, so a test silently uses `app.db` | Step 9.1 (one `get_db`, identity asserted); the route table's dependency column |
| R3 | A second `ThreadPoolExecutor` (two AI chains at once) or a second `_sse_clients` list (events reach nobody) | The single-definition tests in 9.3 and 9.4; `dashboard_e2e` (SSE connect) |
| R4 | `/jobs/<literal>` shadowed by `/jobs/{job_id}` after router reordering (422 instead of data, for example the evidence export or scan's known-ids) | `test_literal_job_paths_reach_their_own_endpoint` (generic) and `test_literal_job_paths_answer` (behavioural) |
| R5 | The lifespan starts the real idle loop in tests because it captured `_processing_idle_loop` at import | `lifespan` calls `idle_loop._processing_idle_loop()` at lifespan time; review checks for no `from app.api.idle_loop import _processing_idle_loop` |
| R6 | A missing `<script>` tag, wrong order, or a duplicate top-level `const` across files, so the panel half-loads | `sidebar_load_e2e.py` (`pageerror` empty), `js_globals.py --check`, `dashboard_e2e.py` |
| R7 | A TDZ or ReferenceError only on one tab or one handler (call time) | Top-down extraction keeps load order identical; the load e2e clicks every tab and fires every SSE event; `quick_apply_e2e` (card, Personalise); owner H1-H6 |
| R8 | An injected function (`pageScrapeDetail`, `pageCollectJobLinks`, `seekProfileExtract`) starts using a panel global, so it throws only inside the Seek tab | `js_globals.py --check` (injected functions reference no top-level name); owner H2 and H5 |
| R9 | Log lines move to new logger names (`app.api.idle_loop`, …), so `scripts/check_retention.py` captures nothing | That script is updated in 9.5 (uses `idle_loop.logger.name`); no test filters on `app.api.main` (searched: no `caplog` use) |
| R10 | Scan policy weakened during the move (delay, ceiling, `active: false`, links only from the user's tab, API `le=25`) | `tests/test_scan_policy.py` in every step; owner H2 |
| R11 | `test_selectors_sync` passes vacuously once the scan code leaves `sidebar.js` | Step 10.0 makes it scan every `extension/*.js` |
| R12 | A moved endpoint's annotation name isn't imported in its new module | Fails at import (loud): the suite and the OpenAPI snapshot |
