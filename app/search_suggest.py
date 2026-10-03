"""Search-phrase suggestions mined from the user's own match history.

Replaces the raw bigram/trigram *frequency* counting that shipped with the
extension revamp. Frequency measures what the user already searched for, not
what paid off: on the dev profile ``software engineer`` appeared in 12 titles
but averaged 60.9 against a 69.4 baseline, while it emitted fragments like
``intermediate software`` and ``experienced software`` that nobody would type
into Seek.

The ranking here is score-weighted instead, in four steps (see the module-level
constants and ``rank_phrases`` for the detail):

1. **Segment** the title and drop modifier tokens, so company/logistics junk
   (``| I.T Services | 5 Days Onsite``) and seniority words never reach a
   phrase. Seniority is a *filter* on Seek, not a useful keyword — employers
   rarely write "Intermediate" in a title, so searching it shrinks the result
   set rather than focusing it.
2. **Gate on a role noun**: a phrase must end in a word from ``ROLE_NOUNS``.
   This is a poor-man's POS tag, and on job titles it is reliable enough to
   delete essentially every fragment in one rule.
3. **Rank by shrunk lift x log support.** Shrinkage matters more than the lift:
   without it a single 92-point job hands you a top suggestion off n=1.
4. **Deduplicate**, first nested phrases (``intermediate software`` is a
   substring of ``intermediate software engineer``) and then whole families by
   token overlap, so three slots never hold three flavours of one role.

Pure Python — no LLM call. The optional LLM re-ranker is a separate, opt-in
layer that consumes this module's output as its candidate pool
(``app/llm/search_refine.py``).
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

# Tokens stripped from a title before phrases are built. Three groups, all
# noise for the same reason — they describe the *posting*, not the role, so
# they only ever narrow a Seek keyword search:
#   - grammar glue
#   - seniority/experience level (the big one; see the module docstring)
#   - contract/logistics wording
MODIFIER_TOKENS = frozenset({
    # grammar glue
    "a", "an", "the", "and", "or", "for", "to", "of", "in", "at", "with",
    # seniority / experience level
    "graduate", "grad", "senior", "snr", "junior", "jnr", "mid", "intermediate",
    "experienced", "lead", "principal", "staff", "trainee", "entry", "level",
    "beginner", "advanced", "expert",
    # posting / logistics noise
    "new", "role", "position", "opportunity", "opportunities", "wanted",
    "required", "urgent", "immediate", "month", "months", "contract", "casual",
    "permanent", "temp", "temporary", "onsite", "remote", "hybrid", "days",
    "day", "full", "part", "time", "wfh", "home", "work", "start", "asap",
})

# A phrase is only offered as a search term if its LAST token is one of these.
# Deliberately a closed list of role *head nouns* — the word a job title ends
# in. Adding a word here widens what counts as a role; adding one to
# MODIFIER_TOKENS above narrows what counts as noise. They are different jobs,
# so keep them separate.
ROLE_NOUNS = frozenset({
    "engineer", "engineering", "developer", "development", "programmer",
    "analyst", "analytics", "scientist", "architect", "designer", "tester",
    "assistant", "officer", "administrator", "admin", "administration",
    "clerk", "coordinator", "manager", "supervisor", "lead", "director",
    "technician", "consultant", "specialist", "advisor", "associate",
    "operator", "support", "receptionist", "bookkeeper", "accountant",
    "secretary", "representative", "agent", "planner", "strategist",
})

# Title is split on these before phrases are built; only the FIRST segment is
# kept. Seek titles routinely append the employer, the contract shape and the
# office policy after a separator — "Junior .Net Developer | I.T Services |
# 5 Days Onsite" — and none of that is part of the role name.
_SEGMENT_SPLIT = re.compile(r"[|/()\[\]–—]|\s[-‐‑]\s|,")

_WORD = re.compile(r"[A-Za-z]+")

# Strength of the prior pulling a phrase's mean back toward the baseline. At
# K=3 a single 92-point observation lands at ~75 rather than 92, so one lucky
# job can no longer outrank a phrase with real support behind it. Raise it to
# demand more evidence per suggestion; lower it to react faster on a thin
# history.
SHRINKAGE_K = 3.0

# Jaccard overlap (on token sets) at or above which two phrases are treated as
# the same role family, so only the better-ranked one survives.
FAMILY_OVERLAP = 0.5

# Phrase lengths mined from each title segment.
_NGRAM_SIZES = (2, 3)


@dataclass
class PhraseStat:
    """One candidate search phrase plus the evidence behind it.

    The diagnostics are not cosmetic: they are what the API returns so the
    sidebar can explain *why* a phrase was suggested, and what the optional
    LLM re-ranker is given instead of raw job text.
    """

    phrase: str
    support: int = 0            # how many scored titles contain it (raw count)
    scores: list[float] = field(default_factory=list)
    weights: list[float] = field(default_factory=list)  # parallel to ``scores``
    rank: float = 0.0           # shrunk lift x log support
    shrunk_mean: float = 0.0

    @property
    def effective_support(self) -> float:
        """Sum of weights — what ``support`` is worth once stale evidence is discounted."""
        return sum(self.weights)

    @property
    def mean(self) -> float:
        total = self.effective_support
        if not total:
            return 0.0
        return sum(s * w for s, w in zip(self.scores, self.weights)) / total

    def as_dict(self) -> dict:
        return {
            "phrase": self.phrase,
            "support": self.support,
            "avg_score": round(self.mean, 1),
            "shrunk_score": round(self.shrunk_mean, 1),
            "rank": round(self.rank, 2),
        }


def title_segments(title: str) -> list[str]:
    """The leading segment of a title — the part that actually names the role."""
    parts = [p.strip() for p in _SEGMENT_SPLIT.split(title or "") if p.strip()]
    return parts[:1]


def phrases_in_title(title: str) -> set[str]:
    """Role-noun-terminated bigrams/trigrams from a title's leading segment.

    A set, not a list: a phrase repeated within one title is still a single
    piece of evidence about that title's score.
    """
    found: set[str] = set()
    for segment in title_segments(title):
        words = [w for w in _WORD.findall(segment.lower()) if w not in MODIFIER_TOKENS]
        for size in _NGRAM_SIZES:
            for i in range(len(words) - size + 1):
                window = words[i : i + size]
                if window[-1] in ROLE_NOUNS:
                    found.add(" ".join(window))
    return found


def rank_phrases(
    scored_titles: Sequence[tuple],
    *,
    shrinkage_k: float = SHRINKAGE_K,
) -> list[PhraseStat]:
    """Rank phrases by ``(shrunk_mean - baseline) * log1p(support)``, best first.

    ``scored_titles`` is every scored match for the user — the *whole*
    distribution, not just the good ones. The low scores are what make the
    baseline meaningful; filtering to score>=75 first would leave every phrase
    looking above average.

    Each item is ``(title, score)`` or ``(title, score, weight)``; a missing
    weight is 1.0. Weights discount matches scored against an out-of-date
    profile (see ``app/retention.py``). They enter everywhere a count or a mean
    did: ``support`` becomes the sum of weights, and the baseline is weighted
    with the SAME weights — otherwise a discounted phrase mean would be
    compared to an undiscounted baseline and every phrase would drift down
    together. With all weights 1.0 this is exactly the unweighted formula.
    """
    entries = [
        (item[0], float(item[1]), float(item[2]) if len(item) > 2 else 1.0)
        for item in scored_titles
    ]
    entries = [e for e in entries if e[2] > 0]
    total_weight = sum(w for _t, _s, w in entries)
    if not total_weight:
        return []

    baseline = sum(score * w for _t, score, w in entries) / total_weight

    stats: dict[str, PhraseStat] = {}
    for title, score, weight in entries:
        for phrase in phrases_in_title(title):
            stat = stats.setdefault(phrase, PhraseStat(phrase=phrase))
            stat.scores.append(score)
            stat.weights.append(weight)
            stat.support += 1

    for stat in stats.values():
        # Bayesian shrink toward the baseline: a phrase seen once is mostly
        # prior, a phrase seen often is mostly its own mean.
        weight = stat.effective_support
        weighted_sum = sum(s * w for s, w in zip(stat.scores, stat.weights))
        stat.shrunk_mean = (weighted_sum + shrinkage_k * baseline) / (weight + shrinkage_k)
        stat.rank = (stat.shrunk_mean - baseline) * math.log1p(weight)

    ranked = sorted(stats.values(), key=lambda s: (-s.rank, -s.support, s.phrase))
    return [s for s in ranked if s.rank > 0]


def deduplicate(stats: list[PhraseStat]) -> list[PhraseStat]:
    """Drop nested phrases, then near-duplicate role families.

    Input must already be ranked best-first: both passes keep whichever phrase
    they meet first and discard the later, worse-ranked duplicate.
    """
    kept: list[PhraseStat] = []
    for stat in stats:
        if any(stat.phrase in k.phrase or k.phrase in stat.phrase for k in kept):
            continue
        kept.append(stat)

    families: list[PhraseStat] = []
    for stat in kept:
        tokens = set(stat.phrase.split())
        if any(
            len(tokens & set(k.phrase.split())) / len(tokens | set(k.phrase.split()))
            >= FAMILY_OVERLAP
            for k in families
        ):
            continue
        families.append(stat)
    return families


def mine(scored_titles: Sequence[tuple]) -> list[PhraseStat]:
    """``rank_phrases`` then ``deduplicate`` — the whole Layer-1 pipeline."""
    return deduplicate(rank_phrases(scored_titles))
