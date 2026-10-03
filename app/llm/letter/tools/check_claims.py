"""``check_claims``: verify every factual claim in the latest draft (plan §5.2).

The writer's own claims list is self-reported, so it can't be the only check. Two
stages:

1. **Code (no LLM).** Every declared claim that names a source must name a pointer
   that resolves to real profile text. A pointer that doesn't exist means the writer
   cited something it made up: blocking. So is any link or email address in the
   letter that isn't in the profile: an ad asking for "a link to a 2 minute video"
   once got an invented ``youtu.be/...`` link (eval workflow-v1), which the judge
   passed because it isn't a claim about experience.
2. **Small model, independently.** It reads the letter sentence by sentence, lists
   every factual claim about the candidate (and about the employer), pairs each with
   the declared claim it corresponds to, and judges it against the profile (or the
   ad, for employer claims): supported / overstated / unsupported. Code then
   disposes: a "supported" verdict needs at least one pointer that resolves, or it
   counts as unsupported.

The judge may also answer ``not_a_claim`` for a sentence it listed that turns out
to be interest, intent, the application itself or an admission ("I have not used
Power Apps"). Without that outlet a forced choice put such sentences under
"unsupported" (seen on the eval run). They are ignored but listed in the summary,
so a judge hiding a real overclaim there can be audited.

Blocking: unresolvable pointers, overstated or unsupported claims, employer claims
the ad doesn't make. Warnings: claims the writer left off its list (or listed with
no source) that the profile does back. A missed declaration is bookkeeping, and
burning a strong-model revision on it isn't worth it; a made-up pointer is a
fabrication signal, so it blocks (decision log 2026-10-03).

Runs on ``mid`` (``CLAIMS_TIER``): plan §3 started it on ``small`` and said to move
it up if it missed overclaims, which it did. ``tier`` lets ``letter_lab.py plant``
compare tiers.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Literal

from pydantic import BaseModel

from app.llm.client import complete_json
from app.llm.letter.runner import ToolContext, ToolError
from app.llm.letter.state import Check, LetterState
from app.models import JobListing

logger = logging.getLogger(__name__)

# Moved small -> mid on 2026-10-03 (plan Decision log). The planted test was close
# (small 35/36, mid 36/36), but on real letters small passed overclaims mid caught
# ("practical experience in C#" from a bare skill listing, in 3 of 3 small runs), hid
# real claims under not_a_claim, and flipped verdicts between identical runs.
CLAIMS_TIER = "mid"


class JudgedClaim(BaseModel):
    quote: str
    about: Literal["candidate", "employer"]
    declared: int  # number of the matching DECLARED CLAIM, 0 if none
    # Field order is generation order: evidence and reasoning BEFORE the verdict. With
    # the verdict first, flash-lite committed to "unsupported" and then wrote a reason
    # arguing the claim was backed (eval run tools-v1).
    support: list[str]  # profile pointers that back it
    reason: str
    verdict: Literal["supported", "overstated", "unsupported", "not_a_claim"]


class ClaimJudgement(BaseModel):
    claims: list[JudgedClaim]


_SYSTEM_PROMPT = """\
You check a cover letter for claims the candidate cannot back up. Be strict: a claim \
passes only when the source text actually says it, at the same level. A letter that \
overstates gets the candidate caught out in an interview.

Go through the LETTER sentence by sentence and list EVERY factual claim it makes, \
including ones in the opening and closing:
- about the candidate: what they studied or hold, built, used, did, achieved, how \
much, how long, how well, for whom. Self-descriptions ("I am a strong \
communicator", "I work well under pressure") are claims too.
- about the employer: any statement about the company, its products, customers, team \
or the role.
Skip, because they can never overstate the profile:
- statements of interest, motivation or intent ("I am keen to...", "I would like \
to...", "I look forward to...");
- statements about the application itself ("I am applying for the Graduate role");
- statements that the candidate does NOT have or has not done something ("I have not \
used Power Apps yet"). An honest admission is not a claim.

For each claim:
- quote: the exact words from the letter.
- about: candidate or employer.
- declared: the number of the DECLARED CLAIM it corresponds to, or 0 if none matches. \
Find claims from the letter itself; do not just copy the declared list, which may \
leave claims out.
- support: for candidate claims, the profile pointers (copied from the square brackets, \
without them) whose text backs the claim. Empty for employer claims and when nothing \
backs it.
- reason: one short sentence on what the source does or does not say. For overstated, \
say what the profile actually says.
- verdict, decided from your reason:
  - supported: the PROFILE (candidate claims) or the JOB AD (employer claims) says \
this. Plain rewording and natural summaries are fine ("BIT, UQ" -> "an IT degree from \
the University of Queensland"). A self-description is supported when the profile \
shows work that reasonably demonstrates it.
  - overstated: the source has something related, but the letter claims more: a \
bigger audience, scope, team or number; a lead role where they contributed; using a \
tool in daily work when they only built something about it or studied it; a \
different tool than the one stated; an outcome or metric the profile does not give.
  - unsupported: nothing in the source says it.
  - not_a_claim: on reflection it is one of the kinds to skip above (interest, \
intent, the application itself, an admission). Use this rather than unsupported."""


# A URL (scheme, www., or a lowercase domain followed by a path) or an email address.
# The bare-domain form is case-sensitive so "ASP.NET/C#" and "Node.js/React" don't match.
_LINK_RE = re.compile(
    r"(?i:https?://|www\.)\S+"
    r"|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|net|org|io|be|au|dev|me|ly|co|app|ai|tv|gg|edu|gov|info)/\S*"
    r"|[\w.+-]+@[\w-]+(?:\.[\w-]+)+"
)


def invented_links(text: str, ctx: ToolContext) -> list[str]:
    """Links and email addresses in ``text`` that appear nowhere in the profile."""
    known = " ".join([*ctx.index.catalog().values(), ctx.profile.email or "", ctx.profile.phone or ""]).lower()
    found = (m.group(0).rstrip(".,;:!?)'\"") for m in _LINK_RE.finditer(text))
    return list(dict.fromkeys(link for link in found if link.lower() not in known))


def _stage1(state: LetterState, ctx: ToolContext) -> list[str]:
    issues = []
    for c in state.latest_draft.claims:
        if c.source and ctx.index.resolve(c.source) is None:
            issues.append(f"bad_source: {c.text!r} cites {c.source!r}, which is not in the profile")
    for link in invented_links(state.latest_draft.text, ctx):
        issues.append(
            f"invented_link: {link!r} is not in the profile. Links and contact details come from the "
            "user, never the writer: remove it (a video, portfolio or attachment the ad asks for is "
            "the user's to add)"
        )
    return issues


def _declared_block(state: LetterState, ctx: ToolContext) -> str:
    claims = state.latest_draft.claims
    if not claims:
        return "(the writer declared no claims)"
    lines = []
    for n, c in enumerate(claims, start=1):
        cited = ctx.index.resolve(c.source) if c.source else None
        src = f"[{c.source}] {cited}" if cited else f"{c.source or 'no source'} (does not resolve)"
        lines.append(f"{n}. {c.text!r}  -- cited: {src}")
    return "\n".join(lines)


def _ad_text(state: LetterState, ctx: ToolContext) -> str:
    job = ctx.db.get(JobListing, state.job.job_id)
    return (job.raw_description or "").strip() if job else ""


def judge_claims(state: LetterState, ctx: ToolContext, tier: str) -> ClaimJudgement:
    draft = state.latest_draft
    user = (
        "=== PROFILE (pointers in square brackets) ===\n" + ctx.index.prompt_catalog()
        + "\n\n=== JOB AD ===\n" + (_ad_text(state, ctx) or "(not available)")
        + f"\n\n=== LETTER (draft {draft.version}) ===\n" + draft.text
        + "\n\n=== DECLARED CLAIMS (the writer's own list, with the profile text each cites) ===\n"
        + _declared_block(state, ctx)
    )
    data = complete_json(
        _SYSTEM_PROMPT, user, schema=ClaimJudgement, tier=tier, task="check_claims",
        job_id=state.job.job_id, match_id=ctx.run.match_id, run_id=ctx.run.id,
    )
    return ClaimJudgement.model_validate(data)


def _clean_pointer(p: str) -> str:
    return p.strip().strip("[]`'\" ").strip()


def assess(state: LetterState, ctx: ToolContext, judged: ClaimJudgement) -> tuple[Check, dict[str, Any]]:
    """Code's verdict from stage 1 + the model's stage-2 judgement."""
    draft = state.latest_draft
    issues = _stage1(state, ctx)
    warnings: list[str] = []
    verdicts: dict[str, int] = {}
    not_claims: list[str] = []  # kept in the summary so a lenient judge can be audited

    for j in judged.claims:
        quote = " ".join(j.quote.split())
        if j.verdict == "not_a_claim":
            not_claims.append(f"{quote!r}: {j.reason}")
            continue
        if j.about == "employer":
            verdicts[f"employer_{j.verdict}"] = verdicts.get(f"employer_{j.verdict}", 0) + 1
            if j.verdict != "supported":
                issues.append(f"employer_claim_not_in_ad: {quote!r}: {j.reason}")
            continue

        declared = draft.claims[j.declared - 1] if 0 < j.declared <= len(draft.claims) else None
        support = [p for p in (_clean_pointer(s) for s in j.support) if ctx.index.resolve(p)]
        if not support and declared and declared.source and ctx.index.resolve(declared.source):
            support = [declared.source]
        verdict = j.verdict
        if verdict == "supported" and not support:
            verdict = "unsupported"  # "supported" by nothing that exists
        verdicts[verdict] = verdicts.get(verdict, 0) + 1

        if verdict == "overstated":
            issues.append(f"overstated: {quote!r}: {j.reason}")
        elif verdict == "unsupported":
            issues.append(f"unsupported: {quote!r}: {j.reason}")
        elif declared is None or not declared.source:
            warnings.append(f"undeclared: {quote!r} (backed by {', '.join(support)}); list it with its source")

    issues = list(dict.fromkeys(issues))
    warnings = list(dict.fromkeys(warnings))
    matched = {j.declared for j in judged.claims}
    summary = {
        "draft": draft.version,
        "passed": not issues,
        "issues": issues,
        "warnings": warnings,
        "declared": len(draft.claims),
        "found": len(judged.claims),
        "declared_not_found_in_letter": sum(1 for n in range(1, len(draft.claims) + 1) if n not in matched),
        "verdicts": verdicts,
        "not_claims": not_claims,
    }
    return Check(passed=not issues, issues=issues, warnings=warnings), summary


def check_claims(state: LetterState, ctx: ToolContext, tier: str = CLAIMS_TIER) -> dict[str, Any]:
    """Verify every factual claim in the latest draft against the profile (and the ad).

    Use after every new draft, alongside check_requirements and style_lint. If it
    fails, pass its issues to revise_letter: each names the claim and what the
    profile actually says. Never finish on a draft that failed this check.
    """
    if state.latest_draft is None:
        raise ToolError("check_claims needs a draft; call generate_letter first")
    check, summary = assess(state, ctx, judge_claims(state, ctx, tier))
    state.record_check("claims", check)
    return summary
