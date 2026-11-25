# Compare c0 → c1

Written by `letter_lab.py compare --set v2 c0 c1` from `evals/results/v2/checkpoints.jsonl`. Don't edit it by hand: run the command again.

## 1. Headline

A = **c0** (baseline): run `dev-agent-v1`, judged `v1-dev`.  
B = **c1** (baseline, repeat of c0): run `dev-agent-v1`, judged `pts-cal-2` + `pts-check-1`.

| Measure | Ads | A (c0) | B (c1) | B − A | Noise | Ads up / same / down |
|---|---|---|---|---|---|---|
| Points (0-10) | all 17 | 7.13 | 7.32 | +0.19 | within noise (T 0.31)† | 5 / 11 / 1 |
|  | relevant 10 | 7.15 | 7.38 | +0.23 | within noise (T 0.41)† | 4 / 5 / 1 |
| $ / letter, today's prices | all 17 | $0.2581 | $0.2581 | +$0.0000 | no noise reference yet | 0 / 17 / 0 |
|  | relevant 10 | $0.2869 | $0.2869 | +$0.0000 | no noise reference yet | 0 / 10 / 0 |
| $ / letter, as recorded | all 17 | $0.2581 | $0.2581 | +$0.0000 | no noise reference yet | 0 / 17 / 0 |
|  | relevant 10 | $0.2869 | $0.2869 | +$0.0000 | no noise reference yet | 0 / 10 / 0 |
| Seconds / letter | all 17 | 487 | 487 | +0 | no noise reference yet | 0 / 17 / 0 |
|  | relevant 10 | 497 | 497 | +0 | no noise reference yet | 0 / 10 / 0 |

† provisional: judge noise only, writer noise not included (reference c1 vs c0, the same letters judged again).

## 2. What changed

| | A (c0) | B (c1) |
|---|---|---|
| Experiment | baseline | baseline |
| Created | 2026-11-25T18:52:45 | 2026-11-25T18:52:58 |
| Note | dev baseline, agent engine, judge v1 | repeat check of c0: same letters, judged again |
| Kept | yes | no |
| Verdict | none | none |
| Rule (written before the run) | — | — |
| Parent | — | c0 |
| Repeats (A/A of) | — | c0 |
| Replaces | — | — |

Settings (prices are $ per 1M tokens: input / output / cached input; *unknown* = the run did not save it and it was not typed in):

| Setting | A | B | Differs |
|---|---|---|---|
| run | `dev-agent-v1` | `dev-agent-v1` |  |
| engine | `agent` | `agent` |  |
| commit | `b423842` (typed in) | `b423842` (typed in) |  |
| judge | `v1`: `v1-dev` on `claude-cli` | `v1`: `pts-cal-2` + `pts-check-1` on `claude-cli` | yes |
| model, strong | `gemini-3.1-pro-preview` | `gemini-3.1-pro-preview` |  |
| model, mid | `gemini-3.8-flash` | `gemini-3.8-flash` |  |
| model, small | `gemini-3.1-flash-lite` | `gemini-3.1-flash-lite` |  |
| thinking, strong | `high` (typed in) | `high` (typed in) |  |
| thinking, mid | `medium` (typed in) | `medium` (typed in) |  |
| thinking, small | `low` (typed in) | `low` (typed in) |  |
| price `gemini-3.1-flash-lite` | $0.25 / $1.50 / $0.025 | $0.25 / $1.50 / $0.025 |  |
| price `gemini-3.1-pro-preview` | $2.00 / $12.00 / $0.200 | $2.00 / $12.00 / $0.200 |  |
| price `gemini-3.8-flash` | $0.75 / $3.75 / $0.075 | $0.75 / $3.75 / $0.075 |  |

Comparable (must match, and does): set `v2`, split `dev`, judge `v1`, scorer `0f939b5e88ac`, 1 judging(s) per letter.

Prices: every model both runs used costs the same today as when it ran.

## 3. Problems per letter

Each problem type the judge charges (`scripts/letter_points.py`): instances and points lost per letter. Better = fewer points lost on that letter.

### All paired ads (17)

| Problem | Instances A | B | B − A | Points lost A | B | B − A | Noise (points lost) | Letters better / same / worse |
|---|---|---|---|---|---|---|---|---|
| `untrue` | 0.47 | 0.35 | -0.12 | 0.47 | 0.35 | -0.12 | within noise (T 0.16)† | 2 / 15 / 0 |
| `missed_row` | 0.88 | 0.82 | -0.06 | 0.79 | 0.75 | -0.04 | within noise (T 0.27)† | 2 / 14 / 1 |
| `mix_up` | 0.53 | 0.47 | -0.06 | 0.32 | 0.29 | -0.03 | within noise (T 0.06)† | 1 / 16 / 0 |
| `weak_example` | 0.47 | 0.47 | +0.00 | 0.35 | 0.35 | +0.00 | no noise reference yet (c1 vs c0 never moved) | 0 / 17 / 0 |
| `no_personal_reason` | 0.65 | 0.65 | +0.00 | 0.32 | 0.32 | +0.00 | no noise reference yet (c1 vs c0 never moved) | 0 / 17 / 0 |
| `too_long` | 0.06 | 0.06 | +0.00 | 0.03 | 0.03 | +0.00 | no noise reference yet (c1 vs c0 never moved) | 0 / 17 / 0 |
| `generic` | 0.94 | 0.94 | +0.00 | 0.30 | 0.30 | +0.00 | no noise reference yet (c1 vs c0 never moved) | 0 / 17 / 0 |
| `stock_phrasing` | 0.76 | 0.76 | +0.00 | 0.28 | 0.28 | +0.00 | no noise reference yet (c1 vs c0 never moved) | 0 / 17 / 0 |

† provisional: judge noise only, writer noise not included (reference c1 vs c0, the same letters judged again).

### Relevant ads (10)

| Problem | Instances A | B | B − A | Points lost A | B | B − A | Noise (points lost) | Letters better / same / worse |
|---|---|---|---|---|---|---|---|---|
| `untrue` | 0.30 | 0.20 | -0.10 | 0.30 | 0.20 | -0.10 | within noise (T 0.21)† | 1 / 9 / 0 |
| `missed_row` | 1.10 | 1.00 | -0.10 | 0.97 | 0.90 | -0.07 | within noise (T 0.35)† | 2 / 7 / 1 |
| `mix_up` | 0.60 | 0.50 | -0.10 | 0.30 | 0.25 | -0.05 | within noise (T 0.08)† | 1 / 9 / 0 |
| `weak_example` | 0.40 | 0.40 | +0.00 | 0.30 | 0.30 | +0.00 | no noise reference yet (c1 vs c0 never moved) | 0 / 10 / 0 |
| `no_personal_reason` | 0.60 | 0.60 | +0.00 | 0.30 | 0.30 | +0.00 | no noise reference yet (c1 vs c0 never moved) | 0 / 10 / 0 |
| `too_long` | 0.10 | 0.10 | +0.00 | 0.05 | 0.05 | +0.00 | no noise reference yet (c1 vs c0 never moved) | 0 / 10 / 0 |
| `generic` | 1.00 | 1.00 | +0.00 | 0.30 | 0.30 | +0.00 | no noise reference yet (c1 vs c0 never moved) | 0 / 10 / 0 |
| `stock_phrasing` | 0.80 | 0.80 | +0.00 | 0.33 | 0.33 | +0.00 | no noise reference yet (c1 vs c0 never moved) | 0 / 10 / 0 |

† provisional: judge noise only, writer noise not included (reference c1 vs c0, the same letters judged again).

## 4. Cost and time by step

Per `llm_usage` task, per letter over the 17 paired letters, at today's prices. Seconds are the time inside that step's model calls; a letter's wall-clock time (headline) also counts code and waits.

| Step | Calls / letter A → B | $ A | $ B | $ B − A | Seconds A | Seconds B | Seconds B − A |
|---|---|---|---|---|---|---|---|
| `generate_letter` | 1.0 → 1.0 | $0.1178 | $0.1178 | +$0.0000 | 143 | 143 | +0 |
| `revise_letter` | 0.7 → 0.7 | $0.0795 | $0.0795 | +$0.0000 | 53 | 53 | +0 |
| `check_claims` | 1.6 → 1.6 | $0.0307 | $0.0307 | +$0.0000 | 73 | 73 | +0 |
| `orchestrate` | 10.0 → 10.0 | $0.0159 | $0.0159 | +$0.0000 | 128 | 128 | +0 |
| `match_profile` | 1.0 → 1.0 | $0.0123 | $0.0123 | +$0.0000 | 40 | 40 | +0 |
| `check_requirements` | 1.6 → 1.6 | $0.0019 | $0.0019 | +$0.0000 | 12 | 12 | +0 |
| **all steps** | | $0.2581 | $0.2581 | +$0.0000 | 448 | 448 | +0 |

## 5. Per letter

One row per paired ad, the letter that fell most first. Points in brackets are each judging's score when a checkpoint averages several. $ at today's prices.

| Ad | Relevant | Drafts A → B | Points A → B | Δ | $ A → B | Seconds A → B | What changed |
|---|---|---|---|---|---|---|---|
| `seek-94890841` | yes | 2 → 2 | 6.70 → 5.20 | -1.50 | $0.3208 → $0.3208 | 947 → 947 | `missed_row` +1; missed rows +R5 |
| `seek-94904560` | yes | 1 → 1 | 8.70 → 8.70 | +0.00 | $0.1443 → $0.1443 | 857 → 857 | — |
| `seek-94932262` | yes | 2 → 2 | 8.70 → 8.70 | +0.00 | $0.2905 → $0.2905 | 398 → 398 | — |
| `seek-95085104` | no | 1 → 1 | 4.20 → 4.20 | +0.00 | $0.1986 → $0.1986 | 214 → 214 | — |
| `seek-95140202` | yes | 1 → 1 | 5.70 → 5.70 | +0.00 | $0.2282 → $0.2282 | 456 → 456 | — |
| `seek-95158567` | yes | 1 → 1 | 7.20 → 7.20 | +0.00 | $0.1609 → $0.1609 | 176 → 176 | — |
| `seek-95191929` | no | 1 → 1 | 8.65 → 8.65 | +0.00 | $0.1650 → $0.1650 | 200 → 200 | — |
| `seek-95219353` | yes | 3 → 3 | 8.45 → 8.45 | +0.00 | $0.4520 → $0.4520 | 600 → 600 | — |
| `seek-95220494` | no | 3 → 3 | 9.00 → 9.00 | +0.00 | $0.4158 → $0.4158 | 487 → 487 | — |
| `seek-95220951` | no | 2 → 2 | 6.95 → 6.95 | +0.00 | $0.2415 → $0.2415 | 391 → 391 | — |
| `seek-95256463` | no | 2 → 2 | 7.45 → 7.45 | +0.00 | $0.2570 → $0.2570 | 832 → 832 | — |
| `seek-95364016` | no | 1 → 1 | 7.20 → 7.20 | +0.00 | $0.1431 → $0.1431 | 1016 → 1016 | — |
| `seek-95100426` | yes | 2 → 2 | 8.45 → 8.95 | +0.50 | $0.2980 → $0.2980 | 316 → 316 | `mix_up` -1 |
| `seek-95110025` | yes | 2 → 2 | 7.20 → 7.95 | +0.75 | $0.3327 → $0.3327 | 416 → 416 | `missed_row` -1; missed rows -R6 |
| `seek-95205206` | no | 1 → 1 | 6.20 → 7.20 | +1.00 | $0.0968 → $0.0968 | 171 → 171 | `untrue` -1 |
| `seek-95260505` | yes | 3 → 3 | 5.70 → 6.70 | +1.00 | $0.4705 → $0.4705 | 602 → 602 | `untrue` -1 |
| `seek-95084704` | yes | 1 → 1 | 4.70 → 6.20 | +1.50 | $0.1712 → $0.1712 | 204 → 204 | `missed_row` -1; missed rows -R4 |

## 6. Left out

- Only in A (c0), not paired: none
- Only in B (c1), not paired: none
- No draft on one side (a failed letter), not paired: none
- Relevant on one side only (in *all*, not in *relevant*): none
- Cost at today's prices unknown (a model today's table doesn't list), left out of that row: none

## 7. How to read this

- **Noise threshold T = 2 × s ÷ √n.** s is how much each letter's number moved between two checkpoints that should match (a repeat check, A/A); n is the number of paired ads in the row. A difference bigger than T is *beyond noise*; otherwise *within noise*.
- Judge measures (points, problems): repeat check **c1 vs c0** (judge-only: the same letters judged again, so writer noise is not in it; 17 paired letters).
- Writer measures (cost, time, drafts): no noise reference yet: that needs a writer + judge repeat (new letters written with the same settings), such as E3's control arm.
- A measure the repeat check never moved (s = 0) shows *no noise reference yet*, never T = 0.
- **No conclusion from one letter.** The per-letter table says where to look; the means decide.
- Cost at today's prices is recomputed from each letter's tokens with `client.PRICES` (thinking billed as output, cached input at the cached price); *as recorded* is what was logged at the time.
