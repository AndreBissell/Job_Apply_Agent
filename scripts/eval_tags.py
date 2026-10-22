"""The eval scenarios' tagging rule, in one place (docs/eval-set-plan.md, "Scenarios").

Used by scripts/eval_pool.py (provisional tags from real.db) and scripts/eval_tag.py (final
tags from the frozen set's eval copy). No side effects on import, and it reads no DB: the
callers hand it a score and the requirements of an analysis run.

Terms, as the agent's code defines them:
  must-have gap  = Requirement.needs_user (essential, headline/mention, status gap, no decision)
  decided gap    = the same requirement, but a user decision is set on it (e.g. a remembered
                   "no" that apply_remembered applied), so needs_user is False
  nice-to-have   = importance nice_to_have, any letter role but not_for_letter; unsupported =
                   status partial or gap
  listing-only   = guardrails.listing_only: backed only by skills the profile lists
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.llm.letter.guardrails import listing_only
from app.llm.letter.state import Requirement

UNRELATED_BELOW = 40
STRONG_FROM = 85
BORDERLINE_FROM = 70


@dataclass
class Facts:
    must_gaps: list[Requirement] = field(default_factory=list)
    decided_gaps: list[Requirement] = field(default_factory=list)
    nice_unsupported: list[Requirement] = field(default_factory=list)
    listing_only: list[Requirement] = field(default_factory=list)
    essential_partials: int = 0


def facts(requirements: list[Requirement] | list[dict]) -> Facts:
    reqs = [r if isinstance(r, Requirement) else Requirement.model_validate(r) for r in requirements]
    return Facts(
        must_gaps=[r for r in reqs if r.needs_user],
        decided_gaps=[r for r in reqs if r.importance == "essential" and r.letter_role in ("headline", "mention")
                      and r.status == "gap" and r.user_decision is not None],
        nice_unsupported=[r for r in reqs if r.importance == "nice_to_have" and r.letter_role != "not_for_letter"
                          and r.status in ("partial", "gap")],
        listing_only=[r for r in reqs if listing_only(r)],
        essential_partials=sum(1 for r in reqs if r.importance == "essential"
                               and r.letter_role != "not_for_letter" and r.status == "partial"),
    )


def label(r: Requirement) -> str:
    return r.skill or r.text[:60]


def scenario(score: int, *, must_gap: bool, nice_unsupported: bool, listing_only: bool,
             listing_min_score: int | None = None) -> int | None:
    """First match wins: 7, then 85+ (3, 2 or 1), then 6, then 4, then 3. None = no scenario.

    listing_min_score: eval_pool's provisional tags only count listing-only from 60 up; the
    plan's table has no floor, so the final tags pass None."""
    if score < UNRELATED_BELOW:
        return 7
    if score >= STRONG_FROM:
        return 3 if must_gap else 2 if nice_unsupported else 1
    if score >= BORDERLINE_FROM:
        return 6
    if listing_only and (listing_min_score is None or score >= listing_min_score):
        return 4
    if must_gap:
        return 3
    return None
