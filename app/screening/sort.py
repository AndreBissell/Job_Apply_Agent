"""Sort a Quick Apply question: kind + answering strategy + parameters, no LLM.

Plan §10.1 "Sorting a question, cheapest first". Layer 1 (bank hit) lives in bank.py;
this module is layers 2-4, tried in order:

2. ``library_id``  Seek's standard-library ids (``AU_Q_<n>``), a small table.
3. ``keyword``     `user` topics in any wording (work rights, salary, notice, ...).
                   Runs before anything that could send a question to a model, so
                   these never reach the LLM.
4. ``template``    Seek's generated per-role questions, recognised by their text.

Anything none of them sorts returns None: the bank row stays kind ``unknown`` and
status ``new`` until layer 5 (the small model, Phase 9b) or the user's review.

The keyword patterns are deliberately specific ("in office", not "office"): a
`user` hit means no help is ever offered, so "years as an office manager" or
"Microsoft Office" must not trip them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.screening.identity import clean_text, normalise_text, parse_library_id

KINDS = ("user", "assisted", "unknown")
ASSISTED_STRATEGIES = (
    "years_role_bracket",
    "years_skill_text",
    "skill_in_role_yes_no",
    "skill_multi_select",
    "free_text_describe",
)
STRATEGIES = ("user", *ASSISTED_STRATEGIES)
CLASSIFIED_BY = ("library_id", "keyword", "template", "model", "user")
USER_TOPICS = (
    "work_rights", "identity", "legal", "salary", "notice",
    "work_arrangement", "source", "motivation",
)


@dataclass(frozen=True)
class Sorting:
    kind: str
    strategy: str
    parameters: dict = field(default_factory=dict)
    classified_by: str = ""


# --- layer 2: Seek library ids ---------------------------------------------------
# Confirmed stable across jobs for AU_Q_6 and AU_Q_13 (docs/quick-apply-samples.md).
# Whether AU_Q_218 is one id per skill is still open (plan §10.1): its skill comes
# from the text, so either answer works.
_LIBRARY_TABLE: dict[str, tuple[str, str, dict]] = {
    "AU_Q_6": ("user", "user", {"topic": "work_rights"}),
    "AU_Q_8": ("user", "user", {"topic": "salary"}),
    "AU_Q_13": ("user", "user", {"topic": "notice"}),
    "AU_Q_136": ("assisted", "skill_multi_select", {"options_are": "skills"}),
    "AU_Q_218": ("assisted", "skill_in_role_yes_no", {}),
}

# --- layer 3: `user` topics ----------------------------------------------------
_USER_KEYWORDS: tuple[tuple[str, re.Pattern], ...] = tuple(
    (topic, re.compile(pattern)) for topic, pattern in (
        ("work_rights", r"\bright to work\b|\bworking rights\b|\bwork rights\b|\bentitled to work\b"
                        r"|\blegally (?:able|allowed|entitled)\b|\bvisa\b|\bcitizen(?:ship)?\b"
                        r"|\bpermanent resident\b|\bsponsorship\b|\bwork permit\b"),
        ("identity", r"\baboriginal\b|\btorres strait\b|\bgender\b|\bdisabilit(?:y|ies)\b"
                     r"|\bethnicity\b|\bidentify as\b|\bpronouns?\b|\bveteran\b"),
        ("legal", r"\bcriminal\b|\bconvict(?:ed|ion|ions)?\b|\bfindings? against\b"
                  r"|\bcompliance action\b|\bpolice check\b|\bdeclar(?:e|ation)\b"
                  r"|\bworking with children\b|\bblue card\b|\ballegations?\b|\bbackground check\b"),
        ("salary", r"\bsalary\b|\bsalaries\b|\bremuneration\b|\bpay\b|\bcompensation\b"
                   r"|\bhourly rate\b|\bday rate\b"),
        ("notice", r"\bnotice\b|\bstart date\b|\bavailable to start\b|\bwhen can you start\b"),
        ("work_arrangement", r"\bin[- ]office\b|\bin the office\b|\bwork(?:ing)? from home\b|\bwfh\b"
                             r"|\bhybrid\b|\bon[- ]?site\b|\bwork remotely\b|\bremote work(?:ing)?\b"
                             r"|\bworking hours\b|\bhours of work\b|\bmonday to friday\b"
                             r"|\bshifts?\b|\brosters?\b|\bweekends?\b|\brelocat(?:e|ing|ion)\b"),
        ("source", r"\bhow did you (?:hear|find out|learn) about\b|\bwhere did you (?:see|hear|find)\b"
                   r"|\breferred by\b|\breferral\b"),
        ("motivation", r"\bmotivat(?:ed|es|ion)\b|\bwhy do you want\b|\bwhy are you interested\b"
                       r"|\bwhat interests you\b"),
    )
)

# --- layer 4: text templates for Seek's generated questions -----------------------
_YEARS_AS_ROLE = re.compile(
    r"^how many years'? (?:of )?experience do you have as (?:an? )?(?P<role>.+?)\s*\??$",
    re.IGNORECASE,
)
_SKILL_IN_ROLE = re.compile(
    r"^have you worked in a role (?:which|that) requires? (?P<skill>.+?) experience\s*\??$",
    re.IGNORECASE,
)
# "C# development" -> skill "C#"; the full phrase is kept too.
_SKILL_SUFFIX = re.compile(r"\s+(?:development|programming|software development)$", re.IGNORECASE)


def _skill_in_role_params(text: str) -> dict | None:
    m = _SKILL_IN_ROLE.match(clean_text(text))
    if not m:
        return None
    phrase = m.group("skill").strip()
    return {"skill": _SKILL_SUFFIX.sub("", phrase).strip() or phrase, "phrase": phrase}


def by_library_id(seek_question_id: str, text: str) -> Sorting | None:
    lib = parse_library_id(seek_question_id)
    if not lib or lib[0] not in _LIBRARY_TABLE:
        return None
    kind, strategy, params = _LIBRARY_TABLE[lib[0]]
    params = dict(params)
    if strategy == "skill_in_role_yes_no":
        params.update(_skill_in_role_params(text) or {})
    return Sorting(kind, strategy, params, "library_id")


def user_topic(text: str) -> str | None:
    """The `user` topic the question's wording names, or None."""
    norm = normalise_text(text)
    for topic, pattern in _USER_KEYWORDS:
        if pattern.search(norm):
            return topic
    return None


def by_keyword(text: str) -> Sorting | None:
    topic = user_topic(text)
    return Sorting("user", "user", {"topic": topic}, "keyword") if topic else None


def by_template(text: str, input_type: str, option_labels: list[str]) -> Sorting | None:
    cleaned = clean_text(text)
    m = _YEARS_AS_ROLE.match(cleaned)
    if m and input_type in ("dropdown", "single") and option_labels:
        return Sorting("assisted", "years_role_bracket", {"role": m.group("role").strip()}, "template")
    if input_type == "single":
        params = _skill_in_role_params(cleaned)
        if params:
            return Sorting("assisted", "skill_in_role_yes_no", params, "template")
    return None


def sort_question(seek_question_id: str, text: str, input_type: str,
                  option_labels: list[str]) -> Sorting | None:
    """Layers 2-4 in order; None = unsorted (left for the model or the user)."""
    return (
        by_library_id(seek_question_id, text)
        or by_keyword(text)
        or by_template(text, input_type, option_labels)
    )
