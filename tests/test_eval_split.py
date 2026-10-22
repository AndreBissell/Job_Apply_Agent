"""scripts/eval_split.py: the near-duplicate rules for choosing and splitting the eval set
(docs/eval-set-plan.md, Decision log 2026-10-21). Pure functions on made-up ads; no DB."""
import importlib.util
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("eval_split_under_test", ROOT / "scripts" / "eval_split.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["eval_split_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


es = _load()
Ad = es.Ad


def ads(scenario, *keys):
    return [Ad(k, int(k[1:]), scenario, f"Job {k}", "Co") for k in keys]


def test_families_join_near_duplicates_transitively():
    base = "Graduate program in engineering, finance and technology. " * 30
    texts = {
        "a": base + "Stream: technology.",
        "b": base + "Stream: finance.",
        "c": base.replace("engineering", "mining") + "Stream: mining.",
        "d": "Senior data engineer, Spark and Airflow, five years. " * 30,
    }
    assert es.families(texts) == [["a", "b", "c"]]


def test_families_respect_the_threshold():
    texts = {"a": "alpha beta gamma " * 50, "b": "alpha beta gamma " * 25 + "delta epsilon zeta " * 25}
    assert es.families(texts, threshold=0.9) == []
    assert es.families(texts, threshold=0.4) == [["a", "b"]]


def test_one_ad_per_family_when_the_scenario_has_spares():
    pool = ads(2, "k1", "k2", "k3", "k4")  # k1 and k2 are twins; need 2 of 4
    chosen, notes = es.choose(pool, {2: 2}, {"k1": 0, "k2": 0}, random.Random(1))
    keys = {a.key for a in chosen[2]}
    assert len(keys) == 2 and not {"k1", "k2"} <= keys


def test_second_family_member_only_when_the_scenario_is_short():
    pool = ads(1, "k1", "k2", "k3")  # need 3 of 3: both twins are needed
    chosen, notes = es.choose(pool, {1: 3}, {"k1": 0, "k2": 0}, random.Random(1))
    assert {a.key for a in chosen[1]} == {"k1", "k2", "k3"}
    assert "second of its family: scenario short" in {notes["k1"], notes["k2"]}


def test_scarcest_scenario_gets_the_family_member():
    # Twins k1 (scenario 1, no spares) and k9 (scenario 6, spares): k1 must win on any seed.
    pool = ads(1, "k1", "k2") + ads(6, "k9", "k7", "k8")
    for seed in range(50):
        chosen, notes = es.choose(pool, {1: 2, 6: 2}, {"k1": 0, "k9": 0}, random.Random(seed))
        assert "k1" in {a.key for a in chosen[1]}
        assert "k9" not in {a.key for a in chosen[6]}
        assert notes["k9"] == "left out: a family member is already in"


def test_a_family_with_two_chosen_ads_goes_to_dev_whole():
    targets = {1: ("Strong", 2, 2)}
    chosen = {1: ads(1, "k1", "k2", "k3", "k4")}
    for seed in range(50):
        dev, test, warnings = es.split(chosen, targets, {"k1": 0, "k2": 0}, random.Random(seed))
        assert {"k1", "k2"} <= {a.key for a in dev}
        assert len(dev) == 2 and len(test) == 2 and not warnings


def test_a_family_never_straddles_the_split():
    targets = {1: ("Strong", 7, 5), 2: ("Nice", 3, 2), 6: ("Border", 2, 2)}
    pool = ads(1, *(f"k{i}" for i in range(10, 20))) + ads(2, *(f"k{i}" for i in range(20, 26))) \
        + ads(6, *(f"k{i}" for i in range(30, 33)))
    family_of = {"k10": 0, "k11": 0, "k20": 1, "k30": 1, "k12": 2, "k21": 2}
    need = {s: d + t for s, (_n, d, t) in targets.items()}
    for seed in range(200):
        rng = random.Random(seed)
        chosen, _ = es.choose(pool, need, family_of, rng)
        dev, test, _ = es.split(chosen, targets, family_of, rng)
        sides = {}
        for side, group in (("dev", dev), ("test", test)):
            for a in group:
                if a.key in family_of:
                    sides.setdefault(family_of[a.key], set()).add(side)
        assert all(len(s) == 1 for s in sides.values()), (seed, sides)


def test_targets_are_met_and_a_short_scenario_splits_in_proportion():
    targets = {1: ("Strong", 7, 5), 7: ("Unrelated", 2, 1)}
    chosen = {1: ads(1, *(f"k{i}" for i in range(10))), 7: ads(7, "k50", "k51", "k52")}
    dev, test, _ = es.split(chosen, targets, {}, random.Random(3))
    count = lambda group, s: sum(1 for a in group if a.scenario == s)  # noqa: E731
    assert (count(dev, 7), count(test, 7)) == (2, 1)
    assert (count(dev, 1), count(test, 1)) == (6, 4)  # 10 of 12: 10 * 7/12 = 5.8 -> 6


def test_the_same_seed_gives_the_same_split():
    targets = {1: ("Strong", 3, 2)}
    pool = ads(1, *(f"k{i}" for i in range(8)))

    def run(seed):
        rng = random.Random(seed)
        chosen, _ = es.choose(pool, {1: 5}, {}, rng)
        dev, test, _ = es.split(chosen, targets, {}, rng)
        return [a.key for a in dev], [a.key for a in test]

    assert run(7) == run(7)
    assert any(run(7) != run(s) for s in range(8, 20))


def test_read_tags(tmp_path):
    frozen = {12: {"key": "seek-1", "title": "A", "company": "X"},
              13: {"key": "seek-2", "title": "B", "company": "Y"},
              14: {"key": "seek-3", "title": "C", "company": "Z"}}
    sheet = tmp_path / "c.csv"
    sheet.write_text("job_id,scenario,final_scenario,scenario_ok (Y/N)\n12,1,2,Y\n13,1,,\n14,3,,N\n",
                     encoding="utf-8-sig")
    got = es.read_tags(sheet, frozen)
    assert [(a.job_id, a.scenario) for a in got] == [(12, 2), (13, 1)]  # final wins; N dropped
    sheet.write_text("job_id,scenario,final_scenario\n12,1,none\n13,1,4\n14,3,\n", encoding="utf-8-sig")
    got = es.read_tags(sheet, frozen)  # "none" = fits no scenario: dropped, never the draw's tag
    assert [(a.job_id, a.scenario) for a in got] == [(13, 4), (14, 3)]
    sheet.write_text("job_id,scenario\n12,1\n13,1\n", encoding="utf-8-sig")
    with pytest.raises(SystemExit, match=r"frozen but not tagged.*\[14\]"):
        es.read_tags(sheet, frozen)
    sheet.write_text("job_id,scenario\n12,1\n13,1\n14,1\n99,1\n", encoding="utf-8-sig")
    with pytest.raises(SystemExit, match=r"tagged but not frozen.*\[99\]"):
        es.read_tags(sheet, frozen)


def test_targets_come_from_the_tracker():
    targets = es.load_targets()
    assert sorted(targets) == [1, 2, 3, 4, 6, 7]
    assert sum(d for _n, d, _t in targets.values()) == 20
    assert sum(t for _n, _d, t in targets.values()) == 15
