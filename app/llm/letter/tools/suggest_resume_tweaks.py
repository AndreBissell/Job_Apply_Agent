"""``suggest_resume_tweaks``: résumé tailoring notes for this ad (plan §5.6, decided 2026-10-01).

Advice only: nothing is edited. Reads the requirements with their evidence, the FINAL
draft (``guardrails.letter_final``: the letter the user gets, so the advice agrees with
it) and the résumé: the stored CV (``user_cvs``, the default one first) when there is
one, otherwise the profile's experiences. Output, as the plan names it:

    lead_with           what to put first, each resting on profile evidence
    keywords_to_mirror  the ad's own terms, where the profile backs them
    consider_cutting    what to shorten or drop for this ad
    gaps_to_address     how to handle a gap or partial match honestly

It may only reference experience that exists. The model proposes; code disposes, as in
``match_profile``: evidence pointers must resolve (an item left with none is dropped), a
keyword must appear in the ad, an item to cut must be in the CV or resolve as a pointer,
and a gap must be one of this run's requirements the profile doesn't fully back. Links
are removed. What was dropped is counted, so a model inventing things shows up.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select

from app.llm.client import complete_json
from app.llm.letter import guardrails
from app.llm.letter.runner import ToolContext, ToolError
from app.llm.letter.state import LetterState
from app.llm.letter.tools.check_claims import _LINK_RE
from app.llm.letter.tools.check_requirements import quote_in_letter
from app.models import JobListing, UserCv

RESUME_TIER = "mid"
MAX_ITEMS = {"lead_with": 4, "keywords_to_mirror": 8, "consider_cutting": 4, "gaps_to_address": 4}


class LeadItem(BaseModel):
    point: str
    evidence: list[str]
    why: str


class KeywordItem(BaseModel):
    keyword: str
    evidence: list[str]


class CutItem(BaseModel):
    item: str  # the CV's own words, or a short description of the profile entry
    source: str  # a profile pointer, or "cv" when ``item`` quotes the CV
    why: str


class GapItem(BaseModel):
    id: str  # requirement id
    advice: str


class ResumeAdvice(BaseModel):
    lead_with: list[LeadItem]
    keywords_to_mirror: list[KeywordItem]
    consider_cutting: list[CutItem]
    gaps_to_address: list[GapItem]


_SYSTEM_PROMPT = """\
You give a job seeker short, specific notes on tailoring their résumé to ONE job ad. \
Advice only: they edit the résumé themselves. You get the ad's requirements with what \
the candidate's profile shows for each, the cover letter they are sending, and their \
résumé (or, when none is stored, their profile).

Truth comes first: every note must rest on what the profile or résumé already says. \
Never suggest adding a skill, tool, number, title or outcome they don't have, and never \
suggest wording that claims more than the evidence ("experience with X" for a skill \
they only list).

- lead_with: up to 4 things to move to the top or expand, the strongest evidence for \
this ad's headline requirements. point says what to lead with; evidence lists the \
profile pointers (copied exactly from the square brackets, without them) it rests on; \
why names the requirement it answers.
- keywords_to_mirror: up to 8 terms copied from the AD, word for word, that the résumé \
should use where they are true, each with the pointers that show the candidate has it.
- consider_cutting: up to 4 entries to shorten or drop because this ad doesn't value \
them (space is better spent on the leads). item quotes the résumé's words, with source \
"cv"; or, with no résumé, describes the profile entry, with its pointer as source.
- gaps_to_address: up to 4 requirements marked gap or partial worth handling: how to \
present the related experience honestly, or to leave it off. Never advise claiming it. \
id is the requirement id.
Leave a list empty rather than pad it. No links."""


def _cv_text(ctx: ToolContext, profile_id: int) -> str | None:
    """The stored CV's text: the default one, else the newest with content."""
    cvs = ctx.db.scalars(
        select(UserCv).where(UserCv.user_id == profile_id).order_by(UserCv.is_default.desc(), UserCv.id.desc())
    )
    for cv in cvs:
        if cv.content and cv.content.strip():
            return cv.content.strip()
    return None


def _clean(p: str) -> str:
    return p.strip().strip("[]`'\" ").strip()


def _strip_links(text: str) -> str:
    return " ".join(_LINK_RE.sub("", text).split())


def _in_ad(keyword: str, ad: str, keywords: list[str]) -> bool:
    k = " ".join(keyword.lower().split())
    if not k:
        return False
    if any(k == " ".join(x.lower().split()) for x in keywords):
        return True
    return re.search(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])", " ".join(ad.lower().split())) is not None


def _requirements_block(state: LetterState, ctx: ToolContext) -> str:
    lines = []
    for r in state.requirements:
        if r.letter_role == "not_for_letter":
            continue
        left_out = " (the candidate said they don't have it)" if (
            r.user_decision and r.user_decision.choice == "leave_out" and not r.user_decision.assumed
        ) else ""
        lines.append(f"{r.id} [{r.importance}/{r.letter_role}] {r.status}{left_out}: {r.text}")
        lines += [f"    evidence [{p}] {ctx.index.resolve(p)}" for p in r.evidence if ctx.index.resolve(p)]
        if r.note:
            lines.append(f"    note: {r.note}")
    return "\n".join(lines) or "(none)"


def suggest_resume_tweaks(state: LetterState, ctx: ToolContext, tier: str = RESUME_TIER) -> dict[str, Any]:
    """Résumé tailoring notes from the requirements, the evidence and the final draft.

    Use once per run, after the letter is final (every check passed, or the draft limit
    was reached). Do not use while the letter can still change.
    """
    final = guardrails.letter_final(state)
    if final is None:
        raise ToolError("the letter is not final yet: suggest_resume_tweaks reads the final draft")
    job = ctx.db.get(JobListing, state.job.job_id)
    ad = (job.raw_description or "").strip() if job else ""
    cv = _cv_text(ctx, state.profile_id)

    user = (
        "=== REQUIREMENTS (what the profile shows for each) ===\n" + _requirements_block(state, ctx)
        + "\n\n=== PROFILE (pointers in square brackets) ===\n" + ctx.index.prompt_catalog()
        + ("\n\n=== RÉSUMÉ (their stored CV) ===\n" + cv if cv else
           "\n\n=== RÉSUMÉ ===\n(no CV stored: advise on the profile's entries above)")
        + f"\n\n=== COVER LETTER BEING SENT (draft {final.version}) ===\n" + final.text
        + "\n\n=== JOB AD ===\n" + (ad or "(not available)")
        + (f"\nKeywords: {', '.join(state.job.keywords)}" if state.job.keywords else "")
    )
    data = complete_json(
        _SYSTEM_PROMPT, user, schema=ResumeAdvice, tier=tier, task="suggest_resume_tweaks",
        job_id=state.job.job_id, match_id=ctx.run.match_id, run_id=ctx.run.id,
    )
    advice = ResumeAdvice.model_validate(data)
    dropped = 0

    def pointers(raw: list[str]) -> list[str]:
        return list(dict.fromkeys(p for p in (_clean(x) for x in raw) if ctx.index.resolve(p)))

    lead_with = []
    for item in advice.lead_with:
        ev = pointers(item.evidence)
        if not ev or not item.point.strip():
            dropped += 1
            continue
        lead_with.append({"point": _strip_links(item.point), "evidence": ev, "why": _strip_links(item.why)})

    keywords, seen = [], set()
    for item in advice.keywords_to_mirror:
        kw = " ".join(item.keyword.split())
        ev = pointers(item.evidence)
        if not ev or not _in_ad(kw, ad, state.job.keywords) or kw.lower() in seen:
            dropped += 1
            continue
        seen.add(kw.lower())
        keywords.append({"keyword": kw, "evidence": ev})

    cutting = []
    for item in advice.consider_cutting:
        source = _clean(item.source)
        if source.lower() == "cv":
            ok = bool(cv) and quote_in_letter(item.item, cv)
        else:
            ok = ctx.index.resolve(source) is not None
        if not ok or not item.item.strip():
            dropped += 1
            continue
        cutting.append({"item": _strip_links(item.item), "source": "cv" if source.lower() == "cv" else source,
                        "why": _strip_links(item.why)})

    eligible = {
        r.id: r for r in state.requirements
        if r.letter_role != "not_for_letter" and (r.status in ("gap", "partial") or (
            r.user_decision is not None and r.user_decision.choice == "leave_out"))
    }
    gaps_out, gap_ids = [], set()
    for item in advice.gaps_to_address:
        r = eligible.get(item.id.strip())
        advice_text = _strip_links(item.advice)
        if r is None or not advice_text or r.id in gap_ids:
            dropped += 1
            continue
        gap_ids.add(r.id)
        gaps_out.append({"requirement_id": r.id, "requirement": r.text, "advice": advice_text})

    notes = {
        "based_on": "cv" if cv else "profile",
        "draft_version": final.version,
        "lead_with": lead_with[:MAX_ITEMS["lead_with"]],
        "keywords_to_mirror": keywords[:MAX_ITEMS["keywords_to_mirror"]],
        "consider_cutting": cutting[:MAX_ITEMS["consider_cutting"]],
        "gaps_to_address": gaps_out[:MAX_ITEMS["gaps_to_address"]],
        "dropped": dropped,
    }
    state.side_outputs.resume_notes = notes
    return {
        "based_on": notes["based_on"],
        **{k: len(notes[k]) for k in MAX_ITEMS},
        "dropped": dropped,
    }
