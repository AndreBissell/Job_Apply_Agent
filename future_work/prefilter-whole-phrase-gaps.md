# Prefilter: false skill gaps from exact-name matching

**Put off:** 2026-10-16, found while building the Pipeline tab (docs/pipeline-tab-plan.md).
Moved here from CLAUDE.md on 2026-11-24.
**Waiting on:** a measurement first. Fixing it changes scores, so it goes through an eval
run and the owner's go-ahead.

## What

`app/llm/prefilter.py` matches job skills to profile skills by exact name. The real profile's
Seek-imported skill names ("Typescript Programming") don't equal the ad's ("TypeScript"), so
prefilter reports TypeScript / React / Docker as gaps the user doesn't have, and those false
"Gaps:" lines go into the match prompt.

Measured on real.db (pipeline-tab-plan.md): 565 gap mentions with exact matching, 395 with
whole-phrase matching (`gaps.skill_matches`, both directions). The Pipeline's "Skills ads ask
for" view already uses whole-phrase matching; the scorer does not.

## How

1. Re-score a fixed set of jobs with prefilter switched to whole-phrase matching and compare
   scores and gap lines with the current ones (cost: one small-tier match call per job).
2. If scores move only where the false gaps were, switch prefilter over. Keep prefilter's
   broad aliases (HTML=CSS etc.) out of anything that labels evidence.
