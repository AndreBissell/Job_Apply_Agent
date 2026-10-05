"""Cover-letter pipeline: shared state, tools, guardrails, workflow and agent.

See docs/cover-letter-loop-plan.md §5 and §8. Built so far: ``state`` and
``rubric`` (Phase 2), ``runner`` + the analysis tools (Phase 3), ``style`` +
``style_lint`` (Phase 4), ``guardrails`` + the draft and check tools (Phase 5).
The drivers (``workflow.py``, ``agent.py``) land in Phases 6–7.
"""
