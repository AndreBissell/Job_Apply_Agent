# Plan: a second worker for scoring

Status: APPROVED by the owner 2026-11-23, in progress (Step 0 done; next: Step 1). Branch `avaliability-check`
(to be built before that branch's PR). Source: Part 1 of `future_work/second-worker-and-faster-letters.md`.
Part 2 of that file (faster letters) is OUT of scope; it stays in future_work.

## Goal

Newly scanned jobs get quick-screened, extracted and scored **while a cover letter is being
written**, instead of waiting until the letter queue is empty. Letters, their quality, their
cost and their speed do not change.

## Context: what the code does today

- **One worker thread does all LLM work.** `app/api/worker.py:30`,
  `_bg_executor = ThreadPoolExecutor(max_workers=1)`. The idle loop
  (`app/api/idle_loop.py::_processing_idle_loop`) runs, in order every pass: retention sweep,
  letters (`_letters_phase`), then Phase 0 quick screen, Phase 1 extract + match, Phase 1b match.
  A pass that did letter work `continue`s, so **scoring waits until no letter is due**.
- **A pipeline letter takes 3 to 14 minutes** (about 8 on average). Measured in `real.db`, last 12
  agent runs: 8 to 16 tool steps and 14 to 35 model calls per letter.
- **The database is not the bottleneck.** SQLite allows many readers and one writer at a time,
  and a writer only holds the lock between its first write and `commit()`. Every path the
  scorer runs (`extract_job`, `match_job`, `quick_screen`) reads, calls the model with nothing
  pending, then writes and commits in milliseconds. A letter makes about 25 to 55 short commits
  over its whole run (one per tool step in `runner.persist`, one per `llm_usage` row).
  `autoflush=False` and the sqlite driver's "BEGIN only on a write" mean no lock is held while
  a model call runs. Request threads (`/ingest`, the scan's quick screen, availability posts)
  already write alongside the worker today.
- **SQLite settings:** default rollback journal (`journal_mode=delete`), and the Python driver's
  default lock wait of 5 seconds before "database is locked".
- **Claims that exist today** (in memory, one process): `quickscreen._in_flight` ("being
  quick-screened"), `letter_bands.queued` ("a letter is waiting for or being written by the
  worker"; drives the "Writing…" cards), `drafts._in_flight` (screening drafts).
- **The overlap that a second worker creates.** `/regenerate` (Rewrite, "Try again now", and the
  Quick Apply overlay's "Create a cover letter", `extension/screening_assist.js:505`) runs on the
  letter worker and re-extracts and re-scores the job (`_process_listing(force=True)`). The
  Quick Apply path can reach a job that is still unscored, so it can meet the scorer on the
  same job. Today the single thread makes that impossible.
- **Rate limit:** `LLM_RPM=8` is one in-process throttle for all threads (`client._throttle`).
  Letters average 2 to 4 calls a minute (their calls are long); scoring is about 3 calls per job
  plus a 20 s pause. Together they fit under 8 a minute.
- **Crash behaviour today:** work is found by query (`extracted_at IS NULL`, no match row), and
  each step marks itself done in the same commit that stores its result, so a crash mid-job
  leaves the job looking untouched and it is picked up again. Only letter runs persist an
  "in progress" state (`letter_runs.status='running'`), which `recover_orphaned_runs` clears at
  start-up.
- **A test guards the old design:** `tests/test_route_table.py::test_there_is_one_worker_thread_pool`
  asserts exactly one `ThreadPoolExecutor(` in `app/api/`.

## Decisions (agreed with the owner, 2026-11-23)

**D1. Two independent loops, each with its own single-thread executor.**
- *Letter loop* on `worker._bg_executor` (name kept, so existing tests and callers still work):
  `_recover_orphaned_runs` once at start, then the retention sweep and `_letters_phase`.
  `/regenerate`, band letters and "Write a letter" keep submitting to `_bg_executor`.
- *Scoring loop* on a new `worker._score_executor = ThreadPoolExecutor(max_workers=1)`:
  Phase 0, 1 and 1b only, all `small` tier.
- Each loop has its own try/except and its own pauses: the budget pause stops only letters,
  the scorer's 3-minute back-off stops only scoring. One loop failing never stops the other.
- Both are started in `main.lifespan` and both are cancelled on shutdown.

**D2. An in-memory claim set, new module `app/job_claims.py`, one meaning:** "a worker is
quick-screening, extracting or matching this job right now".
- `try_claim(job_id) -> bool` for the scorer: taken means skip this job.
- `wait_claim(job_id, timeout) -> bool` for `/regenerate`'s extract + match: sleeps on a
  `threading.Condition` until released or timed out.
- `release(job_id)` always in a `finally`, AFTER the commit.
- Order: claim, re-read the job, do the work, commit, release.
- Not reused: `letter_bands.queued` (would show "Writing…" for jobs being scored, refuse the
  user's clicks, and make `/regenerate` wait on its own claim) and `quickscreen._in_flight`
  (`quick_screen` claims it itself, so the scorer would block itself). Neither of those changes.
- Why memory, not a DB column: one process, so every worker sees it; a crash kills every holder,
  so forgetting claims is correct and needs no recovery step. Revisit (DB lease or Postgres
  `SELECT ... FOR UPDATE SKIP LOCKED`) only when hosting with several processes.

**D3. The background worker skips, the user's request waits.** The scorer skips a claimed job
(its candidate queries leave claimed ids out, so it moves on to the next job rather than
spinning). `/regenerate` waits for the claim. Accepted: after waiting, `/regenerate` still
re-extracts with `force=True`, so a rare overlap pays the small calls twice.

**D4. SQLite lock wait 5 s -> 30 s.** `PRAGMA busy_timeout=30000` in the existing connect listener
in `app/db.py`, only for `sqlite3` connections (Postgres untouched). No WAL.

**D5. No change** to `LLM_RPM`, the budget guard, or usage logging.

**D6. When `/regenerate`'s `wait_claim` times out (120 s): log a WARNING and proceed anyway**
(owner, 2026-11-23, option (a)).
- Applies only to the claim wait: the scorer holds THIS job and has not released it within
  120 s. Waiting in the letter queue behind another letter is normal and has no limit.
- The WARNING names the job id and how long the claim had been held, so a leaked claim (a bug)
  or a hung model call shows up in the log even though the user still gets their letter.
- Why not give up: a leaked claim would block that job's letter until a restart, and a hung
  call (no request timeout yet) would make every retry give up again. Proceeding costs at most
  a duplicate small call.
- Accepted: under 429 back-off it adds calls to a limit already hit (slower, not broken); if
  the stuck scorer finishes later, the last extraction commit wins and its match insert fails
  on `UNIQUE(user_id, job_id)`, logged once as a scoring failure, then skipped on the next pass.
- Rejected: (b) give up, log, release the letter claim, send `letter_rewrite_done` (which means
  "the attempt ended", not "succeeded"; failures already send it).

## Constraints: what not to touch

- **Part 2, all of it:** letter engines, prompts, models, thinking levels, check order or
  parallelism, request timeouts, `letter_engine`, `LLM_RPM`'s value.
- **Letters stay on one thread.** Nothing letter-related is ever submitted to `_score_executor`.
- **No schema change, no migration, no new preference.**
- **No API change:** routes, request/response shapes, SSE event names. The route table and
  OpenAPI fixtures (`tests/fixtures/route_table.txt`, `openapi.json`) must stay byte-for-byte.
- **No extension change.** The Seek Access Policy is unaffected (no new Seek traffic).
- **Scoring logic unchanged:** quick-screen rules, `extract_job`, `match_job`, prompts, the
  freshness gate (`availability.llm_gate`), the relevance ordering, retry counts and pauses.
- **Existing claim sets unchanged:** `letter_bands.queued`, `quickscreen._in_flight`,
  `drafts._in_flight`.
- **No WAL**, no other PRAGMA beyond D4.
- **No paid calls in tests;** never point a test or experiment at `real.db`. `run_api.py` runs
  with `reload=True`, so the code must stay importable after every edit.
- **Public repo:** no personal data in any commit; check `git diff --cached` before each one.

## Steps

Each step is its own commit (one reason per commit) and leaves the full suite green.

0. **Baseline.** Run `python -m pytest -q` and every `tests/e2e/*.py` suite on the unchanged
   code; record the pass counts in the Implementation log below. These are the numbers the end
   result is compared against.
1. **SQLite lock wait (D4).** `app/db.py` connect listener + a test.
2. **Claim module (D2).** `app/job_claims.py` + its unit tests. Not wired in yet.
3. **Wire the claims (D3, D6).** The scorer phases take `try_claim` around each job's work and
   leave claimed ids out of their candidate queries; `_process_listing` gets a way for
   `/regenerate` to `wait_claim` (the scorer's calls use the skip path); on timeout it logs the
   WARNING and proceeds. Tests.
4. **Second executor and two loops (D1).** `worker._score_executor`; split
   `_processing_idle_loop` into a letter loop and a scoring loop, each built from a one-pass
   function the tests can call; `main.lifespan` starts and cancels both. Update the pool guard
   test (see Test rules). Tests.
5. **Existing tests.** Where a test fakes `_bg_executor` for SCORING work, it also fakes
   `_score_executor`. Nothing else in them changes.
6. **Wording.** Grep for "single worker", "one worker", "serialis" in `app/`, `CLAUDE.md` and the
   docstrings of `worker.py`, `idle_loop.py`, `main.py`; correct only what is now false.
7. **Docs.** CLAUDE.md (LLM Layer's rate-limit paragraph, repo layout lines for `worker.py`,
   `idle_loop.py`, the new `job_claims.py`), PROGRESS.md entry, this plan's status and
   Implementation log. `future_work/second-worker-and-faster-letters.md` keeps Part 2 only;
   its README index line is updated to match.
8. **Full suite and e2e** (Done when, below), then the owner's manual checks.

## Test rules: no test is weakened

- **No existing test is deleted, skipped, marked xfail, or has an assertion loosened,** and no
  timeout is raised just to make a test pass.
- **The only allowed changes to existing tests:**
  1. also patching `_score_executor` where the test already patches `_bg_executor` for
     scoring work (Step 5);
  2. `test_there_is_one_worker_thread_pool`, rewritten to the NEW rule and kept at least as
     strict: exactly two pools, both in `worker.py`, both `max_workers=1`, named `_bg_executor`
     and `_score_executor`; and `_score_executor` is referenced only in `worker.py` and
     `idle_loop.py`, so no letter path can reach it.
- **Every changed existing test is listed in the Implementation log** with the reason.
- **New concurrency tests use `threading.Event` with a timeout, never bare sleeps,** so a failure
  fails fast instead of hanging or flaking.

## New tests (the new behaviour)

`tests/test_job_claims.py`:
- a second `try_claim` on the same job returns False; another job's claim is unaffected;
- `release` then `try_claim` succeeds; releasing an unclaimed id is harmless;
- `wait_claim` returns True as soon as another thread releases (Event-driven);
- `wait_claim` returns False after its timeout when nobody releases;
- the claim is released when the work raises (the `finally`).

`tests/test_two_workers.py` (stub LLM or fakes, scratch DB):
- **the goal itself:** while a letter is blocked on the letter executor (held on an Event), one
  scoring pass extracts and scores a fresh job, and the letter is still running afterwards;
- the scorer skips a job another worker has claimed and takes the next candidate;
- `/regenerate` on a job the scorer holds waits, then runs once the scorer releases; the job
  ends with exactly one match row;
- D6: with the claim never released, `/regenerate` proceeds after the timeout (patched short in
  the test), logs one WARNING naming the job and the held time, and the letter still lands;
- a budget pause on letters does not stop a scoring pass; a scoring back-off does not delay a
  letter pass;
- an exception in one loop's pass does not stop the other loop;
- `_recover_orphaned_runs` runs before the first letter and never on the scoring executor;
- nothing letter-related is submitted to `_score_executor` (regenerate, band letters,
  write-letter all land on `_bg_executor`);
- `lifespan` starts two loop tasks and cancels both on shutdown.

`app/db.py`: a new SQLite connection reports `PRAGMA busy_timeout` = 30000, and a second thread's
write waits for (and succeeds after) a first thread's short open write transaction on a scratch
file DB.

## Done when

- [ ] `python -m pytest -q` passes in full: zero failures, zero errors, no new skips or xfails;
      passed count = baseline (Step 0) + the new tests.
- [ ] Every `tests/e2e/*.py` suite passes at its baseline count or higher.
- [ ] Test diff reviewed against the Test rules: no test removed or loosened; every changed
      test listed in the Implementation log with its reason.
- [ ] Every test in "New tests" exists and passes.
- [ ] `tests/fixtures/route_table.txt` and `openapi.json` unchanged (`git diff` shows nothing).
- [ ] No file outside the Steps' list changed (in particular: no `app/llm/letter/`, no prompts,
      no `extension/`, no `alembic/`).
- [ ] Docs updated (Step 7); the future_work file holds Part 2 only.
- [ ] Each commit's staged diff checked for personal data.
- [ ] The owner's manual checks below passed.

## Manual checks (owner, at the end)

Run on the TEST environment only (`python scripts/run_api.py test`), never real. The first two
use the real model on the test profile: about one pipeline letter plus a few small calls,
roughly $0.25; get the go-ahead before running them.

- [ ] With a letter being written (server log shows `Idle: cover letter (...)`), scan a Seek
      search page: the log shows `Idle: quick-screen` / `extract+match` lines for the new jobs
      BEFORE the letter's `letter_run_done`, and their scores appear in the Pipeline tab.
- [ ] The letter that was running lands as normal (Keep Applying card, no "Writing…" stuck).
- [ ] Stop the server mid-letter and mid-scoring, start it again: the letter run is marked
      failed and retried later as before; the job being scored is picked up again; no job
      stays stuck.
- [ ] No "database is locked" in the server log during any of the above.

## Implementation log

(Filled in while building: baseline counts, each changed existing test and why, choices made.)

**Step 0, baseline (2026-11-23, unchanged code at `81578bc` + this plan).** These are the numbers
the end result is compared against.

| Suite | Baseline |
|---|---|
| `python -m pytest -q` | 2065 passed, 0 failed, 0 skipped, 0 xfail (84 s) |
| `tests/e2e/sidebar_load_e2e.py` | 67/67 |
| `tests/e2e/dashboard_e2e.py` | 199/199 |
| `tests/e2e/pipeline_e2e.py` | 86/86 |
| `tests/e2e/settings_e2e.py` | 47/47 |
| `tests/e2e/availability_e2e.py` | 70/70 |
| `tests/e2e/apply_sent_e2e.py` | 51/51 |
| `tests/e2e/letter_rewrite_e2e.py` | 35/35 (see the flake below) |
| `tests/e2e/quick_apply_e2e.py` | 124/124 |

Two notes from the baseline run, neither caused by this work:
- **Run the e2e suites with `PYTHONIOENCODING=utf-8`** when their output goes to a file. Without
  it `settings_e2e.py` crashes printing "≈" (Windows' cp1252 console encoding), which is not a
  test failure.
- **Known flake, `letter_rewrite_e2e.py` "card: your edits were replaced (D1)":** failed 1 of 3
  runs (34/35), passed the other two. Cause: `worker._rewrite_letter` commits the new letter,
  then clears `edited_content` in a second commit; the test waits for the new letter and reads
  the edits straight away, so it can read between the two commits. A race in the test, not the
  app. Left as is (the Test rules forbid changing it here). If it fails at Step 8, rerun it; a
  pass on rerun with only this check failing counts as the baseline.
