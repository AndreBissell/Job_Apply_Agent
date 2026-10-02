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

    def catalog(self, include_sentences: bool = True) -> dict[str, str]:
        """Every resolvable pointer -> its text. What a writer prompt cites from."""
        if include_sentences:
            return dict(self._text)
        return {k: v for k, v in self._text.items() if "#s" not in k}


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
RequirementStatus = Literal["unknown", "supported", "partial", "gap"]


class UserDecision(BaseModel):
    """How a gap was resolved. Never by invention (plan §5.5)."""

    choice: Literal["have_it", "leave_out"]
    answer: str | None = None
    saved_as: list[str] = Field(default_factory=list)  # pointers to the new profile rows
    remembered: bool = False  # True when a stored gap_decisions "no" answered it


class Requirement(BaseModel):
    id: str  # "R1", "R2", ... stable within a run
    text: str  # in the employer's words
    priority: Literal["must", "should"]
    evidence: list[str] = Field(default_factory=list)  # pointers
    status: RequirementStatus = "unknown"
    user_decision: UserDecision | None = None

    @property
    def needs_user(self) -> bool:
        """A must-have gap nobody has decided on — blocks drafting."""
        return self.priority == "must" and self.status == "gap" and self.user_decision is None


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


class SideOutputs(BaseModel):
    screening_answers: list[dict] = Field(default_factory=list)
    learning_suggestions: list[dict] = Field(default_factory=list)
    resume_notes: dict | None = None


class Budget(BaseModel):
    drafts_used: int = 0
    max_drafts: int = 3
    tool_calls: int = 0
    max_tool_calls: int = 15
    cost_usd: float = 0.0
    max_cost_usd: float = 0.50

    def exceeded(self) -> str | None:
        """Why the run must stop, or None."""
        if self.tool_calls >= self.max_tool_calls:
            return f"tool-call limit reached ({self.tool_calls}/{self.max_tool_calls})"
        if self.cost_usd >= self.max_cost_usd:
            return f"run budget reached (${self.cost_usd:.2f}/${self.max_cost_usd:.2f})"
        return None


REQUIRED_CHECKS = ("claims", "requirements", "style")


class LetterState(BaseModel):
    profile_id: int
    job: JobInfo
    requirements: list[Requirement] = Field(default_factory=list)
    drafts: list[Draft] = Field(default_factory=list)
    user_questions: list[dict] = Field(default_factory=list)
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
    def requirement(self, req_id: str) -> Requirement | None:
        return next((r for r in self.requirements if r.id == req_id), None)

    def pending_gaps(self) -> list[Requirement]:
        return [r for r in self.requirements if r.needs_user]

    def waiting_on_user(self) -> bool:
        """Questions are out and unanswered (``ask_user`` pauses the run)."""
        return any(q.get("status") == "open" for q in self.user_questions)

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
                decision = f", user: {r.user_decision.choice}" if r.user_decision else ""
                flag = "  <- needs a user decision" if r.needs_user else ""
                lines.append(
                    f"  {r.id} [{r.priority}] {r.status}{decision} "
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
