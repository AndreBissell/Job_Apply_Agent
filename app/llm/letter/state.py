"""``LetterState`` — the one object every cover-letter tool reads and writes —
plus the evidence-pointer resolver (docs/cover-letter-loop-plan.md §5.3).

The state replaces passing prose critiques between steps: requirements are a
checklist with ids, checks belong to a draft version, and evidence points into
the profile by stable database id. It is serialised to JSON and persisted per
run (``letter_runs.state`` from Phase 3).

Pointer grammar (an evidence pointer names one piece of profile text):

    experience:<id>          the whole experience (title, org, dates, description)
    experience:<id>#s<n>     the n-th sentence (1-based) of its description
    qualification:<id>
    skill:<id>
    profile:summary

``ProfileIndex`` is built from ONE loaded profile, so a pointer to another
user's row simply doesn't resolve — claim checking (stage 1) is a lookup.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from app.models import Profile
from app.preferences import (
    DEFAULT_LETTER_MAX_DRAFTS,
    DEFAULT_LETTER_MAX_TOOL_CALLS,
    DEFAULT_LLM_RUN_BUDGET_USD,
)

# ---------------------------------------------------------------------------
# Evidence pointers
# ---------------------------------------------------------------------------
_POINTER_RE = re.compile(
    r"^(?:(?P<kind>experience|qualification|skill):(?P<id>\d+)(?:#s(?P<sent>\d+))?|profile:summary)$"
)
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")
_BULLET_RE = re.compile(r"^\s*(?:[-*•·]|\d+[.)])\s+")


def split_sentences(text: str | None) -> list[str]:
    """Split free text into sentences, treating each line/bullet as a boundary.

    Experience descriptions are a mix of prose and pasted CV bullets, so lines
    are split first and then sentence punctuation within each line. Deterministic
    on purpose: ``#s<n>`` pointers are only stable if the split never changes for
    the same text.
    """
    if not text:
        return []
    out: list[str] = []
    for line in text.splitlines():
        line = _BULLET_RE.sub("", line).strip()
        if not line:
            continue
        out.extend(s.strip() for s in _SENTENCE_END_RE.split(line) if s.strip())
    return out


def is_valid_pointer(pointer: str) -> bool:
    return bool(_POINTER_RE.match(pointer or ""))


def _year(d) -> str:
    return str(d.year) if d else "present"


class ProfileIndex:
    """Resolves evidence pointers against one loaded profile.

    Build it from a ``Profile`` with ``qualifications``, ``experiences`` and
    ``skills`` loaded (the caller's session decides how). Holds plain strings
    afterwards, so it is safe to use after the session closes.
    """

    def __init__(self, profile: Profile):
        self.profile_id = profile.id
        self._text: dict[str, str] = {}
        # Eligibility facts (not citable evidence): lets match_profile judge items like
        # "Australian work rights" without them ever becoming letter claims.
        self.facts: dict[str, str] = {
            k: v
            for k, v in (
                ("visa/work status", profile.visa_status),
                ("location", profile.location),
                ("target location", profile.target_location),
            )
            if v
        }
        self._heads: dict[str, str] = {}  # experience pointer -> "Title at Org (span)", no description
        if profile.summary:
            self._text["profile:summary"] = profile.summary.strip()
        for q in profile.qualifications:
            parts = [q.title, q.institution, q.field_of_study]
            status = f" ({q.status})" if q.status else ""
            self._text[f"qualification:{q.id}"] = ", ".join(p for p in parts if p) + status
        for sk in profile.skills:
            self._text[f"skill:{sk.id}"] = sk.name
        for e in profile.experiences:
            org = f" at {e.organization}" if e.organization else ""
            span = f" ({_year(e.start_date)}–{_year(e.end_date)})" if e.start_date or e.end_date else ""
            head = f"{e.title}{org}{span}"
            self._heads[f"experience:{e.id}"] = head
            self._text[f"experience:{e.id}"] = (
                f"{head}: {e.description.strip()}" if e.description else head
            )
            for n, sentence in enumerate(split_sentences(e.description), start=1):
                self._text[f"experience:{e.id}#s{n}"] = sentence

    def resolve(self, pointer: str) -> str | None:
        """The profile text a pointer names, or None if it is malformed or unknown."""
        if not is_valid_pointer(pointer):
            return None
        return self._text.get(pointer)

    def prompt_catalog(self) -> str:
        """The profile as an LLM-readable list of citable pointers.

        Experiences appear as a header line with their description split into
        sentence-level pointers underneath, so a model cites the narrowest piece of
        evidence (``experience:12#s3``) instead of a whole role. Every pointer shown
        resolves, so a model that copies them exactly cannot cite something that
        does not exist.
        """
        lines: list[str] = []
        if "profile:summary" in self._text:
            lines.append(f"[profile:summary] {self._text['profile:summary']}")
        for key, text in self._text.items():
            if key.startswith("qualification:"):
                lines.append(f"[{key}] {text}")
        for key, head in self._heads.items():
            lines.append(f"[{key}] {head}")
            n = 1
            while f"{key}#s{n}" in self._text:
                lines.append(f"    [{key}#s{n}] {self._text[f'{key}#s{n}']}")
                n += 1
        skills = [(k, t) for k, t in self._text.items() if k.startswith("skill:")]
        if skills:
            lines.append("Skills the user lists (a bare listing is weak evidence on its own):")
            lines.extend(f"[{k}] {t}" for k, t in skills)
        return "\n".join(lines)

    def catalog(self, include_sentences: bool = True) -> dict[str, str]:
        """Every resolvable pointer -> its text. What a writer prompt cites from."""
        if include_sentences:
            return dict(self._text)
        return {k: v for k, v in self._text.items() if "#s" not in k}


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
RequirementStatus = Literal["unknown", "supported", "partial", "gap"]
# How much the EMPLOYER cares, inferred from the ad's wording (not from the heading
# alone: many ads use one flat "you likely have" list).
Importance = Literal["essential", "important", "nice_to_have"]
# What the LETTER should do with it: a separate question from importance.
#   headline        a core, distinguishing requirement; a lead point backed by evidence
#   mention         relevant; worth a brief mention if the candidate has it
#   implied         anyone competent at a headline/mention item has it (CI/CD, git); don't
#                   name it, but it MAY be used if it fits a sentence neatly
#   not_for_letter  eligibility/admin (work rights, licence, clearance, availability);
#                   shown to the user as a note, never written into the letter
LetterRole = Literal["headline", "mention", "implied", "not_for_letter"]


class UserDecision(BaseModel):
    """How a gap was resolved. Never by invention (plan §5.5)."""

    choice: Literal["have_it", "leave_out"]
    answer: str | None = None
    saved_as: list[str] = Field(default_factory=list)  # pointers to the new profile rows
    remembered: bool = False  # True when a stored gap_decisions "no" answered it
    # True when no user decided it: the evals' leave_out_gaps policy leaves every pending
    # gap out without asking. Not a confirmed gap, so suggest_learning ignores it.
    assumed: bool = False


class Requirement(BaseModel):
    id: str  # "R1", "R2", ... stable within a run
    text: str  # in the employer's words
    importance: Importance
    letter_role: LetterRole
    theme: str = ""  # related requirements share one, so a letter makes one point per theme
    implied_by: list[str] = Field(default_factory=list)  # ids of the headline/mention items it follows from
    # Short skill name ("Power BI"); empty for attitudes and duties. The key remembered
    # "no"s and the to-work-on list count by (plan §5.9). Added in ANALYSIS_VERSION 3.
    skill: str = ""
    evidence: list[str] = Field(default_factory=list)  # pointers
    status: RequirementStatus = "unknown"
    note: str | None = None  # match_profile's one-line reason (e.g. "Tableau, not Power BI")
    user_decision: UserDecision | None = None

    @property
    def needs_user(self) -> bool:
        """An essential requirement the letter would address, with no evidence and no
        user decision yet: blocks drafting (plan 5.7 "no silent gaps"). Eligibility
        items are not asked about: they become a note, not a letter claim."""
        return (
            self.importance == "essential"
            and self.letter_role in ("headline", "mention")
            and self.status == "gap"
            and self.user_decision is None
        )


class UserQuestion(BaseModel):
    """One ask_user question about a must-have gap (plan §5.5). Questions for a run
    are asked together; the run waits until every one is ``answered``.

    open           sent to the user, no answer yet
    needs_confirm  the user said Yes; ``proposal`` holds the parsed profile rows,
                   waiting for the user's one-click confirm (plan Q11)
    answered       a No (the requirement is left out and remembered), or a
                   confirmed Yes (the rows are saved; ``saved_as`` points at them)
    """

    id: str  # "Q1", "Q2", ... stable within a run
    requirement_id: str
    requirement_text: str  # the employer's words, shown to the user
    skill: str = ""
    skill_key: str  # gaps.skill_key(): what a "No" is remembered under
    prompt: str
    status: Literal["open", "needs_confirm", "answered"] = "open"
    choice: Literal["yes", "no"] | None = None
    answer: str | None = None  # the user's text for a Yes
    proposal: dict | None = None  # ProposedRows awaiting confirm
    saved_as: list[str] = Field(default_factory=list)


class Claim(BaseModel):
    text: str
    source: str | None = None  # pointer; None means the writer gave no source


class Check(BaseModel):
    """One check's result on one draft. ``issues`` block; ``warnings`` don't."""

    passed: bool
    issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class Draft(BaseModel):
    version: int
    text: str
    claims: list[Claim] = Field(default_factory=list)
    checks: dict[str, Check] = Field(default_factory=dict)  # "claims" | "requirements" | "style"


class JobInfo(BaseModel):
    job_id: int
    title: str
    company: str | None = None
    tone: str | None = None
    keywords: list[str] = Field(default_factory=list)
    screening_questions: list[str] = Field(default_factory=list)
    # What the ad asks applicants to include ("show off projects you've built in your
    # cover letter", "attach your transcript"). The writer follows the letter ones.
    application_instructions: list[str] = Field(default_factory=list)
    # Concrete facts the ad states about the employer/product/team that a letter can
    # cite: the raw material for the rubric's "specific detail" item.
    company_facts: list[str] = Field(default_factory=list)


# The side-output tools (plan §5.6): extras beside the letter, each behind its own
# preference toggle. They never change the letter, run at most once per run, and are not
# charged to the letter's tool-call cap (``Budget.side_calls`` counts them instead).
SIDE_OUTPUT_TOOLS = ("answer_screening", "suggest_learning", "suggest_resume_tweaks")


class SideOutputs(BaseModel):
    # Which side-output tools this run may call, fixed when the run opens (from the
    # toggles), so a resumed run keeps the settings it started with. Empty = none: the
    # letter-only behaviour from before Phase 7c.
    enabled: list[str] = Field(default_factory=list)
    ran: list[str] = Field(default_factory=list)  # tools that have run, ok or not (once each)
    errors: dict[str, str] = Field(default_factory=dict)  # tool -> why it failed
    screening_answers: list[dict] = Field(default_factory=list)
    learning_suggestions: list[dict] = Field(default_factory=list)
    resume_notes: dict | None = None


class Budget(BaseModel):
    # The three limits open at the user's preferences (``open_run(limits=...)``); these
    # defaults are the same constants, for states built without them (the evals, old runs).
    drafts_used: int = 0
    max_drafts: int = DEFAULT_LETTER_MAX_DRAFTS
    tool_calls: int = 0  # the letter's tools: what max_tool_calls caps
    max_tool_calls: int = DEFAULT_LETTER_MAX_TOOL_CALLS
    # Side-output tool calls, counted apart: each runs at most once, so they are bounded
    # without the cap, and charging them to it would let them crowd out a revision.
    side_calls: int = 0
    cost_usd: float = 0.0  # every call of the run, side outputs and orchestrator included
    max_cost_usd: float = DEFAULT_LLM_RUN_BUDGET_USD

    def over_cost(self) -> bool:
        return self.cost_usd >= self.max_cost_usd

    def exceeded(self) -> str | None:
        """Why the run must stop, or None."""
        if self.tool_calls >= self.max_tool_calls:
            return f"tool-call limit reached ({self.tool_calls}/{self.max_tool_calls})"
        if self.over_cost():
            return f"run budget reached (${self.cost_usd:.2f}/${self.max_cost_usd:.2f})"
        return None


REQUIRED_CHECKS = ("claims", "requirements", "style")


class LetterState(BaseModel):
    profile_id: int
    job: JobInfo
    requirements: list[Requirement] = Field(default_factory=list)
    drafts: list[Draft] = Field(default_factory=list)
    user_questions: list[UserQuestion] = Field(default_factory=list)
    side_outputs: SideOutputs = Field(default_factory=SideOutputs)
    budget: Budget = Field(default_factory=Budget)

    # -- drafts -------------------------------------------------------------
    @property
    def latest_draft(self) -> Draft | None:
        return self.drafts[-1] if self.drafts else None

    def add_draft(self, text: str, claims: list[Claim] | None = None) -> Draft:
        """Append a new draft version. Checks start empty: a pass on draft N says
        nothing about draft N+1."""
        draft = Draft(version=len(self.drafts) + 1, text=text, claims=claims or [])
        self.drafts.append(draft)
        self.budget.drafts_used = len(self.drafts)
        return draft

    def record_check(self, name: str, result: Check) -> None:
        """Attach a check result to the LATEST draft."""
        if self.latest_draft is None:
            raise ValueError(f"check {name!r} has no draft to attach to")
        self.latest_draft.checks[name] = result

    # -- requirements -------------------------------------------------------
    def requirements_by_role(self, role: str) -> list[Requirement]:
        return [r for r in self.requirements if r.letter_role == role]

    def eligibility_notes(self) -> list[str]:
        """Admin/eligibility items shown to the user, never written into the letter."""
        return [r.text for r in self.requirements if r.letter_role == "not_for_letter"]

    def requirement(self, req_id: str) -> Requirement | None:
        return next((r for r in self.requirements if r.id == req_id), None)

    def pending_gaps(self) -> list[Requirement]:
        return [r for r in self.requirements if r.needs_user]

    def waiting_on_user(self) -> bool:
        """Questions are out and unanswered (``ask_user`` pauses the run)."""
        return any(q.status != "answered" for q in self.user_questions)

    def question(self, question_id: str) -> UserQuestion | None:
        return next((q for q in self.user_questions if q.id == question_id), None)

    # -- budget -------------------------------------------------------------
    def budget_exceeded(self) -> str | None:
        return self.budget.exceeded()

    # -- orchestrator view --------------------------------------------------
    def summary_for_orchestrator(self) -> str:
        """Compact state for the orchestrator: statuses, check results and budget,
        never draft text (tools read the full text from state themselves)."""
        lines = [f"JOB: {self.job.title} at {self.job.company or 'unknown company'}"]

        if not self.requirements:
            lines.append("REQUIREMENTS: not analysed yet")
        else:
            lines.append("REQUIREMENTS:")
            for r in self.requirements:
                decision = ""
                if r.user_decision:
                    remembered = " (remembered)" if r.user_decision.remembered else ""
                    decision = f", user: {r.user_decision.choice}{remembered}"
                flag = "  <- needs a user decision" if r.needs_user else ""
                lines.append(
                    f"  {r.id} [{r.importance}/{r.letter_role}] {r.status}{decision} "
                    f"({len(r.evidence)} evidence){flag}"
                )

        draft = self.latest_draft
        if draft is None:
            lines.append("DRAFTS: none")
        else:
            lines.append(f"LATEST DRAFT: v{draft.version} of {self.budget.max_drafts} allowed")
            for name in REQUIRED_CHECKS:
                check = draft.checks.get(name)
                if check is None:
                    lines.append(f"  {name}: not run on this draft")
                else:
                    verdict = "pass" if check.passed else "FAIL"
                    detail = f" — {'; '.join(check.issues)}" if check.issues else ""
                    warn = f" (warnings: {'; '.join(check.warnings)})" if check.warnings else ""
                    lines.append(f"  {name}: {verdict}{detail}{warn}")

        if self.waiting_on_user():
            lines.append("WAITING: questions sent to the user")
        b = self.budget
        lines.append(
            f"BUDGET: tool calls {b.tool_calls}/{b.max_tool_calls}, "
            f"drafts {b.drafts_used}/{b.max_drafts}, ${b.cost_usd:.3f}/${b.max_cost_usd:.2f}"
        )
        return "\n".join(lines)
