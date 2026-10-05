"""``answer_screening``: draft answers to the screening questions written in the ad (plan §5.6).

v1 reads only the questions ``analyze_job`` found in the ad text (Q10). Reading the live
questions from Seek's Quick Apply page is Phase 9.

Same grounding rules as the letter: every statement about the candidate must rest on the
profile. The writer (mid tier) returns each answer with the claims it makes and the
pointer each one rests on, and code then checks them the way ``check_claims`` stage 1
checks a letter (no LLM): every cited pointer must resolve, every claim's quote must be in
the answer, and a link or email address the profile doesn't hold is an invented one. An
answer that fails is kept but flagged (``issues``), never shown as verified. There is no
stage-2 judge: these are drafts the user reads and pastes one by one, and a second mid
call per run wasn't worth it for v1 (decision log 2026-10-04).

Eligibility questions ("Do you have the right to work in Australia?") are answered from
the profile's facts (visa status, location), cited as ``fact:<name>``. A question the
profile can't answer comes back ``covered: no`` with a note telling the user to answer it.

The core (``draft_answers``) needs no letter run: it takes the questions, a
``ProfileContext`` (or a run's ``ToolContext``, which has the same ``profile`` / ``index``),
the ad text and, optionally, facts the app computed in code for each question. Phase 9c's
Quick Apply drafts (app/screening/drafts.py) call it with that per-question context; the
letter-run tool below calls it without, so its prompt and output are as before.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

from app.llm.client import complete_json
from app.llm.letter.runner import ToolContext, ToolError
from app.llm.letter.state import LetterState, ProfileIndex
from app.llm.letter.tools.check_claims import invented_links
from app.llm.letter.tools.check_requirements import quote_in_letter
from app.models import JobListing, Profile

SCREENING_TIER = "mid"
MAX_ANSWER_WORDS = 150

# Citable profile facts: pointer -> the ProfileIndex.facts key it names.
FACT_POINTERS = {
    "fact:work_rights": "visa/work status",
    "fact:location": "location",
    "fact:target_location": "target location",
}


class AnswerClaim(BaseModel):
    quote: str  # the exact words in the answer
    source: str  # one profile pointer, or fact:<name>


class DraftedAnswer(BaseModel):
    number: int  # the question's number, as listed
    covered: Literal["yes", "partly", "no"]  # how far the profile answers it
    answer: str
    claims: list[AnswerClaim]
    note: str  # one short line for the user: what to check or add


class ScreeningDraft(BaseModel):
    answers: list[DraftedAnswer]


_SYSTEM_PROMPT = f"""\
You draft answers to the screening questions in a job ad, in the first person, as the \
candidate. The candidate reads every answer and pastes it in themselves.

Truth comes first, exactly as in their cover letter:
- Every statement about the candidate must be backed by the PROFILE or the PROFILE \
FACTS. Never invent, round up or embellish: no new tools, numbers, durations, team \
sizes, titles or outcomes. "Contributed to" must not become "led".
- A skill the profile only LISTS (a skill: pointer with no experience entry describing \
its use) backs "skills in" or "knowledge of" it, never "experience with" it.
- Yes/no eligibility questions (work rights, location, licence, clearance) are answered \
only from the PROFILE FACTS. If no fact states it, do not guess.
- Never invent links, email addresses, phone numbers, salary figures, notice periods or \
availability dates.
- If the profile can't answer a question, set covered to "no", leave the answer empty \
and say in the note what the candidate needs to answer themselves. Use "partly" when \
the profile answers some of it, and say in the note what is missing.

Write each answer plainly and specifically, in Australian English, at most \
{MAX_ANSWER_WORDS} words, with no em dashes. Answer the question asked; don't pad it \
into a cover letter.

For each answer list its claims: every statement about the candidate, one entry per \
fact. quote is the exact words from the answer; source is ONE pointer copied exactly \
from the square brackets (for example experience:12#s3 or fact:work_rights), without \
the brackets. Statements of interest or intent are not claims."""


@dataclass
class ProfileContext:
    """What the grounding checks need, without a letter run: the loaded profile (for
    its email/phone, which ``invented_links`` allows) and its pointer index. A run's
    ``ToolContext`` has the same two attributes and works anywhere this does."""

    profile: Profile
    index: ProfileIndex


def _facts_block(ctx: ToolContext | ProfileContext) -> str:
    lines = [
        f"[{pointer}] {ctx.index.facts[key]}"
        for pointer, key in FACT_POINTERS.items()
        if key in ctx.index.facts
    ]
    return "\n".join(lines) or "(none on file)"


def resolve(ctx: ToolContext | ProfileContext, pointer: str) -> str | None:
    """A profile pointer's text, or a profile fact's value."""
    pointer = pointer.strip().strip("[]`'\" ").strip()
    key = FACT_POINTERS.get(pointer)
    if key is not None:
        return ctx.index.facts.get(key)
    return ctx.index.resolve(pointer)


def verify(ctx: ToolContext | ProfileContext, drafted: DraftedAnswer) -> tuple[list[str], list[str]]:
    """Code-only grounding check of one answer (check_claims stage 1, applied to an
    answer). Returns (resolved evidence pointers, issues)."""
    issues: list[str] = []
    evidence: list[str] = []
    answer = drafted.answer.strip()
    for c in drafted.claims:
        source = c.source.strip().strip("[]`'\" ").strip()
        if resolve(ctx, source) is None:
            issues.append(f"{c.quote!r} cites {source!r}, which is not in your profile")
            continue
        if not quote_in_letter(c.quote, answer):
            issues.append(f"{c.quote!r} is listed as a claim but isn't in the answer")
            continue
        if source not in evidence:
            evidence.append(source)
    for link in invented_links(answer, ctx):
        issues.append(f"{link!r} is not in your profile: remove it or add your own")
    if answer and drafted.covered != "no" and not evidence and not issues:
        issues.append("no statement in this answer is tied to your profile")
    return evidence, list(dict.fromkeys(issues))


def _question_lines(questions: list[str], contexts: list[str | None] | None) -> str:
    lines = []
    for n, q in enumerate(questions, start=1):
        lines.append(f"{n}. {q}")
        context = contexts[n - 1] if contexts and n - 1 < len(contexts) else None
        if context:
            lines.extend(f"   {line}" for line in context.strip().splitlines())
    return "\n".join(lines)


def draft_answers(
    questions: list[str],
    ctx: ToolContext | ProfileContext,
    ad: str,
    *,
    contexts: list[str | None] | None = None,
    system_prompt: str = _SYSTEM_PROMPT,
    tier: str = SCREENING_TIER,
    task: str = "answer_screening",
    job_id: int | None = None,
    match_id: int | None = None,
    run_id: int | None = None,
) -> list[dict]:
    """One model call drafting every question, then the code-only ``verify`` on each.

    ``contexts`` (optional, one per question) is text the app computed in code, shown
    under its question. Returns one dict per question, in the questions' order:
    question, answer, covered, evidence, note, issues, verified. A ``covered: no``
    answer is blanked; a question the model skipped comes back as not drafted."""
    user = (
        "=== SCREENING QUESTIONS ===\n" + _question_lines(questions, contexts)
        + "\n\n=== PROFILE (pointers in square brackets) ===\n" + ctx.index.prompt_catalog()
        + "\n\n=== PROFILE FACTS (cite as the pointer in square brackets) ===\n" + _facts_block(ctx)
        + "\n\n=== JOB AD (context only; never a source for claims about the candidate) ===\n"
        + (ad or "(not available)")
    )
    data = complete_json(
        system_prompt, user, schema=ScreeningDraft, tier=tier, task=task,
        job_id=job_id, match_id=match_id, run_id=run_id,
    )
    drafted = {a.number: a for a in ScreeningDraft.model_validate(data).answers}

    out: list[dict] = []
    for n, question in enumerate(questions, start=1):
        a = drafted.get(n)
        if a is None or (not a.answer.strip() and a.covered != "no"):
            out.append({"question": question, "answer": "", "covered": "no", "evidence": [],
                        "note": "Not drafted: answer this one yourself.", "issues": [], "verified": False})
            continue
        # A "no" carries no answer, so there is nothing to verify (and nothing to paste).
        evidence, issues = verify(ctx, a) if a.covered != "no" else ([], [])
        answer = a.answer.strip() if a.covered != "no" else ""
        out.append({
            "question": question, "answer": answer, "covered": a.covered, "evidence": evidence,
            "note": a.note.strip(), "issues": issues, "verified": bool(answer) and not issues,
            # kept for callers that run further checks (drafts.py); not part of the run's output
            "_claims": [c.model_dump() for c in a.claims] if a.covered != "no" else [],
        })
    return out


def answer_screening(state: LetterState, ctx: ToolContext, tier: str = SCREENING_TIER) -> dict[str, Any]:
    """Draft an answer to each screening question in the ad, grounded in the profile.

    Use once per run, after match_profile and any ask_user decisions (so answers can use
    what the user added). Do not use when the ad has no screening questions.
    """
    questions = state.job.screening_questions
    if not questions:
        raise ToolError("the ad has no screening questions: there is nothing to answer")
    job = ctx.db.get(JobListing, state.job.job_id)
    ad = (job.raw_description or "").strip() if job else ""
    out = draft_answers(
        questions, ctx, ad, tier=tier, task="answer_screening",
        job_id=state.job.job_id, match_id=ctx.run.match_id, run_id=ctx.run.id,
    )
    for a in out:
        a.pop("_claims", None)
    state.side_outputs.screening_answers = out
    return {
        "questions": len(questions),
        "answered": sum(1 for a in out if a["answer"]),
        "verified": sum(1 for a in out if a["verified"]),
        "flagged": sum(1 for a in out if a["issues"]),
        "for_the_user": sum(1 for a in out if a["covered"] == "no"),
    }
