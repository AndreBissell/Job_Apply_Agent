# Revision loop: workflow-v3

Per draft: check_claims / check_requirements / style_lint (✓ pass, ✗ fail, – not run), word count, and the must-cover ids found missing. Per revision: share of words changed, must-cover items covered before and missing after (**dropped**), whether it went over the word limit, and the checks it broke or fixed.

| Job | Score | Drafts (c/r/s, words, missing) | Revisions | Returned | Run | Cost | Time |
|---|---|---|---|---|---|---|---|
| Software Engineer Graduate | 90 | v1 ✓✓✗ 357w<br>v2 ✓✓✓ 301w | →v2: 19% changed, 357->301w, fixed style | v2 | done | $0.252 | 168s |
| Junior-Intermediate Software Engineer | 68 | v1 ✓✓✓ 335w | - | v1 | done | $0.193 | 127s |
| Software Engineering Graduate | 88 | v1 ✓✓✓ 312w | - | v1 | done | $0.118 | 84s |
| Graduate / Intermediate .NET Developer | 88 | v1 ✓✓✓ 309w | - | v1 | done | $0.137 | 102s |
| Software Developer 6 Month Contract | 75 | v1 ✓✗✓ 316w missing R1<br>v2 ✓✓✓ 325w | →v2: 2% changed, 316->325w, fixed requirements | v2 | done | $0.255 | 169s |
| Software Engineer | 88 | v1 ✓✓✓ 326w | - | v1 | done | $0.207 | 138s |
| Graduate LCNC Developer | 75 | v1 ✓✓✓ 323w | - | v1 | done | $0.219 | 149s |
| Software Engineer - Business Systems (Au | 75 | v1 ✓✓✓ 337w | - | v1 | done | $0.210 | 153s |
| Junior .Net Developer / I.T Services / 5 | 90 | v1 ✗✓✓ 304w<br>v2 ✓✓✓ 310w | →v2: 2% changed, 304->310w, fixed claims | v2 | done | $0.212 | 151s |
| Software engineer | 75 | v1 ✗✓✗ 349w<br>v2 ✓✓✓ 291w | →v2: 17% changed, 349->291w, fixed claims,style | v2 | done | $0.337 | 259s |
| AI Developer | 60 | v1 ✗✓✗ 358w<br>v2 ✓✓✓ 310w | →v2: 21% changed, 358->310w, fixed claims,style | v2 | done | $0.283 | 189s |
| Front End Developer - React | 40 | v1 ✓✓✓ 276w | - | v1 | done | $0.206 | 220s |
| Developer/Support | 88 | v1 ✓✓✓ 313w | - | v1 | done | $0.150 | 118s |
| Frontend Developer | 85 | v1 ✓✓✓ 310w | - | v1 | done | $0.134 | 104s |
| Graduate AI & Technology Developer | 88 | v1 ✗✓✓ 316w<br>v2 ✗✓✓ 316w<br>v3 ✓✓✓ 315w | →v2: 0% changed, 316->316w<br>→v3: 1% changed, 316->315w, fixed claims | v3 | done | $0.433 | 433s |

## Summary

| Measure | workflow-v3 |
|---|---|
| Letters | 15 |
| Returned draft passes all 3 checks | 15/15 |
| …on draft 1 | 9/15 |
| Revisions | 7 |
| Revisions that dropped a must-cover item | 0/7 |
| Revisions that went over the word limit | 0/7 |
| Revisions that broke check_claims | 0/7 |
| Revisions that broke any check | 0/7 |
| Avg words changed per revision | 9% |
| Returned draft was not the latest | 0/15 |
| Runs that hit the cap (budget_stopped) | 0/15 |
| Avg cost / letter | $0.223 |
| Avg time / letter | 171s |

## Against `workflow-v2` on the 15 jobs both ran

Same jobs only. `workflow-v2` may allow fewer revisions, so compare the per-revision rates.

| Measure | workflow-v3 | workflow-v2 |
|---|---|---|
| Letters | 15 | 15 |
| Returned draft passes all 3 checks | 15/15 | 15/15 |
| …on draft 1 | 9/15 | 9/15 |
| Revisions | 7 | 6 |
| Revisions that dropped a must-cover item | 0/7 | 0/6 |
| Revisions that went over the word limit | 0/7 | 0/6 |
| Revisions that broke check_claims | 0/7 | 0/6 |
| Revisions that broke any check | 0/7 | 0/6 |
| Avg words changed per revision | 9% | 5% |
| Returned draft was not the latest | 0/15 | 0/15 |
| Runs that hit the cap (budget_stopped) | 0/15 | 0/15 |
| Avg cost / letter | $0.223 | $0.184 |
| Avg time / letter | 171s | 145s |
