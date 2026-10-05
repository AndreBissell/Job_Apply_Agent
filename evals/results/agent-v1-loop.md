# Revision loop: agent-v1

Per draft: check_claims / check_requirements / style_lint (✓ pass, ✗ fail, – not run), word count, and the must-cover ids found missing. Per revision: share of words changed, must-cover items covered before and missing after (**dropped**), whether it went over the word limit, and the checks it broke or fixed.

| Job | Score | Drafts (c/r/s, words, missing) | Revisions | Returned | Run | Cost | Time |
|---|---|---|---|---|---|---|---|
| Software Engineer Graduate | 90 | v1 ✓✓✓ 325w | - | v1 | done | $0.163 | 155s |
| Junior-Intermediate Software Engineer | 68 | v1 ✓✓✓ 302w | - | v1 | done | $0.136 | 148s |
| Software Engineering Graduate | 88 | v1 ✓✓✓ 326w | - | v1 | done | $0.122 | 130s |
| Graduate / Intermediate .NET Developer | 88 | v1 ✓✓✓ 249w | - | v1 | done | $0.172 | 160s |
| Software Developer 6 Month Contract | 75 | v1 ✓✓✓ 280w | - | v1 | done | $0.136 | 140s |
| Software Engineer | 88 | v1 ✓✗✓ 305w missing R1,R3,R7<br>v2 ✓✓✓ 319w | →v2: 3% changed, 305->319w, fixed requirements | v2 | done | $0.302 | 270s |
| Graduate LCNC Developer | 75 | v1 ✓✓✗ 373w<br>v2 ✓✓✓ 283w | →v2: 26% changed, 373->283w, fixed style | v2 | done | $0.214 | 214s |
| Software Engineer - Business Systems (Au | 75 | v1 ✗✓✗ 349w<br>v2 ✓✗✓ 304w missing R4<br>v3 ✓✓✓ 324w | →v2: 9% changed, 349->304w, **dropped R4**, broke requirements, fixed claims,style<br>→v3: 3% changed, 304->324w, fixed requirements | v3 | done | $0.332 | 327s |
| Junior .Net Developer / I.T Services / 5 | 90 | v1 ✗✓✓ 291w<br>v2 ✓✓✓ 287w | →v2: 1% changed, 291->287w, fixed claims | v2 | done | $0.169 | 211s |
| Software engineer | 75 | v1 ✓✓✓ 313w | - | v1 | done | $0.132 | 138s |
| AI Developer | 60 | v1 ✗✓✓ 330w<br>v2 ✓✓✓ 333w | →v2: 2% changed, 330->333w, fixed claims | v2 | done | $0.235 | 262s |
| Front End Developer - React | 40 | v1 ✓✓✓ 257w | - | v1 | done | $0.132 | 139s |
| Developer/Support | 88 | v1 ✓✓✓ 273w | - | v1 | done | $0.159 | 155s |
| Frontend Developer | 85 | v1 ✓✓✓ 334w | - | v1 | done | $0.151 | 156s |
| Graduate AI & Technology Developer | 88 | v1 ✓✓✓ 340w | - | v1 | done | $0.119 | 130s |

## Summary

| Measure | agent-v1 |
|---|---|
| Letters | 15 |
| Returned draft passes all 3 checks | 15/15 |
| …on draft 1 | 10/15 |
| Revisions | 6 |
| Revisions that dropped a must-cover item | 1/6 |
| Revisions that went over the word limit | 0/6 |
| Revisions that broke check_claims | 0/6 |
| Revisions that broke any check | 1/6 |
| Avg words changed per revision | 7% |
| Returned draft was not the latest | 0/15 |
| Runs that hit the cap (budget_stopped) | 0/15 |
| Avg cost / letter | $0.178 |
| Avg time / letter | 182s |

## Against `workflow-v2` on the 15 jobs both ran

Same jobs only. `workflow-v2` may allow fewer revisions, so compare the per-revision rates.

| Measure | agent-v1 | workflow-v2 |
|---|---|---|
| Letters | 15 | 15 |
| Returned draft passes all 3 checks | 15/15 | 15/15 |
| …on draft 1 | 10/15 | 9/15 |
| Revisions | 6 | 6 |
| Revisions that dropped a must-cover item | 1/6 | 0/6 |
| Revisions that went over the word limit | 0/6 | 0/6 |
| Revisions that broke check_claims | 0/6 | 0/6 |
| Revisions that broke any check | 1/6 | 0/6 |
| Avg words changed per revision | 7% | 5% |
| Returned draft was not the latest | 0/15 | 0/15 |
| Runs that hit the cap (budget_stopped) | 0/15 | 0/15 |
| Avg cost / letter | $0.178 | $0.184 |
| Avg time / letter | 182s | 145s |

## Agent: path, refusals and orchestrator overhead

vs workflow: the fixed workflow's tool sequence for the same number of drafts. "check order only" means the same calls with the three checks in a different order.

| Job | Steps ((refused) in brackets) | vs workflow | Refused | Orch. calls | Orch. cost | Share of cost |
|---|---|---|---|---|---|---|
| Software Engineer Graduate | analyze_job → match_profile → generate_letter → check_claims → check_requirements → style_lint → finish | same | 0 | 7 | $0.0108 | 7% |
| Junior-Intermediate Software Eng | analyze_job → match_profile → generate_letter → check_claims → check_requirements → style_lint → finish | same | 0 | 7 | $0.0115 | 8% |
| Software Engineering Graduate | analyze_job → match_profile → generate_letter → check_claims → check_requirements → style_lint → finish | same | 0 | 7 | $0.0107 | 9% |
| Graduate / Intermediate .NET Dev | analyze_job → match_profile → generate_letter → check_claims → check_requirements → style_lint → finish | same | 0 | 7 | $0.0099 | 6% |
| Software Developer 6 Month Contr | analyze_job → match_profile → generate_letter → check_claims → check_requirements → style_lint → finish | same | 0 | 7 | $0.0108 | 8% |
| Software Engineer | analyze_job → match_profile → generate_letter → check_claims → check_requirements → style_lint → revise_letter → check_claims → check_requirements → style_lint → finish | same | 0 | 11 | $0.0179 | 6% |
| Graduate LCNC Developer | analyze_job → match_profile → generate_letter → check_claims → check_requirements → style_lint → revise_letter → check_claims → check_requirements → style_lint → finish | same | 0 | 11 | $0.0163 | 8% |
| Software Engineer - Business Sys | analyze_job → match_profile → generate_letter → check_claims → check_requirements → style_lint → revise_letter → check_claims → check_requirements → style_lint → revise_letter → check_claims → check_requirements → style_lint → finish | same | 0 | 15 | $0.0240 | 7% |
| Junior .Net Developer / I.T Serv | analyze_job → match_profile → generate_letter → check_claims → check_requirements → style_lint → revise_letter → check_claims → check_requirements → style_lint → finish | same | 0 | 11 | $0.0153 | 9% |
| Software engineer | analyze_job → match_profile → generate_letter → check_claims → check_requirements → style_lint → finish | same | 0 | 7 | $0.0103 | 8% |
| AI Developer | analyze_job → match_profile → generate_letter → check_claims → check_requirements → style_lint → revise_letter → check_claims → check_requirements → style_lint → finish | same | 0 | 11 | $0.0168 | 7% |
| Front End Developer - React | analyze_job → match_profile → generate_letter → check_claims → check_requirements → style_lint → finish | same | 0 | 7 | $0.0104 | 8% |
| Developer/Support | analyze_job → match_profile → generate_letter → check_claims → check_requirements → style_lint → finish | same | 0 | 7 | $0.0104 | 7% |
| Frontend Developer | analyze_job → match_profile → generate_letter → check_claims → check_requirements → style_lint → finish | same | 0 | 7 | $0.0097 | 6% |
| Graduate AI & Technology Develop | analyze_job → match_profile → generate_letter → check_claims → check_requirements → style_lint → finish | same | 0 | 7 | $0.0110 | 9% |

| Measure | Value |
|---|---|
| Same path as the workflow | 15/15 (+0 with only the check order changed) |
| Different path | 0/15 |
| Guardrail refusals | 0 in 0/15 runs |
| Orchestrator calls / letter | 8.6 |
| Orchestrator tokens / letter | 12204 in, 1037 out+thinking |
| Orchestrator cost / letter | $0.0130 (7% of the run cost) |
| Avg tokens / letter (all calls) | 29168 in, 18448 out+thinking |
