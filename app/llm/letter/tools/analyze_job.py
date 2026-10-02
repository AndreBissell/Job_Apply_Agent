"""``analyze_job``: turn the full job ad into a requirements checklist.

Distinct from ``extract.py``, which is a cheap pass over every scanned job (skills,
seniority, summary) that feeds matching. This reads the *original ad* for one job
that is headed for a letter, and answers three questions per requirement:

  1. How much does the EMPLOYER care?          importance: essential | important | nice_to_have
  2. What should the LETTER do with it?        letter_role: headline | mention | implied | not_for_letter
  3. Which requirements belong to one point?   theme

Importance and letter role are deliberately separate. "Idempotency and retry safety"
can be genuinely important to the employer and still be something nobody writes in a
cover letter; "Australian work rights" is essential and never belongs in one. The ads
this has to handle often use one flat "you likely have" list with no must/should
split, so importance is inferred from the wording, not the heading.

The result depends only on the job, never the profile, so it is cached on
``job_listings.requirements_checklist`` keyed by a fingerprint of the description.
The model's judgement is then constrained in code (``_postprocess``): a nice-to-have
can't be a headline, headlines are capped, and obvious eligibility items are forced
to ``not_for_letter`` whatever the model said.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import re
from typing import Any, Literal

from pydantic import BaseModel

from app.llm.client import complete_json, model_for
from app.llm.letter.runner import ToolContext, ToolError
from app.llm.letter.state import Importance, LetterRole, LetterState, Requirement
from app.models import JobListing

logger = logging.getLogger(__name__)

# Bump when the prompt or schema changes meaning, so stale cached analyses are redone.
ANALYSIS_VERSION = 1

MAX_REQUIREMENTS = 16
# A one-page letter can make about this many lead points; more is a list, not a letter.
MAX_HEADLINES = 5


# ---------------------------------------------------------------------------
# Model output schema (Gemini constrains decoding to this). No defaults: the
# structured-output schema doesn't support them.
# ---------------------------------------------------------------------------
class AnalyzedRequirement(BaseModel):
    n: int  # 1-based position; implied_by refers to these
    text: str
    importance: Importance
    letter_role: LetterRole
    theme: str
    implied_by: list[int]


class JobAnalysis(BaseModel):
    requirements: list[AnalyzedRequirement]
    tone: Literal["formal_corporate", "professional_friendly", "startup_casual", "technical", "other"]
    keywords: list[str]
    screening_questions: list[str]
    company_facts: list[str]


_SYSTEM_PROMPT = """\
You analyse ONE job advertisement so a candidate can write a short (one page, about \
250-300 words) cover letter for it. Turn the ad into a checklist of what the employer \
asks for, and decide how each item should be treated in the letter. Use only what the \
ad says or clearly implies. Never invent requirements, company facts or screening \
questions.

REQUIREMENTS: each is one distinct thing the employer asks for, in the employer's own \
words (lightly trimmed, not paraphrased into generic terms). Number them n=1,2,3... \
Cover both explicit requirements and the skills/experience the described duties \
clearly demand. Give at most 16; merge near-duplicates, and where the ad lists several \
closely related technologies or duties as one idea, make that one requirement.

For EACH requirement set two separate ratings.

importance = how much the EMPLOYER cares. Judge by the wording, position and \
repetition, not just a heading, because many ads use one flat "you likely have" list \
with no must/should split.
- essential: stated as required/must/essential, or so central to the role that the \
role makes no sense without it ("you will own the production systems").
- important: strongly asked for but not framed as mandatory ("strong experience in", \
"solid understanding of", "you likely have").
- nice_to_have: "desirable", "bonus", "highly regarded", "exposure to", "advantageous".
Do NOT mark everything essential. In a flat list, separate the core items from the \
supporting ones.

letter_role = what the LETTER should do with it. This is a different question from \
importance.
- headline: a core requirement that distinguishes a strong candidate; a lead point \
the letter should make with concrete evidence. Choose at most 5, and never a \
nice_to_have.
- mention: relevant, and worth a brief mention if the candidate has it, but not a \
lead point.
- implied: anyone competent at a headline/mention item would obviously have it, so \
naming it adds nothing (e.g. git, CI/CD, debugging or deployment when the ad asks for \
experience building and running production web applications; basic data integrity or \
idempotency under "design reliable systems"). The letter will not list it, but may \
use it if it fits a sentence naturally. For these, put the n of the headline/mention \
item(s) it follows from in implied_by; otherwise implied_by is empty.
- not_for_letter: eligibility, admin or logistics facts a letter should not state, \
even when essential: work rights, citizenship or visa status, security clearance, \
police or working-with-children checks, a driver's licence, own transport, location \
or onsite days, availability or start date, hours, salary. These are screened at \
application time and shown to the candidate as a note.

theme: a short label (2-4 words) grouping related requirements, e.g. "APIs and \
integrations", "Production operations", "AI-assisted development". Related items \
share the same theme string, so the letter makes one strong point per theme instead of \
ticking off every item.

tone: how the employer writes (formal_corporate, professional_friendly, startup_casual, \
technical, other); the letter should roughly match it.

keywords: up to 8 distinctive terms or phrases the ad itself uses that are worth \
echoing in the letter (the employer's vocabulary, not generic words).

screening_questions: questions the ad explicitly asks applicants to answer in their \
application. Empty if none.

company_facts: up to 5 concrete things the ad states about the employer, product, \
team or mission that a letter could genuinely refer to (what they build, who they \
serve, their stage, a named project). Short factual phrases taken from the ad. Skip \
boilerplate such as "we are a great place to work". Empty if the ad says nothing \
specific.
"""

# Safety net for the one category where a miss is embarrassing: a letter that writes
# "I hold Australian work rights" because the model labelled it a headline. Applied in
# code after the model, only to requirement text. Deliberately narrow (no bare "visa":
# Visa is also a company).
_ELIGIBILITY_RE = re.compile(
    r"\b(work(?:ing)? rights?|right(?:s)? to work|(?:australian|permanent)\s+(?:citizen|resident)\w*|"
    r"citizen(?:ship)?|permanent residen\w+|visa (?:status|holder|sponsorship|conditions)|"
    r"valid visa|security clearance|nv[12]\b|baseline clearance|police (?:check|clearance)|"
    r"criminal (?:history|record)|working with children|blue card|"
    r"driver'?s? licen[cs]e|own (?:car|vehicle|transport)|reliable transport)\b",
    re.IGNORECASE,
)

_IMPORTANCE_RANK = {"essential": 0, "important": 1, "nice_to_have": 2}


def _fingerprint(description: str) -> str:
    return hashlib.sha256(description.encode("utf-8")).hexdigest()[:16]


def _postprocess(analysis: JobAnalysis) -> list[dict[str, Any]]:
    """Constrain the model's output in code. Returns requirement dicts with final ids."""
    kept: list[AnalyzedRequirement] = []
    seen: set[str] = set()
    for r in analysis.requirements:
        text = " ".join((r.text or "").split())
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        kept.append(r.model_copy(update={"text": text}))
    kept = kept[:MAX_REQUIREMENTS]

    id_for_n: dict[int, str] = {}
    for pos, r in enumerate(kept, start=1):
        id_for_n.setdefault(r.n, f"R{pos}")

    reqs: list[dict[str, Any]] = []
    for pos, r in enumerate(kept, start=1):
        role, importance = r.letter_role, r.importance
        if _ELIGIBILITY_RE.search(r.text):
            role = "not_for_letter"
        if role == "headline" and importance == "nice_to_have":
            role = "mention"  # a nice-to-have is never a lead point
        reqs.append(
            {
                "id": f"R{pos}",
                "text": r.text,
                "importance": importance,
                "letter_role": role,
                "theme": " ".join((r.theme or "").split()) or "General",
                "implied_by": [id_for_n[n] for n in r.implied_by if n in id_for_n and id_for_n[n] != f"R{pos}"],
            }
        )

    headlines = [r for r in reqs if r["letter_role"] == "headline"]
    if len(headlines) > MAX_HEADLINES:
        # Keep the most important, earliest-listed; demote the rest to "mention".
        ranked = sorted(headlines, key=lambda r: (_IMPORTANCE_RANK[r["importance"]], int(r["id"][1:])))
        for r in ranked[MAX_HEADLINES:]:
            r["letter_role"] = "mention"

    by_id = {r["id"]: r for r in reqs}
    for r in reqs:
        if r["letter_role"] != "implied":
            r["implied_by"] = []
        else:  # it can only follow from something the letter actually leads with
            r["implied_by"] = [i for i in r["implied_by"] if by_id[i]["letter_role"] in ("headline", "mention")]
    return reqs


def _clean_list(items: list[str], limit: int) -> list[str]:
    out: list[str] = []
    for item in items:
        item = " ".join((item or "").split())
        if item and item.lower() not in {o.lower() for o in out}:
            out.append(item)
    return out[:limit]


def _apply_to_state(state: LetterState, payload: dict[str, Any]) -> None:
    state.requirements = [Requirement(**r) for r in payload["requirements"]]
    state.job.tone = payload["tone"]
    state.job.keywords = payload["keywords"]
    state.job.screening_questions = payload["screening_questions"]
    state.job.company_facts = payload["company_facts"]


def _summarise(source: str, state: LetterState) -> dict[str, Any]:
    roles: dict[str, int] = {}
    importances: dict[str, int] = {}
    for r in state.requirements:
        roles[r.letter_role] = roles.get(r.letter_role, 0) + 1
        importances[r.importance] = importances.get(r.importance, 0) + 1
    return {
        "source": source,
        "requirements": len(state.requirements),
        "by_letter_role": roles,
        "by_importance": importances,
        "company_facts": len(state.job.company_facts),
        "eligibility_notes": len(state.eligibility_notes()),
    }


def analyze_job(state: LetterState, ctx: ToolContext, force: bool = False) -> dict[str, Any]:
    """Build (or load from cache) the requirements checklist for ``state.job``.

    Use first, before anything else, for a job headed for a letter. Not needed again
    for the same job: the result is cached on the job row and reused until the ad
    text changes (or ``force``).
    """
    job = ctx.db.get(JobListing, state.job.job_id)
    if job is None:
        raise ToolError(f"job {state.job.job_id} not found")
    if not (job.raw_description or "").strip():
        raise ToolError("this job has no stored description, so there is nothing to analyse")

    fingerprint = _fingerprint(job.raw_description)
    if job.requirements_checklist and not force:
        try:
            cached = json.loads(job.requirements_checklist)
        except json.JSONDecodeError:
            cached = None
        if (
            isinstance(cached, dict)
            and cached.get("version") == ANALYSIS_VERSION
            and cached.get("description_sha") == fingerprint
        ):
            _apply_to_state(state, cached)
            return _summarise("cache", state)

    user_content = (
        f"JOB TITLE: {job.title}\n"
        f"COMPANY: {job.company or 'not stated'}\n"
        f"LOCATION: {job.location or 'not stated'}  |  WORK TYPE: {job.work_type or 'not stated'}\n\n"
        f"JOB AD:\n{job.raw_description}"
    )
    data = complete_json(
        _SYSTEM_PROMPT,
        user_content,
        schema=JobAnalysis,
        tier="mid",
        task="analyze_job",
        job_id=job.id,
        run_id=ctx.run.id,
    )
    analysis = JobAnalysis.model_validate(data)
    requirements = _postprocess(analysis)
    if not requirements:
        raise ToolError("the model returned no usable requirements for this ad; not cached")

    payload = {
        "version": ANALYSIS_VERSION,
        "description_sha": fingerprint,
        "model": model_for("mid"),
        "requirements": requirements,
        "tone": analysis.tone,
        "keywords": _clean_list(analysis.keywords, 8),
        "screening_questions": _clean_list(analysis.screening_questions, 10),
        "company_facts": _clean_list(analysis.company_facts, 5),
    }
    job.requirements_checklist = json.dumps(payload, ensure_ascii=False)
    job.requirements_checklist_at = datetime.datetime.now(datetime.timezone.utc)
    ctx.db.commit()

    _apply_to_state(state, payload)
    return _summarise("llm", state)
