# Letter eval: styled-oneshot

Engine `oneshot-styled`, strong model `gemini-3.1-pro-preview`, run 2026-10-03T11:45:59. 9 letters (0 failed). Rubric: evals/rubric.md.

| Score | Job | Words | em_dash_limit | generic_phrase_limit | within_word_limit | no_placeholders | has_sign_off | supported_musts_covered | no_unsupported_claims | specific_detail | would_send | Pass | Cost | Time |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 90 | Software Engineer Graduate (B&R Enclosures Pty Ltd) | 290 | ✓ | ✓ | ✓ | ✓ | ✓ | ? | ? | ? | ? | ? | $0.0332 | 25.1s |
| 60 | Junior-Intermediate Software Engineer (Sullivan Digital) | 304 | ✓ | ✓ | ✓ | ✓ | ✓ | ? | ? | ? | ? | ? | $0.0375 | 19.6s |
| 88 | Software Engineering Graduate (Boeing Defence Australia) | 323 | ✓ | ✓ | ✓ | ✓ | ✓ | ? | ? | ? | ? | ? | $0.0470 | 25.2s |
| 60 | Graduate / Intermediate .NET Developer (?) | 312 | ✓ | ✓ | ✓ | ✓ | ✓ | ? | ? | ? | ? | ? | $0.0415 | 24.0s |
| 88 | Software Developer 6 Month Contract (?) | 301 | ✓ | ✓ | ✓ | ✓ | ✓ | ? | ? | ? | ? | ? | $0.0369 | 19.8s |
| 60 | Software Engineer (Jacobi) | 325 | ✓ | ✓ | ✓ | ✓ | ✓ | ? | ? | ? | ? | ? | $0.0386 | 22.2s |
| 60 | Graduate LCNC Developer (KBR) | 334 | ✓ | ✓ | ✓ | ✓ | ✓ | ? | ? | ? | ? | ? | $0.0437 | 25.2s |
| 55 | Software Engineer - Business Systems (Automation & Integrations) (Maxum Foods) | 300 | ✓ | ✓ | ✓ | ✓ | ✓ | ? | ? | ? | ? | ? | $0.0349 | 20.5s |
| 55 | Junior .Net Developer / I.T Services / 5 Days Onsite (?) | 340 | ✓ | ✓ | ✓ | ✓ | ✓ | ? | ? | ? | ? | ? | $0.0404 | 22.9s |

## Per-item pass rate

| Item | Passed |
|---|---|
| em_dash_limit | 9/9 |
| generic_phrase_limit | 9/9 |
| within_word_limit | 9/9 |
| no_placeholders | 9/9 |
| has_sign_off | 9/9 |
| supported_musts_covered | 0/0 |
| no_unsupported_claims | 0/0 |
| specific_detail | 0/0 |
| would_send | 0/0 |

## Comparison-table row (plan §9)

| Measure | Value |
|---|---|
| Rubric pass rate | 0/0 fully graded letters (9 not yet graded) |
| Avg tokens / run | 2647 in, 2835 out+thinking |
| Avg cost / run | $0.0393 |
| Avg time / run | 22.7s |
| Runs that hit the budget cap | n/a (one-shot) |
| Runs where the user had to fix a factual error | 0 of 0 graded |

## style_lint (writer's check, not in the pass rate): 8/9 pass

Blocking issues plain, warnings in brackets. Sentence stdev: words; under 6 warns.

| Job | Pass | Sentence stdev | Issues (warnings) |
|---|---|---|---|
| Software Engineer Graduate | ✓ | 6.11 | (i_openers) |
| Junior-Intermediate Software Engineer | ✓ | 6.3 | - |
| Software Engineering Graduate | ✓ | 5.51 | (flat_rhythm) |
| Graduate / Intermediate .NET Developer | ✓ | 5.83 | (flat_rhythm), (i_openers) |
| Software Developer 6 Month Contract | ✓ | 6.26 | - |
| Software Engineer | ✓ | 7.37 | - |
| Graduate LCNC Developer | ✓ | 7.12 | (i_openers) |
| Software Engineer - Business Systems (Automation & | ✗ | 7.17 | too_many_paragraphs |
| Junior .Net Developer / I.T Services / 5 Days Onsi | ✓ | 5.57 | (flat_rhythm), (i_openers) |
