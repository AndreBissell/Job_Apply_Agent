"""``check_requirements``: does the latest draft still address every must-cover item?

After a revision, coverage is rechecked by requirement id instead of by rereading
everything (plan §5.3). Which items must be covered is ``guardrails.must_cover``,
the same list the writer was told to address: headline items and essential
mentions the profile supports, fully or partly. A missing one blocks. ``may_use``
items are reported but never required: a one-page letter can't name 15 things.

The small model must quote the words in the letter that address each item, and
code checks the quote is really there, so "addressed" can't rest on a sentence
the checker imagined.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

from app.llm.client import complete_json
from app.llm.letter import guardrails
from app.llm.letter.runner import ToolContext, ToolError
from app.llm.letter.state import Check, LetterState

REQUIREMENTS_TIER = "small"
# Share of a quote's words that must appear in the letter for it to count as found
# (models drop or swap a word when quoting; an invented quote shares little).
QUOTE_MATCH = 0.8


class Coverage(BaseModel):
    id: str
    addressed: bool
    quote: str  # the letter's words that address it; empty if not addressed


class CoverageReport(BaseModel):
    items: list[Coverage]


_SYSTEM_PROMPT = """\
You check whether a cover letter addresses specific job requirements. For EVERY \
requirement id listed, decide whether the letter addresses it: it says the candidate \
has, has done or has related experience with that thing, in words a hiring manager \
would recognise as answering it. The wording does not need to match the ad; grouping \
several requirements into one sentence is fine. A requirement is NOT addressed if it \
is only hinted at, only appears as a generic phrase ("my technical skills"), or is \
mentioned only as something the employer wants.

Items marked PARTIAL are ones the candidate only has related experience for (Tableau \
when Power BI is asked, customer requests when internal stakeholders are asked). The \
letter must NOT claim the full thing, so for these, addressed means the letter \
presents the related experience and connects it to this requirement. That honest \
framing counts as addressed.

quote: copy the exact words from the letter that address it (one sentence or less). \
Empty when it is not addressed."""


_WORD_RE = re.compile(r"[a-z0-9]+")


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower().replace("’", "'"))


def quote_in_letter(quote: str, letter: str) -> bool:
    q = _words(quote)
    if not q:
        return False
    if " ".join(q) in " ".join(_words(letter)):
        return True
    present = set(_words(letter))
    return sum(1 for w in q if w in present) / len(q) >= QUOTE_MATCH


def check_requirements(state: LetterState, ctx: ToolContext, tier: str = REQUIREMENTS_TIER) -> dict[str, Any]:
    """Confirm the latest draft addresses every requirement it must cover.

    Use after every new draft, alongside check_claims and style_lint. If it fails,
    pass the uncovered ids to revise_letter, which adds them from their evidence.
    """
    draft = state.latest_draft
    if draft is None:
        raise ToolError("check_requirements needs a draft; call generate_letter first")
    required = guardrails.must_cover(state)
    optional = guardrails.may_use(state)
    to_check = required + optional
    if not to_check:
        state.record_check("requirements", Check(passed=True))
        return {"draft": draft.version, "passed": True, "must_cover": 0, "uncovered": [], "optional_covered": []}

    user = (
        "REQUIREMENTS\n"
        + "\n".join(f"{r.id}{' (PARTIAL)' if r.status == 'partial' else ''}: {r.text}" for r in to_check)
        + f"\n\nLETTER\n{draft.text}"
    )
    data = complete_json(
        _SYSTEM_PROMPT, user, schema=CoverageReport, tier=tier, task="check_requirements",
        job_id=state.job.job_id, match_id=ctx.run.match_id, run_id=ctx.run.id,
    )
    report = CoverageReport.model_validate(data)
    found = {
        c.id.strip(): c for c in report.items
        if c.addressed and quote_in_letter(c.quote, draft.text)
    }

    uncovered = [r for r in required if r.id not in found]
    issues = [f"{r.id} not addressed: {r.text!r}" for r in uncovered]
    state.record_check("requirements", Check(passed=not issues, issues=issues))
    return {
        "draft": draft.version,
        "passed": not issues,
        "must_cover": len(required),
        "uncovered": [r.id for r in uncovered],
        "optional_covered": [r.id for r in optional if r.id in found],
        "unverified_quotes": [
            c.id for c in report.items if c.addressed and not quote_in_letter(c.quote, draft.text)
        ],
    }
