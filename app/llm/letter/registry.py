"""Tool registry for the cover-letter agent (plan §5.2, Phase 7).

Maps each tool name to the plain function that does the work, a model-facing
``ToolSpec``, and a gate. Only the agent needs this: the fixed workflow imports the
tool functions directly.

A gate is a guardrail run BEFORE the tool. When it refuses, the tool does not run,
no tool call is charged, and the refusal goes back to the orchestrator as the
result, worded so it can recover ("check_claims already ran on draft 2"). The
gates are the same ``guardrails`` functions the tools and the workflow use, so the
agent can't do anything the workflow wouldn't be allowed to.

Each description says when to use the tool AND when not to (plan §5.2, "What a good
tool definition needs"). The tools take no arguments: everything they need is in
the run's state, which the orchestrator never reads in full.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from app.llm.client import ToolSpec
from app.llm.letter import guardrails, side_outputs
from app.llm.letter.outcome import GapPolicy
from app.llm.letter.gap_policy import ask_user_tool
from app.llm.letter.runner import ToolFn
from app.llm.letter.state import SIDE_OUTPUT_TOOLS, LetterState
from app.llm.letter.tools.analyze_job import analyze_job
from app.llm.letter.tools.answer_screening import answer_screening
from app.llm.letter.tools.check_claims import check_claims
from app.llm.letter.tools.check_requirements import check_requirements
from app.llm.letter.tools.generate import generate_letter
from app.llm.letter.tools.match_profile import match_profile
from app.llm.letter.tools.revise import revise_letter
from app.llm.letter.tools.style_lint import style_lint
from app.llm.letter.tools.suggest_learning import suggest_learning
from app.llm.letter.tools.suggest_resume_tweaks import suggest_resume_tweaks

Gate = Callable[[LetterState], "str | None"]

FINISH = "finish"


@dataclass(frozen=True)
class RegisteredTool:
    spec: ToolSpec
    fn: ToolFn | None  # None for finish, which the agent loop handles in code
    gate: Gate

    @property
    def name(self) -> str:
        return self.spec.name


# ---------------------------------------------------------------------------
# Gates the tools don't already apply to themselves
# ---------------------------------------------------------------------------
def _gate_analyze(state: LetterState) -> str | None:
    if state.requirements:
        return (
            f"the job is already analysed ({len(state.requirements)} requirements in the state); "
            "analyze_job runs once per run"
        )
    return None


def _gate_match(state: LetterState) -> str | None:
    if not state.requirements:
        return "no requirements yet: call analyze_job first"
    if not any(r.status == "unknown" for r in state.requirements):
        return (
            "every requirement is already matched against the profile; match_profile runs again "
            "only after the user adds profile rows"
        )
    return None


def _gate_ask(state: LetterState) -> str | None:
    if not state.requirements:
        return "no requirements yet: call analyze_job, then match_profile"
    if any(r.status == "unknown" for r in state.requirements):
        return "requirements are not matched against the profile yet: call match_profile first"
    if state.waiting_on_user():
        return "questions are already out with the user"
    if not state.pending_gaps():
        return "no must-have gap is waiting for a user decision, so there is nothing to ask"
    return None


def _gate_finish(state: LetterState) -> str | None:
    """Only for symmetry: the agent loop decides finish itself (``can_finish``, or
    the draft limit reached with every check run)."""
    return None


def _check_gate(tool: str) -> Gate:
    return lambda state: guardrails.can_check(state, tool)


# ---------------------------------------------------------------------------
# Model-facing descriptions
# ---------------------------------------------------------------------------
_DESCRIPTIONS = {
    "analyze_job": (
        "Read the job ad and build the requirements checklist (each item rated for importance and "
        "for what the letter should do with it), plus tone, keywords and facts about the employer. "
        "Use it first, once per run. Do not call it again: the checklist is cached and the state "
        "already shows it."
    ),
    "match_profile": (
        "Match requirements against the candidate's profile and mark each supported, partial "
        "or gap, with evidence pointers. Use it right after analyze_job and before any drafting. "
        "Use it again only when requirements show as 'unknown' after the run resumed (the user "
        "added profile rows for them); then only those are judged. Do not use it to change a "
        "verdict you dislike. A skill the profile only lists (no experience entry describing its "
        "use) is at most partial evidence. Gaps the user said 'no' to on earlier ads are left "
        "out automatically (shown as 'user: leave_out (remembered)')."
    ),
    "ask_user": (
        "Ask the user about every must-have requirement marked 'needs a user decision' (an "
        "essential requirement the profile shows no evidence for), all in one batch. Use it after "
        "match_profile and before generate_letter, whenever such a gap exists: drafting is "
        "refused until each one has a decision. The run then pauses until the user answers and "
        "resumes later with their decisions in the state. Do not use it when no requirement "
        "needs a user decision, and never write around a gap instead of asking."
    ),
    "generate_letter": (
        "Write draft 1 of the cover letter from the supported evidence, with the style guide and "
        "the user's voice. Use it once, after match_profile, when no requirement needs a user "
        "decision. Do not use it for later drafts: after draft 1, only revise_letter makes new "
        "drafts. After it, run check_claims, check_requirements and style_lint on the new draft."
    ),
    "check_claims": (
        "Verify every factual claim in the latest draft against the profile (and claims about "
        "the employer against the ad). Code checks each cited source and blocks invented links "
        "or emails; a model judges whether the cited text supports the wording. A skill the "
        "profile only lists backs 'skills in / knowledge of X', never 'experience with X'. Use it "
        "once on every new draft. Do not run it twice on the same draft."
    ),
    "check_requirements": (
        "Confirm the latest draft addresses every requirement it must cover (headline items and "
        "essential mentions the profile backs with more than a skill listing). Use it once on "
        "every new draft. Do not run it twice on the same draft."
    ),
    "style_lint": (
        "Code-only style check of the latest draft: em dashes, banned phrases, length, paragraph "
        "count, placeholders, sign-off; warnings for American spellings, flat sentence rhythm and "
        "sentences that lead with a gap. Free. Use it once on every new draft. Do not run it twice "
        "on the same draft. Warnings never block finish."
    ),
    "revise_letter": (
        "Fix the latest draft's FAILED checks with a narrow edit, creating the next draft. Use it "
        "only after check_claims, check_requirements and style_lint have all run on the latest "
        "draft and at least one failed. Do not use it for the first draft, when every check "
        "passed, or for warnings alone. It will not add experience claims for skills the profile "
        "only lists. Then run all three checks on the new draft."
    ),
    "answer_screening": (
        "Side output: draft answers to the screening questions written in the job ad, grounded "
        "in the profile (code checks every cited source). Use it once, when the state lists it as "
        "due: after match_profile and any ask_user decisions, so the answers can use what the user "
        "added. It does not touch the letter. Do not call it when the ad has no screening "
        "questions, and never call it twice."
    ),
    "suggest_learning": (
        "Side output: one concrete way (course, certification, small project) to close each gap "
        "the USER confirmed (a 'no' to ask_user, now or remembered from an earlier ad), led by the "
        "one most ads ask for. Use it once, when the state lists it as due. Do not call it for gaps "
        "nobody asked the user about, and never call it twice."
    ),
    "suggest_resume_tweaks": (
        "Side output: résumé tailoring notes (what to lead with, the ad's keywords to mirror, what "
        "to cut, how to handle gaps) from the requirements, the evidence and the FINAL letter. Use "
        "it once, after every check passed on the latest draft (or the draft limit is reached), "
        "before finish. Do not call it while the letter can still change, and never call it twice."
    ),
    FINISH: (
        "End the run. Accepted when check_claims, check_requirements and style_lint have all "
        "passed on the latest draft; the letter goes to the user. Also accepted when the draft "
        "limit is reached and a check still fails after all three ran: the run then hands the user "
        "the best draft with its open issues flagged. Refused otherwise, with the reason."
    ),
}


_SIDE_FNS = {
    "answer_screening": lambda: answer_screening,
    "suggest_learning": lambda: suggest_learning,
    "suggest_resume_tweaks": lambda: suggest_resume_tweaks,
}


def side_output_fns() -> dict[str, ToolFn]:
    """The side-output tool functions, looked up when called (tests swap them)."""
    return {name: get() for name, get in _SIDE_FNS.items()}


def build_registry(gap_policy: GapPolicy, side_outputs_enabled: Iterable[str] = ()) -> dict[str, RegisteredTool]:
    """Every tool the agent may call, in the order a run usually uses them.

    The side-output tools are offered only when enabled for the run (the user's toggles):
    with none enabled the agent sees exactly the letter-only tool set of Phase 8.
    """
    wanted = set(side_outputs_enabled)
    sides: list[tuple[str, ToolFn | None, Gate]] = [
        (name, fn, side_outputs.gate(name))
        for name, fn in side_output_fns().items()
        if name in wanted and name in SIDE_OUTPUT_TOOLS
    ]
    entries: list[tuple[str, ToolFn | None, Gate]] = [
        ("analyze_job", analyze_job, _gate_analyze),
        ("match_profile", match_profile, _gate_match),
        ("ask_user", ask_user_tool(gap_policy), _gate_ask),
        ("generate_letter", generate_letter, guardrails.can_generate),
        ("check_claims", check_claims, _check_gate("check_claims")),
        ("check_requirements", check_requirements, _check_gate("check_requirements")),
        ("style_lint", style_lint, _check_gate("style_lint")),
        ("revise_letter", revise_letter, guardrails.can_revise),
        *sides,
        (FINISH, None, _gate_finish),
    ]
    return {
        name: RegisteredTool(spec=ToolSpec(name=name, description=_DESCRIPTIONS[name]), fn=fn, gate=gate)
        for name, fn, gate in entries
    }
