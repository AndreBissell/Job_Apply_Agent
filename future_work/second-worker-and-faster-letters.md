# A second worker for scoring, and faster letters

**Put off:** 2026-11-20, raised by the owner after the availability fast-mode checks.
**Waiting on:** nothing technical. Speed levers that change the letter (thinking level, engine)
belong in docs/agent-optimisation-plan.md and need its go-ahead per paid experiment.

## The problem

All LLM work runs on ONE worker thread (`app/api/worker.py`, `_bg_executor =
ThreadPoolExecutor(max_workers=1)`), and the idle loop checks letters first every pass
(`app/api/idle_loop.py`, `_letters_phase` before Phase 0/1). So:

- Scanning itself is not blocked: the scan's quick screen (`POST /jobs/{id}/quick-screen`,
  `app/api/job_actions.py`) runs on the request thread, not the worker.
- Extraction and matching of newly scanned jobs wait until no letter is due. A pipeline letter
  takes about 8 minutes (487 s mean over 17 in `dev-agent-v1`, docs/agent-optimisation-plan.md
  §2.1), so a queue of 5 letters holds new scores back for a long time.

## Part 1: a second worker for extraction + matching

A second single-thread executor that only runs Phase 0/1/1b (quick screen, extract, match; all
`small` tier), so new jobs get scored while a letter is written. Letters stay on `_bg_executor`.

Things to get right:
- **Rate limit.** `LLM_RPM` (default 8) is one throttle for all calls in `client.py`. Two workers
  share it, so on its own a second worker only splits 8 calls a minute two ways. Check the
  Vertex quota and raise `LLM_RPM` with it (or give each tier its own budget).
- **Database.** SQLite allows one writer at a time; keep each transaction short and expect
  "database is locked" retries under load. Postgres is fine.
- **One job, two workers.** A job must never be extracted/matched by both loops, or matched
  while `/regenerate` re-extracts it. Claim the job first (as `quick_screen` and
  `letter_bands.claim_job` already do).
- **Budget guard** reads `llm_usage`; two writers make the cap slightly leaky (two calls can
  pass the check together). `small` is never blocked, so this mostly doesn't matter.
- **Tests that patch `worker._bg_executor`** need the new executor patched too.

Cheaper alternative to try first: let the loop score up to N new jobs between letters instead
of draining the whole letter queue (a change to the loop's order, no concurrency).

## Part 2: faster letters (measured, dev-agent-v1, seconds per letter)

| Step | Seconds | Lever |
|---|---|---|
| `generate_letter` (Pro, thinking high) | 143 | Lower thinking to medium; 7,747 thinking tokens for 999 out |
| `orchestrate` (10 agent turns) | 128 | The workflow engine takes the same path 16/17 times without these turns |
| `check_claims` | 73 | Run it in parallel with `check_requirements` and `style_lint` (independent) |
| `revise_letter` | 53 | Fewer needless revisions (5 of 12 chased a gap; plan §2.3) |
| `match_profile` | 40 | |
| `check_requirements` | 12 | |

- Quickest win with no quality risk: run the three checks in parallel (each draft).
- `letter_engine=workflow` saves about 2 minutes a letter; the owner chose the agent on purpose
  (2026-10-03), so that is the owner's call.
- Raising `LLM_RPM` helps if calls are being throttled locally; check the logs first.
- Gemini Flex PayGo (plan §9.2) makes calls cheaper but SLOWER: background letters only.
- There is no request timeout; one `generate_letter` call took 926 s. Add one.
