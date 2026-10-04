# Where the money and time go

Per task, averaged per letter, from `llm_usage` (every Gemini call: tokens, estimated cost, call time) and `letter_run_steps` (each tool's time, including code-only work). Cost is the estimate from `client.PRICES`; Cloud Billing is the authority. Thinking tokens bill at the output price.

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

## `agent-v2-side` (15 letters)

| Task | Model | Calls / letter | Input tok / call | Output tok / call | Thinking tok / call | $ / letter | Share of cost | Seconds / call | Seconds / letter |
|---|---|---|---|---|---|---|---|---|---|
| generate_letter | gemini-3.1-pro-preview (strong) | 1.0 | 6,244 | 939 | 8,532 | $0.1262 | 55% | 73.7 | 73.7 |
| revise_letter | gemini-3.1-pro-preview (strong) | 0.3 | 7,044 | 1,031 | 6,402 | $0.0344 | 15% | 48.7 | 16.2 |
| check_claims | gemini-3.8-flash (mid) | 1.3 | 3,869 | 1,544 | 2,327 | $0.0232 | 10% | 26.6 | 35.4 |
| orchestrate | gemini-3.8-flash (mid) | 9.3 | 2,027 | 10 | 96 | $0.0179 | 8% | 5.4 | 50.3 |
| match_profile | gemini-3.8-flash (mid) | 1.0 | 2,073 | 793 | 2,168 | $0.0127 | 6% | 22.9 | 22.9 |
| suggest_resume_tweaks | gemini-3.8-flash (mid) | 1.0 | 3,878 | 859 | 1,465 | $0.0116 | 5% | 19.3 | 19.3 |
| check_requirements | gemini-3.1-flash-lite (small) | 1.3 | 903 | 433 | 104 | $0.0014 | 1% | 7.8 | 10.3 |

| Per letter | |
|---|---|
| Total cost | $0.2274 |
| Wall-clock time | 229s |
| Inside Gemini calls | 228s (100%) |
| Inside tool steps (LLM + code) | 178s; style_lint (code only) 4 ms per call |
| Orchestrator calls | 50s |
| Everything else (DB writes, throttle waits) | 1s |

