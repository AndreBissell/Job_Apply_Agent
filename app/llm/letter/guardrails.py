"""Rules every cover-letter run obeys, enforced in code (docs/cover-letter-loop-plan.md §5.7).

Models declare victory early, skip checks and loop, so the rules that must always
hold live here and the tools call them before doing any work. The fixed workflow
(Phase 6) and the agent (Phase 7) share these, which keeps their comparison fair.
Every refusal is a sentence written for the orchestrator, naming what is missing,
so a refused step can be recovered from.

This module is also the ONE definition of what the letter does with each
requirement. The writer, the reviser and ``check_requirements`` all read it, so
"what the letter must cover" can't mean three different things:

    must_cover     headline items, and essential mentions, the profile supports
                   (fully or partly) with more than a skill listing.
                   check_requirements BLOCKS if one is missing
    may_use        other supported/partial mentions and implied items, and anything
                   backed only by listed skills: use if they fit, never required
    do_not_claim   gaps and anything the user chose to leave out: not mentioned,
                   not written around
    eligibility    not_for_letter items: never in the letter (a job-card note)
"""

from __future__ import annotations

from app.llm.letter.state import REQUIRED_CHECKS, Draft, LetterState, Requirement

_COVERED = ("supported", "partial")


def _left_out(r: Requirement) -> bool:
    return r.user_decision is not None and r.user_decision.choice == "leave_out"


# ---------------------------------------------------------------------------
# What the letter does with each requirement
# ---------------------------------------------------------------------------
def listing_only(r: Requirement) -> bool:
    """Backed only by skills the profile lists, with no experience entry describing
    their use. A listing backs "skills in X", never "experience with X", so such an
    item can't be required: requiring it produced gap-led "I have not ..." sentences
    and overclaims in 7 of 15 eval letters (decision log 2026-10-03)."""
    return bool(r.evidence) and all(p.startswith("skill:") for p in r.evidence)


def must_cover(state: LetterState) -> list[Requirement]:
    return [
        r for r in state.requirements
        if r.status in _COVERED
        and not _left_out(r)
        and not listing_only(r)
        and (r.letter_role == "headline" or (r.letter_role == "mention" and r.importance == "essential"))
    ]


def may_use(state: LetterState) -> list[Requirement]:
    required = {r.id for r in must_cover(state)}
    return [
        r for r in state.requirements
        if r.status in _COVERED
        and not _left_out(r)
        and (r.letter_role in ("mention", "implied") or (r.letter_role == "headline" and listing_only(r)))
        and r.id not in required
    ]


def do_not_claim(state: LetterState) -> list[Requirement]:
    return [
        r for r in state.requirements
        if r.letter_role != "not_for_letter" and (r.status == "gap" or _left_out(r))
    ]


def eligibility(state: LetterState) -> list[Requirement]:
    return state.requirements_by_role("not_for_letter")


def confirmed_gaps(state: LetterState) -> list[Requirement]:
    """Gaps the USER confirmed as real: a "No" to ask_user on this run, or a remembered
    one from an earlier ad. A gap the evals' policy left out without asking is not one,
    nor is an eligibility item. What suggest_learning works from (plan §5.6)."""
    return [
        r for r in state.requirements
        if r.letter_role != "not_for_letter" and _left_out(r) and not r.user_decision.assumed
    ]


# ---------------------------------------------------------------------------
# Gates. Each returns None when the step may run, else the reason it may not.
# ---------------------------------------------------------------------------
def drafting_blocked(state: LetterState) -> str | None:
    """No silent gaps: nothing is drafted until every requirement has been matched
    and every must-have gap has a user decision."""
    if not state.requirements:
        return "no requirements yet: run analyze_job, then match_profile"
    unmatched = [r.id for r in state.requirements if r.status == "unknown"]
    if unmatched:
        return f"requirements not matched against the profile yet ({', '.join(unmatched)}): run match_profile"
    pending = state.pending_gaps()
    if pending:
        listed = "; ".join(f"{r.id} {r.text!r}" for r in pending)
        return f"must-have gaps need a user decision before drafting: {listed}. Call ask_user"
    return None


def can_generate(state: LetterState) -> str | None:
    """generate_letter writes draft 1 only; every later draft is a revision."""
    if state.drafts:
        return (
            f"draft {state.latest_draft.version} already exists: use revise_letter to fix its "
            "failed checks (only the first draft is generated)"
        )
    return drafting_blocked(state)


# Check tool name -> the key its result is stored under on a draft.
CHECK_TOOLS = {"check_claims": "claims", "check_requirements": "requirements", "style_lint": "style"}
_CHECK_TOOL_OF = {v: k for k, v in CHECK_TOOLS.items()}


def can_check(state: LetterState, tool: str) -> str | None:
    """A check runs once per draft: its result belongs to that draft, and running it
    again on the same text only spends money (and a small model may flip its verdict)."""
    draft = state.latest_draft
    if draft is None:
        return f"no draft to check yet: call generate_letter before {tool}"
    check = draft.checks.get(CHECK_TOOLS[tool])
    if check is not None:
        return (
            f"{tool} already ran on draft {draft.version} ({'pass' if check.passed else 'FAIL'}); "
            "a check runs once per draft, so run it again only on a new draft"
        )
    return None


def checks_not_run(state: LetterState) -> list[str]:
    draft = state.latest_draft
    return [n for n in REQUIRED_CHECKS if draft is None or n not in draft.checks]


def failed_checks(state: LetterState) -> list[str]:
    draft = state.latest_draft
    if draft is None:
        return []
    return [n for n in REQUIRED_CHECKS if n in draft.checks and not draft.checks[n].passed]


def can_revise(state: LetterState) -> str | None:
    """A revision fixes listed failures on the latest draft, within the draft cap.

    All three checks must have run first: revising after one failed check and
    finding another failure afterwards would spend two drafts on one fix.
    """
    blocked = drafting_blocked(state)
    if blocked:
        return blocked
    draft = state.latest_draft
    if draft is None:
        return "there is no draft to revise: call generate_letter first"
    if len(state.drafts) >= state.budget.max_drafts:
        return (
            f"draft limit reached ({len(state.drafts)}/{state.budget.max_drafts}): no more revisions; "
            "finish with the best draft and flag its open issues"
        )
    missing = checks_not_run(state)
    if missing:
        return f"run {', '.join(_CHECK_TOOL_OF[n] for n in missing)} on draft {draft.version} before revising it"
    if not failed_checks(state):
        return f"every check passed on draft {draft.version}: there is nothing to revise; call finish"
    return None


def can_finish(state: LetterState) -> str | None:
    """Finish gate: all three checks ran and passed on the LATEST draft."""
    draft = state.latest_draft
    if draft is None:
        return "no draft yet: call generate_letter"
    missing = checks_not_run(state)
    if missing:
        return f"{', '.join(missing)} not checked on draft {draft.version} (the latest)"
    failed = failed_checks(state)
    if failed:
        return f"{', '.join(failed)} failed on draft {draft.version}: revise_letter, or stop at the draft limit"
    return None


def letter_final(state: LetterState) -> Draft | None:
    """The draft the run will hand back, once the letter's work is over: the latest when
    every check passed on it, the best draft when the draft limit is reached with a check
    still failing; None while the letter can still change. What suggest_resume_tweaks
    reads, so its advice matches the letter the user gets."""
    if can_finish(state) is None:
        return state.latest_draft
    if out_of_drafts(state):
        return best_draft(state)
    return None


def out_of_drafts(state: LetterState) -> bool:
    """Nothing is left to try: the draft cap is reached, every check ran on the latest
    draft and one failed. The run ends with the best draft and its open issues."""
    return (
        state.latest_draft is not None
        and len(state.drafts) >= state.budget.max_drafts
        and not checks_not_run(state)
        and bool(failed_checks(state))
    )


# ---------------------------------------------------------------------------
# Stopping short: which draft to hand back, and what is still wrong with it
# ---------------------------------------------------------------------------
def _passed(draft: Draft, name: str) -> bool:
    """A check that never ran on a draft counts as failed: unchecked is not clean."""
    check = draft.checks.get(name)
    return check is not None and check.passed


def uncovered_count(state: LetterState, draft: Draft) -> int:
    """How many must-cover items the draft misses, per its check_requirements result
    (one issue per missing item). Unchecked counts as worse than missing them all."""
    check = draft.checks.get("requirements")
    if check is None:
        return len(must_cover(state)) + 1
    return 0 if check.passed else len(check.issues)


def is_clean(draft: Draft) -> bool:
    """All three checks ran on this draft and passed."""
    return all(_passed(draft, n) for n in REQUIRED_CHECKS)


def best_draft(state: LetterState) -> Draft | None:
    """The draft to return when a run stops (plan §5.7), ranked by: passed
    check_claims, then fewest missing must-cover items, then passed style_lint,
    then the later draft. A run that finished cleanly returns its latest draft,
    because a clean draft always outranks one that failed something.
    """
    if not state.drafts:
        return None
    return max(
        state.drafts,
        key=lambda d: (_passed(d, "claims"), -uncovered_count(state, d), _passed(d, "style"), d.version),
    )


def open_issues(draft: Draft) -> list[str]:
    """What is still wrong with a draft, for the user: every blocking issue of a
    failed check, and a line for each check that never ran on it. Empty means clean.
    A draft that failed check_claims is never handed back without these."""
    out: list[str] = []
    for name in REQUIRED_CHECKS:
        check = draft.checks.get(name)
        if check is None:
            out.append(f"{name}: not checked on draft {draft.version}")
        elif not check.passed:
            out += [f"{name}: {i}" for i in check.issues] or [f"{name}: failed"]
    return out
