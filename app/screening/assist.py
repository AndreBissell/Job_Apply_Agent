"""Help with a job's Quick Apply questions (plan §10.1, Phase 9b). Code only, apart
from layer 5 (classify.py) for a question layers 1-4 couldn't sort.

Who gets help (user decisions 2026-10-04):

    full            the job's letter came from the full pipeline (agent or workflow), so
                    its requirements checklist and evidence exist: help is shown
    one_shot        the letter came from the one-shot: the questions and their kinds only,
                    nothing is analysed on demand
    no_letter       no letter yet: "Create a cover letter to get help" (the existing
                    Regenerate)
    letter_pending  a letter run is writing or waiting on the user's answers
    off             the user switched question help off (Personalise,
                    ``screening_question_help_enabled``): every question is theirs to
                    answer, nothing is analysed, no model is called. Checked first.

For each ``assisted`` question, two views kept apart:

    wanted  WHAT THE JOB WANTS: the checklist items it relates to, with importance and
            the ad's own words (and a years figure if the ad states one)
    have    WHAT YOUR PROFILE HAS: evidence pointers into the CURRENT profile, so a skill
            added a minute ago counts and a deleted row doesn't

The user picks the answer (honour system): nothing here recommends an option. The one
thing code does insist on is that the app's own "you have it" label is backed by a
profile evidence pointer that resolves (``ProfileIndex``). A bracket for years is the
range the profile's dates fall in, rounded down, shown as what the profile says.

Specific beats general: "React Native with Expo" needs React Native AND Expo, and React.js
is neither; "worked in a role which requires C#" needs a work experience (job, internship,
volunteering), not a course, a project or a bare skill listing (the letter's
``listing_only`` rule).

A wanted skill with no trace anywhere in the profile is a gap: a remembered "no" shows
"you said you don't have this" (and counts this ad, source ``quick_apply``); otherwise a
Yes/No card (answers.propose_skill / answer_no_runless). A Yes never touches the letter.
"""

from __future__ import annotations

import datetime
import re

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app import gaps, preferences
from app.llm.letter import view
from app.llm.letter.state import LetterState, ProfileIndex, Requirement, split_sentences
from app.models import CoverLetter, Experience, JobListing, LetterRun, Match, Profile, ScreeningQuestion
from app.screening import bank, classify

FULL, ONE_SHOT, NO_LETTER, PENDING, OFF = "full", "one_shot", "no_letter", "letter_pending", "off"
PIPELINE_ENGINES = ("agent", "workflow")
LIVE_RUN = ("running", "waiting_user", "answered")

MESSAGES = {
    FULL: None,
    ONE_SHOT: ("Question help needs a full-pipeline cover letter. This job's letter was written "
               "in one pass, so only the question kinds are shown."),
    NO_LETTER: "Create a cover letter to get help with these questions.",
    PENDING: "A cover letter is being written for this job (or is waiting on your answers). "
             "Help with these questions appears when it's done.",
    OFF: ("Question help is switched off (Personalise), so every question here is yours to "
          "answer."),
}
OPEN_PROMPT = "Do you have any experience with these?"

# Experience types that count as a ROLE ("worked in a role", "years as a <role>").
WORK_TYPES = ("job", "internship", "volunteer")
BASIS_OF_TYPE = {
    "job": "work", "internship": "work", "volunteer": "work",
    "personal_project": "project", "university_project": "study", "assignment": "study",
}
BASIS_ORDER = ("work", "project", "study", "listed")
TEXT_MAX = 240

# --------------------------------------------------------------------------------------
# Skill matching
# --------------------------------------------------------------------------------------
# Extra words that don't change what a profile skill name is ("Microsoft Excel" is Excel,
# "React.js" is React). Anything else does ("React Native" is not React).
GENERIC_WORDS = frozenset({
    "microsoft", "ms", "google", "amazon", "aws", "apache", "adobe", "js", "programming",
    "language", "languages", "development", "framework", "library", "basic", "advanced",
})
# Names that contain another name but are a different thing: "react" inside "react
# native" is not React, "c" inside "objective c" is not C.
COMPOUNDS = ("react native", "objective c")
MIN_TEXT_LEN = 3  # shorter names (go, r, c) only match a skill name exactly, never prose

# prefilter.normalise_skill folds broad families together for match SCORING (HTML = CSS,
# MySQL = PostgreSQL = "sql", Node.js = JavaScript). For a "you have it" label that would
# be an overclaim, so here only spellings of the same thing are folded. Gap keys still use
# gaps.skill_key (normalise_skill), so remembered "no"s match as before.
_SPELLINGS = {
    "powerbi": "power bi", "ms excel": "excel", "microsoft excel": "excel",
    "restful apis": "rest apis", "restful api": "rest apis", "rest api": "rest apis",
    "python 3": "python", "python3": "python", "js": "javascript", "git hub": "github",
}


def norm_skill(name: str) -> str:
    """Lower-case, ``.-_/`` to spaces, single spaces, spelling variants folded."""
    s = re.sub(r"[.\-_/]", " ", (name or "").lower())
    s = re.sub(r"\s+", " ", s).strip()
    return _SPELLINGS.get(s, s)


_SPLIT = re.compile(r"\s*(?:,|;|\bwith\b|\busing\b|\band\b|&)\s*")


def _bound(phrase: str) -> re.Pattern:
    return re.compile(rf"(?<![a-z0-9#+]){re.escape(phrase)}(?![a-z0-9#+])")


def _searchable(part: str) -> bool:
    """Can ``part`` be looked for inside prose? Long enough, or carries a symbol (c#, c++)."""
    return len(part) >= MIN_TEXT_LEN or bool(re.search(r"[#+]", part))


def find_in(part: str, text_norm: str) -> bool:
    """Is the normalised skill ``part`` a whole phrase of ``text_norm``, not counting the
    places where it is only a piece of a different compound name?"""
    if not part or not text_norm:
        return False
    for compound in COMPOUNDS:
        if compound != part and _bound(part).search(compound):
            text_norm = _bound(compound).sub(" ", text_norm)
    return _bound(part).search(text_norm) is not None


def skill_parts(asked: str) -> list[str]:
    """"React Native with Expo" -> ["react native", "expo"]: every part must be backed."""
    parts = [norm_skill(p) for p in _SPLIT.split(asked or "")]
    return list(dict.fromkeys(p for p in parts if p))


def name_covers(have_name: str, part: str) -> bool:
    """Does a profile skill called ``have_name`` back the normalised ``part``? Exact, or
    ``part`` inside it with only generic words around ("Microsoft Excel" backs excel)."""
    h = norm_skill(have_name)
    if not h or not part:
        return False
    if h == part:
        return True
    if not find_in(part, h):
        return False
    return set(h.split()) - set(part.split()) <= GENERIC_WORDS


def _clip(text: str | None) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= TEXT_MAX else text[: TEXT_MAX - 1].rstrip() + "…"


def _experience_hits(exp: Experience, part: str) -> list[str]:
    """Pointers in one experience that back ``part``: a linked skill (the whole
    experience), its title, or the sentences of its description that name it."""
    hits: list[str] = []
    if any(name_covers(sk.name, part) for sk in exp.skills):
        hits.append(f"experience:{exp.id}")
    if _searchable(part):
        if find_in(part, norm_skill(exp.title or "")):
            hits.append(f"experience:{exp.id}")
        for n, sentence in enumerate(split_sentences(exp.description), start=1):
            if find_in(part, norm_skill(sentence)):
                hits.append(f"experience:{exp.id}#s{n}")
    return list(dict.fromkeys(hits))


def _item(index: ProfileIndex, pointer: str, basis: str, **extra) -> dict | None:
    text = index.resolve(pointer)
    if text is None:  # the label rule: no resolving pointer, no "you have it"
        return None
    return {"pointer": pointer, "text": _clip(text), "basis": basis, **extra}


def skill_evidence(profile: Profile, index: ProfileIndex, asked: str) -> dict:
    """What the profile has for ``asked``, by basis (work / project / study / listed).

    An experience counts only when it backs EVERY part of the skill; listed skills and
    qualifications likewise, between them. ``experiences`` keeps the matched experience
    rows (for years)."""
    parts = skill_parts(asked)
    out: dict = {b: [] for b in BASIS_ORDER}
    out["experiences"] = []
    if not parts:
        return out
    for exp in profile.experiences:
        per_part = [_experience_hits(exp, p) for p in parts]
        if not all(per_part):
            continue
        basis = BASIS_OF_TYPE.get(exp.experience_type, "project")
        pointers = list(dict.fromkeys(p for hits in per_part for p in hits))
        items = [i for i in (_item(index, p, basis, experience_id=exp.id) for p in pointers) if i]
        if items:
            out[basis].extend(items)
            out["experiences"].append(exp)
    listed: list[dict] = []
    covered = set()
    for sk in profile.skills:
        hit = [p for p in parts if name_covers(sk.name, p)]
        if hit:
            covered.update(hit)
            item = _item(index, f"skill:{sk.id}", "listed")
            if item:
                listed.append(item)
    if covered == set(parts):
        out["listed"] = listed
    quals = []
    for q in profile.qualifications:
        text = norm_skill(" ".join(x for x in (q.title, q.field_of_study) if x))
        if all(_searchable(p) and find_in(p, text) for p in parts):
            item = _item(index, f"qualification:{q.id}", "study")
            if item:
                quals.append(item)
    out["study"].extend(quals)
    return out


def best_basis(evidence: dict) -> str | None:
    return next((b for b in BASIS_ORDER if evidence.get(b)), None)


def _missing_parts(profile: Profile, asked: str) -> list[str]:
    """The parts of ``asked`` with no trace anywhere in the profile (gap candidates)."""
    missing = []
    for part in skill_parts(asked):
        if any(_experience_hits(e, part) for e in profile.experiences):
            continue
        if any(name_covers(sk.name, part) for sk in profile.skills):
            continue
        if _searchable(part) and any(
            find_in(part, norm_skill(" ".join(x for x in (q.title, q.field_of_study) if x)))
            for q in profile.qualifications
        ):
            continue
        missing.append(part)
    return missing


# --------------------------------------------------------------------------------------
# Years
# --------------------------------------------------------------------------------------
def _month_index(d: datetime.date) -> int:
    return d.year * 12 + d.month - 1


def merged_months(spans: list[tuple[datetime.date, datetime.date | None]], today: datetime.date) -> int:
    """Months covered by the spans, overlaps merged. Dates are months: a finished role
    counts its end month (Jan-Dec = 12 months); an ongoing one counts the months
    completed so far (the current month isn't over). Never rounds up."""
    intervals = []
    for start, end in spans:
        lo = _month_index(start)
        hi = _month_index(end) + 1 if end is not None else _month_index(today)
        hi = min(hi, _month_index(today) + 1)  # a future end date counts up to now
        if hi > lo:
            intervals.append((lo, hi))
    total, cur_lo, cur_hi = 0, None, None
    for lo, hi in sorted(intervals):
        if cur_hi is None or lo > cur_hi:
            if cur_hi is not None:
                total += cur_hi - cur_lo
            cur_lo, cur_hi = lo, hi
        else:
            cur_hi = max(cur_hi, hi)
    if cur_hi is not None:
        total += cur_hi - cur_lo
    return total


def describe_months(months: int) -> str:
    years, rest = divmod(months, 12)
    if months == 0:
        return "no time"
    parts = []
    if years:
        parts.append(f"{years} year{'s' if years != 1 else ''}")
    if rest:
        parts.append(f"{rest} month{'s' if rest != 1 else ''}")
    return " ".join(parts)


_B_NONE = re.compile(r"^(?:no experience|none|no)$")
_B_LT = re.compile(r"^(?:less than|under|fewer than)\s+(\d+)\s+years?$")
_B_GT = re.compile(r"^(?:more than|over)\s+(\d+)\s+years?$")
_B_GE = re.compile(r"^(\d+)\s*\+\s*years?$|^(\d+)\s+years? or more$")
_B_EQ = re.compile(r"^(\d+)\s+years?$")
_B_RANGE = re.compile(r"^(\d+)\s*(?:-|to)\s*(\d+)\s+years?$")


def bracket_for(months: int, labels: list[str]) -> str | None:
    """The option the profile's months fall in, rounding DOWN to whole years: 5 years 11
    months is "5 years", never "More than 5 years". None when no option fits."""
    whole = months // 12
    fits: list[tuple[int, str]] = []  # (priority, label)
    for label in labels:
        t = re.sub(r"\s+", " ", (label or "").lower().replace("’", "'")).strip(" .")
        if _B_NONE.match(t):
            if months == 0:
                fits.append((0, label))
        elif m := _B_EQ.match(t):
            if whole == int(m.group(1)):
                fits.append((1, label))
        elif m := _B_RANGE.match(t):
            if int(m.group(1)) <= whole <= int(m.group(2)):
                fits.append((1, label))
        elif m := _B_LT.match(t):
            if months < int(m.group(1)) * 12:
                fits.append((2, label))
        elif m := _B_GT.match(t):
            if whole > int(m.group(1)):
                fits.append((3, label))
        elif m := _B_GE.match(t):
            if whole >= int(m.group(1) or m.group(2)):
                fits.append((3, label))
    return min(fits)[1] if fits else None


# --------------------------------------------------------------------------------------
# Roles
# --------------------------------------------------------------------------------------
_FOLDS = ((re.compile(r"\bfull stack\b"), "fullstack"), (re.compile(r"\bfront end\b"), "frontend"),
          (re.compile(r"\bback end\b"), "backend"))
ROLE_FAMILIES = (
    frozenset({"developer", "engineer", "programmer", "dev"}),
    frozenset({"administrator", "admin"}),
    frozenset({"analyst"}), frozenset({"designer"}), frozenset({"tester", "qa"}),
)
ROLE_FILLER = frozenset({
    "a", "an", "the", "of", "in", "senior", "snr", "junior", "jnr", "graduate", "grad",
    "intermediate", "mid", "level", "entry", "lead", "principal", "trainee", "software",
})


def _fold(text: str) -> str:
    t = norm_skill(text or "")
    for pattern, repl in _FOLDS:
        t = pattern.sub(repl, t)
    return t


def role_terms(role: str) -> tuple[frozenset, list[str]]:
    """(head family, distinctive words): "Front End React Developer" ->
    ({developer, engineer, ...}, ["frontend", "react"])."""
    toks = [t for t in _fold(role).split() if t not in ROLE_FILLER]
    if not toks:
        return frozenset(), []
    head = toks[-1]
    family = next((f for f in ROLE_FAMILIES if head in f), frozenset({head}))
    return family, toks[:-1]


def experience_matches_role(exp: Experience, role: str) -> bool:
    """A WORK experience whose title has the role's head noun (developer ~ engineer) and
    whose title or linked skills carry every distinctive word ("react" may come from
    the skills of a "Frontend Developer" role)."""
    if exp.experience_type not in WORK_TYPES:
        return False
    family, distinctive = role_terms(role)
    title = set(_fold(exp.title).split())
    if not family or not family & title:
        return False
    skills = " ".join(_fold(sk.name) for sk in exp.skills)
    return all(t in title or find_in(t, skills) for t in distinctive)


# --------------------------------------------------------------------------------------
# What the job wants (the run's requirements checklist)
# --------------------------------------------------------------------------------------
_FIGURE = re.compile(r"\b(\d+)\s*(?:\+|plus|or more)?\s*(?:(?:-|to)\s*\d+\s*)?\+?\s*years?\b", re.IGNORECASE)


def _req_view(r: Requirement) -> dict:
    return {"requirement_id": r.id, "text": r.text, "importance": r.importance,
            "letter_role": r.letter_role, "skill": r.skill}


def _ad_items(state: LetterState) -> list[Requirement]:
    return [r for r in state.requirements if r.letter_role != "not_for_letter"]


def wanted_for_skill(state: LetterState, asked: str) -> list[Requirement]:
    """Checklist items that ask for (part of) ``asked``: by their skill name, or the
    skill named in their wording. Information only; matched loosely on purpose."""
    parts = skill_parts(asked)
    out = []
    for r in _ad_items(state):
        skill = norm_skill(r.skill or "")
        text = norm_skill(r.text or "")
        if any(
            (skill and (skill == p or find_in(p, skill) or (_searchable(skill) and find_in(skill, p))))
            or (_searchable(p) and find_in(p, text))
            for p in parts
        ):
            out.append(r)
    return out


def wanted_for_role(state: LetterState, role: str) -> list[Requirement]:
    """Checklist items about the role: they name one of its distinctive words, or ask
    for years of experience."""
    _, distinctive = role_terms(role)
    out = []
    for r in _ad_items(state):
        text = _fold(r.text)
        if any(find_in(t, text) for t in distinctive) or _FIGURE.search(r.text or ""):
            out.append(r)
    return out


def wanted_figure(reqs: list[Requirement]) -> str | None:
    for r in reqs:
        m = _FIGURE.search(r.text or "")
        if m:
            return m.group(0).strip()
    return None


# --------------------------------------------------------------------------------------
# Gaps
# --------------------------------------------------------------------------------------
_RANK = {"essential": 0, "important": 1, "nice_to_have": 2}


def _top_importance(reqs: list[Requirement]) -> str | None:
    ranked = sorted((r.importance for r in reqs if r.importance), key=lambda i: _RANK.get(i, 9))
    return ranked[0] if ranked else None


class _Gaps:
    """Collects the gap cards for one job and the remembered "no"s to count."""

    def __init__(self, decisions):
        self.decisions = decisions
        self.remembered_seen: dict[int, str | None] = {}  # gap_id -> importance

    def card(self, label: str, wanted: list[Requirement], question_text: str) -> dict:
        importance = _top_importance(wanted)
        requirement_text = wanted[0].text if wanted else question_text
        decision = gaps.find_decision(self.decisions, label)
        card = {
            "skill": label, "skill_key": gaps.skill_key(label), "importance": importance,
            # False = no ad requirement asks for it, only the form's question does: the card
            # must not say "wanted" (found in the live check 2026-10-05, Azure DevOps).
            "wanted_by_ad": bool(wanted),
            "requirement_text": requirement_text, "remembered": decision is not None,
            "gap_id": decision.id if decision else None,
        }
        if decision is not None:
            prev = self.remembered_seen.get(decision.id)
            self.remembered_seen[decision.id] = gaps._higher(prev, importance)
        return card


def _labels_for_parts(asked: str, missing: list[str]) -> list[str]:
    """Readable names for the missing parts, in the question's own spelling."""
    raw = [p.strip() for p in _SPLIT.split(asked or "") if p.strip()]
    by_norm = {norm_skill(p): p for p in raw}
    return [by_norm.get(m, m) for m in missing]


# --------------------------------------------------------------------------------------
# One question
# --------------------------------------------------------------------------------------
def _have_view(evidence: dict) -> list[dict]:
    return [i for b in BASIS_ORDER for i in evidence.get(b, [])]


def compact(items: list[dict]) -> list[dict]:
    """The evidence rows to SHOW: each pointer once, and a sentence of an experience
    (``experience:12#s2``) dropped when the whole experience (``experience:12``, whose
    text holds that sentence) is already listed. Labels still come from the full list."""
    whole = {i["pointer"] for i in items if "#" not in i["pointer"]}
    seen, out = set(), []
    for i in items:
        p = i["pointer"]
        if p in seen or ("#" in p and p.split("#")[0] in whole):
            continue
        seen.add(p)
        out.append(i)
    return out


def _empty_assist(strategy: str, subject: str | None) -> dict:
    return {"strategy": strategy, "subject": subject, "wanted": [], "wanted_figure": None,
            "have": {"summary": "", "evidence": [], "years": None, "not_counted": []},
            "options": [], "profile_option": None, "open_ended": False, "prompt": None, "gaps": []}


def _skill_question(q: dict, state, profile, index, gap_box: _Gaps, today, *, in_role: bool,
                    years: bool) -> dict:
    skill = (q["parameters"] or {}).get("skill") or ""
    out = _empty_assist(q["strategy"], skill or None)
    if not skill:
        out["open_ended"], out["prompt"] = True, OPEN_PROMPT
        return out
    wanted = wanted_for_skill(state, skill)
    out["wanted"] = [_req_view(r) for r in wanted]
    out["wanted_figure"] = wanted_figure(wanted) if years else None
    ev = skill_evidence(profile, index, skill)

    if in_role:
        counted, not_counted = ev["work"], ev["project"] + ev["study"] + ev["listed"]
    else:
        counted, not_counted = _have_view(ev), []
    out["have"]["evidence"] = compact(counted)
    out["have"]["not_counted"] = compact(not_counted)

    if years:
        work_exps = [e for e in ev["experiences"] if e.experience_type in WORK_TYPES]
        dated = [e for e in work_exps if e.start_date]
        all_dated = [e for e in ev["experiences"] if e.start_date]
        work_m = merged_months([(e.start_date, e.end_date) for e in dated], today)
        all_m = merged_months([(e.start_date, e.end_date) for e in all_dated], today)
        out["have"]["years"] = {
            "work_months": work_m, "work": describe_months(work_m),
            "all_months": all_m, "all": describe_months(all_m),
            "undated": [e.title for e in ev["experiences"] if not e.start_date],
        }

    if counted:
        if in_role:
            out["have"]["summary"] = f"Used in a work role: {counted[0]['text']}"
        elif years:
            y = out["have"]["years"]
            if y["all_months"]:
                out["have"]["summary"] = (
                    f"In work roles: {y['work']}; with projects and study: {y['all']} (from your dates)"
                )
            else:
                out["have"]["summary"] = ("In your profile, but no dated experience uses it, so no "
                                          "years can be counted")
        else:
            out["have"]["summary"] = f"In your profile ({best_basis(ev)})"
    elif in_role and not_counted:
        out["have"]["summary"] = ("In your profile, but not in a work role (a course, project or "
                                  "listed skill doesn't count as a role)")
    else:
        out["have"]["summary"] = "Nothing in your profile backs this"

    missing = _missing_parts(profile, skill)
    for label in _labels_for_parts(skill, missing):
        out["gaps"].append(gap_box.card(label, wanted_for_skill(state, label) or wanted, q["text"]))
    if not counted and not not_counted:
        out["open_ended"], out["prompt"] = True, OPEN_PROMPT
    return out


def _years_role(q: dict, state, profile, index, today) -> dict:
    role = (q["parameters"] or {}).get("role") or ""
    out = _empty_assist(q["strategy"], role or None)
    wanted = wanted_for_role(state, role) if role else []
    out["wanted"] = [_req_view(r) for r in wanted]
    out["wanted_figure"] = wanted_figure(wanted)
    exps = [e for e in profile.experiences if role and experience_matches_role(e, role)]
    items = [i for i in (_item(index, f"experience:{e.id}", "work", experience_id=e.id) for e in exps) if i]
    out["have"]["evidence"] = items
    dated = [e for e in exps if e.start_date]
    months = merged_months([(e.start_date, e.end_date) for e in dated], today)
    undated = [e.title for e in exps if not e.start_date]
    out["have"]["years"] = {"work_months": months, "work": describe_months(months),
                            "all_months": months, "all": describe_months(months), "undated": undated}
    if items and dated:
        out["profile_option"] = bracket_for(months, q["options"] or [])
        out["have"]["summary"] = (f"{describe_months(months)} as a {role}, from the dates of "
                                  f"{len(dated)} role{'s' if len(dated) != 1 else ''} in your profile")
        if undated:
            out["have"]["summary"] += f" ({len(undated)} more without dates not counted)"
    elif items:
        out["have"]["summary"] = "Matching roles in your profile have no dates, so the years can't be counted"
        out["open_ended"], out["prompt"] = True, OPEN_PROMPT
    else:
        out["have"]["summary"] = f"No role in your profile matches \"{role}\""
        out["open_ended"], out["prompt"] = True, OPEN_PROMPT
    return out


def _multi_select(q: dict, state, profile, index, gap_box: _Gaps) -> dict:
    out = _empty_assist(q["strategy"], None)
    for label in q["options"] or []:
        wanted = wanted_for_skill(state, label)
        ev = skill_evidence(profile, index, label)
        have_items = _have_view(ev)
        is_wanted, has = bool(wanted), bool(have_items)
        tag = ("wanted_have" if is_wanted and has else "wanted_missing" if is_wanted
               else "have" if has else None)
        option = {
            "label": label, "wanted": is_wanted, "wanted_by": [_req_view(r) for r in wanted],
            "have": has, "have_basis": best_basis(ev), "evidence": have_items, "tag": tag,
        }
        out["options"].append(option)
        if is_wanted and not has:
            out["gaps"].append(gap_box.card(label, wanted, q["text"]))
    seen = set()
    for o in out["options"]:
        for w in o["wanted_by"]:
            if w["requirement_id"] not in seen:
                seen.add(w["requirement_id"])
                out["wanted"].append(w)
    out["have"]["evidence"] = compact([i for o in out["options"] for i in o["evidence"]])
    n_have = sum(1 for o in out["options"] if o["have"])
    out["have"]["summary"] = f"Your profile backs {n_have} of {len(out['options'])} options"
    return out


def assist_question(q: dict, state: LetterState, profile: Profile, index: ProfileIndex,
                    gap_box: _Gaps, today: datetime.date) -> dict | None:
    strategy = q.get("strategy")
    if q.get("kind") != "assisted" or not strategy:
        return None
    if strategy == "years_role_bracket":
        return _years_role(q, state, profile, index, today)
    if strategy == "skill_in_role_yes_no":
        return _skill_question(q, state, profile, index, gap_box, today, in_role=True, years=False)
    if strategy == "years_skill_text":
        return _skill_question(q, state, profile, index, gap_box, today, in_role=False, years=True)
    if strategy == "skill_multi_select":
        return _multi_select(q, state, profile, index, gap_box)
    if strategy == "free_text_describe":
        return _skill_question(q, state, profile, index, gap_box, today, in_role=False, years=False)
    return None


# --------------------------------------------------------------------------------------
# The job
# --------------------------------------------------------------------------------------
def help_for_job(db: Session, job_id: int, profile_id: int) -> tuple[str, LetterState | None]:
    """Which help this job gets, and the full-pipeline run's state when it is ``full``."""
    status, _, state = full_run(db, job_id, profile_id)
    return status, state


def full_run(db: Session, job_id: int, profile_id: int) -> tuple[str, LetterRun | None, LetterState | None]:
    """``help_for_job`` plus the run itself (its id fingerprints a 9c draft)."""
    match = db.scalar(select(Match).where(Match.user_id == profile_id, Match.job_id == job_id))
    if match is None:
        return NO_LETTER, None, None
    letter = db.scalar(select(CoverLetter.generated_content).where(CoverLetter.match_id == match.id))
    runs = db.scalars(
        select(LetterRun)
        .where(LetterRun.match_id == match.id, LetterRun.engine.in_(PIPELINE_ENGINES))
        .order_by(LetterRun.id.desc())
    ).all()
    live = bool(runs) and runs[0].status in LIVE_RUN
    if not letter:
        return (PENDING if live else NO_LETTER), None, None
    found = view.letter_run(runs, letter)
    if found is None:
        return (PENDING if live else ONE_SHOT), None, None
    run, state = found[0], found[1]
    if not any(r.status != "unknown" for r in state.requirements):
        return ONE_SHOT, None, None  # no evidence to work from
    return FULL, run, state


def _load_profile(db: Session, profile_id: int) -> Profile | None:
    return db.scalar(
        select(Profile).where(Profile.id == profile_id).options(
            selectinload(Profile.experiences).selectinload(Experience.skills),
            selectinload(Profile.skills), selectinload(Profile.qualifications),
        )
    )


def assist_job(db: Session, job_id: int, profile_id: int, *, today: datetime.date | None = None) -> dict:
    """GET /jobs/{id}/screening-assist: the job's questions in form order, each with its
    kind and, for a ``full`` job, the help for an assisted one. Sorts an ``unknown``
    question with the small model only for a ``full`` job (once per bank row). Never
    drafts: an open-ended assisted question is marked ``draftable`` and carries its
    stored draft (with ``stale`` reasons) if the user asked for one before."""
    today = today or datetime.date.today()
    job = db.get(JobListing, job_id)
    if preferences.question_help_enabled(db, profile_id):
        status, run, state = full_run(db, job_id, profile_id)
    else:
        status, run, state = OFF, None, None
    questions = bank.job_questions(db, job_id)
    out = {
        "job_id": job_id, "job_title": job.title if job else None, "help": status,
        "message": MESSAGES[status], "action": "regenerate" if status == NO_LETTER else None,
        "open_prompt": OPEN_PROMPT, "questions": questions,
    }
    for q in questions:
        q["assist"] = None
        q["draftable"], q["draft"] = False, None
    profile = _load_profile(db, profile_id) if status == FULL else None
    if status != FULL or profile is None:
        return out

    from app.screening import drafts  # drafts imports this module

    index = ProfileIndex(profile)
    gap_box = _Gaps(gaps.active_decisions(db, profile_id))
    for q in questions:
        if q["kind"] == "unknown":
            row = db.get(ScreeningQuestion, q["bank_id"])
            if row is not None and classify.classify(db, row, job_id=job_id):
                q.update(bank.question_view(row))
        q["assist"] = assist_question(q, state, profile, index, gap_box, today)
    drafts.attach(db, job_id, profile_id, questions, profile, index, run)

    # A remembered "no" the job asks for again: count the ad (once per ad), as a run does.
    by_id = {d.id: d for d in gap_box.decisions}
    for gap_id, importance in gap_box.remembered_seen.items():
        gaps.record_sighting(db, by_id[gap_id], job_id=job_id, job_title=job.title if job else None,
                             source="quick_apply", importance=importance)
    db.commit()
    return out
