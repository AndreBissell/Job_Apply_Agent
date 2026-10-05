"""Deciding must-have gaps before drafting: remembered "no"s and ``ask_user`` (plan §5.5).

A must-have requirement the profile shows no evidence for blocks drafting until it
has a user decision ("no silent gaps", §5.7). Both engines decide it through one
``GapPolicy`` hook (outcome.py): the workflow calls it after match_profile, the agent
calls it as the ``ask_user`` tool. Two policies:

    leave_out_gaps  (outcome.py) every pending gap is left out. The evals use it, so
                    workflow and agent runs stay comparable.
    ask_user_gaps   the production policy: each pending gap becomes a question, all
                    in one batch, and the run pauses as ``waiting_user`` (§5.5).

Before either runs, ``apply_remembered`` (called at the end of match_profile) leaves
out every gap the user already said "No" to on an earlier ad, without asking, and
counts this ad for the to-work-on list (§5.9). So each gap is asked once.
"""

from __future__ import annotations

from typing import Any

from app import gaps
from app.llm.letter.outcome import GapPolicy
from app.llm.letter.runner import ToolContext, ToolFn
from app.llm.letter.state import LetterState, UserDecision, UserQuestion

ANSWER_HINT = "Tell us what you did, where and roughly how long."


def apply_remembered(state: LetterState, ctx: ToolContext) -> list[str]:
    """Leave out every pending gap matching an active remembered "no", and record a
    sighting for every requirement that asks for one. Returns the ids left out.

    Sightings are counted for any requirement naming a remembered skill, not only for
    pending gaps: the to-work-on list counts ads asking for the skill. Eligibility
    items are never counted (they are not skills the user can learn).
    """
    decisions = gaps.active_decisions(ctx.db, state.profile_id)
    if not decisions:
        return []
    left_out = []
    for r in state.requirements:
        if r.letter_role == "not_for_letter":
            continue
        decision = gaps.find_decision(decisions, r.skill, r.text)
        if decision is None:
            continue
        gaps.record_sighting(
            ctx.db, decision, job_id=state.job.job_id, job_title=state.job.title,
            source="letter_run", importance=r.importance,
        )
        if r.needs_user:
            r.user_decision = UserDecision(
                choice="leave_out", answer=f"remembered: you said you don't have {decision.label}",
                remembered=True,
            )
            left_out.append(r.id)
    ctx.db.commit()
    return left_out


def question_prompt(requirement_text: str) -> str:
    return f'This role asks for "{requirement_text}". Do you have experience that covers this?'


def ask_user_gaps(state: LetterState, ctx: ToolContext) -> None:
    """The production gap policy: ask about every pending must-have gap, in one batch.

    Remembered "no"s are applied first (in case match_profile's pass was skipped).
    A requirement that already has an unanswered question is not asked twice. When
    questions are out, ``state.waiting_on_user()`` is True and the engine ends the run
    as ``waiting_user``; it resumes after the answers (answers.py, engines.py).
    """
    apply_remembered(state, ctx)
    asked = {q.requirement_id for q in state.user_questions if q.status != "answered"}
    n = len(state.user_questions)
    for r in state.pending_gaps():
        if r.id in asked:
            continue
        n += 1
        state.user_questions.append(UserQuestion(
            id=f"Q{n}", requirement_id=r.id, requirement_text=r.text, skill=r.skill,
            skill_key=gaps.skill_key(r.skill, r.text), prompt=question_prompt(r.text),
        ))


def ask_user_tool(gap_policy: GapPolicy) -> ToolFn:
    """The run's gap policy as a tool: the agent's ``ask_user``, and the workflow's
    step when a gap is pending, so both engines log and count it the same way."""

    def ask_user(state: LetterState, ctx: ToolContext) -> dict[str, Any]:
        pending = [r.id for r in state.pending_gaps()]
        gap_policy(state, ctx)
        decided = {
            r.id: r.user_decision.choice + (" (remembered)" if r.user_decision.remembered else "")
            for r in state.requirements
            if r.id in pending and r.user_decision is not None
        }
        questions = [q.id for q in state.user_questions if q.status == "open" and q.requirement_id in pending]
        return {
            "asked_about": pending,
            "decided": decided,
            "questions": questions,
            "waiting_on_user": state.waiting_on_user(),
        }

    return ask_user
