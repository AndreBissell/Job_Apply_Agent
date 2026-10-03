# Revision loop: workflow-v2

Per draft: check_claims / check_requirements / style_lint (✓ pass, ✗ fail, – not run), word count, and the must-cover ids found missing. Per revision: share of words changed, must-cover items covered before and missing after (**dropped**), whether it went over the word limit, and the checks it broke or fixed.

| Job | Score | Drafts (c/r/s, words, missing) | Revisions | Returned | Run | Cost | Time |
|---|---|---|---|---|---|---|---|
| Software Engineer Graduate | 90 | v1 ✓✗✓ 309w missing R2<br>v2 ✓✓✓ 311w | →v2: 2% changed, 309->311w, fixed requirements | v2 | done | $0.263 | 189s |
| Junior-Intermediate Software Engineer | 68 | v1 ✗✓✗ 373w<br>v2 ✓✓✓ 331w | →v2: 13% changed, 373->331w, fixed claims,style | v2 | done | $0.280 | 193s |
| Software Engineering Graduate | 88 | v1 ✓✓✓ 316w | - | v1 | done | $0.128 | 153s |
| Graduate / Intermediate .NET Developer | 88 | v1 ✓✓✓ 282w | - | v1 | done | $0.121 | 83s |
| Software Developer 6 Month Contract | 75 | v1 ✓✓✓ 260w | - | v1 | done | $0.158 | 111s |
| Software Engineer | 88 | v1 ✓✓✗ 345w<br>v2 ✓✓✓ 322w | →v2: 4% changed, 345->322w, fixed style | v2 | done | $0.305 | 201s |
| Graduate LCNC Developer | 75 | v1 ✗✓✓ 320w<br>v2 ✓✓✓ 320w | →v2: 2% changed, 320->320w, fixed claims | v2 | done | $0.181 | 122s |
| Software Engineer - Business Systems (Au | 75 | v1 ✓✓✗ 347w<br>v2 ✓✓✓ 310w | →v2: 6% changed, 347->310w, fixed style | v2 | done | $0.257 | 182s |
| Junior .Net Developer / I.T Services / 5 | 90 | v1 ✗✓✓ 314w<br>v2 ✓✓✓ 314w | →v2: 1% changed, 314->314w, fixed claims | v2 | done | $0.227 | 180s |
| Software engineer | 75 | v1 ✓✓✓ 317w | - | v1 | done | $0.117 | 80s |
| AI Developer | 60 | v1 ✓✓✓ 318w | - | v1 | done | $0.153 | 106s |
| Front End Developer - React | 40 | v1 ✓✓✓ 249w | - | v1 | done | $0.136 | 105s |
| Developer/Support | 88 | v1 ✓✓✓ 324w | - | v1 | done | $0.138 | 98s |
| Frontend Developer | 85 | v1 ✓✓✓ 292w | - | v1 | done | $0.131 | 96s |
| Graduate AI & Technology Developer | 88 | v1 ✓✓✓ 319w | - | v1 | done | $0.158 | 276s |

## Summary

| Measure | workflow-v2 |
|---|---|
| Letters | 15 |
| Returned draft passes all 3 checks | 15/15 |
| …on draft 1 | 9/15 |
| Revisions | 6 |
| Revisions that dropped a must-cover item | 0/6 |
| Revisions that went over the word limit | 0/6 |
| Revisions that broke check_claims | 0/6 |
| Revisions that broke any check | 0/6 |
| Avg words changed per revision | 5% |
| Returned draft was not the latest | 0/15 |
| Runs that hit the cap (budget_stopped) | 0/15 |
| Avg cost / letter | $0.184 |
| Avg time / letter | 145s |

## Against `workflow-v1` on the 15 jobs both ran

Same jobs only. `workflow-v1` may allow fewer revisions, so compare the per-revision rates.

| Measure | workflow-v2 | workflow-v1 |
|---|---|---|
| Letters | 15 | 15 |
| Returned draft passes all 3 checks | 15/15 | 14/15 |
| …on draft 1 | 9/15 | 10/15 |
| Revisions | 6 | 6 |
| Revisions that dropped a must-cover item | 0/6 | 2/6 |
| Revisions that went over the word limit | 0/6 | 0/6 |
| Revisions that broke check_claims | 0/6 | 0/6 |
| Revisions that broke any check | 0/6 | 0/6 |
| Avg words changed per revision | 5% | 8% |
| Returned draft was not the latest | 0/15 | 0/15 |
| Runs that hit the cap (budget_stopped) | 0/15 | 1/15 |
| Avg cost / letter | $0.184 | $0.194 |
| Avg time / letter | 145s | 138s |
