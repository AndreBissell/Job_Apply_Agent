# Planted-claim test: plant-tools-v1

Letters from `tools-v1` that passed `check_claims`, each with one sentence rewritten (mid model) to carry one false claim. **undeclared**: the false claim is not on the writer's claims list (stage 2 must find it). **miscited**: it is listed, citing a related profile pointer that does not back it. *Caught* = the check failed with an issue naming the planted words; *failed_other* = it failed, but not on the plant.

| Tier | Caught (all) | undeclared | miscited | failed_other | False alarms on clean letters | check_claims cost |
|---|---|---|---|---|---|---|
| small | 35/36 | 17/18 | 18/18 | 0 | 0/9 | $0.1716 |
| mid | 36/36 | 18/18 | 18/18 | 0 | 2/9 | $0.8280 |

## By claim type

| Type | small | mid |
|---|---|---|
| invented_tool | 8/8 | 8/8 |
| inflated_scope | 7/8 | 8/8 |
| invented_metric | 8/8 | 8/8 |
| study_to_work | 6/6 | 6/6 |
| wrong_context | 6/6 | 6/6 |

Planting cost $0.1257. Details (letter text, gitignored): `evals/runs/plant-tools-v1/plants.json`.

**Read before using the false-alarm column (added by hand, 2026-10-03):** the "clean"
letters are the ones `small` passed during `tools-v1`. Mid's 2 "false alarms" were read
one by one and are real overclaims that `small` let through (a bare skill listing turned
into "practical experience in C#", "apply my skills in automation", and "exposure to
operational workflows" from a job title alone). Counted properly, both tiers have 0
false alarms here, and mid also catches overclaims small misses. Decision: `check_claims`
moves to `mid` (docs/cover-letter-loop-plan.md, Decision log 2026-10-03).
