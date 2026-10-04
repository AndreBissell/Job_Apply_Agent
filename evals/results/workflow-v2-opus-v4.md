# Letter eval: workflow-v2

Engine `workflow`, strong model `gemini-3.1-pro-preview`, run 2026-10-03T18:50:46. 15 letters (0 failed). Rubric: evals/rubric.md. Grades: `grades-opus-v4.csv`.

| Score | Job | Words | em_dash_limit | generic_phrase_limit | within_word_limit | no_placeholders | has_sign_off | supported_musts_covered | no_unsupported_claims | specific_detail | would_send | Pass | Cost | Time |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 90 | Software Engineer Graduate (B&R Enclosures Pty Ltd) | 311 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | $0.2632 | 189.3s |
| 68 | Junior-Intermediate Software Engineer (Sullivan Digital) | 331 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | $0.2800 | 193.2s |
| 88 | Software Engineering Graduate (Boeing Defence Australia) | 316 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ | ✗ | ✗ | $0.1280 | 152.9s |
| 88 | Graduate / Intermediate .NET Developer (?) | 282 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | $0.1211 | 83.2s |
| 75 | Software Developer 6 Month Contract (?) | 260 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | ✗ | $0.1583 | 110.9s |
| 88 | Software Engineer (Jacobi) | 322 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | $0.3050 | 201.3s |
| 75 | Graduate LCNC Developer (KBR) | 320 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | $0.1809 | 122.3s |
| 75 | Software Engineer - Business Systems (Automation & Integrations) (Maxum Foods) | 310 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | $0.2571 | 181.9s |
| 90 | Junior .Net Developer / I.T Services / 5 Days Onsite (?) | 314 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ | ✗ | ✗ | $0.2269 | 179.8s |
| 75 | Software engineer (CloudPro Technologies) | 317 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | ✗ | $0.1175 | 79.6s |
| 60 | AI Developer (?) | 318 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | $0.1534 | 106.4s |
| 40 | Front End Developer - React (?) | 249 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | $0.1359 | 105.1s |
| 88 | Developer/Support (?) | 324 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | $0.1384 | 98.2s |
| 85 | Frontend Developer (Moonward) | 292 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ | ✗ | ✗ | $0.1310 | 95.9s |
| 88 | Graduate AI & Technology Developer (?) | 319 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ | ✗ | ✗ | $0.1576 | 275.7s |

## Per-item pass rate

| Item | Passed |
|---|---|
| em_dash_limit | 15/15 |
| generic_phrase_limit | 15/15 |
| within_word_limit | 15/15 |
| no_placeholders | 15/15 |
| has_sign_off | 15/15 |
| supported_musts_covered | 15/15 |
| no_unsupported_claims | 11/15 |
| specific_detail | 13/15 |
| would_send | 0/15 |

## Comparison-table row (plan §9)

| Measure | Value |
|---|---|
| Rubric pass rate | 0/15 fully graded letters |
| Avg tokens / run | 17149 in, 19855 out+thinking |
| Avg cost / run | $0.1836 |
| Avg time / run | 145.0s |
| Runs that hit the budget cap | 0/15 |
| Runs where the user had to fix a factual error | 4 of 15 graded |

## style_lint (writer's check, not in the pass rate): 15/15 pass

Blocking issues plain, warnings in brackets. Sentence stdev: words; under 6 warns.

| Job | Pass | Sentence stdev | Issues (warnings) |
|---|---|---|---|
| Software Engineer Graduate | ✓ | 6.91 | (stock_close), (stock_bridge) |
| Junior-Intermediate Software Engineer | ✓ | 7.1 | (stock_close) |
| Software Engineering Graduate | ✓ | 8.33 | (stock_close), (stock_bridge) |
| Graduate / Intermediate .NET Developer | ✓ | 8.15 | (stock_close) |
| Software Developer 6 Month Contract | ✓ | 6.61 | (stock_bridge) |
| Software Engineer | ✓ | 5.43 | (flat_rhythm), (stock_close), (stock_bridge) |
| Graduate LCNC Developer | ✓ | 6.16 | (stock_close), (stock_bridge) |
| Software Engineer - Business Systems (Automation & | ✓ | 7.75 | (stock_close), (stock_close), (stock_bridge) |
| Junior .Net Developer / I.T Services / 5 Days Onsi | ✓ | 9.31 | (stock_close), (stock_bridge) |
| Software engineer | ✓ | 6.86 | (stock_close), (stock_bridge) |
| AI Developer | ✓ | 7.7 | (stock_close) |
| Front End Developer - React | ✓ | 6.82 | (stock_close) |
| Developer/Support | ✓ | 7.88 | (stock_close), (stock_bridge) |
| Frontend Developer | ✓ | 6.06 | (stock_close), (stock_bridge) |
| Graduate AI & Technology Developer | ✓ | 7.89 | - |

15 letter(s) graded by the blind Opus panel (evals/grading-standard.md); their reasons are in evals/runs/workflow-v2/grades-opus-v4.csv (gitignored: they quote the letters).
