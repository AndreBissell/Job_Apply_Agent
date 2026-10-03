# Planted-claim test: plant-workflow-v2

Letters from `workflow-v2` that passed `check_claims`, each with one sentence rewritten (mid model) to carry one false claim. **undeclared**: the false claim is not on the writer's claims list (stage 2 must find it). **miscited**: it is listed, citing a related profile pointer that does not back it. *Caught* = the check failed with an issue naming the planted words; *failed_other* = it failed, but not on the plant.

| Tier | Caught (all) | undeclared | miscited | failed_other | False alarms on clean letters | check_claims cost |
|---|---|---|---|---|---|---|
| mid | 59/60 | 29/30 | 30/30 | 0 | 0/15 | $1.2286 |

## By claim type

| Type | mid |
|---|---|
| invented_tool | 12/12 |
| inflated_scope | 12/12 |
| invented_metric | 12/12 |
| study_to_work | 11/12 |
| wrong_context | 12/12 |

Planting cost $0.2755. Details (letter text, gitignored): `evals/runs/plant-workflow-v2/plants.json`.
