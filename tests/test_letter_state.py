"""Tests for app/llm/letter/state.py (LetterState + pointer resolver) and the
code-checked rubric items in app/llm/letter/rubric.py. No network, no DB: the
profile is built from unsaved model instances.
"""
from __future__ import annotations

import datetime

import pytest

from app.llm.letter import rubric
from app.llm.letter.state import (
    Check,
    Claim,
    LetterState,
    JobInfo,
    ProfileIndex,
    Requirement,
    UserDecision,
    is_valid_pointer,
    split_sentences,
)
from app.models import Experience, Profile, Qualification, Skill


@pytest.fixture()
def profile() -> Profile:
    p = Profile(id=1, name="Bob", email="b@x.com", password_hash="x", summary="Data-minded grad.")
    p.qualifications = [
        Qualification(id=4, qualification_type="degree", title="BIT", institution="UQ",
                      field_of_study="Data Science", status="completed"),
    ]
    p.skills = [Skill(id=7, name="SQL"), Skill(id=8, name="Python")]
    p.experiences = [
        Experience(
            id=12, experience_type="job", title="Reporting Assistant", organization="Acme",
            start_date=datetime.date(2022, 1, 1), end_date=None,
            description="Built SQL reports for 3 teams. Presented weekly reports to team leads.\n"
                        "- Automated a Python data-cleaning script",
        ),
        Experience(id=13, experience_type="project", title="Capstone", description=None),
    ]
    return p


# ---------------------------------------------------------------------------
# Pointers
# ---------------------------------------------------------------------------
def test_split_sentences_handles_prose_and_bullets():
    text = "Built SQL reports. Presented to leads!\n- Automated a script\n\n2. Cut run time by 40%"
    assert split_sentences(text) == [
        "Built SQL reports.", "Presented to leads!", "Automated a script", "Cut run time by 40%",
    ]
    assert split_sentences(None) == []


def test_split_sentences_does_not_split_on_decimals_or_lowercase():
    assert split_sentences("Improved accuracy to 99.5 percent. e.g. dashboards") == [
        "Improved accuracy to 99.5 percent. e.g. dashboards"
    ]


@pytest.mark.parametrize("ptr", [
    "experience:12", "experience:12#s2", "qualification:4", "skill:7", "profile:summary",
])
def test_valid_pointers(ptr):
    assert is_valid_pointer(ptr)


@pytest.mark.parametrize("ptr", [
    "", "experience", "experience:abc", "experience:12#2", "skill:7#s1x", "profile:name", "job:3",
    "experience:12 ", "EXPERIENCE:12",
])
def test_invalid_pointers(ptr):
    assert not is_valid_pointer(ptr)


def test_resolve_each_kind(profile):
    idx = ProfileIndex(profile)
    assert idx.resolve("experience:12").startswith("Reporting Assistant at Acme (2022–present): Built SQL")
    assert idx.resolve("experience:12#s1") == "Built SQL reports for 3 teams."
    assert idx.resolve("experience:12#s2") == "Presented weekly reports to team leads."
    assert idx.resolve("experience:12#s3") == "Automated a Python data-cleaning script"
    assert idx.resolve("experience:13") == "Capstone"  # no dates -> no span
    assert idx.resolve("qualification:4") == "BIT, UQ, Data Science (completed)"
    assert idx.resolve("skill:7") == "SQL"
    assert idx.resolve("profile:summary") == "Data-minded grad."


def test_unknown_or_foreign_ids_do_not_resolve(profile):
    idx = ProfileIndex(profile)
    assert idx.resolve("experience:99") is None  # another user's row, or deleted
    assert idx.resolve("experience:12#s4") is None  # past the last sentence
    assert idx.resolve("experience:13#s1") is None  # no description
    assert idx.resolve("not a pointer") is None


def test_catalog_can_drop_sentences(profile):
    idx = ProfileIndex(profile)
    assert "experience:12#s1" in idx.catalog()
    assert "experience:12#s1" not in idx.catalog(include_sentences=False)
    assert "experience:12" in idx.catalog(include_sentences=False)


def test_no_summary_means_no_summary_pointer(profile):
    profile.summary = None
    assert ProfileIndex(profile).resolve("profile:summary") is None


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
def _state() -> LetterState:
    return LetterState(
        profile_id=1,
        job=JobInfo(job_id=412, title="Data Analyst", company="Example Pty Ltd"),
        requirements=[
            Requirement(id="R1", text="Power BI", priority="must", status="gap"),
            Requirement(id="R2", text="Strong SQL", priority="must", status="supported",
                        evidence=["experience:12#s1", "skill:7"]),
            Requirement(id="R3", text="Tableau", priority="should", status="gap"),
        ],
    )


def test_pending_gaps_are_undecided_must_haves_only():
    s = _state()
    assert [r.id for r in s.pending_gaps()] == ["R1"]  # R3 is only a "should"
    s.requirement("R1").user_decision = UserDecision(choice="leave_out", remembered=True)
    assert s.pending_gaps() == []


def test_checks_belong_to_the_latest_draft():
    s = _state()
    with pytest.raises(ValueError):
        s.record_check("claims", Check(passed=True))
    s.add_draft("v1 text", [Claim(text="Built SQL reports", source="experience:12#s1")])
    s.record_check("claims", Check(passed=True))
    s.add_draft("v2 text")
    assert s.latest_draft.version == 2
    assert s.latest_draft.checks == {}  # a pass on v1 says nothing about v2
    assert s.drafts[0].checks["claims"].passed
    assert s.budget.drafts_used == 2


def test_summary_has_statuses_and_checks_but_never_draft_text():
    s = _state()
    s.add_draft("SECRET LETTER BODY that the orchestrator must not see")
    s.record_check("requirements", Check(passed=False, issues=["R2 not addressed"]))
    s.record_check("style", Check(passed=True, warnings=["low_sentence_variance"]))
    summary = s.summary_for_orchestrator()
    assert "SECRET LETTER BODY" not in summary
    assert "R1 [must] gap (0 evidence)  <- needs a user decision" in summary
    assert "R2 [must] supported (2 evidence)" in summary
    assert "claims: not run on this draft" in summary
    assert "requirements: FAIL — R2 not addressed" in summary
    assert "style: pass (warnings: low_sentence_variance)" in summary
    assert "LATEST DRAFT: v1 of 3 allowed" in summary


def test_summary_before_analysis():
    s = LetterState(profile_id=1, job=JobInfo(job_id=1, title="X"))
    out = s.summary_for_orchestrator()
    assert "REQUIREMENTS: not analysed yet" in out
    assert "DRAFTS: none" in out


def test_budget_limits():
    s = _state()
    assert s.budget_exceeded() is None
    s.budget.tool_calls = 15
    assert "tool-call limit" in s.budget_exceeded()
    s.budget.tool_calls = 3
    s.budget.cost_usd = 0.51
    assert "run budget" in s.budget_exceeded()


def test_waiting_on_user():
    s = _state()
    assert not s.waiting_on_user()
    s.user_questions.append({"requirement_id": "R1", "status": "open"})
    assert s.waiting_on_user()
    assert "WAITING" in s.summary_for_orchestrator()
    s.user_questions[0]["status"] = "answered"
    assert not s.waiting_on_user()


def test_state_round_trips_through_json():
    s = _state()
    s.requirement("R1").user_decision = UserDecision(
        choice="have_it", answer="Tableau at uni", saved_as=["experience:31", "skill:44"])
    s.add_draft("text", [Claim(text="c", source=None)])
    s.record_check("style", Check(passed=False, issues=["em_dash"]))
    again = LetterState.model_validate_json(s.model_dump_json())
    assert again == s


# ---------------------------------------------------------------------------
# Rubric (code-checked items)
# ---------------------------------------------------------------------------
_GOOD = (
    "Dear Hiring Manager,\n\n" + "I built SQL reports for three teams at Acme. " * 25
    + "\n\nSincerely,\nBob"
)


def test_clean_letter_passes_every_auto_item():
    checks = rubric.auto_checks(_GOOD)
    assert all(checks[i] for i in rubric.AUTO_ITEMS), checks
    assert not checks["under_min_words"]


def test_each_auto_item_can_fail():
    bad = (
        "Dear Hiring Manager — I am thrilled to apply to [Company]. "
        "I have a proven track record in a fast-paced environment. " + "word " * 400
    )
    checks = rubric.auto_checks(bad)
    assert checks["em_dash_limit"] and checks["em_dash_count"] == 1  # one is allowed
    assert not rubric.auto_checks("a — b — c")["em_dash_limit"]
    assert set(checks["banned_found"]) >= {"i am thrilled to apply", "proven track record", "fast-paced environment"}
    assert not checks["generic_phrase_limit"]
    assert not checks["within_word_limit"]
    assert checks["placeholders_found"] == ["[Company]"]
    assert not checks["has_sign_off"]


def test_one_generic_phrase_is_allowed_but_repeats_count():
    assert rubric.auto_checks("I thrive in a fast-paced environment.")["generic_phrase_limit"]
    twice = "A fast-paced environment suits me. Yes, a fast-paced environment."
    assert rubric.find_banned(twice) == ["fast-paced environment"] * 2
    assert not rubric.auto_checks(twice)["generic_phrase_limit"]
    assert not rubric.auto_checks("proven track record in a fast-paced environment")["generic_phrase_limit"]


def test_banned_phrase_match_is_whole_word_and_handles_curly_quotes():
    assert rubric.find_banned("We delved into it") == []  # 'delve' must be a whole word
    assert rubric.find_banned("Let me DELVE in") == ["delve"]
    assert rubric.find_banned("in today’s ever-evolving market") == ["in today's ever-evolving"]


def test_en_dash_in_date_ranges_is_not_an_em_dash():
    assert rubric.auto_checks("Worked 2022–2024.\n\nSincerely,\nBob")["em_dash_limit"]


def test_banned_list_loads_and_ignores_comments():
    phrases = rubric.banned_phrases()
    assert "proven track record" in phrases
    assert not any(p.startswith("#") for p in phrases)
