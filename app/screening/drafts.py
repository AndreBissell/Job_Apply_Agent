"""Draft answers to open-ended Quick Apply questions (plan §10.1, Phase 9c).

Only on the user's click ("Draft an answer"): ``POST /jobs/{id}/screening-drafts``
with one bank id = one mid-tier call (``task='quick_apply_draft'``). The assist view
(GET) never drafts; it shows the stored draft, or a stale notice when what it was built
from has changed.

Refused in code BEFORE any call (``DraftRefused``):
* question help switched off (Personalise);
* a question whose bank kind isn't ``assisted`` (a ``user`` question is never sent);
* anything but an open-ended (free-text) question with strategy ``years_skill_text`` or
  ``free_text_describe`` (choice questions get the 9b views, not a draft);
* a job without a full-pipeline letter (one-shot or no letter: no help, decided
  2026-10-04).

The writer is ``answer_screening.draft_answers`` (the letter's grounding rules and the
code-only ``verify``), given what code already knows about the question: what the job
wants, the evidence pointers, the years the profile's dates support, and the skill parts
the profile has no trace of. On top of ``verify``, code checks the draft:

* a duration above the code-computed years for the skill is an issue (never rounded up);
* a sentence naming a skill part the profile lacks, without saying the candidate hasn't
  used it, is an issue;
* a claim resting only on a listed skill (``skill:<id>``) that speaks of experience is
  an issue (the ``listing_only`` rule: a listing backs "skills in", not "experience").

A draft with any issue is shown with its issues, never as verified.

Stored on ``job_screening_questions.draft`` with a fingerprint of the profile text and
dates, the bank row's sorting and the letter run used. When any differs, the draft is
served with ``stale`` reasons ("redraft") instead of as current.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
import threading

from sqlalchemy.orm import Session

from app import preferences
from app.llm.letter.state import ProfileIndex, split_sentences
from app.llm.letter.tools import answer_screening as writer
from app.models import JobListing, JobScreeningQuestion, LetterRun, Profile, ScreeningQuestion
from app.screening import assist, bank

TIER = "mid"
TASK = "quick_apply_draft"
DRAFTABLE = ("years_skill_text", "free_text_describe")

BASIS_LABEL = {"work": "work", "project": "project", "study": "study", "listed": "listed skill"}

STALE_PROFILE = "Your profile changed since this draft was written: redraft it."
STALE_QUESTION = "This question's sorting changed since this draft was written: redraft it."
STALE_RUN = "Your cover letter was rewritten since this draft was written: redraft it."

_ADDENDUM = """

These questions come from the employer's application form. Under a question you may \
find FACTS COMPUTED BY THE APP. They come from code and are exact:
- YEARS: the most time the profile's dates support for that skill. Never state a longer \
time and never round up (for 1 year 8 months, "about 2 years" and "2 years" are both \
wrong; say "1 year and 8 months" or "under 2 years"). When it says no years can be \
counted, state no duration for the skill.
- NOT IN PROFILE: parts of the question the profile has no trace of. Never claim them or \
imply experience with them. You may say plainly that the candidate hasn't used them yet. \
Set covered to "partly" (or "no" when nothing else is left to answer) and name them in \
the note.
- EVIDENCE: the pointers where the profile backs the skill. Cite these. Evidence marked \
"listed skill" backs "skills in" or "knowledge of" only, never "experience with".
- THE JOB WANTS: what the ad asks for, for relevance only; never a source for claims."""

SYSTEM_PROMPT = writer._SYSTEM_PROMPT + _ADDENDUM

_in_flight: set[tuple[int, int]] = set()
_lock = threading.Lock()


class DraftRefused(Exception):
    """A draft that code won't ask for. ``status`` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------------------
# Fingerprints and the stored draft
# --------------------------------------------------------------------------------------
def _hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


def profile_fingerprint(profile: Profile, index: ProfileIndex) -> str:
    """The profile text a draft can cite, its facts, and the dates/types/skills years and
    the role rules are computed from. Any edit to these changes it."""
    exps = sorted(
        ([e.id, e.experience_type, e.start_date, e.end_date, sorted(sk.name for sk in e.skills)]
         for e in profile.experiences),
        key=lambda x: x[0],
    )
    return _hash({"catalog": index.catalog(), "facts": index.facts, "experiences": exps})


def question_fingerprint(q: dict) -> str:
    return _hash([q.get("text"), q.get("input_type"), q.get("strategy"), q.get("parameters") or {}])


def is_draftable(q: dict) -> bool:
    return q.get("kind") == "assisted" and q.get("input_type") == "text" and q.get("strategy") in DRAFTABLE


def _stored(link: JobScreeningQuestion | None) -> dict | None:
    if link is None or not link.draft:
        return None
    try:
        data = json.loads(link.draft)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def draft_view(data: dict, *, profile_fp: str, question_fp: str, run_id: int | None) -> dict:
    """What the UI gets: the stored draft plus why it is stale (empty = current)."""
    fp = data.get("fingerprint") or {}
    stale = []
    if fp.get("profile") != profile_fp:
        stale.append(STALE_PROFILE)
    if fp.get("question") != question_fp:
        stale.append(STALE_QUESTION)
    if fp.get("run_id") != run_id:
        stale.append(STALE_RUN)
    keys = ("answer", "covered", "note", "evidence", "evidence_text", "issues", "verified",
            "years_months", "drafted_at")
    return {**{k: data.get(k) for k in keys}, "stale": stale}


def attach(db: Session, job_id: int, profile_id: int, questions: list[dict], profile: Profile,
           index: ProfileIndex, run: LetterRun | None) -> None:
    """For a ``full`` job: mark each draftable question and attach its stored draft."""
    profile_fp = profile_fingerprint(profile, index)
    for q in questions:
        q["draftable"] = is_draftable(q)
        q["draft"] = None
        if not q["draftable"]:
            continue
        data = _stored(db.get(JobScreeningQuestion, (job_id, q["bank_id"])))
        if data is None or data.get("profile_id") != profile_id:
            continue
        q["draft"] = draft_view(data, profile_fp=profile_fp, question_fp=question_fingerprint(q),
                                run_id=run.id if run else None)


# --------------------------------------------------------------------------------------
# Code checks on a draft
# --------------------------------------------------------------------------------------
_WORD_NUMS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
_NUM = r"\d+(?:\.\d+)?|half an?|" + "|".join(sorted(_WORD_NUMS, key=len, reverse=True))
_DURATION = re.compile(
    rf"(?P<mod>just under|less than|under|nearly|almost|close to|up to|over|more than|at least|"
    rf"about|around|approximately|roughly)?\s*"
    rf"(?:\b(?P<lo>{_NUM})\s*(?:-|–|to)\s*)?\b(?P<num>{_NUM})(?P<half>\s+and\s+a\s+half)?\s*"
    rf"(?P<plus>\+)?\s*-?\s*"
    rf"(?P<unit>years?|yrs?|months?)"
    rf"(?:\s*(?:,|and)?\s*(?P<num2>{_NUM})\s*-?\s*(?P<unit2>months?))?\b",
    re.IGNORECASE,
)
# A bound from below never claims more than the figure.
_BELOW = {"just under", "less than", "under", "nearly", "almost", "close to", "up to"}


def _value(token: str) -> float:
    t = token.lower()
    if t.startswith("half"):
        return 0.5
    return float(t) if t[0].isdigit() else float(_WORD_NUMS[t])


def _months(num: str, unit: str) -> float:
    return _value(num) * (12 if unit.lower().startswith("y") else 1)


def stated_durations(text: str) -> list[tuple[str, float]]:
    """(phrase, months claimed at least) for each duration the text states as a claim.
    "under 2 years" claims nothing above 2 years, so it isn't listed; "over 2 years" claims
    more than 24 months; a range "1-2 years" is read at its top."""
    out = []
    for m in _DURATION.finditer(text or ""):
        mod = (m.group("mod") or "").lower()
        if mod in _BELOW:
            continue
        months = _months(m.group("num"), m.group("unit"))
        if m.group("half"):
            months += _months("0.5", m.group("unit"))
        if m.group("num2"):
            months += _months(m.group("num2"), m.group("unit2"))
        if mod in ("over", "more than"):
            months += 1
        out.append((m.group(0).strip(), months))
    return out


_NEGATED = re.compile(
    r"\b(?:not|no|never|haven't|have not|hasn't|has not|don't|do not|didn't|did not|without|"
    r"yet to|lack|new to|keen to learn|eager to learn|would like to learn|haven’t|don’t|didn’t)\b",
    re.IGNORECASE,
)
_EXPERIENCE_WORDS = re.compile(
    r"\b(?:experience[ds]?|worked|work with|used|using|built|developed|delivered|years?|months?)\b",
    re.IGNORECASE,
)


def code_issues(answer: str, claims: list[dict], *, ceiling_months: int | None, skill: str | None,
                missing_labels: list[str]) -> list[str]:
    """The 9c checks on top of ``verify`` (see the module docstring)."""
    issues: list[str] = []
    if not answer:
        return issues
    if ceiling_months is not None:
        for phrase, months in stated_durations(answer):
            if months > ceiling_months:
                support = (assist.describe_months(ceiling_months) if ceiling_months
                           else "no dated time")
                issues.append(f"{phrase!r} is more than your profile's dates support for {skill}: "
                              f"{support}")
    for label in missing_labels:
        part = assist.norm_skill(label)
        for sentence in split_sentences(answer):
            if assist.find_in(part, assist.norm_skill(sentence)) and not _NEGATED.search(sentence):
                issues.append(f"mentions {label}, which isn't in your profile: {sentence!r}")
                break
    for c in claims:
        source = (c.get("source") or "").strip().strip("[]`'\" ").strip()
        if source.startswith("skill:") and _EXPERIENCE_WORDS.search(c.get("quote") or ""):
            issues.append(f"{c.get('quote')!r} rests only on a listed skill, which backs \"skills in\" "
                          "or \"knowledge of\", not experience")
    return list(dict.fromkeys(issues))


# --------------------------------------------------------------------------------------
# What code tells the writer
# --------------------------------------------------------------------------------------
def part_facts(profile: Profile, index: ProfileIndex, skill: str, missing: list[str],
               today: datetime.date) -> list[dict]:
    """Evidence and years for each part of ``skill`` the profile HAS ("React Native with
    Expo" with no Expo -> React Native only). 9b's view needs one experience backing every
    part, which is right for a "you have it" label; a draft may instead say what the
    profile has of each part, so long as it never claims the missing ones (checked)."""
    missing_norm = {assist.norm_skill(m) for m in missing}
    raw = assist._labels_for_parts(skill, assist.skill_parts(skill))
    out = []
    for label in raw:
        if assist.norm_skill(label) in missing_norm:
            continue
        ev = assist.skill_evidence(profile, index, label)
        dated = [e for e in ev["experiences"] if e.start_date]
        work = [e for e in dated if e.experience_type in assist.WORK_TYPES]
        out.append({
            "label": label,
            "evidence": [i for b in assist.BASIS_ORDER for i in ev.get(b, [])],
            "all_months": assist.merged_months([(e.start_date, e.end_date) for e in dated], today),
            "work_months": assist.merged_months([(e.start_date, e.end_date) for e in work], today),
        })
    return out


def years_ceiling(parts: list[dict]) -> int:
    """The most time any stated duration may claim: the best-backed part's months."""
    return max((p["all_months"] for p in parts), default=0)


def context_text(a: dict | None, skill: str | None, parts: list[dict], missing: list[str]) -> str:
    """The per-question block the writer sees under the question."""
    lines = ["FACTS COMPUTED BY THE APP (exact):"]
    lines.append(f"- Skill asked about: {skill}" if skill else "- No specific skill named in the question.")
    for w in (a or {}).get("wanted", []):
        lines.append(f"- THE JOB WANTS ({w['importance']}): \"{w['text']}\"")
    for p in parts:
        for item in p["evidence"]:
            basis = BASIS_LABEL.get(item["basis"], item["basis"])
            lines.append(f"- EVIDENCE for {p['label']} [{item['pointer']}] ({basis}): {item['text']}")
        if p["all_months"]:
            work = (f" ({assist.describe_months(p['work_months'])} of it in work roles)"
                    if p["work_months"] else " (none of it in work roles)")
            lines.append(f"- YEARS with {p['label']}, from the profile's dates: at most "
                         f"{assist.describe_months(p['all_months'])}{work}.")
        else:
            lines.append(f"- YEARS with {p['label']}: no dated experience uses it, so no years can be "
                         "counted. State no duration for it.")
    for label in missing:
        lines.append(f"- NOT IN PROFILE (never claim): {label}")
    return "\n".join(lines)


# --------------------------------------------------------------------------------------
# The draft
# --------------------------------------------------------------------------------------
def _claim(job_id: int, bank_id: int) -> bool:
    with _lock:
        if (job_id, bank_id) in _in_flight:
            return False
        _in_flight.add((job_id, bank_id))
        return True


def _release(job_id: int, bank_id: int) -> None:
    with _lock:
        _in_flight.discard((job_id, bank_id))


def draft_question(db: Session, job_id: int, bank_id: int, profile_id: int, *,
                   today: datetime.date | None = None) -> dict:
    """Draft one open-ended question for one job. Raises ``LookupError`` (404),
    ``DraftRefused`` (no call made) or the LLM errors; stores and returns the draft view."""
    today = today or datetime.date.today()
    if not preferences.question_help_enabled(db, profile_id):
        raise DraftRefused("Question help is switched off (Personalise), so nothing is drafted.")
    link = db.get(JobScreeningQuestion, (job_id, bank_id))
    row = db.get(ScreeningQuestion, bank_id) if link is not None else None
    if link is None or row is None:
        raise LookupError(f"question {bank_id} is not on job {job_id}'s form")
    q = bank.question_view(row)
    if q["kind"] != "assisted":
        raise DraftRefused("This question is yours to answer: it is never sent to a model.")
    if not is_draftable(q):
        raise DraftRefused("Only open-ended (free-text) questions about your experience get a draft.")
    status, run, state = assist.full_run(db, job_id, profile_id)
    if status != assist.FULL:
        raise DraftRefused(assist.MESSAGES[status] or "This job doesn't get question help.")
    profile = assist._load_profile(db, profile_id)
    if profile is None:
        raise LookupError(f"profile {profile_id} not found")

    if not _claim(job_id, bank_id):
        raise DraftRefused("A draft for this question is already being written.")
    try:
        index = ProfileIndex(profile)
        a = assist.assist_question(q, state, profile, index, assist._Gaps([]), today)
        skill = (q["parameters"] or {}).get("skill") or None
        missing = assist._labels_for_parts(skill, assist._missing_parts(profile, skill)) if skill else []
        parts = part_facts(profile, index, skill, missing, today) if skill else []
        ceiling = years_ceiling(parts) if skill else None  # no subject = nothing to measure against
        job = db.get(JobListing, job_id)
        ad = (job.raw_description or "").strip() if job else ""
        [out] = writer.draft_answers(
            [row.text], writer.ProfileContext(profile, index), ad,
            contexts=[context_text(a, skill, parts, missing)], system_prompt=SYSTEM_PROMPT,
            tier=TIER, task=TASK, job_id=job_id, match_id=run.match_id, run_id=None,
        )
    finally:
        _release(job_id, bank_id)

    claims = out.pop("_claims", [])
    best = max(parts, key=lambda p: p["all_months"])["label"] if parts else skill
    issues = [*out["issues"], *code_issues(out["answer"], claims, ceiling_months=ceiling, skill=best,
                                          missing_labels=missing)]
    issues = list(dict.fromkeys(issues))
    data = {
        "profile_id": profile_id,
        "answer": out["answer"],
        "covered": out["covered"],
        "note": out["note"],
        "evidence": out["evidence"],
        "evidence_text": {p: assist._clip(writer.resolve(writer.ProfileContext(profile, index), p))
                          for p in out["evidence"]},
        "issues": issues,
        "verified": bool(out["answer"]) and not issues,
        "years_months": ceiling,
        "fingerprint": {"profile": profile_fingerprint(profile, index),
                        "question": question_fingerprint(q), "run_id": run.id},
        "drafted_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    }
    link.draft = json.dumps(data)
    db.commit()
    view = draft_view(data, profile_fp=data["fingerprint"]["profile"],
                      question_fp=data["fingerprint"]["question"], run_id=run.id)
    return {"job_id": job_id, "bank_id": bank_id, "draft": view}
