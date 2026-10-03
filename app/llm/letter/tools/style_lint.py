"""``style_lint``: the code-only style check on a draft (plan §5.4, Phase 4).

The *detection* half of "sounds machine-written"; the style guide
(``app/llm/skills/cover_letter_style/SKILL.md``) is the *prevention* half, loaded
into the writer's prompt. No LLM call, so it costs nothing and never varies.

Blocking issues fail the draft (``finish`` is refused until a revision clears
them). Warnings are passed to ``revise_letter`` but don't block.

    blocking   more than 1 em dash; more than 1 banned phrase; over one page
               (> MAX_WORDS words or > MAX_BODY_PARAGRAPHS body paragraphs);
               placeholders like ``[Company]``; no sign-off
    warnings   under MIN_WORDS; low sentence-length variance; 3+ paragraphs
               opening with "I"; American spellings from ``us_to_au.txt``

The word counter, banned-phrase list and placeholder pattern live here and the
eval rubric (``app/llm/letter/rubric.py``) imports them, so the writer's check
and the eval yardstick can't disagree about what a word or a phrase is.
"""

from __future__ import annotations

import re
import statistics
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.llm.letter.runner import ToolContext, ToolError
from app.llm.letter.state import Check, LetterState, split_sentences

SKILL_DIR = Path(__file__).resolve().parent.parent.parent / "skills" / "cover_letter_style"
BANNED_PHRASES_PATH = SKILL_DIR / "banned_phrases.txt"
US_TO_AU_PATH = SKILL_DIR / "us_to_au.txt"

# Limits agreed 2026-10-02/03. Counts are occurrences, so the same phrase twice is 2.
MAX_EM_DASHES = 1  # user decision 2026-10-03: one allowed, same as the eval rubric
MAX_GENERIC_PHRASES = 1
# One page (plan Q7): aim 250–300 words; > 340 is treated as over a page.
MAX_WORDS = 340
MIN_WORDS = 230
MAX_BODY_PARAGRAPHS = 5  # greeting and sign-off lines don't count
# Warnings. Sentence-length stdev tuned 2026-10-03: the 9 baseline-oneshot letters
# ran 5.1-8.8 words (4 under 6), the user's own writing samples 8.0-9.9.
MIN_SENTENCES_FOR_VARIANCE = 5
MIN_SENTENCE_STDEV = 6.0  # words; below this every sentence is about the same length
MAX_PARAGRAPHS_STARTING_WITH_I = 2

PLACEHOLDER_RE = re.compile(r"\[[^\]\n]{1,40}\]|\{[^}\n]{1,40}\}|<[^>\n]{1,40}>")
SIGN_OFF_RE = re.compile(r"^\s*(sincerely|kind regards|regards|yours sincerely)\b", re.I | re.M)
# An en dash with spaces round it is an em dash in disguise; a tight one ("2022–2024") is a range.
_DASH_RE = re.compile(r"—|(?<=\s)–(?=\s)")
_GREETING_RE = re.compile(r"^\s*(dear|to whom|hi|hello)\b", re.I)
_STARTS_WITH_I_RE = re.compile(r"^\s*I\b")


# ---------------------------------------------------------------------------
# Shared helpers (also used by rubric.py)
# ---------------------------------------------------------------------------
def _read_list(path: Path) -> list[str]:
    return [
        line.strip() for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


@lru_cache(maxsize=1)
def banned_phrases() -> tuple[str, ...]:
    return tuple(p.lower() for p in _read_list(BANNED_PHRASES_PATH))


@lru_cache(maxsize=1)
def us_to_au() -> dict[str, str]:
    pairs = (line.split("=", 1) for line in _read_list(US_TO_AU_PATH) if "=" in line)
    return {us.strip().lower(): au.strip().lower() for us, au in pairs}


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'’-]+\b", text))


def find_banned(text: str) -> list[str]:
    """Every banned-phrase occurrence (a phrase used twice appears twice)."""
    low = text.lower().replace("’", "'")
    found: list[str] = []
    for p in banned_phrases():
        found.extend([p] * len(re.findall(rf"\b{re.escape(p)}\b", low)))
    return found


# ---------------------------------------------------------------------------
# Style-only measures
# ---------------------------------------------------------------------------
def count_dashes(text: str) -> int:
    return len(_DASH_RE.findall(text))


def body_paragraphs(text: str) -> list[str]:
    """Paragraphs separated by blank lines, minus the greeting and the sign-off."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return [p for p in paras if not _GREETING_RE.match(p) and not SIGN_OFF_RE.match(p)]


def sentence_stdev(text: str) -> float | None:
    """Population stdev of sentence lengths in words, or None if too few sentences."""
    lengths = [word_count(s) for s in split_sentences("\n".join(body_paragraphs(text)))]
    lengths = [n for n in lengths if n]
    if len(lengths) < MIN_SENTENCES_FOR_VARIANCE:
        return None
    return statistics.pstdev(lengths)


def find_us_spellings(text: str) -> list[str]:
    table = us_to_au()
    return [w.lower() for w in re.findall(r"\b[A-Za-z]+\b", text) if w.lower() in table]


def lint(text: str) -> dict[str, Any]:
    """Every check plus the raw numbers. ``passed`` is False iff ``issues`` is non-empty."""
    words = word_count(text)
    dashes = count_dashes(text)
    banned = find_banned(text)
    placeholders = PLACEHOLDER_RE.findall(text)
    has_sign_off = bool(SIGN_OFF_RE.search(text))
    paras = body_paragraphs(text)
    stdev = sentence_stdev(text)
    starts_with_i = sum(1 for p in paras if _STARTS_WITH_I_RE.match(p))
    us = find_us_spellings(text)

    issues: list[str] = []
    if dashes > MAX_EM_DASHES:
        issues.append(f"em_dash: {dashes} found, at most {MAX_EM_DASHES} allowed; use a comma or full stop")
    if len(banned) > MAX_GENERIC_PHRASES:
        issues.append(f"banned_phrase: {', '.join(repr(p) for p in sorted(set(banned)))}; say it specifically or cut it")
    if words > MAX_WORDS:
        issues.append(f"too_long: {words} words, over one page (max {MAX_WORDS}; aim 250-300)")
    if len(paras) > MAX_BODY_PARAGRAPHS:
        issues.append(f"too_many_paragraphs: {len(paras)} body paragraphs (max {MAX_BODY_PARAGRAPHS})")
    if placeholders:
        issues.append(f"placeholder: {', '.join(placeholders)}")
    if not has_sign_off:
        issues.append("no_sign_off: end with 'Sincerely,' and the candidate's name")

    warnings: list[str] = []
    if words < MIN_WORDS:
        warnings.append(f"short: {words} words (aim 250-300)")
    if stdev is not None and stdev < MIN_SENTENCE_STDEV:
        warnings.append(f"flat_rhythm: sentence lengths barely vary (stdev {stdev:.1f} words); mix short and long")
    if starts_with_i > MAX_PARAGRAPHS_STARTING_WITH_I:
        warnings.append(f"i_openers: {starts_with_i} paragraphs start with 'I'")
    if us:
        fixes = sorted({f"{w} -> {us_to_au()[w]}" for w in us})
        warnings.append(f"us_spelling: {', '.join(fixes)}")

    return {
        "passed": not issues,
        "issues": issues,
        "warnings": warnings,
        "words": words,
        "em_dash_count": dashes,
        "banned_found": banned,
        "placeholders_found": placeholders,
        "has_sign_off": has_sign_off,
        "body_paragraphs": len(paras),
        "sentence_stdev": None if stdev is None else round(stdev, 2),
        "paragraphs_starting_with_i": starts_with_i,
        "us_spellings_found": us,
    }


def check_style(text: str) -> Check:
    result = lint(text)
    return Check(passed=result["passed"], issues=result["issues"], warnings=result["warnings"])


# ---------------------------------------------------------------------------
# The tool
# ---------------------------------------------------------------------------
def style_lint(state: LetterState, ctx: ToolContext) -> dict[str, Any]:
    """Check the latest draft's style and record ``checks.style`` on it.

    Use after every new draft, alongside ``check_claims`` and
    ``check_requirements``. Costs nothing (no model call). If it fails, pass the
    listed issues to ``revise_letter``; warnings are worth fixing in the same
    revision but don't block ``finish``.
    """
    draft = state.latest_draft
    if draft is None:
        raise ToolError("style_lint needs a draft; call generate_letter first")
    result = lint(draft.text)
    state.record_check("style", Check(passed=result["passed"], issues=result["issues"], warnings=result["warnings"]))
    return {
        "draft": draft.version,
        "passed": result["passed"],
        "issues": result["issues"],
        "warnings": result["warnings"],
        "words": result["words"],
    }
