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
    monkeypatch.setattr(gp, "CALIBRATION_DIR", tmp_path / "calibration")  # never the real split or examples
    monkeypatch.setattr(gp, "CALIBRATION_SPLIT", tmp_path / "calibration" / "split.json")
    monkeypatch.setattr(gp, "CALIBRATION_EXAMPLES", tmp_path / "calibration" / "examples.md")
    monkeypatch.setattr(gp, "ACCEPTED_UNTRUE", tmp_path / "calibration" / "accepted-untrue.json")
    return tmp_path


def pack(tag="t1", only=None):
    return gp.cmd_v2_pack(argparse.Namespace(tag=tag, runs=[RUN_A, RUN_B], only=only, half=None, seed=1))


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
        gp.cmd_v2_check(argparse.Namespace(tag="t1", graders=list(gp.GRADERS)))
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
    gp.cmd_v2_disagreements(argparse.Namespace(tag="t1", graders=list(gp.GRADERS)))
    out = gp.PANELS / "t1" / "disagreements"
    assert not (out / f"{alpha}.json").exists()
    split = json.loads((out / f"{beta}.json").read_text(encoding="utf-8"))
    assert {c["item"] for c in split["cells"]} == {"send_readiness", "D6d", "req:R2", "tick:weak_example"}
    cell = next(c for c in split["cells"] if c["item"] == "send_readiness")
    assert cell["grader_A"]["grade"] == 3 and cell["grader_B"]["grade"] == 2


def test_merge_refuses_until_every_split_cell_has_a_ruling(lab):
    graded_panel()
    with pytest.raises(SystemExit, match="no ruling"):
        gp.cmd_v2_merge(argparse.Namespace(tag="t1", graders=list(gp.GRADERS)))


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
    gp.cmd_v2_merge(argparse.Namespace(tag="t1", graders=list(gp.GRADERS)))

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
                                           only=["k1", "k3"], half=None, accept=None))
    md = (gp.PANELS / "t1" / f"agreement-{RUN_A}.md").read_text(encoding="utf-8")
    assert "(2 letters)" in md and "| Send-readiness, exact | 0/2 |" in md
    assert "| Send-readiness, within ±1 | 1/2 |" in md
    assert "- k3:\n  - fabricated: 'ran Kubernetes clusters'" in md


# --- v2-run: the judge runner (a fake backend or a fake subprocess; never a real CLI) ------------

CLI = "9.9.9"


@pytest.fixture
def runner(lab, monkeypatch):
    monkeypatch.setattr(gp, "JUDGE_DIR", lab / "judge-cwd")
    monkeypatch.setattr(gp, "cli_version", lambda: CLI)
    monkeypatch.setenv("JUDGE_CLAUDE_BIN", "claude-test.exe")  # `claude` on PATH may be a .cmd shim, refused
    return lab


def fake_backend(reply):
    """A backend whose answer is reply(prompt, schema); it records every prompt it was sent."""
    def backend(prompt, schema):
        backend.calls.append(prompt)
        out = reply(prompt, schema)
        if isinstance(out, Exception):
            raise out
        return out, {"seconds": 1, "models_used": [gp.JUDGE_MODEL], "notional_usd": 0.5}
    backend.calls = []
    return backend


def graded(ans_for):
    """A grade reply for whichever letter the schema asks about."""
    def reply(prompt, schema):
        lid = next(iter(schema["properties"]["letters"]["properties"]))
        return {"standard": "v2", "letters": {lid: ans_for(lid)}}
    return reply


def run_args(stage="grade", graders=("A", "B"), redo=False):
    return argparse.Namespace(tag="t1", backend="claude-cli", stage=stage, graders=list(graders), only=None,
                              half=None, jobs=1, redo=redo, no_examples=False)


def test_the_judge_gets_the_standard_and_its_packet_but_no_file_paths(lab):
    pack(only=["acme-dev"])
    lid, m = sorted(mapping().items())[0]
    packet = (gp.PANELS / "t1" / "packets" / f"{lid}.md").read_text(encoding="utf-8")
    prompt = gp.judge_prompt("grade", packet, lid, m["job"])
    assert gp.STANDARD_V2.read_text(encoding="utf-8") in prompt
    after = prompt.split("# Your packet")[1]
    assert "Name: Liam Chen" in after and LETTERS[m["run"]] in after
    assert "evals/" not in after  # the packet's read/write lines for a subagent are left out
    assert "STOCK_" not in prompt  # the owner's sounds_ai list never reaches a judge
    assert prompt.rstrip().endswith(f"Answer with the §9 JSON for letter {lid}.")


def test_the_cli_judge_runs_with_nothing_but_the_prompt(runner, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, **kw)
        out = {"subtype": "success", "is_error": False, "structured_output": {"ok": 1}, "total_cost_usd": 0.2,
               "modelUsage": {gp.JUDGE_MODEL: {}, "claude-haiku-5-5": {}}}
        return gp.subprocess.CompletedProcess(cmd, 0, json.dumps(out), "")

    monkeypatch.setattr(gp.subprocess, "run", fake_run)
    answer_, facts = gp.run_claude_cli("the prompt", {"type": "object"})
    cmd = seen["cmd"]
    flag = {cmd[i]: cmd[i + 1] for i in range(len(cmd) - 1)}
    assert flag["--tools"] == "" and flag["--setting-sources"] == ""
    assert {"-p", "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"} <= set(cmd)
    assert flag["--model"] == gp.JUDGE_MODEL and gp.JUDGE_MODEL.startswith("claude-opus-")  # a full id, no alias
    assert flag["--effort"] == gp.JUDGE_EFFORT and json.loads(flag["--json-schema"]) == {"type": "object"}
    assert seen["input"] == "the prompt" and seen["cwd"] == gp.JUDGE_DIR
    assert "ANTHROPIC_API_KEY" not in seen["env"]  # the subscription, never API billing
    assert answer_ == {"ok": 1} and facts["models_used"] == ["claude-haiku-5-5", gp.JUDGE_MODEL]


@pytest.mark.parametrize("stdout, match", [
    (json.dumps({"subtype": "success", "is_error": True, "result": "API Error: 400 version too old"}), "too old"),
    (json.dumps({"subtype": "success", "is_error": False, "structured_output": {},
                 "modelUsage": {"claude-sonnet-5-5": {}}}), "not claude-opus"),
    (json.dumps({"subtype": "success", "is_error": False, "modelUsage": {gp.JUDGE_MODEL: {}}}), "no structured"),
    ("Error: not logged in", "exit 1"),
])
def test_a_cli_judge_without_a_usable_answer_raises(runner, monkeypatch, stdout, match):
    monkeypatch.setattr(gp.subprocess, "run", lambda cmd, **kw: gp.subprocess.CompletedProcess(cmd, 1, stdout, ""))
    with pytest.raises(gp.JudgeError, match=match):
        gp.run_claude_cli("p", {})


def test_the_judge_folder_must_be_empty(runner):
    gp.JUDGE_DIR.mkdir()
    (gp.JUDGE_DIR / "notes.md").write_text("x", encoding="utf-8")
    with pytest.raises(SystemExit, match="must be empty"):
        gp._check_judge_dir()


def test_run_writes_one_answer_per_letter_and_grader_with_its_judge(runner, monkeypatch):
    pack(only=["acme-dev"])
    backend = fake_backend(graded(lambda lid: answer()))
    monkeypatch.setitem(gp.BACKENDS, "claude-cli", backend)
    assert gp.cmd_v2_run(run_args()) == 0 and len(backend.calls) == 4
    for lid in mapping():
        for g in gp.GRADERS:
            saved = json.loads((gp.PANELS / "t1" / "answers" / g / f"{lid}.json").read_text(encoding="utf-8"))
            assert saved["letters"][lid]["send_readiness"]["grade"] == 3
            j = saved["judge"]
            assert (j["backend"], j["model"], j["cli_version"]) == ("claude-cli", gp.JUDGE_MODEL, CLI)
            assert j["standard_sha256"] == gp._sha(gp.STANDARD_V2.read_text(encoding="utf-8"))
    gp.cmd_v2_check(argparse.Namespace(tag="t1", graders=list(gp.GRADERS)))  # the existing check reads them as they are
    assert gp.cmd_v2_run(run_args()) == 0 and len(backend.calls) == 4  # nothing to redo
    assert gp.cmd_v2_run(run_args(graders=("B",), redo=True)) == 0 and len(backend.calls) == 6


def test_a_wrongly_shaped_answer_is_set_aside_and_a_fresh_judge_tried(runner, monkeypatch):
    pack(only=["acme-dev"])
    bad = answer()
    del bad["ticks"]
    backend = fake_backend(graded(lambda lid: bad))
    monkeypatch.setitem(gp.BACKENDS, "claude-cli", backend)
    assert gp.cmd_v2_run(run_args(graders=("A",))) == 1
    assert len(backend.calls) == 2 * gp.JUDGE_ATTEMPTS
    answers = gp.PANELS / "t1" / "answers" / "A"
    for lid in mapping():
        assert not (answers / f"{lid}.json").exists()
        rejected = json.loads((answers / f"{lid}.rejected.json").read_text(encoding="utf-8"))
        assert any(e.startswith("ticks") for e in rejected["errors"])
    monkeypatch.setitem(gp.BACKENDS, "claude-cli", fake_backend(graded(lambda lid: answer())))
    assert gp.cmd_v2_run(run_args(graders=("A",))) == 0
    assert sorted(p.name for p in answers.iterdir()) == sorted(f"{lid}.json" for lid in mapping())


def test_an_adjudicator_sees_the_split_cells_and_must_rule_on_each(runner, monkeypatch):
    alpha, beta = graded_panel()
    gp.cmd_v2_disagreements(argparse.Namespace(tag="t1", graders=list(gp.GRADERS)))
    partial = [{"item": "send_readiness", "value": "2", "reason": "", "sided_with": "B"}]

    def adjudicator(prompt, schema):
        assert schema is gp.ADJUDICATION_SCHEMA and "# The disagreements" in prompt and '"item": "req:R2"' in prompt
        return {"rulings": partial}

    monkeypatch.setitem(gp.BACKENDS, "claude-cli", fake_backend(adjudicator))
    assert gp.cmd_v2_run(run_args(stage="adjudicate")) == 1  # only beta split, and D6d etc. have no ruling
    adjudication = gp.PANELS / "t1" / "adjudication"
    rejected = json.loads((adjudication / f"{beta}.rejected.json").read_text(encoding="utf-8"))
    assert "no ruling for D6d" in rejected["errors"]
    assert not (adjudication / f"{alpha}.rejected.json").exists()


def test_a_panel_run_end_to_end_records_one_judge_and_refuses_a_mixed_one(runner, monkeypatch, capsys):
    pack(only=["acme-dev"])
    grades = iter([3, 2] * 2)  # A then B for each letter: one worker runs them in order
    monkeypatch.setitem(gp.BACKENDS, "claude-cli", fake_backend(graded(lambda lid: answer(grade=next(grades)))))
    assert gp.cmd_v2_run(run_args()) == 0
    gp.cmd_v2_disagreements(argparse.Namespace(tag="t1", graders=list(gp.GRADERS)))
    monkeypatch.setitem(gp.BACKENDS, "claude-cli", fake_backend(lambda p, s: {"rulings": [
        {"item": "send_readiness", "value": "2", "reason": "two paragraphs to rework", "sided_with": "B"}]}))
    assert gp.cmd_v2_run(run_args(stage="adjudicate")) == 0
    gp.cmd_v2_merge(argparse.Namespace(tag="t1", graders=list(gp.GRADERS)))
    assert "Judge: claude-cli `claude-opus-" in capsys.readouterr().out
    rec = json.loads((gp.V2_RUNS / RUN_A / "judge-t1.json").read_text(encoding="utf-8"))["acme-dev"]
    assert rec["send_readiness"] == 2 and [v["backend"] for v in rec["judge"]] == ["claude-cli"]
    assert gp.panel_judge({"acme-dev": rec}).startswith(f"claude-cli `{gp.JUDGE_MODEL}`, effort")

    write_answer("t1", "A", rec["letter"], answer(grade=3))  # the same letter, now by a subagent
    with pytest.raises(SystemExit, match="more than one judge version"):
        gp.cmd_v2_merge(argparse.Namespace(tag="t1", graders=list(gp.GRADERS)))


# --- v2-split: the calibration / check halves ----------------------------------------------------

def split_owner():
    """A 4, a 1 and one untrue letter (each the only one), then six 2s and 3s."""
    grades = {"a": 4, "b": 1, "c": 2, "d": 2, "e": 2, "f": 2, "g": 3, "h": 3, "i": 2}
    return {k: owned(g, "Y" if k == "c" else "N") for k, g in grades.items()}


RELEVANT = {"a": "Y", "b": "Y", "c": "N", "d": "Y", "e": "Y", "f": "N", "g": "Y", "h": "N", "i": "Y"}


def test_letters_whose_label_is_the_only_one_go_to_calibration():
    s = gp.calibration_split(split_owner(), RELEVANT, 4, seed=7)
    assert s["forced"] == {"a": ["the only letter graded 4"], "b": ["the only letter graded 1"],
                           "c": ["the only untrue letter"]}
    assert {"a", "b", "c"} <= set(s["calibration"]) and len(s["check"]) == 4
    assert sorted(s["calibration"] + s["check"]) == sorted(split_owner())
    assert s == gp.calibration_split(split_owner(), RELEVANT, 4, seed=7)  # seeded


def test_each_grade_and_relevance_gets_its_share_of_the_check_half():
    s = gp.calibration_split(split_owner(), RELEVANT, 4, seed=7)
    seats = {(x["grade"], x["relevant"]): (x["letters"], x["check"]) for x in s["strata"]}
    # 6 letters left for 4 seats: relevant 2s (3 of them) get 2; the other three strata (1 letter
    # each, 2/3 of a seat) tie, and the seed picks which two get the last seats
    assert seats[("2", "Y")] == (3, 2) and sum(c for _, c in seats.values()) == 4
    assert sum(s["check"].count(k) for k in "dei") == 2


def test_two_untrue_letters_are_not_forced_and_a_too_big_check_half_is_refused():
    owner = split_owner() | {"d": owned(2, "Y")}
    assert "c" not in gp.calibration_split(owner, RELEVANT, 4, seed=7)["forced"]
    with pytest.raises(SystemExit, match="needs at least"):
        gp.calibration_split(split_owner(), RELEVANT, 7, seed=7)


def test_the_split_is_written_once_and_never_redrawn(lab, monkeypatch):
    monkeypatch.setattr(gp, "_relevance", lambda: RELEVANT)
    owner_csv(gp.V2_RUNS / RUN_A / "grades-owner.csv", [{**r, "key": k} for k, r in split_owner().items()])
    args = argparse.Namespace(run=RUN_A, check=4, seed=7)
    gp.cmd_v2_split(args)
    saved = json.loads(gp.CALIBRATION_SPLIT.read_text(encoding="utf-8"))
    assert saved["run"] == RUN_A and len(saved["owner_grades_sha256"]) == 64
    assert gp.calibration_half("check") == saved["check"]
    with pytest.raises(SystemExit, match="never redrawn"):
        gp.cmd_v2_split(args)


# --- derived ticks and the pass bar --------------------------------------------------------------

def test_derived_ticks_come_from_the_rows_and_d4b_not_the_judge_s_own_ticks():
    ans = answer(r2="N")
    ans["checklist"]["D4b"]["answer"] = "N"
    assert gp.missable_rows(REFERENCE) == ["R1", "R2"]  # R3 is a gap, R4 not_for_letter
    assert gp.missed_rows(REFERENCE, ans["requirements"]) == ["R2"]
    assert gp.derived_ticks(REFERENCE, ans) == {"missed_important": True, "no_personal_reason": True}
    assert not any(c.startswith(("tick:missed_important", "tick:no_personal_reason")) for c in gp.cells(ans))


def test_merge_writes_the_derived_ticks_and_keeps_the_judge_s_own(lab):
    pack(only=["acme-dev"])
    for lid in mapping():
        for g in gp.GRADERS:
            write_answer("t1", g, lid, answer(r2="N", ticks=("generic",)))
    gp.cmd_v2_merge(argparse.Namespace(tag="t1", graders=list(gp.GRADERS)))
    rec = json.loads((gp.V2_RUNS / RUN_A / "judge-t1.json").read_text(encoding="utf-8"))["acme-dev"]
    assert rec["ticks"]["missed_important"] is True and rec["judge_ticks"]["missed_important"] is False
    assert rec["ticks"]["generic"] is True and rec["missed_rows"] == ["R2"]


def test_owner_missed_rows_are_read_from_the_notes():
    assert gp.owner_missed_rows("Missed R7 (async, designing for failure) and R8 (ownership); two examples in "
                                "one paragraph, R9 fine") == ["R7", "R8"]
    assert gp.owner_missed_rows("No essential/important row missed: R7 only partly covered") == []
    assert gp.owner_missed_rows(None) == []


def test_the_pass_bar_counts_ready_rows_and_the_always_2_baseline():
    owner = {"k1": owned(2, note="Missed R1 (Python)"), "k2": owned(3), "k3": owned(2, no_personal_reason="Y")}
    judge = {k: {**judged(g), "requirements": [{"id": "R1", "addressed": a}, {"id": "R2", "addressed": "Y"}]}
             for k, g, a in (("k1", 2, "N"), ("k2", 2, "Y"), ("k3", 3, "N"))}
    judge["k3"]["ticks"]["no_personal_reason"] = True
    ag = gp.agreement(owner, judge, refs={k: REFERENCE for k in owner})
    assert (ag["exact"], ag["baseline_exact"], ag["ready"]) == (1, 2, 1)
    assert ag["missed"] | {"owner_only": [], "judge_only": []} == {
        "owner": 1, "caught": 1, "owner_only": [], "judge_only": [], "stray": [], "rows": 6}
    assert ag["missed"]["judge_only"] == [("k3", "R1")]
    bar = {m: p for m, _, _, p in gp.pass_bar(ag)}
    assert bar["Send-readiness, exact"] is False  # 1 is not more than always-2's 2
    assert bar["Ready / not ready (3+ vs ≤ 2)"] is True  # 1 >= 3 - 2
    assert bar["Untrue: caught the owner's flags (TPR)"] is None  # no owner flag: planted claims test it
    assert bar["Missed rows: the owner's, also missed by the judge"] is True


# --- worked examples (standard §10) ----------------------------------------------------------------

def write_split(calibration, check):
    gp.CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)
    gp.CALIBRATION_SPLIT.write_text(json.dumps({"run": RUN_A, "calibration": calibration, "check": check}),
                                    encoding="utf-8")


def example(key, reading="The second paragraph mixes two jobs, so it needs reworking: a 2."):
    return f"{gp.EXAMPLE_HEAD}{key}\n\nJob: Junior Developer, Acme\n\nWorked reading: {reading}\n\n"


def test_examples_must_be_written_and_from_the_calibration_half(lab):
    write_split(["acme-dev"], ["acme-data"])
    path = gp.CALIBRATION_EXAMPLES
    path.write_text("# Worked examples\n\n" + example("acme-dev", gp.READING_TODO), encoding="utf-8")
    with pytest.raises(SystemExit, match="isn't written"):
        gp.load_examples(path)
    path.write_text(example("acme-dev") + example("acme-data"), encoding="utf-8")
    with pytest.raises(SystemExit, match="acme-data: not in the calibration half"):
        gp.load_examples(path)
    path.write_text(example("acme-dev"), encoding="utf-8")
    assert list(gp.load_examples(path)) == ["acme-dev"]


def test_a_letter_is_never_shown_its_own_example():
    block = gp.examples_block({"k1": "Job: one", "k2": "Job: two"}, skip="k1")
    assert "Job: two" in block and "Job: one" not in block and "## Example 1\n" in block


def test_the_examples_are_drafted_once_without_the_sounds_ai_tick(lab):
    write_split(["acme-dev"], [])
    owner_csv(gp.V2_RUNS / RUN_A / "grades-owner.csv", [{"key": "acme-dev", "send_readiness": "2", "untrue": "N",
                                                         "weak_example": "Y", "sounds_ai": "Y",
                                                         "note": "Missed R2 (SQL)"}])
    gp.cmd_v2_examples(argparse.Namespace())
    text = gp.CALIBRATION_EXAMPLES.read_text(encoding="utf-8")
    assert f"{gp.EXAMPLE_HEAD}acme-dev" in text and LETTERS[RUN_A] in text
    assert "- R2 [important · mention · supported] Knowledge of SQL" in text and "R4" not in text
    assert "send-readiness **2**" in text and "Reasons ticked: weak_example." in text and "sounds_ai" not in text
    assert "Missed R2 (SQL)" in text and gp.READING_TODO in text
    with pytest.raises(SystemExit, match="edit it by hand"):
        gp.cmd_v2_examples(argparse.Namespace())


def test_run_adds_the_other_examples_and_records_their_hash(runner, monkeypatch):
    pack(only=["acme-dev"])
    write_split(["acme-dev", "acme-other"], [])
    gp.CALIBRATION_EXAMPLES.write_text(example("acme-dev") + example("acme-other", "A clean 3."), encoding="utf-8")
    backend = fake_backend(graded(lambda lid: answer()))
    monkeypatch.setitem(gp.BACKENDS, "claude-cli", backend)
    assert gp.cmd_v2_run(run_args(graders=("A",))) == 0
    for prompt in backend.calls:  # every letter here is acme-dev: its own example is left out
        assert "A clean 3." in prompt and "mixes two jobs" not in prompt
        assert prompt.index("# Worked examples") < prompt.index("# Your packet")
    lid = sorted(mapping())[0]
    saved = json.loads((gp.PANELS / "t1" / "answers" / "A" / f"{lid}.json").read_text(encoding="utf-8"))
    assert saved["judge"]["examples_sha256"] == gp._sha(gp.CALIBRATION_EXAMPLES.read_text(encoding="utf-8"))
    bare = fake_backend(graded(lambda lid: answer()))
    monkeypatch.setitem(gp.BACKENDS, "claude-cli", bare)
    gp.cmd_v2_run(argparse.Namespace(**{**vars(run_args(graders=("B",))), "no_examples": True}))
    assert all("# Worked examples" not in p for p in bare.calls)


def test_a_one_grader_run_merges_without_adjudication(lab):
    pack(only=["acme-dev"])
    for lid in mapping():
        write_answer("t1", "A", lid, answer(grade=2, r2="N"))
    with pytest.raises(SystemExit, match="missing"):  # both graders by default
        gp.cmd_v2_merge(argparse.Namespace(tag="t1", graders=list(gp.GRADERS)))
    gp.cmd_v2_check(argparse.Namespace(tag="t1", graders=["A"]))
    gp.cmd_v2_merge(argparse.Namespace(tag="t1", graders=["A"]))
    rec = json.loads((gp.V2_RUNS / RUN_A / "judge-t1.json").read_text(encoding="utf-8"))["acme-dev"]
    assert rec["send_readiness"] == 2 and rec["adjudicated"] == [] and rec["missed_rows"] == ["R2"]


def test_a_flag_the_owner_accepted_earlier_is_always_counted(lab):
    (gp.V2_RUNS / RUN_A / "judge-t1.json").write_text(json.dumps(JUDGE), encoding="utf-8")
    owner_csv(gp.V2_RUNS / RUN_A / "grades-owner.csv", [{**r, "key": k} for k, r in OWNER.items()])
    (gp.PANELS / "t1").mkdir()
    gp.CALIBRATION_DIR.mkdir()
    gp.ACCEPTED_UNTRUE.write_text(json.dumps({"k3": "a stretch the owner missed"}), encoding="utf-8")
    gp.cmd_v2_agreement(argparse.Namespace(tag="t1", run=RUN_A, owner="grades-owner.csv", only=None, half=None,
                                           accept=None))
    md = (gp.PANELS / "t1" / f"agreement-{RUN_A}.md").read_text(encoding="utf-8")
    assert "| Untrue: caught the owner's flags (TPR) | 2/2 |" in md


# --- probes: padded and planted copies --------------------------------------------------------------

PLANT = " At LogiCo I also ran the Kubernetes clusters."


def probe_spec(tmp, items):
    path = tmp / "spec.json"
    path.write_text(json.dumps(items), encoding="utf-8")
    return str(path)


def test_a_probe_is_inserted_after_the_one_place_its_anchor_appears():
    assert gp.probe_letter("A. B. C.", "B.", " X.") == "A. B. X. C."
    with pytest.raises(SystemExit, match="exactly once"):
        gp.probe_letter("A. A.", "A.", " X.")


def test_a_probe_run_is_a_run_folder_that_v2_pack_can_pack(lab):
    spec = probe_spec(lab, [{"key": "acme-dev", "kind": "plant", "after": "every night.", "text": PLANT,
                             "why": "a gap claimed inside a real job"}])
    gp.cmd_v2_probe(argparse.Namespace(name="probe-1", run=RUN_A, spec=spec))
    text = (gp.V2_RUNS / "probe-1" / "letters" / "acme-dev.txt").read_text(encoding="utf-8")
    assert "every night. At LogiCo I also ran the Kubernetes clusters." in text
    meta = json.loads((gp.V2_RUNS / "probe-1" / "run.json").read_text(encoding="utf-8"))
    assert meta["probe_of"] == RUN_A and [r["key"] for r in meta["results"]] == ["acme-dev"]
    gp.cmd_v2_pack(argparse.Namespace(tag="pr", runs=["probe-1"], only=None, half=None, seed=1))
    assert len(mapping("pr")) == 1
    with pytest.raises(SystemExit, match="written once"):
        gp.cmd_v2_probe(argparse.Namespace(name="probe-1", run=RUN_A, spec=spec))


def test_a_padded_copy_over_the_word_cap_is_refused(lab):
    spec = probe_spec(lab, [{"key": "acme-dev", "kind": "pad", "after": "every night.", "text": " filler" * 400}])
    with pytest.raises(SystemExit, match="test the cap"):
        gp.cmd_v2_probe(argparse.Namespace(name="probe-2", run=RUN_A, spec=spec))


def test_probe_results_pass_a_caught_plant_and_a_pad_that_grades_no_higher():
    meta = {"probes": [{"key": "k1", "kind": "plant", "text": PLANT}, {"key": "k2", "kind": "pad", "text": " x"},
                       {"key": "k3", "kind": "pad", "text": " y"}]}
    flagged = {"label": "fabricated", "quote": "I also ran the Kubernetes clusters"}
    probe = {"k1": {"send_readiness": 2, "claims": [flagged]}, "k2": {"send_readiness": 3, "claims": []},
             "k3": {"send_readiness": 2, "claims": []}}
    original = {"k2": {"send_readiness": 2}, "k3": {"send_readiness": 2}}
    passed = {r["key"]: r["passed"] for r in gp.probe_results(meta, probe, original)}
    assert passed == {"k1": True, "k2": False, "k3": True}
    probe["k1"]["claims"] = [{"label": "supported", "quote": "I also ran the Kubernetes clusters"}]
    assert gp.probe_results(meta, probe, original)[0]["passed"] is False


def test_a_usage_limit_stops_the_run_without_retrying(runner, monkeypatch, capsys):
    pack(only=["acme-dev"])
    backend = fake_backend(lambda p, s: gp.UsageLimit("You've hit your 5-hour limit, resets 3:40pm"))
    monkeypatch.setitem(gp.BACKENDS, "claude-cli", backend)
    assert gp.cmd_v2_run(run_args()) == 1
    assert len(backend.calls) == 1  # one worker: the first judge hit the limit, the other three never ran
    assert "Stopped: You've hit your 5-hour limit" in capsys.readouterr().out
    out = {"subtype": "success", "is_error": True, "result": "You've hit your 5-hour limit · resets 3:40pm"}
    monkeypatch.setattr(gp.subprocess, "run", lambda cmd, **kw: gp.subprocess.CompletedProcess(cmd, 1, json.dumps(out), ""))
    with pytest.raises(gp.UsageLimit):
        gp.run_claude_cli("p", {})
