"""Tests for Phase 4: style_lint (code-only style check), the style-guide and voice
prompt text (app/llm/letter/style.py), and the styled one-shot system prompt."""
from __future__ import annotations

import pytest

from app.llm.cover_letter import _SYSTEM_PROMPT, _styled_system_prompt
from app.llm.letter import rubric
from app.llm.letter.runner import ToolError
from app.llm.letter.state import JobInfo, LetterState
from app.llm.letter.style import VOICE_MAX_WORDS, style_guide_prompt, voice_prompt, voice_reference
from app.llm.letter.tools import style_lint as sl
from app.models import Experience, Profile

# A letter that should pass everything: ~260 words, AU spelling, varied sentences,
# three body paragraphs, greeting and sign-off.
GOOD = """Dear Hiring Manager,

I recently finished a Bachelor of Engineering (Honours) in Software Engineering at The University of Queensland, and the Graduate Developer role at Acme stood out because your team builds the scheduling software that hospitals use every day. That is work I want to be close to.

My honours thesis was an itinerary planner. It used a fine-tuned model to pull preferences out of plain-English requests, then fed them into a constraint solver that allocated a budget across flights, meals and attractions. I spent most of my time analysing why some itineraries failed. Some cost categories scaled cleanly. Others did not, and the pipeline had to treat them differently. The final system beat a GPT-5 mini baseline on four of five metrics, including the pass/fail check on whether an itinerary was valid at all. During my internship at Rio Tinto I worked on a tailings dam monitoring system, where every change had to meet the company's own safety and compliance rules before it shipped.

Your ad mentions a move from a legacy desktop client to a web platform. Migration work like that rewards patience with old code and care with data, and both are habits I picked up on that internship. I also have four years at Coles, which taught me to stay calm when the queue is long and the customer is unhappy. I would like to bring the same steadiness to a team whose software sits underneath clinical work, where getting the details right matters more than getting them done fast.

The legacy-to-web migration is the part of this role I would most like to work on. It is where the care with old code and data from the internship would count from the first week.

Sincerely,
Andre Bissell"""


def test_good_letter_passes_clean():
    r = sl.lint(GOOD)
    assert r["passed"], r["issues"]
    assert r["warnings"] == []
    assert 230 <= r["words"] <= 340
    assert r["body_paragraphs"] == 4  # greeting and sign-off excluded


# -- blocking ----------------------------------------------------------------
def test_one_em_dash_allowed_two_block():
    assert sl.lint(GOOD.replace("That is work", "That is — work"))["passed"]
    two = GOOD.replace("That is work", "That — is — work")
    r = sl.lint(two)
    assert not r["passed"] and r["issues"][0].startswith("em_dash")


def test_spaced_en_dash_counts_as_em_dash_but_ranges_do_not():
    assert sl.count_dashes("a – b") == 1
    assert sl.count_dashes("Worked 2022–2024 at Coles") == 0
    assert not sl.lint(GOOD.replace("That is work", "That – is – work"))["passed"]


def test_one_banned_phrase_allowed_two_block():
    one = GOOD.replace("That is work", "I am a team player and that is work")
    assert sl.lint(one)["passed"]
    two = one.replace("My honours thesis", "I have a proven track record. My honours thesis")
    r = sl.lint(two)
    assert not r["passed"]
    assert any(i.startswith("banned_phrase") and "team player" in i for i in r["issues"])


def test_over_a_page_blocks():
    long = GOOD.replace("The legacy-to-web", "More words here. " * 30 + "The legacy-to-web")
    r = sl.lint(long)
    assert any(i.startswith("too_long") for i in r["issues"])


def test_too_many_body_paragraphs_blocks():
    many = GOOD.replace("The legacy-to-web", "Extra.\n\nExtra.\n\nThe legacy-to-web")
    assert any(i.startswith("too_many_paragraphs") for i in sl.lint(many)["issues"])


def test_placeholder_and_missing_sign_off_block():
    r = sl.lint(GOOD.replace("at Acme", "at [Company]").replace("Sincerely,", "Cheers,"))
    assert any(i.startswith("placeholder") for i in r["issues"])
    assert any(i.startswith("no_sign_off") for i in r["issues"])


# -- warnings -----------------------------------------------------------------
def test_short_letter_warns_but_passes():
    short = "Dear Hiring Manager,\n\nI built a planner. It worked.\n\nSincerely,\nA"
    r = sl.lint(short)
    assert r["passed"]
    assert any(w.startswith("short") for w in r["warnings"])


def test_flat_rhythm_warns():
    same = " ".join(["I built one small tool for the team last year."] * 8)
    r = sl.lint(f"Dear Hiring Manager,\n\n{same}\n\nSincerely,\nA")
    assert r["sentence_stdev"] == 0.0
    assert any(w.startswith("flat_rhythm") for w in r["warnings"])


def test_too_few_sentences_skips_rhythm():
    assert sl.sentence_stdev("Dear Hiring Manager,\n\nOne. Two.\n\nSincerely,\nA") is None


def test_i_openers_warn_at_three():
    paras = "\n\n".join(["I did a thing that mattered."] * 3)
    r = sl.lint(f"Dear Hiring Manager,\n\n{paras}\n\nSincerely,\nA")
    assert r["paragraphs_starting_with_i"] == 3
    assert any(w.startswith("i_openers") for w in r["warnings"])
    # "It", "In" are not "I"
    assert sl.lint("Dear Hiring Manager,\n\nIt was.\n\nIn short.\n\nIndeed.\n\nSincerely,\nA")[
        "paragraphs_starting_with_i"] == 0


def test_us_spelling_warns_with_the_fix():
    r = sl.lint(GOOD.replace("analysing", "analyzing").replace("Coles", "Coles organization"))
    assert set(r["us_spellings_found"]) == {"analyzing", "organization"}
    warn = next(w for w in r["warnings"] if w.startswith("us_spelling"))
    assert "analyzing -> analysing" in warn and "organization -> organisation" in warn
    assert r["passed"]  # a warning, not a block


def test_ambiguous_au_words_are_not_flagged():
    assert sl.find_us_spellings("a graduate program, a driver licence, a license to practise") == []


# -- the tool -----------------------------------------------------------------
def _state() -> LetterState:
    return LetterState(profile_id=1, job=JobInfo(job_id=1, title="Dev"))


def test_tool_records_check_on_latest_draft():
    state = _state()
    state.add_draft("Dear Hiring Manager,\n\nA — b — c.\n\nSincerely,\nA")
    summary = sl.style_lint(state, ctx=None)
    check = state.latest_draft.checks["style"]
    assert not check.passed and summary["draft"] == 1
    assert check.issues == summary["issues"]
    state.add_draft(GOOD)  # a new draft starts with no checks
    assert "style" not in state.latest_draft.checks
    sl.style_lint(state, ctx=None)
    assert state.latest_draft.checks["style"].passed


def test_tool_without_draft_explains_itself():
    with pytest.raises(ToolError, match="generate_letter"):
        sl.style_lint(_state(), ctx=None)


# -- shared with the rubric ---------------------------------------------------
def test_rubric_uses_the_same_helpers_and_limits():
    assert rubric.find_banned is sl.find_banned
    assert rubric.word_count is sl.word_count
    assert (rubric.MAX_EM_DASHES, rubric.MAX_WORDS) == (sl.MAX_EM_DASHES, sl.MAX_WORDS)
    # ...but the rubric still counts only real em dashes, so old runs stay comparable
    assert rubric.auto_checks("a – b – c")["em_dash_limit"]


# -- style guide + voice --------------------------------------------------------
def test_style_guide_prompt_has_rules_and_banned_list():
    text = style_guide_prompt()
    assert "Australian spelling" in text and "No em dashes" in text
    assert '"proven track record"' in text
    assert "detection" not in text.split("## Rules")[0]  # developer intro stripped


def _profile(sample=None, summary=None, descriptions=()) -> Profile:
    p = Profile(name="A", email="a@x", password_hash="x", writing_sample=sample, summary=summary)
    p.experiences = [Experience(title=f"Job {i}", description=d) for i, d in enumerate(descriptions)]
    return p


def test_voice_prefers_the_writing_sample():
    text, source = voice_reference(_profile(sample="My own words.", summary="CV summary"))
    assert (text, source) == ("My own words.", "writing_sample")


def test_voice_falls_back_to_profile_text():
    text, source = voice_reference(_profile(summary="Summary.", descriptions=["Built X.", None]))
    assert source == "profile_text"
    assert text == "Summary.\n\nBuilt X."
    assert "CV text" in voice_prompt(_profile(summary="Summary."))


def test_no_voice_material_means_no_block():
    assert voice_reference(_profile()) == ("", "none")
    assert voice_prompt(_profile()) == ""


def test_voice_is_trimmed_at_a_paragraph_boundary():
    para = " ".join(["word"] * 400)
    text, _ = voice_reference(_profile(sample="\n\n".join([para] * 5)))
    assert len(text.split()) == 1200 <= VOICE_MAX_WORDS
    huge, _ = voice_reference(_profile(sample=" ".join(["w"] * 5000)))
    assert len(huge.split()) == VOICE_MAX_WORDS


def test_voice_prompt_says_tone_only_not_facts():
    block = voice_prompt(_profile(sample="My words."))
    assert "My words." in block and "take no facts from it" in block


def test_styled_prompt_adds_style_and_voice_and_keeps_grounding():
    prompt = _styled_system_prompt(_profile(sample="My words."))
    assert "Use ONLY facts" in prompt
    assert "STYLE GUIDE" in prompt and "VOICE REFERENCE" in prompt
    assert prompt != _SYSTEM_PROMPT


def test_gap_led_sentences_warn_but_do_not_block():
    text = ("Dear Hiring Manager,\n\nWhile I have not used Kafka, I built event pipelines at Acme. "
            "Although my role did not involve support, I fixed production bugs. "
            "I have never written Go. I built REST APIs while I was at university.\n\nSincerely,\nBob")
    res = sl.lint(text)
    assert res["gap_led_sentences"] == [
        "While I have not used Kafka, I built event pipelines at Acme.",
        "Although my role did not involve support, I fixed production bugs.",
        "I have never written Go.",
    ]
    assert sum(w.startswith("gap_led") for w in res["warnings"]) == 3
    assert not any(i.startswith("gap_led") for i in res["issues"])


def test_related_experience_framing_is_not_gap_led():
    text = ("Dear Hiring Manager,\n\nI used Tableau at university, which carries over to Power BI. "
            "While at Acme I built REST APIs.\n\nSincerely,\nBob")
    assert sl.gap_led_sentences(text) == []


# -- stock closes and bridges (workflow-v3) -----------------------------------
_SPECIFIC_CLOSE = (
    "The legacy-to-web migration is the part of this role I would most like to work on. It is where "
    "the care with old code and data from the internship would count from the first week."
)


def _with_last_paragraph(text: str) -> str:
    assert _SPECIFIC_CLOSE in GOOD
    return GOOD.replace(_SPECIFIC_CLOSE, text)


def _warnings(res: dict, prefix: str) -> list[str]:
    return [w for w in res["warnings"] if w.startswith(prefix)]


def test_stock_close_in_last_paragraph_warns_but_passes():
    res = sl.lint(_with_last_paragraph("Thank you for considering my application."))
    assert res["passed"], res["issues"]
    assert len(_warnings(res, "stock_close:")) == 1
    assert res["stock_close_sentences"] == ["Thank you for considering my application."]


@pytest.mark.parametrize("sentence", [
    "I look forward to discussing the role with you.",
    "I am keen to show how I can contribute to your team.",
    "I would value the chance to contribute to the migration.",
    "I hope to hear from you.",
])
def test_stock_close_variants_are_flagged_in_the_last_paragraph(sentence):
    res = sl.lint(_with_last_paragraph(f"The migration appeals to me. {sentence}"))
    assert res["stock_close_sentences"] == [sentence]
    assert len(_warnings(res, "stock_close:")) == 1
    assert res["passed"]


def test_stock_close_in_an_earlier_paragraph_is_not_flagged():
    early = GOOD.replace("That is work I want to be close to.",
                         "That is work I want to be close to. Thank you for considering my application.")
    res = sl.lint(early)
    assert res["stock_close_sentences"] == []
    assert _warnings(res, "stock_close:") == []


@pytest.mark.parametrize("phrase", [
    "translates well", "translates directly", "translate seamlessly",
    "carries over directly", "maps directly", "map directly",
])
def test_stock_bridge_phrases_warn_once(phrase):
    res = sl.lint(GOOD.replace("That is work I want", f"My thesis work {phrase}, and that is work I want"))
    assert res["stock_bridges"] == [phrase]
    warns = _warnings(res, "stock_bridge:")
    assert len(warns) == 1 and phrase in warns[0]
    assert res["passed"]


def test_two_stock_bridges_are_two_entries_but_one_warning():
    text = GOOD.replace("That is work I want", "That translates well and maps directly to work I want")
    res = sl.lint(text)
    assert res["stock_bridges"] == ["translates well", "maps directly"]
    assert len(_warnings(res, "stock_bridge:")) == 1


def test_honest_partial_framing_is_not_a_stock_bridge():
    res = sl.lint(GOOD.replace("That is work I want", "I used Tableau, which carries over to Power BI. Work I want"))
    assert res["stock_bridges"] == []
    assert _warnings(res, "stock_bridge:") == []


def test_greeting_and_sign_off_are_not_checked_for_stock_phrases():
    text = GOOD.replace("Dear Hiring Manager,", "Dear Hiring Manager, thank you for considering my application.")
    text = text.replace("Sincerely,\nAndre Bissell",
                        "Sincerely,\nThank you for considering my application. It translates well.\nAndre Bissell")
    res = sl.lint(text)
    assert res["stock_close_sentences"] == [] and res["stock_bridges"] == []
    assert res["warnings"] == []


def test_rubric_does_not_read_the_stock_warnings():
    stock = _with_last_paragraph(
        "My work translates directly to yours. Thank you for considering my application.")
    plain = _with_last_paragraph("My work applies to yours. A specific close about the migration here.")
    assert sl.lint(stock)["stock_close_sentences"] and sl.lint(stock)["stock_bridges"]
    a, b = rubric.auto_checks(stock), rubric.auto_checks(plain)
    assert {k: a[k] for k in rubric.AUTO_ITEMS} == {k: b[k] for k in rubric.AUTO_ITEMS}
    assert all(a[k] for k in rubric.AUTO_ITEMS), a
    assert not any("stock" in k for k in a)
