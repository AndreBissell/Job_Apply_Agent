# Faster letters

**Put off:** 2026-11-20, raised by the owner after the availability fast-mode checks.
**Waiting on:** nothing technical. Speed levers that change the letter (thinking level, engine)
belong in docs/agent-optimisation-plan.md and need its go-ahead per paid experiment.

Part 1 of this file (a second worker so new jobs are scored while a letter is written) was built
2026-11-23: docs/second-worker-plan.md. What is left is the letter's own speed: a pipeline letter
takes about 8 minutes (487 s mean over 17 in `dev-agent-v1`, docs/agent-optimisation-plan.md §2.1).

## Where the time goes (measured, dev-agent-v1, seconds per letter)

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
