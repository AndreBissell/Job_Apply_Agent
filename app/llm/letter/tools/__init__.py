"""Cover-letter tools: plain functions ``fn(state, ctx, **args) -> summary dict``.

Run them through ``app.llm.letter.runner.execute_tool`` so each call is logged,
costed and persisted. Phase 3: ``analyze_job``, ``match_profile``. Phase 4:
``style_lint``. Phase 5: ``generate_letter`` (generate.py), ``revise_letter``
(revise.py), ``check_claims``, ``check_requirements``. The rules they enforce live
in ``app.llm.letter.guardrails``.
"""
