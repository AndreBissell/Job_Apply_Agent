# Revision loop: tools-v1

Per draft: check_claims / check_requirements / style_lint (✓ pass, ✗ fail, – not run), word count, and the must-cover ids found missing. Per revision: share of words changed, must-cover items covered before and missing after (**dropped**), whether it went over the word limit, and the checks it broke or fixed.

| Job | Score | Drafts (c/r/s, words, missing) | Revisions | Returned | Run | Cost | Time |
|---|---|---|---|---|---|---|---|
| Software Engineer Graduate | 90 | v1 ✓✓✓ 283w | - | v1 | - | $0.123 | 110s |
| Junior-Intermediate Software Engineer | 60 | v1 ✓✓✓ 277w | - | v1 | - | $0.159 | 123s |
| Software Engineering Graduate | 88 | v1 ✗✗✓ 284w missing R4<br>v2 ✓✗✓ 267w missing R4,R5 | →v2: 10% changed, 284->267w, **dropped R5**, fixed claims | v2 | - | $0.163 | 114s |
| Graduate / Intermediate .NET Developer | 60 | v1 ✓✓✓ 250w | - | v1 | - | $0.095 | 69s |
| Software Developer 6 Month Contract | 88 | v1 ✓✓✓ 309w | - | v1 | - | $0.131 | 86s |
| Software Engineer | 60 | v1 ✗✓✓ 320w<br>v2 ✓✗✓ 285w missing R3 | →v2: 6% changed, 320->285w, **dropped R3**, broke requirements, fixed claims | v2 | - | $0.218 | 189s |
| Graduate LCNC Developer | 60 | v1 ✓✗✓ 335w missing R4<br>v2 ✗✓✗ 347w | →v2: 2% changed, 335->347w, **over length**, broke claims,style, fixed requirements | v2 | - | $0.162 | 114s |
| Software Engineer - Business Systems (Au | 55 | v1 ✓✗✗ 374w missing R1<br>v2 ✓✓✓ 320w | →v2: 26% changed, 374->320w, fixed requirements,style | v2 | - | $0.211 | 158s |
| Junior .Net Developer / I.T Services / 5 | 55 | v1 ✗✓✓ 307w<br>v2 ✓✓✓ 297w | →v2: 2% changed, 307->297w, fixed claims | v2 | - | $0.225 | 156s |

## Summary

| Measure | tools-v1 |
|---|---|
| Letters | 9 |
| Returned draft passes all 3 checks | 6/9 |
| …on draft 1 | 4/9 |
| Revisions | 5 |
| Revisions that dropped a must-cover item | 2/5 |
| Revisions that went over the word limit | 1/5 |
| Revisions that broke check_claims | 1/5 |
| Revisions that broke any check | 2/5 |
| Avg words changed per revision | 9% |
| Returned draft was not the latest | 0/9 |
| Runs that hit the cap (budget_stopped) | 0/9 |
| Avg cost / letter | $0.165 |
| Avg time / letter | 125s |
