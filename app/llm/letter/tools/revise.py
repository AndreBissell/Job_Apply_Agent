"""``revise_letter``: targeted fixes to the latest draft, for its failed checks only.

An edit, not a rewrite. The reviser gets the same system prompt and context block
as ``generate_letter`` (so the call reuses the cached prefix), then the current
draft and exactly the issues the checks listed. Blocking issues must be fixed;
warnings are fixed only if that is easy, so a revision for one bad claim doesn't
churn the rest of the letter.

How much text a revision changed is measured (``changed_pct``) but not yet
enforced: the plan's "reject a revision that changes more than X%" is a 🧪 to set
from the eval runs (plan §5.7).
"""

from __future__ import annotations

import difflib
from typing import Any

from app.llm.letter import guardrails
from app.llm.letter.runner import ToolContext, ToolError
from app.llm.letter.state import REQUIRED_CHECKS, LetterState
from app.llm.letter.tools.generate import call_writer, draft_summary
from app.llm.letter.tools.style_lint import MAX_WORDS, word_count

_HOW_TO_FIX = {
    "claims": "a claim the profile doesn't back: reword it to what the cited profile text actually says, or cut it",
    "requirements": "a must-address requirement the letter misses: add it, using only its evidence from the LETTER PLAN",
    "style": "a style rule broken: fix it without changing what the letter claims",
}


def changed_pct(before: str, after: str) -> int:
    """Share of the letter's words that changed, 0-100."""
    ratio = difflib.SequenceMatcher(a=before.split(), b=after.split(), autojunk=False).ratio()
    return round(100 * (1 - ratio))


def _task_block(state: LetterState) -> str:
    draft = state.latest_draft
    blocking: list[str] = []
    optional: list[str] = []
    for name in REQUIRED_CHECKS:
        check = draft.checks.get(name)
        if check is None:
            continue
        if not check.passed:
            blocking.append(f"[{name}: {_HOW_TO_FIX[name]}]")
            blocking += [f"- {i}" for i in check.issues]
        optional += [f"- ({name}) {w}" for w in check.warnings]

    lines = [
        f"Revise draft {draft.version}. This is an edit, not a rewrite: fix ONLY the problems "
        "below and keep every other sentence as it is, unless a fix forces it to change.",
        "",
        "MUST FIX:",
        *blocking,
    ]
    if optional:
        lines += ["", "FIX ONLY IF IT IS EASY AND CHANGES NOTHING ELSE:", *optional]
    lines += [
        "",
        "While fixing these, do not break what already works: every MUST ADDRESS requirement "
        "in the LETTER PLAN must still be addressed afterwards, and the letter must stay about "
        f"the same length (it is {word_count(draft.text)} words; the limit is {MAX_WORDS}). If "
        "you add something, make room by tightening another sentence. Keep each paragraph's "
        "link to this employer's work and the specific closing: when you cut or reword a claim, "
        "don't replace it with a generic sentence or a stock close.",
        "",
        f"CURRENT DRAFT {draft.version}:",
        "<<<",
        draft.text,
        ">>>",
        "",
        "Return the full revised letter, and the claims list for the WHOLE revised letter "
        "(every claim in it, not only the ones you changed).",
    ]
    return "\n".join(lines)


def revise_letter(state: LetterState, ctx: ToolContext) -> dict[str, Any]:
    """Fix the latest draft's failed checks with a narrow edit; creates the next draft.

    Use after check_claims, check_requirements and style_lint have all run on the
    latest draft and at least one failed. Fixes only the listed issues. Do not use
    for the first draft (generate_letter) or when every check passed (finish).
    Run all three checks again on the new draft: a pass on the old one says nothing
    about it.
    """
    refusal = guardrails.can_revise(state)
    if refusal:
        raise ToolError(refusal)
    before = state.latest_draft
    fixing = guardrails.failed_checks(state)
    text, claims = call_writer(state, ctx, _task_block(state), task="revise_letter")
    state.add_draft(text, claims)
    return {
        **draft_summary(state),
        "revised_from": before.version,
        "fixing": fixing,
        "changed_pct": changed_pct(before.text, text),
    }
