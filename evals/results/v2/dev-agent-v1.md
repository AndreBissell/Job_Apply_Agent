# Letter eval (v2): dev-agent-v1

Engine `agent`, strong model `gemini-3.1-pro-preview`, run 2026-10-26T17:29:41. Split `dev`. 17 letters (0 failed). Judge: none. Owner grades: 17. Metrics: docs/letter-metrics-plan.md §3.

Relevant = the owner would apply (Would apply = Y). Send-readiness is the owner's grade where there is one, else the judge's. Rates are shares of letters; M1, honest coverage and the checklist are per-letter means.

## Headline

| Measure | Relevant ads (10) | All ads (17) |
|---|---|---|
| 1. Send-readiness, mean 1-4 | 2.30 (owner 10, judge 0) | 2.35 (owner 17, judge 0) |
| &nbsp;&nbsp;&nbsp;send-ready rate (3 or 4) | 30% | 35% |
| 2. Fabrication-free rate | — of 0 judged; owner: 10 of 10 with nothing untrue | — of 0 judged; owner: 16 of 17 with nothing untrue |
| 3. Must-cover recall (M1) | — | — |
| 4. Honest coverage | — | — |
| 5. Recruiter checklist (custom 2:1 generic) | — (custom —, generic —) | — (custom —, generic —) |
| 6. Cost per letter | $0.287 | $0.258 |

## Diagnostics (explain a headline move; never optimised alone)

| Measure | Relevant ads (10) | All ads (17) |
|---|---|---|
| M2 weighted backed coverage | — | — |
| M3 best-evidence rate | — | — |
| M4 fabrications per letter (gap claims) | — (—) | — (—) |
| M4 stretches per letter | — | — |
| M4 claim precision | — | — |
| D2 checklist | — | — |
| D3 checklist | — | — |
| D4 checklist | — | — |
| D5 checklist | — | — |
| D6 checklist | — | — |
| D7 checklist | — | — |
| Code gates (5 rubric items) passed | 10/10 | 17/17 |
| G1 no stuffing passed | 7/10 | 14/17 |
| C3 ad echo (6+ word runs) | 3% | 3% |
| C3 profile copy (8+ word runs) | 15% | 14% |
| C3 writing-sample copy (6+ word runs) | 3% | 3% |
| C5 style warnings per letter | 0.1 | 0.3 |
| C5b stock-phrase hits per letter | 11.1 (8/10 over the owner's sounds_ai limit) | 10.8 (13/17 over the owner's sounds_ai limit) |
| C5c motivation naming the candidate's own history | 7/10 | 10/17 |
| C6 seconds per letter | 497 | 487 |
| C7 near-templates (under 10%) | 0 | 0 |

C4 cross-letter repetition: **98** phrase(s) of 4-6 words in 3+ letters (listed in the detail file).

## Per letter

| Ad | Scen | Rel | SR | By | Fab | M1 | M2 | Honest | M3 | Checklist | Gates | G1 | Echo | Copy | Sample | Warn | Stock | C7 | Cost |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `seek-94890841` | 4 | Y | 2 | owner | — | — | — | — | — | — | ✓ | ✓ | 0% | 12% | 2% | 1 | 14 | 81% | $0.321 |
| `seek-94904560` | 3 | Y | 4 | owner | — | — | — | — | — | — | ✓ | ✓ | 0% | 10% | 2% | 0 | 8 | 82% | $0.144 |
| `seek-94932262` | 6 | Y | 2 | owner | — | — | — | — | — | — | ✓ | ✓ | 4% | 13% | 0% | 0 | 13 | 78% | $0.291 |
| `seek-95084704` | 2 | Y | 2 | owner | — | — | — | — | — | — | ✓ | ✗ | 9% | 3% | 3% | 0 | 10 | 85% | $0.171 |
| `seek-95100426` | 6 | Y | 3 | owner | — | — | — | — | — | — | ✓ | ✗ | 9% | 16% | 4% | 0 | 11 | 86% | $0.298 |
| `seek-95110025` | 1 | Y | 1 | owner | — | — | — | — | — | — | ✓ | ✓ | 0% | 10% | 0% | 0 | 12 | 78% | $0.333 |
| `seek-95140202` | 1 | Y | 2 | owner | — | — | — | — | — | — | ✓ | ✓ | 0% | 23% | 4% | 0 | 13 | 79% | $0.228 |
| `seek-95158567` | 2 | Y | 2 | owner | — | — | — | — | — | — | ✓ | ✓ | 5% | 30% | 2% | 0 | 12 | 81% | $0.161 |
| `seek-95219353` | 3 | Y | 3 | owner | — | — | — | — | — | — | ✓ | ✓ | 0% | 8% | 8% | 0 | 8 | 87% | $0.452 |
| `seek-95260505` | 1 | Y | 2 | owner | — | — | — | — | — | — | ✓ | ✗ | 2% | 22% | 6% | 0 | 10 | 77% | $0.470 |
| `seek-95085104` | 4 | N | 2 | owner | — | — | — | — | — | — | ✓ | ✓ | 4% | 3% | 0% | 1 | 11 | 86% | $0.199 |
| `seek-95191929` | 7 | N | 2 | owner | — | — | — | — | — | — | ✓ | ✓ | 9% | 22% | 9% | 2 | 11 | 84% | $0.165 |
| `seek-95205206` | 1 | N | 2 | owner | — | — | — | — | — | — | ✓ | ✓ | 4% | 19% | 0% | 0 | 8 | 85% | $0.097 |
| `seek-95220494` | 3 | N | 3 | owner | — | — | — | — | — | — | ✓ | ✓ | 2% | 29% | 4% | 0 | 16 | 77% | $0.416 |
| `seek-95220951` | 7 | N | 2 | owner | — | — | — | — | — | — | ✓ | ✓ | 0% | 4% | 2% | 1 | 11 | 83% | $0.242 |
| `seek-95256463` | 3 | N | 3 | owner | — | — | — | — | — | — | ✓ | ✓ | 4% | 12% | 2% | 0 | 10 | 85% | $0.257 |
| `seek-95364016` | 1 | N | 3 | owner | — | — | — | — | — | — | ✓ | ✓ | 2% | 5% | 0% | 0 | 5 | 78% | $0.143 |

Phrases, lists and warnings that quote the letters: `evals\work\v2\runs\dev-agent-v1\report-detail.md` (gitignored).
