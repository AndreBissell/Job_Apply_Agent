"""Cover-letter tools: plain functions ``fn(state, ctx, **args) -> summary dict``.

Run them through ``app.llm.letter.runner.execute_tool`` so each call is logged,
costed and persisted. Phase 3 has ``analyze_job`` and ``match_profile``; the draft
and check tools land in Phase 5.
"""
