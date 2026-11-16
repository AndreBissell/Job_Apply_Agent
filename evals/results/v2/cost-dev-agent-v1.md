# Where the money and time go

Per task, averaged per letter, from `llm_usage` (every Gemini call: tokens, estimated cost, call time) and `letter_run_steps` (each tool's time, including code-only work). Cost is the estimate from `client.PRICES`; Cloud Billing is the authority. Thinking tokens bill at the output price.

## `dev-agent-v1` (17 letters)

| Task | Model | Calls / letter | Input tok / call | Output tok / call | Thinking tok / call | $ / letter | Share of cost | Seconds / call | Seconds / letter |
|---|---|---|---|---|---|---|---|---|---|
| generate_letter | gemini-3.1-pro-preview (strong) | 1.0 | 6,829 | 999 | 7,747 | $0.1178 | 46% | 142.6 | 142.6 |
| revise_letter | gemini-3.1-pro-preview (strong) | 0.7 | 7,788 | 1,085 | 7,056 | $0.0795 | 31% | 75.5 | 53.3 |
| check_claims | gemini-3.8-flash (mid) | 1.6 | 4,423 | 1,656 | 2,423 | $0.0307 | 12% | 44.1 | 72.6 |
| orchestrate | gemini-3.8-flash (mid) | 10.0 | 1,546 | 9 | 105 | $0.0159 | 6% | 12.8 | 128.3 |
| match_profile | gemini-3.8-flash (mid) | 1.0 | 2,487 | 759 | 2,026 | $0.0123 | 5% | 39.6 | 39.6 |
| check_requirements | gemini-3.1-flash-lite (small) | 1.6 | 926 | 450 | 181 | $0.0019 | 1% | 7.5 | 11.9 |

| Per letter | |
|---|---|
| Total cost | $0.2581 |
| Wall-clock time | 487s |
| Inside Gemini calls | 448s (92%) |
| Inside tool steps (LLM + code) | 320s; style_lint (code only) 3 ms per call |
| Orchestrator calls | 128s |
| Everything else (DB writes, throttle waits) | 39s |

