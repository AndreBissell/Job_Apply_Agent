# Letter eval: agent-v1

Engine `agent`, strong model `gemini-3.1-pro-preview`, run 2026-10-03T22:01:11. 15 letters (0 failed). Rubric: evals/rubric.md. Grades: `grades-opus-v3.csv`.

| Score | Job | Words | em_dash_limit | generic_phrase_limit | within_word_limit | no_placeholders | has_sign_off | supported_musts_covered | no_unsupported_claims | specific_detail | would_send | Pass | Cost | Time |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 90 | Software Engineer Graduate (B&R Enclosures Pty Ltd) | 325 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ | ✗ | ✗ | $0.1626 | 155.1s |
| 68 | Junior-Intermediate Software Engineer (Sullivan Digital) | 302 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | $0.1356 | 148.0s |
| 88 | Software Engineering Graduate (Boeing Defence Australia) | 326 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | $0.1218 | 129.9s |
| 88 | Graduate / Intermediate .NET Developer (?) | 249 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | $0.1723 | 159.6s |
| 75 | Software Developer 6 Month Contract (?) | 280 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | ✗ | $0.1356 | 140.4s |
| 88 | Software Engineer (Jacobi) | 319 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | $0.3017 | 269.5s |
| 75 | Graduate LCNC Developer (KBR) | 283 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | $0.2143 | 214.4s |
| 75 | Software Engineer - Business Systems (Automation & Integrations) (Maxum Foods) | 324 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | $0.3322 | 327.2s |
| 90 | Junior .Net Developer / I.T Services / 5 Days Onsite (?) | 287 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ | ✗ | ✗ | $0.1686 | 210.7s |
| 75 | Software engineer (CloudPro Technologies) | 313 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | ✗ | $0.1320 | 137.6s |
| 60 | AI Developer (?) | 333 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | $0.2349 | 262.3s |
| 40 | Front End Developer - React (?) | 257 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | $0.1323 | 138.8s |
| 88 | Developer/Support (?) | 273 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | ✗ | $0.1591 | 154.7s |
| 85 | Frontend Developer (Moonward) | 334 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | $0.1513 | 156.3s |
| 88 | Graduate AI & Technology Developer (?) | 340 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ | ✗ | ✗ | $0.1186 | 129.9s |

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
| specific_detail | 12/15 |
| would_send | 0/15 |

## Comparison-table row (plan §9)

| Measure | Value |
|---|---|
| Rubric pass rate | 0/15 fully graded letters |
| Avg tokens / run | 29168 in, 18448 out+thinking |
| Avg cost / run | $0.1782 |
| Avg time / run | 182.3s |
| Runs that hit the budget cap | 0/15 |
| Runs where the user had to fix a factual error | 3 of 15 graded |

## style_lint (writer's check, not in the pass rate): 15/15 pass

Blocking issues plain, warnings in brackets. Sentence stdev: words; under 6 warns.

| Job | Pass | Sentence stdev | Issues (warnings) |
|---|---|---|---|
| Software Engineer Graduate | ✓ | 7.11 | - |
| Junior-Intermediate Software Engineer | ✓ | 8.33 | - |
| Software Engineering Graduate | ✓ | 8.89 | - |
| Graduate / Intermediate .NET Developer | ✓ | 8.56 | - |
| Software Developer 6 Month Contract | ✓ | 8.26 | - |
| Software Engineer | ✓ | 7.09 | - |
| Graduate LCNC Developer | ✓ | 8.09 | - |
| Software Engineer - Business Systems (Automation & | ✓ | 6.5 | - |
| Junior .Net Developer / I.T Services / 5 Days Onsi | ✓ | 7.21 | - |
| Software engineer | ✓ | 8.29 | - |
| AI Developer | ✓ | 8.84 | - |
| Front End Developer - React | ✓ | 6.73 | - |
| Developer/Support | ✓ | 7.39 | - |
| Frontend Developer | ✓ | 5.47 | (flat_rhythm) |
| Graduate AI & Technology Developer | ✓ | 7.19 | (gap_led) |

15 letter(s) graded by the blind Opus panel (evals/grading-standard.md); their reasons are in evals/runs/agent-v1/grades-opus-v3.csv (gitignored: they quote the letters).
