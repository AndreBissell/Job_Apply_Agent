"""``suggest_learning``: concrete ways to close the gaps the user confirmed (plan §5.6).

Only CONFIRMED gaps (``guardrails.confirmed_gaps``): a "No" to ask_user on this run, or a
remembered one from an earlier ad. A gap nobody asked about may be one the user has and
never wrote down, so it gets no learning advice. One suggestion per gap: a course, a
certification, a small project or practice, kept short.

The order is code's, not the model's: gaps are ranked by the to-work-on list (plan §5.9),
so the suggestions lead with the skill the most recent ads ask for. Unranked gaps come
after, essential before important.

Advice, not claims about the candidate, so there are no evidence pointers to check. Code
still removes links (a model's course URL is a guess) and drops suggestions for ids that
aren't confirmed gaps.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel

from app import gaps
from app.llm.client import complete_json
from app.llm.letter import guardrails
from app.llm.letter.runner import ToolContext, ToolError
from app.llm.letter.state import LetterState, Requirement
from app.llm.letter.tools.check_claims import _LINK_RE

LEARNING_TIER = "small"
_IMPORTANCE = {"essential": 0, "important": 1, "nice_to_have": 2}


class Suggestion(BaseModel):
    id: str  # the requirement id
    kind: Literal["course", "certification", "project", "practice"]
    suggestion: str
    effort: str  # rough time, e.g. "a weekend", "2-4 weeks"


class LearningPlan(BaseModel):
    suggestions: list[Suggestion]


_SYSTEM_PROMPT = """\
You help a job seeker close skill gaps they have confirmed they have. For EACH gap \
listed, give ONE concrete, realistic way to close it: a well-known course or \
certification by its name (no links), or a small project they could build and show, or \
a focused way to practise. Prefer what an employer would recognise and what a graduate \
can finish in weeks, not years. Say what to do, in one or two sentences, addressed to \
the candidate ("Build a small Power BI dashboard from a public dataset..."). effort: \
rough time it takes ("a weekend", "2-4 weeks"). Never claim the candidate already has \
any of it, and never include links or prices."""


def _strip_links(text: str) -> str:
    return " ".join(_LINK_RE.sub("", text).split())


def ranked_gaps(state: LetterState, ctx: ToolContext) -> list[tuple[Requirement, gaps.WorkItem | None, int | None]]:
    """Confirmed gaps, highest on the to-work-on list first, each with its list item and
    1-based rank (None when it isn't on the list)."""
    items = gaps.to_work_on(ctx.db, state.profile_id)
    out = []
    for r in guardrails.confirmed_gaps(state):
        key = gaps.skill_key(r.skill, r.text)
        rank = next(
            (n for n, i in enumerate(items, start=1)
             if i.skill_key == key or (r.skill and gaps.skill_matches(i.skill_key, r.skill))),
            None,
        )
        out.append((r, items[rank - 1] if rank else None, rank))
    out.sort(key=lambda t: (t[2] is None, t[2] or 0, _IMPORTANCE.get(t[0].importance, 9), t[0].id))
    return out


def suggest_learning(state: LetterState, ctx: ToolContext, tier: str = LEARNING_TIER) -> dict[str, Any]:
    """Suggest one concrete way to close each gap the user confirmed, ranked by the
    to-work-on list. Use once per run, after the gaps are decided. Do not use when no
    gap was confirmed by the user."""
    ranked = ranked_gaps(state, ctx)
    if not ranked:
        raise ToolError("no gap the user confirmed: there is nothing to suggest learning for")
    lines = []
    for r, item, _ in ranked:
        seen = f" (asked for in {item.recent} recent ads)" if item and item.recent else ""
        lines.append(f"{r.id} [{r.importance}] {r.skill or r.text}: the ad asks for \"{r.text}\"{seen}")
    data = complete_json(
        _SYSTEM_PROMPT, "GAPS\n" + "\n".join(lines), schema=LearningPlan, tier=tier,
        task="suggest_learning", job_id=state.job.job_id, match_id=ctx.run.match_id, run_id=ctx.run.id,
    )
    by_id: dict[str, Suggestion] = {}
    for s in LearningPlan.model_validate(data).suggestions:
        by_id.setdefault(s.id.strip(), s)

    out: list[dict] = []
    for r, item, rank in ranked:
        s = by_id.get(r.id)
        text = _strip_links(s.suggestion) if s else ""
        if not text:
            continue
        out.append({
            "requirement_id": r.id, "skill": r.skill or r.text, "requirement": r.text,
            "importance": r.importance, "kind": s.kind, "suggestion": text,
            "effort": _strip_links(s.effort), "rank": rank,
            "recent_ads": item.recent if item else None,
        })
    state.side_outputs.learning_suggestions = out
    return {
        "gaps": len(ranked),
        "suggestions": len(out),
        "leads_with": out[0]["skill"] if out else None,
        "ignored_ids": sorted(set(by_id) - {r.id for r, _, _ in ranked}),
    }
