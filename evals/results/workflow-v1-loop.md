# Revision loop: workflow-v1

Per draft: check_claims / check_requirements / style_lint (✓ pass, ✗ fail, – not run), word count, and the must-cover ids found missing. Per revision: share of words changed, must-cover items covered before and missing after (**dropped**), whether it went over the word limit, and the checks it broke or fixed.

| Job | Score | Drafts (c/r/s, words, missing) | Revisions | Returned | Run | Cost | Time |
|---|---|---|---|---|---|---|---|
| Graduate LCNC Developer | 75 | v1 ✗✗✗ 355w missing R3<br>v2 ✗✗✓ 309w missing R4<br>v3 ✓✗✓ 308w missing R3,R4 | →v2: 14% changed, 355->309w, **dropped R4**, fixed style<br>→v3: 11% changed, 309->308w, **dropped R3**, fixed claims | v3 | budget_stopped | $0.362 | 236s |
| Software Engineer Graduate | 90 | v1 ✓✓✓ 331w | - | v1 | done | $0.164 | 149s |
| Junior-Intermediate Software Engineer | 68 | v1 ✓✓✓ 315w | - | v1 | done | $0.134 | 94s |
| Software Engineering Graduate | 88 | v1 ✓✗✓ 322w missing R4<br>v2 ✓✓✓ 330w | →v2: 2% changed, 322->330w, fixed requirements | v2 | done | $0.240 | 150s |
| Graduate / Intermediate .NET Developer | 88 | v1 ✓✓✓ 295w | - | v1 | done | $0.122 | 80s |
| Software Developer 6 Month Contract | 75 | v1 ✓✓✓ 298w | - | v1 | done | $0.105 | 82s |
| Software Engineer | 88 | v1 ✓✓✗ 356w<br>v2 ✓✓✓ 307w | →v2: 16% changed, 356->307w, fixed style | v2 | done | $0.267 | 176s |
| Software Engineer - Business Systems (Au | 75 | v1 ✓✓✗ 336w<br>v2 ✓✓✓ 338w | →v2: 1% changed, 336->338w, fixed style | v2 | done | $0.339 | 224s |
| Junior .Net Developer / I.T Services / 5 | 90 | v1 ✓✓✓ 285w | - | v1 | done | $0.140 | 99s |
| Software engineer | 75 | v1 ✓✓✓ 285w | - | v1 | done | $0.165 | 119s |
| AI Developer | 60 | v1 ✓✓✓ 313w | - | v1 | done | $0.155 | 107s |
| Front End Developer - React | 40 | v1 ✓✓✓ 268w | - | v1 | done | $0.146 | 113s |
| Developer/Support | 88 | v1 ✓✗✓ 331w missing R4<br>v2 ✓✓✓ 336w | →v2: 1% changed, 331->336w, fixed requirements | v2 | done | $0.251 | 188s |
| Frontend Developer | 85 | v1 ✓✓✓ 295w | - | v1 | done | $0.146 | 113s |
| Graduate AI & Technology Developer | 88 | v1 ✓✓✓ 310w | - | v1 | done | $0.177 | 136s |

## Summary

| Measure | workflow-v1 |
|---|---|
| Letters | 15 |
| Returned draft passes all 3 checks | 14/15 |
| …on draft 1 | 10/15 |
| Revisions | 6 |
| Revisions that dropped a must-cover item | 2/6 |
| Revisions that went over the word limit | 0/6 |
| Revisions that broke check_claims | 0/6 |
| Revisions that broke any check | 0/6 |
| Avg words changed per revision | 8% |
| Returned draft was not the latest | 0/15 |
| Runs that hit the cap (budget_stopped) | 1/15 |
| Avg cost / letter | $0.194 |
| Avg time / letter | 138s |

## Against `tools-v1` on the 9 jobs both ran

Same jobs only. `tools-v1` may allow fewer revisions, so compare the per-revision rates.

| Measure | workflow-v1 | tools-v1 |
|---|---|---|
| Letters | 9 | 9 |
| Returned draft passes all 3 checks | 8/9 | 6/9 |
| …on draft 1 | 5/9 | 4/9 |
| Revisions | 5 | 5 |
| Revisions that dropped a must-cover item | 2/5 | 2/5 |
| Revisions that went over the word limit | 0/5 | 1/5 |
| Revisions that broke check_claims | 0/5 | 1/5 |
| Revisions that broke any check | 0/5 | 2/5 |
| Avg words changed per revision | 9% | 9% |
| Returned draft was not the latest | 0/9 | 0/9 |
| Runs that hit the cap (budget_stopped) | 1/9 | 0/9 |
| Avg cost / letter | $0.208 | $0.165 |
| Avg time / letter | 143s | 125s |
