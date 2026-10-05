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

## `agent-v1` (15 letters)

| Task | Model | Calls / letter | Input tok / call | Output tok / call | Thinking tok / call | $ / letter | Share of cost | Seconds / call | Seconds / letter |
|---|---|---|---|---|---|---|---|---|---|
| generate_letter | gemini-3.1-pro-preview (strong) | 1.0 | 5,609 | 1,052 | 6,468 | $0.1015 | 57% | 53.7 | 53.7 |
| revise_letter | gemini-3.1-pro-preview (strong) | 0.4 | 6,355 | 1,097 | 4,168 | $0.0304 | 17% | 36.1 | 14.5 |
| check_claims | gemini-3.8-flash (mid) | 1.4 | 3,910 | 1,707 | 1,677 | $0.0219 | 12% | 24.7 | 34.5 |
| orchestrate | gemini-3.8-flash (mid) | 8.6 | 1,419 | 9 | 110 | $0.0130 | 7% | 5.5 | 47.1 |
| match_profile | gemini-3.8-flash (mid) | 1.0 | 2,070 | 808 | 1,414 | $0.0099 | 6% | 20.5 | 20.5 |
| check_requirements | gemini-3.1-flash-lite (small) | 1.4 | 905 | 482 | 106 | $0.0016 | 1% | 8.2 | 11.5 |

| Per letter | |
|---|---|
| Total cost | $0.1782 |
| Wall-clock time | 182s |
| Inside Gemini calls | 182s (100%) |
| Inside tool steps (LLM + code) | 135s; style_lint (code only) 1 ms per call |
| Orchestrator calls | 47s |
| Everything else (DB writes, throttle waits) | 0s |

