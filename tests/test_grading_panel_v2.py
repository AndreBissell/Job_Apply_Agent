"""scripts/grading_panel.py, the v2 commands (evals/grading-standard-v2.md): packets, answer
checks, disagreements, merge, pairwise picks and agreement with the owner. Fake letters, ads and
checklists in a temp folder; no DB, no LLM."""
import argparse
import csv
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load():
    saved_env = {k: os.environ.get(k) for k in ("DATABASE_URL", "APP_ENV")}
    saved_path = list(sys.path)
    try:
        spec = importlib.util.spec_from_file_location("grading_panel_under_test",
                                                      ROOT / "scripts" / "grading_panel.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        sys.path[:] = saved_path
    return mod


gp = _load()

RUN_A, RUN_B = "run-alpha", "run-beta"
KEYS = ("acme-dev", "acme-data")
LETTERS = {
    RUN_A: "Dear Acme team,\n\nAt LogiCo I built Python data pipelines that moved sales records every "
           "night. I also have skills in SQL.\n\nKind regards,\nLiam Chen",
    RUN_B: "Dear Acme team,\n\nAt LogiCo I built Python data pipelines and ran Kubernetes clusters for "
           "the whole company. I also have skills in SQL.\n\nKind regards,\nLiam Chen",
}
REFERENCE = {
    "requirements": [
        {"id": "R1", "text": "Experience with Python", "importance": "essential", "letter_role": "headline",
         "status": "supported", "evidence": ["experience:1#s1"]},
        {"id": "R2", "text": "Knowledge of SQL", "importance": "important", "letter_role": "mention",
         "status": "supported", "evidence": ["skill:2"]},
        {"id": "R3", "text": "Kubernetes in production", "importance": "essential", "letter_role": "headline",
         "status": "gap", "evidence": [], "related_evidence": ["skill:2"]},
        {"id": "R4", "text": "Full Australian work rights", "importance": "essential",
         "letter_role": "not_for_letter", "status": "supported", "evidence": []},
    ],
    "evidence": {"experience:1#s1": {"kind": "work", "text": "Built Python data pipelines at LogiCo"},
                 "skill:2": {"kind": "skill", "text": "SQL"}},
}


def answer(grade=3, r2="Y", d6d="Y", claims=None, doubts=(), ticks=()):
    """One judge's answer for one letter, valid by default."""
    return {
        "job": "J01",
        "requirements": [
            {"id": "R1", "addressed": "Y", "quote": "I built Python data pipelines", "presented_as": "experience",
             "evidence_used": ["experience:1#s1"], "reason": "named with the LogiCo work"},
            {"id": "R2", "addressed": r2, "quote": "I also have skills in SQL" if r2 == "Y" else "",
             "presented_as": "skill" if r2 == "Y" else "none", "evidence_used": [], "reason": ""},
            {"id": "R3", "addressed": "N", "quote": "", "presented_as": "none", "evidence_used": [], "reason": ""},
        ],
        "claims": [{"quote": "At LogiCo I built Python data pipelines", "about": "candidate", "label": "supported",
                    "source": "experience:1#s1", "requirement": "R1", "why": ""}] if claims is None else claims,
        "checklist": {d: {"answer": d6d if d == "D6d" else "Y", "quote": "", "reason": ""} for d in gp.CHECKLIST},
        "send_readiness": {"grade": grade, "edits": [{"what": f"edit by a grade-{grade} judge", "sentences": 1}],
                           "reason": ""},
        "ticks": {t: t in ticks for t in gp.TICKS},
        "note": "",
        "checklist_doubts": list(doubts),
    }


@pytest.fixture
def lab(tmp_path, monkeypatch):
    panels, runs, ref_dir, ads = (tmp_path / d for d in ("panels", "runs", "ref", "ads"))
    for d in (panels, ref_dir, ads):
        d.mkdir()
    for run, text in LETTERS.items():
        (runs / run / "letters").mkdir(parents=True)
        (runs / run / "run.json").write_text(json.dumps({"results": [{"key": k} for k in KEYS]}), encoding="utf-8")
        for k in KEYS:
            (runs / run / "letters" / f"{k}.txt").write_text(text, encoding="utf-8")
    for k in KEYS:
        (ref_dir / f"{k}.json").write_text(json.dumps({"key": k, **REFERENCE}), encoding="utf-8")
        (ads / f"{k}.json").write_text(json.dumps({
            "title": "Junior Developer", "company": "Acme", "location": "Brisbane",
            "raw_description": "Acme wants a developer with Python, SQL and Kubernetes."}), encoding="utf-8")
    monkeypatch.setattr(gp, "PANELS", panels)
    monkeypatch.setattr(gp, "V2_RUNS", runs)
    monkeypatch.setattr(gp, "V2_REF", ref_dir)
    monkeypatch.setattr(gp, "V2_ADS", ads)
    monkeypatch.setattr(gp, "_profile_block_v2", lambda: "# The candidate's profile\n\nName: Liam Chen\n")
    monkeypatch.setattr(gp, "_verify_reference", lambda: [])
    return tmp_path


def pack(tag="t1", only=None):
    return gp.cmd_v2_pack(argparse.Namespace(tag=tag, runs=[RUN_A, RUN_B], only=only, seed=1))


def mapping(tag="t1", name="letters"):
    return json.loads((gp.PANELS / tag / "key" / f"{name}.json").read_text(encoding="utf-8"))


def write_answer(tag, grader, lid, ans):
    path = gp.PANELS / tag / "answers" / grader / f"{lid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"standard": "v2", "letters": {lid: ans}}), encoding="utf-8")


# --- v2-pack ---------------------------------------------------------------------------------

def test_pack_writes_one_blind_packet_per_letter(lab):
    pack()
    m = mapping()
    assert len(m) == 4 and {(v["run"], v["key"]) for v in m.values()} == {(r, k) for r in LETTERS for k in KEYS}
    index = (gp.PANELS / "t1" / "packets" / "index.txt").read_text(encoding="utf-8").split()
    assert index == sorted(m)
    for lid, v in m.items():
        text = (gp.PANELS / "t1" / "packets" / f"{lid}.md").read_text(encoding="utf-8")
        assert RUN_A not in text and RUN_B not in text  # blind: no writer named
        assert LETTERS[v["run"]] in text and "Name: Liam Chen" in text
        assert f"## Letter {lid} (" in text and " words)" in text
        assert f"# JOB {v['job']}" in text
        assert "R1 [essential · headline · supported] must_cover=yes listing_only=no" in text
        assert "R2 [important · mention · supported] must_cover=no listing_only=yes" in text
        assert "related_evidence (does NOT back it)" in text
        assert "R4 (not_for_letter: eligibility, not graded)" in text


def test_pack_refuses_a_used_tag_or_a_reference_that_does_not_verify(lab, monkeypatch):
    pack()
    with pytest.raises(SystemExit, match="use a new tag"):
        pack()
    monkeypatch.setattr(gp, "_verify_reference", lambda: ["acme-dev R9: no such row"])
    with pytest.raises(SystemExit, match="doesn't verify"):
        pack("t2")


def test_pack_only_takes_keys_graded_in_every_run(lab):
    pack(only=["acme-dev"])
    assert {v["key"] for v in mapping().values()} == {"acme-dev"}
    with pytest.raises(SystemExit, match="acme-nope"):
        pack("t2", only=["acme-nope"])


# --- answer checks ---------------------------------------------------------------------------

def test_a_valid_answer_has_no_shape_errors():
    assert gp.validate(answer(), REFERENCE) == []
    assert gp.graded_rows(REFERENCE) == ["R1", "R2", "R3"]


def test_shape_errors_are_each_named():
    bad = answer(grade=4, ticks=("generic",))
    bad["requirements"] = bad["requirements"][1:]           # R1 missing
    bad["claims"][0]["label"] = "probably_true"
    del bad["checklist"]["D7a"]
    errs = gp.validate(bad, REFERENCE)
    assert any(e.startswith("requirements: want") for e in errs)
    assert any("label must be one of" in e for e in errs)
    assert any("'D7a'" in e for e in errs)
    assert "ticks: a 4 has no reasons ticked" in errs
    assert any("grade must be" in e for e in gp.validate(answer(grade=5), REFERENCE))


def test_a_quote_not_in_the_letter_is_not_credited():
    ans = answer()
    ans["requirements"][0]["quote"] = "I led a team of twelve engineers"
    ans["claims"].append({"quote": "I won the Acme hackathon twice", "about": "candidate", "label": "fabricated"})
    checked, problems = gp.check_quotes(ans, LETTERS[RUN_A])
    assert checked["requirements"][0]["addressed"] == "N"
    assert [c["quote"] for c in checked["claims"]] == ["At LogiCo I built Python data pipelines"]
    assert len(problems) == 2
    assert ans["requirements"][0]["addressed"] == "Y"  # the judge's own answer is left as written


def test_check_names_every_missing_or_broken_answer(lab):
    pack(only=["acme-dev"])
    lids = sorted(mapping())
    for lid in lids:
        write_answer("t1", "A", lid, answer())
    bad = answer()
    del bad["ticks"]
    write_answer("t1", "B", lids[0], bad)
    with pytest.raises(SystemExit) as e:
        gp.cmd_v2_check(argparse.Namespace(tag="t1"))
    assert f"B/{lids[0]}: ticks" in str(e.value) and f"B/{lids[1]}: missing" in str(e.value)


# --- disagreements and merge -----------------------------------------------------------------

FABRICATED = {"quote": "ran Kubernetes clusters for the whole company", "about": "candidate", "label": "fabricated",
              "source": "", "requirement": "R3", "why": "no Kubernetes in the profile"}


def graded_panel(tag="t1"):
    """One ad, both letters. The graders agree on run-alpha; on run-beta B gives a 2, says D6d is N
    and that R2 isn't addressed. Both flag run-beta's Kubernetes line; A doubts R3's gap."""
    pack(tag, only=["acme-dev"])
    m = mapping(tag)
    beta = next(lid for lid, v in m.items() if v["run"] == RUN_B)
    alpha = next(lid for lid, v in m.items() if v["run"] == RUN_A)
    write_answer(tag, "A", alpha, answer(grade=4))
    write_answer(tag, "B", alpha, answer(grade=4))
    claims = answer()["claims"] + [FABRICATED]
    write_answer(tag, "A", beta, answer(grade=3, claims=claims,
                                        doubts=[{"id": "R3", "why": "Liam lists Docker; maybe partial"}]))
    write_answer(tag, "B", beta, answer(grade=2, r2="N", d6d="N", claims=claims, ticks=("weak_example",)))
    return alpha, beta


def test_disagreements_write_a_file_only_for_split_letters(lab):
    alpha, beta = graded_panel()
    gp.cmd_v2_disagreements(argparse.Namespace(tag="t1"))
    out = gp.PANELS / "t1" / "disagreements"
    assert not (out / f"{alpha}.json").exists()
    split = json.loads((out / f"{beta}.json").read_text(encoding="utf-8"))
    assert {c["item"] for c in split["cells"]} == {"send_readiness", "D6d", "req:R2", "tick:weak_example"}
    cell = next(c for c in split["cells"] if c["item"] == "send_readiness")
    assert cell["grader_A"]["grade"] == 3 and cell["grader_B"]["grade"] == 2


def test_merge_refuses_until_every_split_cell_has_a_ruling(lab):
    graded_panel()
    with pytest.raises(SystemExit, match="no ruling"):
        gp.cmd_v2_merge(argparse.Namespace(tag="t1"))


def test_merge_applies_the_rulings_and_collects_checklist_doubts(lab):
    alpha, beta = graded_panel()
    rulings = [
        {"item": "send_readiness", "value": 2, "reason": "the Kubernetes line must go", "sided_with": "B"},
        {"item": "D6d", "value": "N", "reason": "one sentence over"},
        {"item": "req:R2", "value": "Y", "sided_with": "A"},
        {"item": "tick:weak_example", "value": "N"},
    ]
    (gp.PANELS / "t1" / "adjudication").mkdir()
    (gp.PANELS / "t1" / "adjudication" / f"{beta}.json").write_text(json.dumps({"rulings": rulings}),
                                                                      encoding="utf-8")
    gp.cmd_v2_merge(argparse.Namespace(tag="t1"))

    b = json.loads((gp.V2_RUNS / RUN_B / "judge-t1.json").read_text(encoding="utf-8"))["acme-dev"]
    assert b["letter"] == beta and b["send_readiness"] == 2
    assert b["checklist"]["D6d"] == "N" and b["checklist"]["D2a"] == "Y"
    assert next(r for r in b["requirements"] if r["id"] == "R2")["addressed"] == "Y"
    assert b["ticks"]["weak_example"] is False
    assert b["untrue"] == "Y" and b["counts"]["fabricated"] == 1 and b["counts"]["gap_fabrications"] == 1
    assert sorted(b["adjudicated"]) == sorted(r["item"] for r in rulings)
    a = json.loads((gp.V2_RUNS / RUN_A / "judge-t1.json").read_text(encoding="utf-8"))["acme-dev"]
    assert a["send_readiness"] == 4 and a["untrue"] == "N" and a["adjudicated"] == []

    doubts = (gp.PANELS / "t1" / "checklist-doubts.md").read_text(encoding="utf-8")
    assert "**R3** (essential, gap) Kubernetes in production" in doubts
    assert "grader A: Liam lists Docker; maybe partial" in doubts


def test_a_claims_ruling_replaces_the_whole_list():
    by = {"A": answer(), "B": answer(claims=[])}
    final = json.loads(json.dumps(by["A"]))
    gp._apply(final, {"item": "claims", "claims": [FABRICATED], "sided_with": "neither"}, by)
    assert final["claims"] == [FABRICATED]


# --- pairwise --------------------------------------------------------------------------------

def test_pick_result_needs_the_same_letter_in_both_orders():
    # order 1 shows alpha first; order 2 shows beta first
    assert gp.pick_result("second", "first", RUN_A, RUN_B) == RUN_B
    assert gp.pick_result("first", "second", RUN_A, RUN_B) == RUN_A
    assert gp.pick_result("first", "first", RUN_A, RUN_B) is None   # followed the position
    assert gp.pick_result("same", "first", RUN_A, RUN_B) is None


def test_sign_test():
    assert gp.sign_test(0, 0) == 1.0
    assert gp.sign_test(1, 0) == 1.0
    assert gp.sign_test(9, 1) == pytest.approx(2 * 11 / 1024)
    assert gp.sign_test(1, 9) == gp.sign_test(9, 1)


def test_pairs_and_picks_count_an_order_dependent_pick_as_a_tie(lab):
    gp.cmd_v2_pairs(argparse.Namespace(tag="p1", run_a=RUN_A, run_b=RUN_B, only=None, seed=1))
    meta = mapping("p1", "pairs")
    assert meta["baseline"] == RUN_A and meta["new"] == RUN_B and len(meta["pairs"]) == 4
    for pid, p in meta["pairs"].items():
        twin = meta["pairs"][p["twin"]]
        assert twin["twin"] == pid and (twin["first"], twin["second"]) == (p["second"], p["first"])
        text = (gp.PANELS / "p1" / "pairs" / f"{pid}.md").read_text(encoding="utf-8")
        assert RUN_A not in text and RUN_B not in text
        assert "## Letter first (" in text and "## Letter second (" in text
        assert text.index(LETTERS[p["first"]]) < text.index(LETTERS[p["second"]])

    for pid, p in meta["pairs"].items():
        if p["key"] == "acme-dev":    # beta both times
            pick = "first" if p["first"] == RUN_B else "second"
        else:                         # the first letter both times: a position pick
            pick = "first"
        path = gp.PANELS / "p1" / "answers" / "pairs" / f"{pid}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"standard": "v2", "pairs": {pid: {"pick": pick, "reason": ""}}}),
                        encoding="utf-8")
    gp.cmd_v2_picks(argparse.Namespace(tag="p1"))
    picks = json.loads((gp.PANELS / "p1" / "picks.json").read_text(encoding="utf-8"))
    assert picks["per_ad"] == {"acme-data": "tie", "acme-dev": RUN_B}
    assert (picks["new_wins"], picks["baseline_wins"], picks["ties"]) == (1, 0, 1)
    assert picks["win_rate"] == 0.75 and picks["sign_test_p"] == 1.0


def test_picks_refuse_a_missing_or_unknown_pick(lab):
    gp.cmd_v2_pairs(argparse.Namespace(tag="p1", run_a=RUN_A, run_b=RUN_B, only=["acme-dev"], seed=1))
    pid = sorted(mapping("p1", "pairs")["pairs"])[0]
    path = gp.PANELS / "p1" / "answers" / "pairs" / f"{pid}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"pairs": {pid: {"pick": "both"}}}), encoding="utf-8")
    with pytest.raises(SystemExit, match="'both'"):
        gp.cmd_v2_picks(argparse.Namespace(tag="p1"))


# --- agreement with the owner ----------------------------------------------------------------

def test_weighted_kappa():
    assert gp.weighted_kappa([1, 2, 3, 4], [1, 2, 3, 4]) == pytest.approx(1.0)
    assert gp.weighted_kappa([1, 1, 2, 2], [1, 2, 2, 2]) == pytest.approx(0.5)   # worked by hand
    assert gp.weighted_kappa([3, 3, 3], [3, 3, 3]) is None   # one grade only: undefined
    assert gp.weighted_kappa([], []) is None
    near = gp.weighted_kappa([1, 2, 3, 4], [2, 2, 3, 4])
    far = gp.weighted_kappa([1, 2, 3, 4], [4, 2, 3, 4])
    assert near > far   # a near miss costs less than a far one


def judged(grade, untrue="N", ticks=()):
    claims = [{"label": "fabricated", "quote": "ran Kubernetes clusters", "why": "not in the profile"}] \
        if untrue == "Y" else []
    return {"send_readiness": grade, "untrue": untrue, "ticks": {t: t in ticks for t in gp.TICKS},
            "claims": claims}


def owned(grade, untrue="N", **ticks):
    return {"key": "", "send_readiness": str(grade), "untrue": untrue, "untrue_text": "", **ticks}


OWNER = {"k1": owned(4), "k2": owned(2, "Y", generic="x"), "k3": owned(3)}
JUDGE = {"k1": judged(3), "k2": judged(2, "Y", ("generic",)), "k3": judged(1, "Y"), "k9": judged(4)}


def test_agreement_counts():
    ag = gp.agreement(OWNER, JUDGE)
    assert ag["n"] == 3   # k9 has no owner grade
    assert (ag["exact"], ag["within_1"]) == (1, 2)
    assert ag["pairs"] == {"k1": (4, 3), "k2": (2, 2), "k3": (3, 1)}
    assert ag["untrue"] == {"tp": 1, "fn": [], "tn": 1, "fp": ["k3"]}
    assert ag["ticks"]["generic"] == {"agree": 3, "owner_only": [], "judge_only": []}


def test_an_accepted_judge_flag_counts_as_the_owner_s_own():
    assert gp.agreement(OWNER, JUDGE, {"k3"})["untrue"] == {"tp": 2, "fn": [], "tn": 1, "fp": []}


def owner_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=gp.OWNER_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({f: r.get(f, "") for f in gp.OWNER_FIELDS})


def test_read_owner_skips_ungraded_rows_and_refuses_bad_values(tmp_path):
    path = tmp_path / "grades-owner.csv"
    owner_csv(path, [{"key": "k1", "send_readiness": "3", "untrue": "n"}, {"key": "k2"}])
    assert list(gp.read_owner(path)) == ["k1"]
    owner_csv(path, [{"key": "k1", "send_readiness": "5", "untrue": "N"}])
    with pytest.raises(SystemExit, match="1-4"):
        gp.read_owner(path)
    owner_csv(path, [{"key": "k1", "send_readiness": "3", "untrue": ""}])
    with pytest.raises(SystemExit, match="Y or N"):
        gp.read_owner(path)


def test_agreement_report(lab):
    (gp.V2_RUNS / RUN_A / "judge-t1.json").write_text(json.dumps(JUDGE), encoding="utf-8")
    owner_csv(gp.V2_RUNS / RUN_A / "grades-owner.csv",
              [{**r, "key": k} for k, r in OWNER.items()])
    (gp.PANELS / "t1").mkdir()
    gp.cmd_v2_agreement(argparse.Namespace(tag="t1", run=RUN_A, owner="grades-owner.csv",
                                           only=["k1", "k3"], accept=None))
    md = (gp.PANELS / "t1" / f"agreement-{RUN_A}.md").read_text(encoding="utf-8")
    assert "(2 letters)" in md and "| Send-readiness, exact | 0/2 |" in md
    assert "| Send-readiness, within ±1 | 1/2 |" in md
    assert "- k3:\n  - fabricated: 'ran Kubernetes clusters'" in md
