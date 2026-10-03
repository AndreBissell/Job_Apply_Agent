"""The code-checkable half of the eval rubric (docs/cover-letter-loop-plan.md §9).

The rubric is pass/fail per item. These items need no judgement, so code checks
them; the rest (supported must-haves covered, no unsupported claims, a specific
company/role detail, "would I send it?") are graded by the user in the eval
run's grades.csv. See evals/rubric.md.

The word counter, banned-phrase list, placeholder/sign-off patterns and limits
come from Phase 4's ``style_lint`` (the writer's own check), so the eval and the
writer agree on them. The rubric deliberately stays narrower: it counts only real
em dashes and has no warnings, so old runs re-scored by ``report`` stay
comparable with the baseline.
"""

from __future__ import annotations

from app.llm.letter.tools.style_lint import (  # noqa: F401 — re-exported for callers/tests
    BANNED_PHRASES_PATH,
    MAX_EM_DASHES,
    MAX_GENERIC_PHRASES,
    MAX_WORDS,
    MIN_WORDS,
    PLACEHOLDER_RE,
    SIGN_OFF_RE,
    banned_phrases,
    find_banned,
    word_count,
)


def auto_checks(text: str) -> dict:
    """Pass/fail for each code-checkable rubric item, plus the raw numbers."""
    words = word_count(text)
    banned = find_banned(text)
    em_dashes = text.count("—")
    placeholders = PLACEHOLDER_RE.findall(text)
    return {
        "words": words,
        "em_dash_limit": em_dashes <= MAX_EM_DASHES,
        "em_dash_count": em_dashes,
        "generic_phrase_limit": len(banned) <= MAX_GENERIC_PHRASES,
        "banned_found": banned,
        "within_word_limit": words <= MAX_WORDS,
        "under_min_words": words < MIN_WORDS,
        "no_placeholders": not placeholders,
        "placeholders_found": placeholders,
        "has_sign_off": bool(SIGN_OFF_RE.search(text)),
    }


# Rubric items that count towards the pass rate, in report order.
AUTO_ITEMS = ("em_dash_limit", "generic_phrase_limit", "within_word_limit", "no_placeholders", "has_sign_off")
HUMAN_ITEMS = ("supported_musts_covered", "no_unsupported_claims", "specific_detail", "would_send")
