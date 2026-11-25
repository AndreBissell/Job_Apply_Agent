# style_lint misses one gap-led sentence shape

**Put off:** 2026-10-04 (cover-letter plan Decision log). Moved here from CLAUDE.md on
2026-11-24.
**Waiting on:** nothing technical; fits with the agent optimisation work
(docs/agent-optimisation-plan.md) because it changes which drafts get a style warning.

## What

`app/llm/letter/tools/style_lint.py` warns on gap-led sentences (a sentence that opens by
admitting a skill the candidate lacks). Its pattern misses the shape "While my X rather than
Y ...", which still leads with the gap.

## How

Add the shape to the gap-led pattern, with a test that uses fake text, and check the dev
letters in the eval set for how many new warnings it raises before switching it on.
