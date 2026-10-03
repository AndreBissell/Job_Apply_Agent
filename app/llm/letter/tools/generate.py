"""``generate_letter``: write the first draft from supported evidence only.

The writer (strong tier) gets the full job ad, ``analyze_job``'s reading of it, the
evidence ``match_profile`` mapped per requirement, the style guide and the user's
voice. It returns the letter **and** a list of the factual claims it made, each with
the evidence pointer it rests on, so ``check_claims`` can verify them.

The letter plan (what must be covered, what may be used, what must not be claimed)
comes from ``guardrails``, the same definitions ``check_requirements`` checks
against. ``revise_letter`` reuses this module's system prompt and context block
unchanged and only swaps the final task, so in one run the revision hits Gemini's
implicit prefix cache (plan §4 item 9: identical blocks first).
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from app.llm.client import complete_json
from app.llm.letter import guardrails
from app.llm.letter.runner import ToolContext, ToolError
from app.llm.letter.state import Claim, LetterState, Requirement
from app.llm.letter.style import style_guide_prompt, voice_prompt
from app.llm.letter.tools.style_lint import word_count
from app.models import JobListing

logger = logging.getLogger(__name__)

WRITER_TIER = "strong"


# Model output schema (no defaults: Gemini's structured output doesn't support them).
class WrittenClaim(BaseModel):
    quote: str  # the exact words in the letter that make the claim
    source: str  # one evidence pointer, copied from the profile


class WriterOutput(BaseModel):
    letter: str
    claims: list[WrittenClaim]


_WRITER_RULES = """\
You write job-application cover letters in the first person, as the candidate. You \
are given the candidate's profile, the full job ad, an analysis of the ad, and a \
LETTER PLAN saying which requirements the letter must address and with what evidence.

Truth comes first:
- Every statement about the candidate (what they studied, built, used, did, achieved, \
hold) must be backed by the profile text you are given. Never invent, round up or \
embellish: no new tools, numbers, team sizes, audiences, titles or outcomes. "Presented \
weekly reports to team leads" must not become "presented to executives"; "contributed \
to" must not become "led".
- PARTIAL evidence is framed honestly as related experience ("I used Tableau at \
university, which carries over to Power BI"), never as the thing itself.
- A skill the profile only LISTS (a skill: pointer, with no experience entry \
describing its use) backs "skills in" or "knowledge of" it, never "experience with", \
"exposure to", "a background in", or "hands-on" or "practical" use of it.
- Do not lead with what the candidate has not done ("Although I have not...", "While \
I am new to..."). Say what they have done and how it carries over, or leave the \
requirement out.
- Requirements under DO NOT CLAIM are not mentioned at all and not written around. \
Items under NEVER IN THE LETTER (eligibility: work rights, licences, clearances) are \
never mentioned.
- Statements about the employer use only what the job ad says.
- Never invent links, email addresses, phone numbers or attachments. If the ad asks \
for something the profile doesn't contain (a video, a portfolio link, a transcript), \
leave it out: the user adds it.

Write the letter from "Dear Hiring Manager," to the sign-off, with no subject line, \
date or address block. Address every MUST ADDRESS requirement, grouping requirements \
that share a theme into one point rather than ticking them off one by one. Use the MAY \
USE items only where they fit naturally. Mirror a few of the ad's keywords where they \
are true of the candidate. Follow any application instructions that concern the cover \
letter. Include at least one specific detail about this employer or role taken from the \
ad, so the letter could not be sent to any other company.

Then list your claims: every statement about the candidate in the letter, one entry \
per fact. "quote" is the exact words from the letter that make the claim (copy them \
character for character). "source" is ONE pointer copied exactly from the square \
brackets in the profile, without the brackets (for example experience:12#s3), naming \
the narrowest profile text that backs the claim. If a sentence makes two claims from \
two different places, list them as two entries. Do not list statements of interest or \
motivation ("I am keen to...") or statements about the employer."""

_NO_COPY = (
    "Do not reuse whole sentences or distinctive phrases from the voice reference; "
    "write new sentences in its style."
)


def writer_system_prompt(ctx: ToolContext) -> str:
    """Identical for every writer call in a run (and across runs for one user)."""
    parts = [_WRITER_RULES, style_guide_prompt(), voice_prompt(ctx.profile)]
    if parts[-1]:
        parts.append(_NO_COPY)
    return "\n\n".join(p for p in parts if p)


def _req_line(ctx: ToolContext, r: Requirement, with_evidence: bool) -> list[str]:
    head = f"- {r.id} [{r.importance}, theme: {r.theme}] {r.text}"
    if guardrails.listing_only(r):
        head += "  (LISTED SKILL ONLY: at most 'skills in / knowledge of'; never claim experience with it)"
    elif r.status == "partial":
        head += "  (PARTIAL: frame honestly as related experience)"
    lines = [head]
    if r.note:
        lines.append(f"    note: {r.note}")
    if with_evidence:
        for p in r.evidence:
            lines.append(f"    evidence [{p}] {ctx.index.resolve(p)}")
        if r.user_decision and r.user_decision.choice == "have_it" and r.user_decision.answer:
            lines.append(f"    the candidate added: {r.user_decision.answer}")
    return lines


def writer_context(state: LetterState, ctx: ToolContext) -> str:
    """Profile, ad, analysis and letter plan: the same for draft 1 and every revision."""
    job = ctx.db.get(JobListing, state.job.job_id)
    if job is None:
        raise ToolError(f"job {state.job.job_id} not found")
    if not (job.raw_description or "").strip():
        raise ToolError("this job has no stored ad text to write from")

    out: list[str] = [
        "=== CANDIDATE ===",
        f"Name (sign off with this): {ctx.profile.name}",
        "Profile (cite pointers from the square brackets):",
        ctx.index.prompt_catalog(),
        "",
        "=== JOB AD ===",
        f"Title: {job.title}",
        f"Company: {state.job.company or job.company or 'not stated'}",
        f"Location: {job.location or 'not stated'}  |  Work type: {job.work_type or 'not stated'}",
        "",
        job.raw_description.strip(),
        "",
        "=== ANALYSIS OF THE AD ===",
        f"Tone: {state.job.tone or 'not analysed'}",
    ]
    if state.job.keywords:
        out.append(f"Keywords worth echoing: {', '.join(state.job.keywords)}")
    if state.job.company_facts:
        out.append("Facts about the employer from the ad:")
        out += [f"- {f}" for f in state.job.company_facts]
    if state.job.application_instructions:
        out.append("Application instructions in the ad:")
        out += [f"- {i}" for i in state.job.application_instructions]

    out += ["", "=== LETTER PLAN ===", "MUST ADDRESS (with this evidence):"]
    for r in guardrails.must_cover(state):
        out += _req_line(ctx, r, with_evidence=True)
    if not guardrails.must_cover(state):
        out.append("- (none: lead with the strongest evidence below)")
    may = guardrails.may_use(state)
    if may:
        out.append("MAY USE if it fits naturally:")
        for r in may:
            out += _req_line(ctx, r, with_evidence=True)
    dont = guardrails.do_not_claim(state)
    if dont:
        out.append("DO NOT CLAIM (the profile does not show these; do not mention or write around them):")
        for r in dont:
            out += _req_line(ctx, r, with_evidence=False)
    elig = guardrails.eligibility(state)
    if elig:
        out.append("NEVER IN THE LETTER (eligibility, shown to the candidate separately):")
        out += [f"- {r.id} {r.text}" for r in elig]
    return "\n".join(out)


def call_writer(state: LetterState, ctx: ToolContext, task_block: str, task: str) -> tuple[str, list[Claim]]:
    data = complete_json(
        writer_system_prompt(ctx),
        writer_context(state, ctx) + "\n\n=== TASK ===\n" + task_block,
        schema=WriterOutput,
        tier=WRITER_TIER,
        task=task,
        job_id=state.job.job_id,
        match_id=ctx.run.match_id,
        run_id=ctx.run.id,
    )
    out = WriterOutput.model_validate(data)
    text = out.letter.strip()
    if not text:
        raise ToolError("the writer returned an empty letter")
    claims = [
        Claim(text=" ".join(c.quote.split()), source=c.source.strip().strip("[]`'\" ").strip() or None)
        for c in out.claims
        if c.quote.strip()
    ]
    return text, claims


def draft_summary(state: LetterState) -> dict[str, Any]:
    d = state.latest_draft
    return {
        "draft": d.version,
        "words": word_count(d.text),
        "claims": len(d.claims),
        "unsourced_claims": sum(1 for c in d.claims if not c.source),
        "drafts_used": f"{state.budget.drafts_used}/{state.budget.max_drafts}",
    }


def generate_letter(state: LetterState, ctx: ToolContext) -> dict[str, Any]:
    """Write draft 1 from the supported evidence, with the style guide and voice loaded.

    Use once, after match_profile, when no must-have gap is waiting on the user. Do
    not use for later drafts: after draft 1, fix failed checks with revise_letter.
    Then run check_claims, check_requirements and style_lint on the new draft.
    """
    refusal = guardrails.can_generate(state)
    if refusal:
        raise ToolError(refusal)
    text, claims = call_writer(
        state, ctx,
        "Write the first draft of the cover letter now, then list its claims.",
        task="generate_letter",
    )
    state.add_draft(text, claims)
    return draft_summary(state)
