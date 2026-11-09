"""scripts/letter_points.py: the points score (owner, 2026-11-03; the severity guide's values and cap)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import letter_points as lp  # noqa: E402


def i(metric, level, **kw):
    return {"metric": metric, "level": level, **kw}


def test_a_clean_letter_keeps_its_ten():
    assert lp.score([]) == 10.0
    assert lp.score([i("generic", "none"), i("weak_example", None)]) == 10.0   # none is not a problem


def test_each_problem_costs_its_set_value():
    assert lp.score([i("untrue", "high")]) == 7.5
    assert lp.score([i("missed_row", "low"), i("missed_row", "high")]) == 7.75
    assert lp.score([i("generic", "low"), i("stock_phrasing", "high")]) == 9.2


def test_weak_examples_and_mix_ups_charge_only_the_two_highest():
    three = [i("weak_example", "low"), i("weak_example", "high"), i("weak_example", "low")]
    assert lp.score(three) == 8.5   # high + low; the third is not charged
    assert lp.by_metric(three)["weak_example"] == ["high", "low"]
    assert lp.score([i("mix_up", "high")] * 3) == 7.0   # two highs = high + high


def test_untrue_claims_and_missed_rows_are_uncapped_and_the_floor_is_zero():
    assert lp.score([i("missed_row", "low")] * 4) == 7.0
    assert lp.score([i("untrue", "high")] * 5) == 0.0


def test_one_per_letter_metrics_refuse_a_second_entry_and_bad_levels_are_refused():
    with pytest.raises(ValueError, match="one per letter"):
        lp.score([i("generic", "low"), i("generic", "high")])
    with pytest.raises(ValueError, match="low or high"):
        lp.score([i("untrue", "medium")])
    with pytest.raises(ValueError, match="unknown metric"):
        lp.score([i("fit", "low")])


def test_the_code_set_levels():
    assert [lp.stock_level(n) for n in (8, 9, 11, 12)] == [None, "low", "low", "high"]
    assert lp.too_long_level(0, 340) is None and lp.too_long_level(0, 341) == "high"
    assert [lp.too_long_level(n, 300) for n in (1, 2, 3)] == ["low", "low", "high"]
    assert lp.missed_level(True) == "high" and lp.missed_level(False) == "low"


def test_label_issues_read_the_owner_s_labels():
    letter = {"untrue": [{"quote": "AWS at the internship", "label": "stretch", "level": "low"}],
              "issues": [i("missed_row", "high", row="R4"), i("stock_phrasing", "none", hits=8),
                         i("mix_up", "high", note="hard to follow")]}
    got = lp.label_issues(letter)
    assert [(x["metric"], x["level"]) for x in got] == [("untrue", "low"), ("missed_row", "high"), ("mix_up", "high")]
    assert lp.score(got) == 10 - 1.0 - 1.5 - 1.5
