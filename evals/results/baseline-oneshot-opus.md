# Letter eval: baseline-oneshot

Engine `oneshot`, strong model `gemini-3.1-pro-preview`, run 2026-10-01T21:02:35. 15 letters (0 failed). Rubric: evals/rubric.md. Grades: `grades-opus.csv`.

| Score | Job | Words | em_dash_limit | generic_phrase_limit | within_word_limit | no_placeholders | has_sign_off | supported_musts_covered | no_unsupported_claims | specific_detail | would_send | Pass | Cost | Time |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 90 | Software Engineer Graduate (?) | 253 | ✓ | ✗ | ✓ | ✓ | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ | $0.0280 | 24.2s |
| 60 | Junior-Intermediate Software Engineer (?) | 274 | ✓ | ✗ | ✓ | ✓ | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ | $0.0286 | 20.4s |
| 88 | Software Engineering Graduate (?) | 260 | ✓ | ✗ | ✓ | ✓ | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ | $0.0245 | 16.5s |
| 60 | Graduate / Intermediate .NET Developer (?) | 254 | ✓ | ✗ | ✓ | ✓ | ✓ | ✗ | ✗ | ✓ | ✗ | ✗ | $0.0250 | 24.9s |
| 88 | Software Developer 6 Month Contract (?) | 220 | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ | ✗ | ✗ | ✗ | $0.0330 | 20.8s |
| 60 | Software Engineer (?) | 225 | ✓ | ✗ | ✓ | ✓ | ✓ | ✗ | ✗ | ✓ | ✗ | ✗ | $0.0193 | 13.1s |
| 60 | Graduate LCNC Developer (?) | 299 | ✓ | ✗ | ✓ | ✓ | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ | $0.0261 | 17.2s |
| 55 | Software Engineer - Business Systems (Automation & Integrations) (?) | 234 | ✓ | ✗ | ✓ | ✓ | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ | $0.0261 | 18.5s |
| 55 | Junior .Net Developer / I.T Services / 5 Days Onsite (?) | 252 | ✓ | ✗ | ✓ | ✓ | ✓ | ✗ | ✗ | ✓ | ✗ | ✗ | $0.0246 | 17.8s |
| 75 | Software engineer (CloudPro Technologies) | 336 | ✓ | ✗ | ✓ | ✓ | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ | $0.0241 | 26.0s |
| 60 | AI Developer (?) | 311 | ✓ | ✗ | ✓ | ✓ | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ | $0.0274 | 18.2s |
| 40 | Front End Developer - React (?) | 288 | ✓ | ✗ | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ | ✗ | ✗ | $0.0205 | 14.0s |
| 88 | Developer/Support (?) | 284 | ✗ | ✗ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | ✗ | $0.0219 | 14.5s |
| 85 | Frontend Developer (Moonward) | 268 | ✓ | ✗ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | ✗ | ✗ | $0.0260 | 16.9s |
| 88 | Graduate AI & Technology Developer (?) | 320 | ✓ | ✗ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | ✗ | ✗ | $0.0232 | 15.8s |

## Per-item pass rate

| Item | Passed |
|---|---|
| em_dash_limit | 14/15 |
| generic_phrase_limit | 1/15 |
| within_word_limit | 15/15 |
| no_placeholders | 15/15 |
| has_sign_off | 15/15 |
| supported_musts_covered | 4/15 |
| no_unsupported_claims | 2/15 |
| specific_detail | 4/15 |
| would_send | 0/15 |

## Comparison-table row (plan §9)

| Measure | Value |
|---|---|
| Rubric pass rate | 0/15 fully graded letters |
| Avg tokens / run | 867 in, 1958 out+thinking |
| Avg cost / run | $0.0252 |
| Avg time / run | 18.6s |
| Runs that hit the budget cap | n/a (`oneshot` doesn't stop early) |
| Runs where the user had to fix a factual error | 13 of 15 graded |

## style_lint (writer's check, not in the pass rate): 1/15 pass

Blocking issues plain, warnings in brackets. Sentence stdev: words; under 6 warns.

| Job | Pass | Sentence stdev | Issues (warnings) |
|---|---|---|---|
| Software Engineer Graduate | ✗ | 5.93 | banned_phrase, (flat_rhythm), (us_spelling) |
| Junior-Intermediate Software Engineer | ✗ | 5.1 | banned_phrase, (flat_rhythm), (us_spelling) |
| Software Engineering Graduate | ✗ | 6.47 | banned_phrase, (us_spelling) |
| Graduate / Intermediate .NET Developer | ✗ | 6.37 | banned_phrase, (us_spelling) |
| Software Developer 6 Month Contract | ✓ | 5.58 | (short), (flat_rhythm), (us_spelling) |
| Software Engineer | ✗ | 7.02 | banned_phrase, (short), (us_spelling) |
| Graduate LCNC Developer | ✗ | 7.32 | banned_phrase, (us_spelling) |
| Software Engineer - Business Systems (Automation & | ✗ | 5.25 | banned_phrase, (flat_rhythm) |
| Junior .Net Developer / I.T Services / 5 Days Onsi | ✗ | 8.76 | banned_phrase, (us_spelling) |
| Software engineer | ✗ | 3.95 | banned_phrase, (flat_rhythm), (us_spelling) |
| AI Developer | ✗ | 5.57 | banned_phrase, (flat_rhythm), (us_spelling) |
| Front End Developer - React | ✗ | 4.81 | banned_phrase, (flat_rhythm) |
| Developer/Support | ✗ | 7.82 | em_dash, banned_phrase |
| Frontend Developer | ✗ | 5.14 | banned_phrase, (flat_rhythm), (i_openers) |
| Graduate AI & Technology Developer | ✗ | 6.99 | banned_phrase, (us_spelling) |

15 letter(s) graded by the blind Opus panel (evals/grading-standard.md); their reasons are in evals/runs/baseline-oneshot/grades-opus.csv (gitignored: they quote the letters).
