# Revision loop: agent-v2-side

Per draft: check_claims / check_requirements / style_lint (✓ pass, ✗ fail, – not run), word count, and the must-cover ids found missing. Per revision: share of words changed, must-cover items covered before and missing after (**dropped**), whether it went over the word limit, and the checks it broke or fixed.

| Job | Score | Drafts (c/r/s, words, missing) | Revisions | Returned | Run | Cost | Time |
|---|---|---|---|---|---|---|---|
| Software Engineer Graduate | 90 | v1 ✓✓✓ 314w | - | v1 | done | $0.161 | 209s |
| Junior-Intermediate Software Engineer | 68 | v1 ✓✓✓ 293w | - | v1 | done | $0.173 | 178s |
| Software Engineering Graduate | 88 | v1 ✗✓✗ 344w<br>v2 ✓✓✓ 292w | →v2: 26% changed, 344->292w, fixed claims,style | v2 | done | $0.376 | 363s |
| Graduate / Intermediate .NET Developer | 88 | v1 ✓✓✓ 319w | - | v1 | done | $0.178 | 171s |
| Software Developer 6 Month Contract | 75 | v1 ✓✓✓ 273w | - | v1 | done | $0.168 | 178s |
| Software Engineer | 88 | v1 ✓✓✓ 335w | - | v1 | done | $0.233 | 286s |
| Graduate LCNC Developer | 75 | v1 ✓✓✓ 332w | - | v1 | done | $0.237 | 207s |
| Software Engineer - Business Systems (Au | 75 | v1 ✓✓✗ 342w<br>v2 ✓✓✓ 299w | →v2: 15% changed, 342->299w, fixed style | v2 | done | $0.284 | 305s |
| Junior .Net Developer / I.T Services / 5 | 90 | v1 ✓✓✓ 288w | - | v1 | done | $0.192 | 187s |
| Software engineer | 75 | v1 ✓✗✓ 293w missing R3,R4<br>v2 ✓✓✓ 315w | →v2: 16% changed, 293->315w, fixed requirements | v2 | done | $0.311 | 288s |
| AI Developer | 60 | v1 ✓✗✓ 330w missing R1<br>v2 ✓✓✓ 334w | →v2: 2% changed, 330->334w, fixed requirements | v2 | done | $0.339 | 306s |
| Front End Developer - React | 40 | v1 ✓✓✓ 260w | - | v1 | done | $0.133 | 148s |
| Developer/Support | 88 | v1 ✓✓✓ 326w | - | v1 | done | $0.148 | 162s |
| Frontend Developer | 85 | v1 ✓✓✓ 277w | - | v1 | done | $0.179 | 177s |
| Graduate AI & Technology Developer | 88 | v1 ✓✓✗ 364w<br>v2 ✓✓✓ 299w | →v2: 17% changed, 364->299w, fixed style | v2 | done | $0.301 | 274s |

## Summary

| Measure | agent-v2-side |
|---|---|
| Letters | 15 |
| Returned draft passes all 3 checks | 15/15 |
| …on draft 1 | 10/15 |
| Revisions | 5 |
| Revisions that dropped a must-cover item | 0/5 |
| Revisions that went over the word limit | 0/5 |
| Revisions that broke check_claims | 0/5 |
| Revisions that broke any check | 0/5 |
| Avg words changed per revision | 15% |
| Returned draft was not the latest | 0/15 |
| Runs that hit the cap (budget_stopped) | 0/15 |
| Avg cost / letter | $0.227 |
| Avg time / letter | 229s |

## Against `agent-v1` on the 15 jobs both ran

Same jobs only. `agent-v1` may allow fewer revisions, so compare the per-revision rates.

| Measure | agent-v2-side | agent-v1 |
|---|---|---|
| Letters | 15 | 15 |
| Returned draft passes all 3 checks | 15/15 | 15/15 |
| …on draft 1 | 10/15 | 10/15 |
| Revisions | 5 | 6 |
| Revisions that dropped a must-cover item | 0/5 | 1/6 |
| Revisions that went over the word limit | 0/5 | 0/6 |
| Revisions that broke check_claims | 0/5 | 0/6 |
| Revisions that broke any check | 0/5 | 1/6 |
| Avg words changed per revision | 15% | 7% |
| Returned draft was not the latest | 0/15 | 0/15 |
| Runs that hit the cap (budget_stopped) | 0/15 | 0/15 |
| Avg cost / letter | $0.227 | $0.178 |
| Avg time / letter | 229s | 182s |

## Agent: path, refusals and orchestrator overhead

vs workflow: the fixed workflow's tool sequence for the same number of drafts. "check order only" means the same calls with the three checks in a different order.

| Job | Steps ((refused) in brackets) | vs workflow | Refused | Orch. calls | Orch. cost | Share of cost |
|---|---|---|---|---|---|---|
| Software Engineer Graduate | analyze_job → match_profile → generate_letter → check_claims → check_requirements → style_lint → suggest_resume_tweaks → finish | same | 0 | 8 | $0.0152 | 9% |
| Junior-Intermediate Software Eng | analyze_job → match_profile → generate_letter → check_claims → check_requirements → style_lint → suggest_resume_tweaks → finish | same | 0 | 8 | $0.0156 | 9% |
| Software Engineering Graduate | analyze_job → match_profile → generate_letter → check_claims → check_requirements → style_lint → revise_letter → check_claims → check_requirements → style_lint → suggest_resume_tweaks → finish | same | 0 | 12 | $0.0232 | 6% |
| Graduate / Intermediate .NET Dev | analyze_job → match_profile → generate_letter → check_claims → check_requirements → style_lint → suggest_resume_tweaks → finish | same | 0 | 8 | $0.0141 | 8% |
| Software Developer 6 Month Contr | analyze_job → match_profile → generate_letter → check_claims → check_requirements → style_lint → suggest_resume_tweaks → finish | same | 0 | 8 | $0.0157 | 9% |
| Software Engineer | analyze_job → match_profile → generate_letter → check_claims → check_requirements → style_lint → suggest_resume_tweaks → finish | same | 0 | 8 | $0.0158 | 7% |
| Graduate LCNC Developer | analyze_job → match_profile → generate_letter → check_claims → check_requirements → style_lint → suggest_resume_tweaks → finish | same | 0 | 8 | $0.0163 | 7% |
| Software Engineer - Business Sys | analyze_job → match_profile → generate_letter → check_claims → check_requirements → style_lint → revise_letter → check_claims → check_requirements → style_lint → suggest_resume_tweaks → finish | same | 0 | 12 | $0.0217 | 8% |
| Junior .Net Developer / I.T Serv | analyze_job → match_profile → generate_letter → check_claims → check_requirements → style_lint → suggest_resume_tweaks → finish | same | 0 | 8 | $0.0148 | 8% |
| Software engineer | analyze_job → match_profile → generate_letter → check_claims → check_requirements → style_lint → revise_letter → check_claims → check_requirements → style_lint → suggest_resume_tweaks → finish | same | 0 | 12 | $0.0234 | 8% |
| AI Developer | analyze_job → match_profile → generate_letter → check_claims → check_requirements → style_lint → revise_letter → check_claims → check_requirements → style_lint → suggest_resume_tweaks → finish | same | 0 | 12 | $0.0238 | 7% |
| Front End Developer - React | analyze_job → match_profile → generate_letter → check_claims → check_requirements → style_lint → suggest_resume_tweaks → finish | same | 0 | 8 | $0.0156 | 12% |
| Developer/Support | analyze_job → match_profile → generate_letter → check_claims → check_requirements → style_lint → suggest_resume_tweaks → finish | same | 0 | 8 | $0.0153 | 10% |
| Frontend Developer | analyze_job → match_profile → generate_letter → check_claims → check_requirements → style_lint → suggest_resume_tweaks → finish | same | 0 | 8 | $0.0151 | 8% |
| Graduate AI & Technology Develop | analyze_job → match_profile → generate_letter → check_claims → check_requirements → style_lint → revise_letter → check_claims → check_requirements → style_lint → suggest_resume_tweaks → finish | same | 0 | 12 | $0.0231 | 8% |

| Measure | Value |
|---|---|
| Same path as the workflow | 15/15 (+0 with only the check order changed) |
| Different path | 0/15 |
| Guardrail refusals | 0 in 0/15 runs |
| Orchestrator calls / letter | 9.3 |
| Orchestrator tokens / letter | 18920 in, 993 out+thinking |
| Orchestrator cost / letter | $0.0179 (8% of the run cost) |
| Avg tokens / letter (all calls) | 39829 in, 24109 out+thinking |

## Side outputs (Phase 7c)

Which side-output tools ran, where in the run (step n of m tool calls; d = drafts written before it), what they produced, and the refusals around them.

| Job | Side outputs run (step n of m; d = drafts before it) | Produced | Side refusals | Finish refused for a side output |
|---|---|---|---|---|
| Software Engineer Graduate | suggest_resume_tweaks 7/7 (d=1) | 0 answers (0 verified), 0 learning, résumé {'lead_with': 3, 'keywords_to_mirror': 5, 'consider_cutting': 2, 'gaps_to_address': 4} | 0 | 0 |
| Junior-Intermediate Software Eng | suggest_resume_tweaks 7/7 (d=1) | 0 answers (0 verified), 0 learning, résumé {'lead_with': 4, 'keywords_to_mirror': 7, 'consider_cutting': 3, 'gaps_to_address': 4} | 0 | 0 |
| Software Engineering Graduate | suggest_resume_tweaks 11/11 (d=2) | 0 answers (0 verified), 0 learning, résumé {'lead_with': 3, 'keywords_to_mirror': 3, 'consider_cutting': 1, 'gaps_to_address': 3} | 0 | 0 |
| Graduate / Intermediate .NET Dev | suggest_resume_tweaks 7/7 (d=1) | 0 answers (0 verified), 0 learning, résumé {'lead_with': 2, 'keywords_to_mirror': 4, 'consider_cutting': 2, 'gaps_to_address': 2}, 2 dropped | 0 | 0 |
| Software Developer 6 Month Contr | suggest_resume_tweaks 7/7 (d=1) | 0 answers (0 verified), 0 learning, résumé {'lead_with': 4, 'keywords_to_mirror': 8, 'consider_cutting': 2, 'gaps_to_address': 4} | 0 | 0 |
| Software Engineer | suggest_resume_tweaks 7/7 (d=1) | 0 answers (0 verified), 0 learning, résumé {'lead_with': 3, 'keywords_to_mirror': 7, 'consider_cutting': 2, 'gaps_to_address': 4} | 0 | 0 |
| Graduate LCNC Developer | suggest_resume_tweaks 7/7 (d=1) | 0 answers (0 verified), 0 learning, résumé {'lead_with': 4, 'keywords_to_mirror': 6, 'consider_cutting': 3, 'gaps_to_address': 4} | 0 | 0 |
| Software Engineer - Business Sys | suggest_resume_tweaks 11/11 (d=2) | 0 answers (0 verified), 0 learning, résumé {'lead_with': 4, 'keywords_to_mirror': 6, 'consider_cutting': 2, 'gaps_to_address': 4} | 0 | 0 |
| Junior .Net Developer / I.T Serv | suggest_resume_tweaks 7/7 (d=1) | 0 answers (0 verified), 0 learning, résumé {'lead_with': 3, 'keywords_to_mirror': 4, 'consider_cutting': 2, 'gaps_to_address': 4} | 0 | 0 |
| Software engineer | suggest_resume_tweaks 11/11 (d=2) | 0 answers (0 verified), 0 learning, résumé {'lead_with': 3, 'keywords_to_mirror': 6, 'consider_cutting': 2, 'gaps_to_address': 4} | 0 | 0 |
| AI Developer | suggest_resume_tweaks 11/11 (d=2) | 0 answers (0 verified), 0 learning, résumé {'lead_with': 3, 'keywords_to_mirror': 6, 'consider_cutting': 2, 'gaps_to_address': 3} | 0 | 0 |
| Front End Developer - React | suggest_resume_tweaks 7/7 (d=1) | 0 answers (0 verified), 0 learning, résumé {'lead_with': 3, 'keywords_to_mirror': 4, 'consider_cutting': 2, 'gaps_to_address': 4} | 0 | 0 |
| Developer/Support | suggest_resume_tweaks 7/7 (d=1) | 0 answers (0 verified), 0 learning, résumé {'lead_with': 4, 'keywords_to_mirror': 6, 'consider_cutting': 3, 'gaps_to_address': 4} | 0 | 0 |
| Frontend Developer | suggest_resume_tweaks 7/7 (d=1) | 0 answers (0 verified), 0 learning, résumé {'lead_with': 3, 'keywords_to_mirror': 7, 'consider_cutting': 2, 'gaps_to_address': 4} | 0 | 0 |
| Graduate AI & Technology Develop | suggest_resume_tweaks 11/11 (d=2) | 0 answers (0 verified), 0 learning, résumé {'lead_with': 3, 'keywords_to_mirror': 5, 'consider_cutting': 2, 'gaps_to_address': 4} | 0 | 0 |

| Measure | Value |
|---|---|
| answer_screening ran | 0/15 |
| suggest_learning ran | 0/15 |
| suggest_resume_tweaks ran | 15/15 |
| Side tool calls refused by a gate | 0 |
| finish refused while a side output was due | 0 |
| Side outputs run before the final draft existed | 0 |
