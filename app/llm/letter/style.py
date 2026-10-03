"""The style skill and the user's voice, as prompt text for the letter writer
(docs/cover-letter-loop-plan.md §5.4, Phase 4).

Prevention half of "sounds machine-written": the writer gets the style rules and
the banned-phrase list (the same file ``style_lint`` checks against) *before*
drafting, plus a sample of the user's own writing to match.

Voice comes from the DB, never the repo: ``profiles.writing_sample`` if the user
pasted one, otherwise their own words in ``profiles.summary`` and
``experiences.description`` (weaker: CV text is terser than a letter). It is
used for tone and rhythm only. Facts come from the profile / evidence pointers.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Literal

from app.llm.letter.tools.style_lint import SKILL_DIR, banned_phrases
from app.models import Profile

VoiceSource = Literal["writing_sample", "profile_text", "none"]

# Enough to carry rhythm and vocabulary without crowding out the job and profile.
VOICE_MAX_WORDS = 1500


@lru_cache(maxsize=1)
def _skill_rules() -> str:
    """SKILL.md from its first section on (the intro is notes for developers)."""
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    start = text.find("## ")
    return text[start:].strip() if start >= 0 else text.strip()


def style_guide_prompt() -> str:
    phrases = "; ".join(f'"{p}"' for p in banned_phrases())
    return (
        "=== STYLE GUIDE (follow it) ===\n"
        f"{_skill_rules()}\n\n"
        f"Phrases never to use: {phrases}."
    )


def _trim_words(text: str, limit: int) -> str:
    """Cut to about ``limit`` words, at a paragraph boundary where possible."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    kept: list[str] = []
    total = 0
    for p in paras:
        n = len(p.split())
        if total + n > limit:
            if not kept:  # one huge paragraph: hard cut
                kept.append(" ".join(p.split()[:limit]))
            break
        kept.append(p)
        total += n
    return "\n\n".join(kept)


def voice_reference(profile: Profile) -> tuple[str, VoiceSource]:
    """The text the writer should sound like, and where it came from.

    Needs ``profile.experiences`` loaded for the fallback.
    """
    sample = (profile.writing_sample or "").strip()
    if sample:
        return _trim_words(sample, VOICE_MAX_WORDS), "writing_sample"
    own_words = [profile.summary or ""] + [e.description or "" for e in profile.experiences]
    fallback = "\n\n".join(t.strip() for t in own_words if t and t.strip())
    if fallback:
        return _trim_words(fallback, VOICE_MAX_WORDS), "profile_text"
    return "", "none"


def voice_prompt(profile: Profile) -> str:
    """The voice block for the writer's prompt, or '' when there is nothing to go on."""
    text, source = voice_reference(profile)
    if source == "none":
        return ""
    what = (
        "samples of the candidate's own writing"
        if source == "writing_sample"
        else "the candidate's own CV text (terser than a letter; borrow the vocabulary, not the clipped style)"
    )
    return (
        f"=== VOICE REFERENCE: {what} ===\n"
        "Match its tone, rhythm, vocabulary and how directly it explains things, so the "
        "letter sounds like this person wrote it. Use it for HOW to say things only: "
        "take no facts from it (facts come only from the candidate profile). The style "
        "guide wins where they differ, e.g. don't copy em dashes or stock phrases from it.\n"
        "<<<\n"
        f"{text}\n"
        ">>>"
    )
