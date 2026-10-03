"""``match_profile``: for each requirement, find the profile evidence that backs it.

Reads the checklist ``analyze_job`` produced and the user's profile, and marks every
requirement ``supported`` / ``partial`` / ``gap`` with evidence pointers
(``experience:12#s3``) into the profile. This is the evidence map the writer later
cites from and the claim checker verifies against: a letter claim with no pointer has
nothing to stand on.

Separate from ``match.py``, which stays the cheap whole-job score deciding *whether*
a letter is worth writing. This is the per-requirement view of *what* it can say.

The model proposes; code disposes (``_validate``): pointers must resolve to real
profile text, a bare skills-list entry can't make a requirement ``supported`` by
itself, and "supported/partial with no valid evidence" is demoted to ``gap``. A model
that hallucinates a pointer or inflates its confidence is corrected here, not trusted.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel

from app.llm.client import complete_json
from app.llm.letter.runner import ToolContext, ToolError
from app.llm.letter.state import LetterState

logger = logging.getLogger(__name__)

MAX_EVIDENCE = 3


class MatchedRequirement(BaseModel):
    id: str
    status: Literal["supported", "partial", "gap"]
    evidence: list[str]
    note: str


class ProfileMatch(BaseModel):
    matches: list[MatchedRequirement]


_SYSTEM_PROMPT = """\
You compare a job's requirements against ONE candidate's profile, to decide what a \
cover letter can honestly claim. Judge only from the profile text given; never assume \
the candidate has experience that is not written there.

For EVERY requirement id, return a status, evidence pointers and a short note.

status:
- supported: the profile directly shows the candidate has done, used or holds this, at \
the level the requirement asks, as the SAME kind of activity. Building a product about \
X is not using X as a tool in day-to-day work (an AI thesis does not show "uses AI \
tools in daily delivery"); studying X is not working with X in production. A shared \
subject is not evidence: if the activity differs, it is a gap (the candidate is then \
asked, and may well have it). Partial means the same kind of activity with a different \
tool or at a lower level, not a different activity on the same topic.
- partial: related but not the same thing (Tableau when Power BI is asked), shown only \
lightly (one university project when "strong experience" is asked), or only listed as \
a skill with no experience demonstrating it. The letter can frame these as related \
experience but must not claim more. A requirement that bundles several asks \
("software development with a focus on cyber security") is partial when the profile \
covers some of them.
- gap: nothing in the profile supports it. Before choosing gap, check BOTH the skills \
list and every experience sentence: if a listed skill or a sentence relates to the \
requirement, it is partial, not gap. Use gap rather than stretching.

Be consistent across requirements: a profile sentence that counts as evidence for one \
requirement (e.g. teamwork, customer requests, software development, problem solving) \
counts for every other requirement it equally supports.

evidence: pointers copied EXACTLY from the square brackets in the profile, e.g. \
experience:12#s3 (the text between the brackets, WITHOUT the brackets). Prefer the narrowest pointer (a sentence, not the whole role). At \
most 3. Leave empty for gap. Never invent or alter a pointer. A skills-list entry \
alone is weak: cite the experience sentence that demonstrates the skill when there is \
one.

note: one short sentence saying why, naming what is matched or missing (for example \
"Tableau at uni, not Power BI" or "no cloud deployment mentioned").

Requirements marked implied are things a competent person in the headline item's area \
would have. Judge them the same way, and they may share evidence with the item they \
follow from.

Requirements marked not_for_letter are eligibility items (work rights, licence, \
clearance, availability). Judge them against the PROFILE FACTS only; evidence stays \
empty. supported only if a profile fact clearly states it, otherwise gap with the note \
"not stated in profile". A location matching the profile's location or target \
location is supported. Terms the candidate accepts simply by applying (contract \
length, full-time hours, start in the office) are not gaps: judge only the parts a \
profile fact could confirm or contradict, and if a fact confirms those, it is \
supported.
"""


def _build_prompt(state: LetterState, ctx: ToolContext) -> str:
    req_lines = []
    for r in state.requirements:
        implied = f" (follows from {', '.join(r.implied_by)})" if r.implied_by else ""
        req_lines.append(f"{r.id} [{r.importance} / {r.letter_role}] {r.text}{implied}")
    facts = "\n".join(f"- {k}: {v}" for k, v in ctx.index.facts.items()) or "- (none on file)"
    return (
        "REQUIREMENTS\n" + "\n".join(req_lines)
        + "\n\nPROFILE (cite pointers from the square brackets)\n" + ctx.index.prompt_catalog()
        + "\n\nPROFILE FACTS (for eligibility items only; not citable)\n" + facts
    )


def _validate(state: LetterState, ctx: ToolContext, proposed: ProfileMatch) -> dict[str, Any]:
    """Apply the model's matches to the state, correcting what code can check."""
    by_id = {m.id.strip(): m for m in proposed.matches}
    corrections: list[str] = []

    for req in state.requirements:
        m = by_id.get(req.id)
        if m is None:
            req.status, req.evidence = "gap", []
            req.note = "not assessed by the model"
            corrections.append(f"{req.id}: missing from the model's answer -> gap")
            continue

        valid: list[str] = []
        for pointer in m.evidence:
            # Models sometimes copy the surrounding brackets or quote the pointer.
            pointer = pointer.strip().strip("[]`'\" ").strip()
            if pointer in valid:
                continue
            if ctx.index.resolve(pointer) is None:
                corrections.append(f"{req.id}: dropped unresolvable pointer {pointer!r}")
                continue
            valid.append(pointer)
        valid = valid[:MAX_EVIDENCE]

        status = m.status
        if req.letter_role == "not_for_letter":
            valid = []  # eligibility is judged from profile facts, never cited in a letter
        elif status in ("supported", "partial") and not valid:
            corrections.append(f"{req.id}: {status} with no valid evidence -> gap")
            status = "gap"
        elif status == "supported" and all(p.startswith("skill:") for p in valid):
            corrections.append(f"{req.id}: only a listed skill backs it -> partial")
            status = "partial"
        if status == "gap":
            valid = []

        req.status, req.evidence = status, valid
        req.note = " ".join((m.note or "").split()) or None

    counts: dict[str, int] = {}
    for r in state.requirements:
        counts[r.status] = counts.get(r.status, 0) + 1
    return {
        "by_status": counts,
        "pending_gaps": [r.id for r in state.pending_gaps()],
        "corrections": corrections,
    }


def match_profile(state: LetterState, ctx: ToolContext) -> dict[str, Any]:
    """Map every requirement to profile evidence and mark it supported/partial/gap.

    Use after ``analyze_job`` and before drafting. Re-run for a single requirement's
    worth of new evidence after the user adds profile rows (it re-judges all of them;
    user decisions are kept).
    """
    if not state.requirements:
        raise ToolError("no requirements yet: run analyze_job first")

    data = complete_json(
        _SYSTEM_PROMPT,
        _build_prompt(state, ctx),
        schema=ProfileMatch,
        tier="mid",
        task="match_profile",
        job_id=state.job.job_id,
        run_id=ctx.run.id,
    )
    summary = _validate(state, ctx, ProfileMatch.model_validate(data))
    if summary["corrections"]:
        logger.info("match_profile corrected %d item(s): %s", len(summary["corrections"]), summary["corrections"])
    return summary
