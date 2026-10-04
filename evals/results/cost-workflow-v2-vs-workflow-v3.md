# Where the money and time go

Per task, averaged per letter, from `llm_usage` (every Gemini call: tokens, estimated cost, call time) and `letter_run_steps` (each tool's time, including code-only work). Cost is the estimate from `client.PRICES`; Cloud Billing is the authority. Thinking tokens bill at the output price.

## `workflow-v2` (15 letters)

| Task | Model | Calls / letter | Input tok / call | Output tok / call | Thinking tok / call | $ / letter | Share of cost | Seconds / call | Seconds / letter |
|---|---|---|---|---|---|---|---|---|---|
| generate_letter | gemini-3.1-pro-preview (strong) | 1.0 | 5,631 | 1,057 | 7,367 | $0.1124 | 61% | 67.6 | 67.6 |
| revise_letter | gemini-3.1-pro-preview (strong) | 0.4 | 6,419 | 1,140 | 4,672 | $0.0330 | 18% | 40.9 | 16.4 |
| check_claims | gemini-3.8-flash (mid) | 1.4 | 3,997 | 1,787 | 2,035 | $0.0243 | 13% | 23.5 | 32.9 |
| match_profile | gemini-3.8-flash (mid) | 1.0 | 2,070 | 817 | 2,052 | $0.0123 | 7% | 23.0 | 23.0 |
| check_requirements | gemini-3.1-flash-lite (small) | 1.4 | 917 | 481 | 149 | $0.0016 | 1% | 3.4 | 4.7 |

| Per letter | |
|---|---|
| Total cost | $0.1836 |
| Wall-clock time | 145s |
| Inside Gemini calls | 145s (100%) |
| Inside tool steps (LLM + code) | 145s; style_lint (code only) 3 ms per call |
| Orchestrator calls | none (fixed order) |
| Everything else (DB writes, throttle waits) | 0s |

## `workflow-v3` (15 letters)

| Task | Model | Calls / letter | Input tok / call | Output tok / call | Thinking tok / call | $ / letter | Share of cost | Seconds / call | Seconds / letter |
|---|---|---|---|---|---|---|---|---|---|
| generate_letter | gemini-3.1-pro-preview (strong) | 1.0 | 6,260 | 1,022 | 8,556 | $0.1275 | 57% | 63.5 | 63.5 |
| revise_letter | gemini-3.1-pro-preview (strong) | 0.5 | 7,200 | 1,063 | 5,243 | $0.0416 | 19% | 61.0 | 28.5 |
| check_claims | gemini-3.8-flash (mid) | 1.5 | 3,829 | 1,556 | 3,404 | $0.0315 | 14% | 28.0 | 41.1 |
| match_profile | gemini-3.8-flash (mid) | 1.0 | 2,073 | 809 | 1,774 | $0.0112 | 5% | 16.1 | 16.1 |
| analyze_job | gemini-3.8-flash (mid) | 1.0 | 2,507 | 1,135 | 930 | $0.0096 | 4% | 16.8 | 16.8 |
| check_requirements | gemini-3.1-flash-lite (small) | 1.5 | 919 | 491 | 105 | $0.0017 | 1% | 3.0 | 4.5 |

| Per letter | |
|---|---|
| Total cost | $0.2230 |
| Wall-clock time | 171s |
| Inside Gemini calls | 170s (100%) |
| Inside tool steps (LLM + code) | 171s; style_lint (code only) 1 ms per call |
| Orchestrator calls | none (fixed order) |
| Everything else (DB writes, throttle waits) | 0s |

