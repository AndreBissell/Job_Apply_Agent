"""The code-checkable half of the eval rubric (docs/cover-letter-loop-plan.md §9).

The rubric is pass/fail per item. These items need no judgement, so code checks
them; the rest (supported must-haves covered, no unsupported claims, a specific
company/role detail, "would I send it?") are graded by the user in the eval
run's grades.csv. See evals/rubric.md.

Interim: Phase 4's ``style_lint`` takes over the style rules and adds the
warnings (sentence variance, AU spelling, ...). It reads the same
banned_phrases.txt so the two can't disagree.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

BANNED_PHRASES_PATH = (
    Path(__file__).resolve().parent.parent / "skills" / "cover_letter_style" / "banned_phrases.txt"
)

# Limits agreed 2026-10-02. Counts are occurrences, so the same phrase twice is 2.
MAX_EM_DASHES = 1
MAX_GENERIC_PHRASES = 1

# One page (plan Q7): aim 250–300 words; >340 is treated as over a page.
MAX_WORDS = 340
MIN_WORDS = 230  # reported, but under-length is a warning, not a fail

_PLACEHOLDER_RE = re.compile(r"\[[^\]\n]{1,40}\]|\{[^}\n]{1,40}\}|<[^>\n]{1,40}>")
_SIGN_OFF_RE = re.compile(r"^\s*(sincerely|kind regards|regards|yours sincerely)\b", re.I | re.M)


@lru_cache(maxsize=1)
def banned_phrases() -> tuple[str, ...]:
    lines = BANNED_PHRASES_PATH.read_text(encoding="utf-8").splitlines()
    return tuple(
        line.strip().lower() for line in lines if line.strip() and not line.lstrip().startswith("#")
    )


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'’-]+\b", text))


def find_banned(text: str) -> list[str]:
    """Every banned-phrase occurrence (a phrase used twice appears twice)."""
    low = text.lower().replace("’", "'")
    found: list[str] = []
    for p in banned_phrases():
        found.extend([p] * len(re.findall(rf"\b{re.escape(p)}\b", low)))
    return found


def auto_checks(text: str) -> dict:
    """Pass/fail for each code-checkable rubric item, plus the raw numbers."""
    words = word_count(text)
    banned = find_banned(text)
    em_dashes = text.count("—")
    placeholders = _PLACEHOLDER_RE.findall(text)
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
        "has_sign_off": bool(_SIGN_OFF_RE.search(text)),
    }


# Rubric items that count towards the pass rate, in report order.
AUTO_ITEMS = ("em_dash_limit", "generic_phrase_limit", "within_word_limit", "no_placeholders", "has_sign_off")
HUMAN_ITEMS = ("supported_musts_covered", "no_unsupported_claims", "specific_detail", "would_send")
