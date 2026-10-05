# Quick Apply: `user` wordings the keyword filter misses

**Put off:** 2026-10-05, found by the Phase 9c eval (`evals/results/screening-v1.md`).
**Waiting on:** nothing technical; a judgement call on the patterns (below).

## What

Layer 3 of the question sorting (`app/screening/sort.py`, `_USER_KEYWORDS`) is what keeps
personal questions away from the model: a question it recognises is `user` and never
sent. The eval's synthetic `user` wordings found three it doesn't recognise, so on a
full-pipeline job they would fall through to layer 5 (the small model) and be sent:

| Eval item | Question | Why it is `user` |
|---|---|---|
| U3 | "Do you hold a current driver's licence?" | an eligibility fact about the applicant |
| U4 | "Please include a link to your portfolio or GitHub profile." | links are never invented; only the applicant has them |
| U5 | "When would you be available for an interview?" | availability, like notice |

Nothing personal leaks in that case (only the question text is sent, and layer 5 may well
answer `user`), but the plan's rule is that a `user` question never reaches the model.

Related: the test bank's "Do you have experience using Microsoft Azure DevOps?" (yes/no)
was labelled `skill_in_role_yes_no`, the only yes/no skill strategy, which counts work
roles only. That is stricter than the question asks. A plain `skill_yes_no` strategy
(any evidence counts, listed skills as "knowledge of") would fit it better.

## How

- Add patterns, keeping the existing care about false positives (the module says why:
  "Microsoft Office" must not trip "office"):
  - licence: `\bdriver'?s licen[cs]e\b`, `\bcurrent licen[cs]e\b`, `\bhold a .* licen[cs]e\b`
    (NOT a bare "licence": "software licence management" is a skill);
  - links: `\blink to your\b`, `\b(?:portfolio|github|linkedin) (?:link|url|profile)\b`
    (NOT a bare "portfolio": "managing a portfolio of projects" is experience);
  - availability: `\bavailable for (?:an )?interview\b`, `\bavailability\b`.
- Decide the topic for each (existing: work_rights, identity, legal, salary, notice,
  work_arrangement, source, motivation; a new `links` topic would need the review list's
  vocabulary, which comes from `USER_TOPICS`).
- Re-run `python scripts/screening_eval.py sort` (free): U3-U5 should move to `keyword`
  and the failure count go to 0, with 45/45 still correct.
- For `skill_yes_no`: add it to `ASSISTED_STRATEGIES`, layer 5's schema and validation,
  `assist.assist_question` (the `_skill_question` path with `in_role=False`), and relabel
  bank-5 in `evals/screening/set.json`.
