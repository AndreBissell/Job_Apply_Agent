"""scripts/letter_metrics.py: the v2 code checks (docs/letter-metrics-plan.md §3: G1, C3-C7).
Fake letters for a fake candidate (Liam Chen) and fake employers; no DB, no LLM."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("letter_metrics_under_test", ROOT / "scripts" / "letter_metrics.py")
lm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lm)

VOCAB = lm.skill_vocab(["Python", "SQL", "Docker", "AWS", "React", "PowerBI", "Machine Learning", "C++"])


def letter(*paras):
    return "\n\n".join(("Dear Acme team,", *paras, "Kind regards,\nLiam Chen"))


def test_body_drops_the_greeting_and_sign_off():
    assert lm.body(letter("At LogiCo I built tools.", "I would like to help.")) == \
        "At LogiCo I built tools.\n\nI would like to help."
    assert lm.body("Dear Acme,\n\nI built tools.\nKind regards,\nLiam Chen") == "I built tools."


# G1 ------------------------------------------------------------------------
@pytest.mark.parametrize("text, longest", [
    ("I use Python, SQL, Docker, AWS and React daily.", 5),
    ("I have used python, sql, docker, aws, and react.", 5),  # no capitals, Oxford comma
    ("I use Python, SQL, Docker and AWS.", 4),
    ("I built Python tools, then wrote SQL reports. Docker and AWS ran them.", 2),
    ("I like Python, data and teams.", 0),  # "I like Python" only counts at the edge of a list
])
def test_g1_finds_the_longest_comma_list_of_skills(text, longest):
    assert lm.no_stuffing(text, VOCAB)["longest_list"] == longest


def test_g1_fails_a_five_skill_list_and_passes_four():
    assert not lm.no_stuffing("Skills: Python, SQL, Docker, AWS, React.", VOCAB)["passed"]
    assert lm.no_stuffing("Skills: Python, SQL, Docker, AWS.", VOCAB)["passed"]


def test_g1_fails_a_skill_named_three_times_counting_spellings_as_one():
    twice = "I wrote Python at LogiCo. Python ran our reports."
    assert lm.no_stuffing(twice, VOCAB)["passed"]
    thrice = "I built PowerBI dashboards, trained staff in Power BI and fixed powerbi models."
    result = lm.no_stuffing(thrice, VOCAB)
    assert not result["passed"] and result["over_mentioned"] == {"power bi": 3}


def test_g1_matches_multi_word_and_symbol_skills_whole():
    mentions = lm.skill_mentions("Machine learning in C++ and machine-learning reviews.", VOCAB)
    assert mentions == {"machine learning": 2, "c++": 1}
    assert lm.skill_mentions("I learned a lot.", VOCAB) == {}  # "learning" alone is not the skill


# C3 ------------------------------------------------------------------------
def test_c3_counts_body_words_in_runs_copied_from_a_source():
    ad = "Acme is looking for someone who loves building reliable data pipelines for clients."
    body = "I love building reliable data pipelines for clients and I enjoy it."
    result = lm.ad_echo(body, ad)
    assert result["runs"] == ["building reliable data pipelines for clients"]
    assert result["share"] == pytest.approx(6 / 12)
    assert lm.copied(body, [ad], 7)["share"] == 0  # a 6-word run isn't an 8-word or 7-word one


def test_c3_profile_copy_never_joins_two_profile_texts():
    profile = ["Built Python tools for the LogiCo warehouse team", "every week for two years"]
    body = "Built Python tools for the LogiCo warehouse team every week for two years."
    assert lm.profile_copy(body, profile)["runs"] == ["built python tools for the logico warehouse team"]
    assert lm.profile_copy(body, [" ".join(profile)])["share"] == 1.0


# C4 ------------------------------------------------------------------------
def test_c4_finds_a_phrase_in_three_letters_but_not_two():
    bodies = {"a": "This role is exactly the kind of work I enjoy.",
              "b": "Acme offers exactly the kind of challenge I want.",
              "c": "It is exactly the kind of team I like.",
              "d": "Nothing alike here at all, honestly speaking."}
    assert lm.repeated_phrases(bodies, VOCAB, []) == [
        {"phrase": "exactly the kind of", "letters": ["a", "b", "c"]}]
    del bodies["c"]
    assert lm.repeated_phrases(bodies, VOCAB, []) == []


def test_c4_skips_phrases_touching_a_skill_or_a_name():
    bodies = {k: "Liam Chen here, keen on Python work." for k in "abc"}
    assert lm.repeated_phrases(bodies, VOCAB, ["Liam Chen", "Acme"]) == []  # only "here keen on" is left
    assert lm.repeated_phrases(bodies, VOCAB, []) == [{"phrase": "liam chen here keen on", "letters": ["a", "b", "c"]}]
    assert lm.repeated_phrases(bodies, frozenset(), [])[0]["phrase"] == "liam chen here keen on python work"


def test_c4_lists_one_shared_sentence_once():
    shared = "I am writing to apply because I enjoy solving hard problems with clear code."
    bodies = {k: f"{shared} Then something different number {k}." for k in ("one", "two", "three")}
    assert lm.repeated_phrases(bodies, VOCAB, []) == [
        {"phrase": "i am writing to apply because i enjoy solving hard problems with clear code then something "
                   "different number", "letters": ["one", "three", "two"]}]


def test_c4_keeps_a_short_phrase_found_in_more_letters_than_its_longer_one():
    bodies = {k: "This is exactly the kind of work." for k in "abc"}
    bodies |= {"d": "Acme seems exactly the kind of place.", "e": "LogiCo offers exactly the kind of growth."}
    assert lm.repeated_phrases(bodies, VOCAB, []) == [
        {"phrase": "exactly the kind of", "letters": ["a", "b", "c", "d", "e"]},
        {"phrase": "this is exactly the kind of work", "letters": ["a", "b", "c"]}]


# C5, C6 --------------------------------------------------------------------
def test_c5_counts_style_lint_warnings_by_kind():
    text = letter("I organized the LogiCo roster and analyzed the color of every report.",
                  "I look forward to hearing from you.")
    kinds = lm.style_warnings(text)["kinds"]
    assert kinds["us_spelling"] == 1 and kinds["stock_close"] == 1 and kinds["short"] == 1


def test_c6_averages_cost_and_time_over_letters_that_worked():
    results = [{"key": "a", "cost_usd": 0.2, "seconds": 100}, {"key": "b", "cost_usd": 0.4, "seconds": 200},
               {"key": "c", "error": "boom", "cost_usd": 0.05}]
    assert lm.cost_time(results) == {"letters": 2, "total_cost": pytest.approx(0.6),
                                     "cost_per_letter": pytest.approx(0.3), "seconds_per_letter": 150}
    assert lm.cost_time([])["cost_per_letter"] is None


# C7 ------------------------------------------------------------------------
def test_c7_edit_distance_counts_whole_words():
    assert lm.edit_distance(["a", "b", "c"], ["a", "x", "c"]) == 1
    assert lm.edit_distance(["a", "b", "c", "d"], ["b", "c"]) == 2
    assert lm.edit_distance([], ["a", "b"]) == 2


def test_c7_flags_a_near_template_and_names_its_nearest_letter():
    base = " ".join(f"word{i}" for i in range(40))
    bodies = {"acme": base, "acme-copy": base.replace("word7", "Acme"),
              "logico": " ".join(f"other{i}" for i in range(40))}
    near = lm.nearest_letters(bodies)
    assert near["acme"] == {"nearest": "acme-copy", "distance": 0.025, "near_template": True}
    assert near["logico"]["near_template"] is False and near["logico"]["distance"] == 1.0


def test_c7_a_run_of_one_letter_has_no_nearest():
    assert lm.nearest_letters({"acme": "I built tools."}) == {
        "acme": {"nearest": None, "distance": None, "near_template": False}}


# C3 writing-sample copy --------------------------------------------------------
def test_sample_copy_counts_six_word_runs_and_is_none_without_a_sample():
    sample = "The part I enjoyed most was the testing."
    body = "At LogiCo the part I enjoyed most was the rota."
    assert lm.sample_copy(body, sample)["runs"] == ["the part i enjoyed most was the"]
    assert lm.sample_copy(body, None) is None


# M1-M4, honest coverage, checklist ---------------------------------------------
LETTER = letter("At LogiCo I built Python pipelines for the warehouse team.", "I wrote SQL reports every week.")
REF = {"requirements": [
    {"id": "R1", "importance": "essential", "letter_role": "headline", "status": "supported",
     "evidence": ["experience:1#s1"]},
    {"id": "R2", "importance": "important", "letter_role": "mention", "status": "supported", "evidence": ["skill:3"]},
    {"id": "R3", "importance": "nice_to_have", "letter_role": "mention", "status": "partial",
     "evidence": ["qualification:2"]},
    {"id": "R4", "importance": "essential", "letter_role": "not_for_letter", "status": "supported", "evidence": []},
    {"id": "R5", "importance": "important", "letter_role": "headline", "status": "gap", "evidence": []},
]}
FLAGS = {"R1": (True, False), "R2": (False, True), "R3": (False, False), "R5": (False, False)}  # (must, listing)
WEIGHTS = {"essential": 4, "important": 2, "nice_to_have": 1}
CHECKLIST = {d: "Y" for d in ("D2a", "D2b", "D2c", "D3a", "D3b", "D5a", "D5b", "D5c", "D6a", "D6b", "D6c", "D6d",
                              "D6e", "D6f", "D6g", "D6h", "D7a")} | {"D4a": "N"}


def judge(fabricated=0, **rows):
    reqs = {"R1": {"addressed": "Y", "quote": "built Python pipelines", "presented_as": "experience",
                   "evidence_used": ["skill:3"]},
            "R2": {"addressed": "Y", "quote": "I wrote SQL reports", "presented_as": "experience"},
            "R3": {"addressed": "Y", "quote": "a degree in data science", "presented_as": "related"},
            "R5": {"addressed": "N", "quote": "", "presented_as": "none"}} | rows
    return {"send_readiness": 3, "checklist": CHECKLIST,
            "requirements": [{"id": k, **v} for k, v in reqs.items()],
            "counts": {"supported": 3, "stretch": 1, "fabricated": fabricated, "employer_fact_not_in_ad": 1,
                       "gap_fabrications": 0}}


def test_addressed_needs_the_quote_in_the_letter_and_a_listing_shown_as_a_skill():
    # R2 is backed only by a listed skill and presented as experience; R3's quote isn't in the letter
    assert lm.addressed_rows(LETTER, judge(), FLAGS) == {"R1"}
    as_skill = judge(R2={"addressed": "Y", "quote": "I wrote SQL reports", "presented_as": "skill"})
    assert lm.addressed_rows(LETTER, as_skill, FLAGS) == {"R1", "R2"}


def test_judged_scores_m1_m2_honest_coverage_and_m3():
    s = lm.judged(LETTER, REF, judge(), FLAGS, {"1": "work"}, WEIGHTS)
    assert s["m1"] == 1.0  # R1, the only must-cover row
    assert s["m2"] == pytest.approx(4 / 7)  # R1 of R1 + R2 + R3 (4 + 2 + 1); R4 and the gap R5 aren't counted
    assert s["honest"] == s["m2"]
    assert s["m3"] == 0.0  # R1 cites a listed skill though the reference holds work experience
    assert (s["claims"], s["supported"], s["stretch"], s["judge_grade"]) == (5, 3, 1, 3)
    best = judge(R1={"addressed": "Y", "quote": "built Python pipelines", "presented_as": "experience",
                     "evidence_used": ["experience:1#s1"]})
    assert lm.judged(LETTER, REF, best, FLAGS, {"1": "work"}, WEIGHTS)["m3"] == 1.0


def test_one_fabrication_zeroes_honest_coverage():
    s = lm.judged(LETTER, REF, judge(fabricated=1), FLAGS, {"1": "work"}, WEIGHTS)
    assert s["m2"] == pytest.approx(4 / 7) and s["honest"] == 0.0 and s["fabricated"] == 1


def test_checklist_weights_custom_2_to_1_and_code_counts_the_length():
    s = lm.checklist_scores(CHECKLIST, LETTER)  # the judge said D6f = Y, but the letter is ~20 words
    assert s["dims"]["D4"] == 0 and s["dims"]["D6"] == pytest.approx(7 / 8)
    assert s["custom"] == pytest.approx(0.75) and s["generic"] == pytest.approx((1 + 7 / 8) / 2)
    assert s["checklist"] == pytest.approx((2 * 0.75 + (1 + 7 / 8) / 2) / 3)
    long_letter = letter(" ".join(["word"] * 280))
    assert lm.checklist_scores(CHECKLIST, long_letter)["dims"]["D6"] == 1


def test_scorecard_uses_the_owner_grade_where_there_is_one_and_counts_judged_letters_only():
    base = {"cost_usd": 0.2, "seconds": 100, "gates": True, "g1": True, "near_template": False, "warnings": 1,
            "ad_echo": 0.1, "profile_copy": 0.2, "sample_copy": None}
    judged_row = lm.judged(LETTER, REF, judge(), FLAGS, {"1": "work"}, WEIGHTS)
    rows = [base | judged_row | {"grade": 4, "grade_by": "owner"},
            base | lm.judged(LETTER, REF, judge(fabricated=2), FLAGS, {"1": "work"}, WEIGHTS)
            | {"grade": 2, "grade_by": "judge"},
            base | {"fabricated": None, "grade": None, "grade_by": None, "g1": False, "cost_usd": 0.5}]
    s = lm.scorecard(rows)
    assert (s["letters"], s["judged"], s["by_owner"], s["by_judge"]) == (3, 2, 1, 1)
    assert s["send_readiness"] == 3 and s["send_ready"] == 0.5
    assert s["fabrication_free"] == 0.5 and s["honest"] == pytest.approx(2 / 7)
    assert s["precision"] == 6 / 12 and s["g1"] == 2 and s["sample_copy"] is None
    assert s["cost_per_letter"] == pytest.approx(0.3)
