# Letter eval: workflow-v3

Engine `workflow`, strong model `gemini-3.1-pro-preview`, run 2026-10-04T10:14:19. 15 letters (0 failed). Rubric: evals/rubric.md. Grades: `grades-opus-v4.csv`.

| Score | Job | Words | em_dash_limit | generic_phrase_limit | within_word_limit | no_placeholders | has_sign_off | supported_musts_covered | no_unsupported_claims | specific_detail | would_send | Pass | Cost | Time |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 90 | Software Engineer Graduate (B&R Enclosures Pty Ltd) | 301 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | $0.2524 | 167.7s |
| 68 | Junior-Intermediate Software Engineer (Sullivan Digital) | 335 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | $0.1930 | 127.3s |
| 88 | Software Engineering Graduate (Boeing Defence Australia) | 312 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ | ✗ | ✗ | $0.1177 | 83.9s |
| 88 | Graduate / Intermediate .NET Developer (?) | 309 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | $0.1369 | 101.6s |
| 75 | Software Developer 6 Month Contract (?) | 325 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | ✗ | $0.2548 | 168.8s |
| 88 | Software Engineer (Jacobi) | 326 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | $0.2072 | 137.7s |
| 75 | Graduate LCNC Developer (KBR) | 323 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | $0.2189 | 149.0s |
| 75 | Software Engineer - Business Systems (Automation & Integrations) (Maxum Foods) | 337 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ | ✗ | ✗ | $0.2104 | 153.0s |
| 90 | Junior .Net Developer / I.T Services / 5 Days Onsite (?) | 310 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | $0.2118 | 150.9s |
| 75 | Software engineer (CloudPro Technologies) | 291 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | ✗ | $0.3374 | 259.2s |
| 60 | AI Developer (?) | 310 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | $0.2831 | 189.1s |
| 40 | Front End Developer - React (?) | 276 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ | ✗ | ✗ | $0.2063 | 219.5s |
| 88 | Developer/Support (?) | 313 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | $0.1497 | 117.9s |
| 85 | Frontend Developer (Moonward) | 310 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | $0.1336 | 104.2s |
| 88 | Graduate AI & Technology Developer (?) | 315 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | $0.4326 | 433.4s |

## Per-item pass rate

| Item | Passed |
|---|---|
| em_dash_limit | 15/15 |
| generic_phrase_limit | 15/15 |
| within_word_limit | 15/15 |
| no_placeholders | 15/15 |
| has_sign_off | 15/15 |
| supported_musts_covered | 15/15 |
| no_unsupported_claims | 12/15 |
| specific_detail | 13/15 |
| would_send | 0/15 |

## Comparison-table row (plan §9)

| Measure | Value |
|---|---|
| Rubric pass rate | 0/15 fully graded letters |
| Avg tokens / run | 21167 in, 25323 out+thinking |
| Avg cost / run | $0.2230 |
| Avg time / run | 170.9s |
| Runs that hit the budget cap | 0/15 |
| Runs where the user had to fix a factual error | 3 of 15 graded |

## style_lint (writer's check, not in the pass rate): 15/15 pass

Blocking issues plain, warnings in brackets. Sentence stdev: words; under 6 warns.

| Job | Pass | Sentence stdev | Issues (warnings) |
|---|---|---|---|
| Software Engineer Graduate | ✓ | 4.32 | (flat_rhythm) |
| Junior-Intermediate Software Engineer | ✓ | 8.69 | - |
| Software Engineering Graduate | ✓ | 6.56 | - |
| Graduate / Intermediate .NET Developer | ✓ | 6.85 | - |
| Software Developer 6 Month Contract | ✓ | 6.68 | - |
| Software Engineer | ✓ | 4.46 | (flat_rhythm) |
| Graduate LCNC Developer | ✓ | 6.0 | (flat_rhythm) |
| Software Engineer - Business Systems (Automation & | ✓ | 7.11 | - |
| Junior .Net Developer / I.T Services / 5 Days Onsi | ✓ | 9.19 | - |
| Software engineer | ✓ | 6.99 | - |
| AI Developer | ✓ | 5.94 | (flat_rhythm) |
| Front End Developer - React | ✓ | 7.99 | - |
| Developer/Support | ✓ | 9.23 | - |
| Frontend Developer | ✓ | 5.95 | (flat_rhythm) |
| Graduate AI & Technology Developer | ✓ | 6.62 | - |

15 letter(s) graded by the blind Opus panel (evals/grading-standard.md); their reasons are in evals/runs/workflow-v3/grades-opus-v4.csv (gitignored: they quote the letters).
