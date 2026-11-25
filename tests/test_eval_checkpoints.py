"""scripts/eval_checkpoints.py: experiment checkpoints and their comparison
(docs/experiment-checkpoints-plan.md §7). Fixtures only: fake ads (Acme, LogiCo, Initech), a fake
candidate (Liam Chen), no real run, no DB, no LLM and no judge."""
import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import eval_checkpoints as ec  # noqa: E402
import letter_points as lp  # noqa: E402

# Planted in every text field a run or judge record holds; it must never reach a checkpoint.
MARKER = "ZZSECRETZZ"
VERSION = {"backend": "claude-cli", "model": "claude-opus-5-5", "effort": "high", "cli_version": "2.1.317",
           "standard_sha256": "a" * 64, "instructions_sha256": "b" * 64, "examples_sha256": "c" * 64}
FROZEN = {"name": "v1", "frozen": "2026-11-05", "version": VERSION, "note": "fixture"}
PRICES = {"gemini-pro": (2.0, 12.0, 0.2), "gemini-flash": (0.75, 3.75, 0.075)}
THINKING = {"strong": "high", "mid": "medium", "small": "low"}
SHA = "d" * 64
RUN = "dev-fake-v1"
KEYS = ("acme-dev", "logico-ops", "initech-data")
ADS = {"acme-dev": {"side": "dev", "relevant": True}, "logico-ops": {"side": "dev", "relevant": False},
       "initech-data": {"side": "dev", "relevant": None}}


def _result(key, run_id, **kw):
    return {"key": key, "title": f"{MARKER} Senior Title", "company": f"{MARKER} Acme Pty Ltd", "score": 88,
            "seconds": 100.0, "calls": 5, "cost_usd": 0.2, "input_tokens": 10, "output_tokens": 5,
            "letter_run_id": run_id, "drafts": 1, "final_version": 1, "status": "done", "clean": True,
            "open_issues": [f"{MARKER} open issue"], "stop_reason": f"{MARKER} stop", "steps": [], **kw}


def _run(results=None, **kw):
    return {"run_id": RUN, "engine": "agent", "strong_model": "gemini-pro", "split": "dev",
            "created_at": "2026-10-26T17:29:41",
            "results": results or [_result(k, 100 + i) for i, k in enumerate(KEYS)], **kw}


def _issues(*pairs):
    """('generic', 'high'), ... -> letter_points issues, each quoting the planted marker."""
    return [{"metric": m, "level": lv, "quote": f"{MARKER} quote", "row": "R3" if m == "missed_row" else None}
            for m, lv in pairs]


def _rec(key, issues, *, missed=(), checklist=None, words=300, stock=5, judge=None, run=RUN, points=None):
    return {
        "key": key, "run": run, "job": "7", "letter": "L1234",
        "points": lp.score(issues) if points is None else points, "by_metric": lp.by_metric(issues),
        "issues": issues, "missed_rows": list(missed), "untrue": "N", "words": words, "stock_hits": stock,
        "checklist": checklist or {"D2a": "Y", "D4b": "N"},
        "judge": [judge or dict(VERSION)],
        "note": f"{MARKER} judge note", "problems": {"generic": {"level": "high", "quote": MARKER, "why": MARKER}},
        "claims": [{"quote": MARKER, "why": MARKER, "label": "supported"}],
        "quote_problems": [MARKER], "checklist_doubts": [MARKER], "adjudicated": [MARKER],
        "requirements": [{"id": "R3", "quote": MARKER, "reason": MARKER}],
        "counts": {"supported": 3}, "letter_text": f"Dear Acme, I am Liam Chen. {MARKER}",
    }


def _judging(issues_by_key=None, **kw):
    issues_by_key = issues_by_key or {
        "acme-dev": _issues(("generic", "high")),
        "logico-ops": _issues(("missed_row", "high"), ("missed_row", "low"), ("stock_phrasing", "low")),
        "initech-data": _issues(),
    }
    out = {}
    for key, issues in issues_by_key.items():
        missed = ["R3", "R10"] if any(i["metric"] == "missed_row" for i in issues) else []
        out[key] = _rec(key, issues, missed=missed, **kw)
    return out


def _usage():
    rows = []
    for i, _ in enumerate(KEYS):
        rid = 100 + i
        rows += [
            {"run_id": rid, "task": "generate_letter", "tier": "strong", "model": "gemini-pro", "input_tokens": 6000,
             "cached_tokens": 1000, "output_tokens": 1000, "thinking_tokens": 7000, "cost_usd": 0.1062,
             "duration_ms": 90000},
            {"run_id": rid, "task": "check_claims", "tier": "mid", "model": "gemini-flash", "input_tokens": 4000,
             "cached_tokens": 0, "output_tokens": 1500, "thinking_tokens": 2500, "cost_usd": 0.018,
             "duration_ms": 40000},
            {"run_id": rid, "task": "check_claims", "tier": "mid", "model": "gemini-flash", "input_tokens": 4000,
             "cached_tokens": 0, "output_tokens": 1500, "thinking_tokens": 2500, "cost_usd": 0.018,
             "duration_ms": 40000},
        ]
    rows.append({"run_id": 999, "task": "generate_letter", "tier": "strong", "model": "gemini-other",
                 "input_tokens": 1, "cached_tokens": 0, "output_tokens": 1, "thinking_tokens": 0, "cost_usd": 9.0,
                 "duration_ms": 1})  # another run's letter: never counted
    return rows


def _build(existing=(), judgings=None, run_meta=None, ads=ADS, **kw):
    args = dict(run_meta=run_meta or _run(), judgings=judgings or [[("v1-dev", _judging())]],
                frozen_judge=FROZEN, usage=_usage(), ads=ads, scorer_sha256=SHA, prices=PRICES,
                thinking=THINKING, commit="abc1234", set_name="v2", experiment="baseline", kept=True,
                created="2026-11-25T10:00:00")
    args.update(kw)
    return ec.build_letters_checkpoint(list(existing), **args)


def _row(cp, key):
    return next(r for r in cp["letters"] if r["key"] == key)


# ---------------------------------------------------------------------------
# Privacy (plan §7): the marker never reaches a checkpoint; every key is in the allowlist
# ---------------------------------------------------------------------------
def test_planted_marker_reaches_no_field_of_the_written_line(tmp_path):
    cp = _build(judgings=[[("v1-dev", _judging())], [("v1-dev-b", _judging())]], note="baseline",
                rule="", verdict="none")
    path = tmp_path / "checkpoints.jsonl"
    ec.append(path, cp)
    raw = path.read_text(encoding="utf-8")
    assert MARKER not in raw
    assert "Liam" not in raw and "Acme Pty" not in raw and "Senior Title" not in raw
    line = json.loads(raw)
    assert set(line) == set(ec.TOP_KEYS)
    assert set(line["comparable"]) <= set(ec.COMPARABLE_KEYS)
    assert set(line["settings"]) <= set(ec.SETTINGS_KEYS)
    assert set(line["totals"]) <= set(ec.TOTALS_KEYS)
    for row in line["letters"]:
        assert set(row) == set(ec.LETTER_KEYS)
        for t in row["by_task"].values():
            assert set(t) == set(ec.TASK_KEYS)
        for p in row["problems"].values():
            assert set(p) == set(ec.PROBLEM_KEYS)


def test_allowlist_is_pinned():
    """A new field is added on purpose, with this test, never by copying a record."""
    assert ec.TOP_KEYS == ("id", "created", "kind", "experiment", "note", "parent", "kept", "verdict", "rule",
                           "aa_of", "replaces", "comparable", "settings", "totals", "letters", "measures")
    assert ec.LETTER_KEYS == ("key", "relevant", "status", "clean", "drafts", "seconds", "cost_usd", "by_task",
                              "words", "stock_hits", "points", "points_each", "problems", "missed_row_ids",
                              "checklist")
    assert ec.COMPARABLE_KEYS == ("set", "split", "judge", "scorer_sha256", "judgings")
    assert ec.SETTINGS_KEYS == ("run", "judge_tags", "judge_backends", "commit", "engine", "models", "thinking",
                                "prices_seen", "given_by_hand")  # given_by_hand: D13, added on purpose in step 2
    assert ec.TASK_KEYS == ("model", "calls", "in", "cached", "out", "think", "cost_usd", "ms")
    assert ec.PROBLEM_KEYS == ("n", "low", "high", "lost")


@pytest.mark.parametrize("mutate", [
    lambda cp: cp["letters"][0].update(title="Senior Title"),           # a key outside the allowlist
    lambda cp: cp.update(issues=[]),
    lambda cp: cp["letters"][0].update(status="Dear Acme team, I am"),  # text inside an allowed key
    lambda cp: cp["letters"][0]["checklist"].update(D2a="Y because the letter says"),
    lambda cp: cp["letters"][0]["missed_row_ids"].append("R3 not addressed"),
    lambda cp: cp["letters"][0]["by_task"]["generate_letter"].update(quote="x"),
    lambda cp: cp["settings"].update(engine="the agent, run on Acme"),
    lambda cp: cp["measures"].update({"a b": 1}),
])
def test_privacy_guard_refuses_unknown_keys_and_text_values(mutate):
    cp = _build()
    mutate(cp)
    with pytest.raises(ec.CheckpointError, match="privacy guard"):
        ec.check_allowlist(cp)


def test_append_refuses_a_checkpoint_the_guard_refuses(tmp_path):
    cp = _build()
    cp["letters"][0]["company"] = "Acme"
    path = tmp_path / "checkpoints.jsonl"
    with pytest.raises(ec.CheckpointError, match="privacy guard"):
        ec.append(path, cp)
    assert not path.exists()


# ---------------------------------------------------------------------------
# What a letters checkpoint holds (plan §1)
# ---------------------------------------------------------------------------
def test_letter_rows_points_problems_cost_and_settings():
    cp = _build(note="baseline run")
    assert cp["id"] == "c0" and cp["kind"] == "letters" and cp["parent"] is None
    assert cp["comparable"] == {"set": "v2", "split": "dev", "judge": "v1", "scorer_sha256": SHA, "judgings": 1}
    s = cp["settings"]
    assert s["run"] == RUN and s["judge_tags"] == [["v1-dev"]] and s["judge_backends"] == ["claude-cli"]
    assert s["models"] == {"strong": "gemini-pro", "mid": "gemini-flash"}  # gemini-other is another run's
    assert s["thinking"] == THINKING and s["engine"] == "agent" and s["commit"] == "abc1234"
    assert s["prices_seen"] == {"gemini-flash": [0.75, 3.75, 0.075], "gemini-pro": [2.0, 12.0, 0.2]}

    logico = _row(cp, "logico-ops")
    assert logico["points"] == 10 - 1.5 - 0.75 - 0.25 and logico["points_each"] == [logico["points"]]
    assert logico["problems"]["missed_row"] == {"n": 2, "low": 1, "high": 1, "lost": 2.25}
    assert logico["problems"]["stock_phrasing"] == {"n": 1, "low": 1, "high": 0, "lost": 0.25}
    assert logico["problems"]["untrue"] == {"n": 0, "low": 0, "high": 0, "lost": 0.0}
    assert set(logico["problems"]) == set(lp.METRICS)  # zeros included
    assert logico["missed_row_ids"] == ["R3", "R10"]  # by row number, not as text
    assert logico["checklist"] == {"D2a": "Y", "D4b": "N"} and logico["relevant"] is False

    gen = logico["by_task"]["generate_letter"]
    assert gen == {"model": "gemini-pro", "calls": 1, "in": 6000, "cached": 1000, "out": 1000, "think": 7000,
                   "cost_usd": 0.1062, "ms": 90000}
    assert logico["by_task"]["check_claims"]["calls"] == 2
    assert logico["cost_usd"] == pytest.approx(0.1422)
    assert cp["totals"] == {"letters": 3, "cost_usd": pytest.approx(0.4266), "seconds": 300.0,
                            "points_mean": pytest.approx((9.4 + 7.5 + 10.0) / 3, abs=1e-4)}


def test_a_failed_letter_without_a_draft_is_kept_unjudged():
    results = [_result("acme-dev", 100), _result("logico-ops", 101, drafts=0, status="failed", clean=False)]
    j = _judging({"acme-dev": _issues()})
    cp = _build(run_meta=_run(results=results), judgings=[[("v1-dev", j)]])
    failed = _row(cp, "logico-ops")
    assert failed["points"] is None and failed["points_each"] == [] and failed["status"] == "failed"
    assert cp["totals"]["points_mean"] == 10.0


def test_a_letter_with_a_draft_but_no_judging_is_refused():
    j = _judging({"acme-dev": _issues(), "logico-ops": _issues()})
    with pytest.raises(ec.CheckpointError, match="initech-data has a draft but no judging"):
        _build(judgings=[[("v1-dev", j)]])


def test_a_judged_letter_of_another_run_is_refused():
    j = _judging()
    j["acme-dev"]["run"] = "other-run"
    with pytest.raises(ec.CheckpointError, match="letter of run other-run"):
        _build(judgings=[[("v1-dev", j)]])
    j2 = _judging()
    j2["stray-ad"] = _rec("stray-ad", _issues())
    with pytest.raises(ec.CheckpointError, match="stray-ad, which is not a letter"):
        _build(judgings=[[("v1-dev", j2)]])


def test_a_task_with_two_models_in_one_letter_is_refused():
    usage = _usage()
    usage[1]["model"] = "gemini-pro"
    with pytest.raises(ec.CheckpointError, match="two models"):
        _build(usage=usage)


# ---------------------------------------------------------------------------
# Refusals (plan §3.1)
# ---------------------------------------------------------------------------
def test_refuses_a_judge_block_that_differs_from_the_frozen_judge():
    j = _judging(judge=dict(VERSION, cli_version="2.1.999"))
    with pytest.raises(ec.CheckpointError, match="not judged by frozen judge v1: cli_version differ"):
        _build(judgings=[[("v1-dev", j)]])


def test_refuses_overlapping_joined_halves():
    full = _judging()
    a = {k: full[k] for k in ("acme-dev", "logico-ops")}
    b = {k: full[k] for k in ("logico-ops", "initech-data")}
    with pytest.raises(ec.CheckpointError, match="half-a and half-b both cover logico-ops"):
        _build(judgings=[[("half-a", a), ("half-b", b)]])


def test_joined_halves_make_one_judging():
    full = _judging()
    a = {k: full[k] for k in ("acme-dev",)}
    b = {k: full[k] for k in ("logico-ops", "initech-data")}
    cp = _build(judgings=[[("half-a", a), ("half-b", b)]])
    assert cp["comparable"]["judgings"] == 1 and cp["settings"]["judge_tags"] == [["half-a", "half-b"]]
    assert [r["points"] for r in cp["letters"]] == [r["points"] for r in _build()["letters"]]


@pytest.mark.parametrize("which", [0, 1])
def test_refuses_a_points_mismatch_in_any_judging(which):
    judgings = [[("j1", _judging())], [("j2", _judging())]]
    judgings[which][0][1]["logico-ops"]["points"] = 9.99
    with pytest.raises(ec.CheckpointError, match=r"j%d.json's logico-ops: the scorer gives 7.5" % (which + 1)):
        _build(judgings=judgings)


def test_refuses_a_per_metric_mismatch():
    j = _judging()
    j["acme-dev"]["by_metric"]["generic"] = ["low"]
    with pytest.raises(ec.CheckpointError, match="problem levels differ"):
        _build(judgings=[[("v1-dev", j)]])


def test_refuses_the_test_split_without_allow_test():
    with pytest.raises(ec.CheckpointError, match="test split stays closed"):
        _build(run_meta=_run(split="test"))
    ads = dict(ADS, **{"acme-dev": {"side": "test", "relevant": True}})  # an --only run on a test ad
    with pytest.raises(ec.CheckpointError, match="test split stays closed"):
        _build(run_meta=_run(split=None), ads=ads)
    assert _build(run_meta=_run(split="test"), allow_test=True)["comparable"]["split"] == "test"


def test_a_verdict_needs_its_rule():
    with pytest.raises(ec.CheckpointError, match="needs the --rule"):
        _build(verdict="pass")
    assert _build(verdict="fail", rule="missed rows halve")["rule"] == "missed rows halve"
    with pytest.raises(ec.CheckpointError, match="verdict must be"):
        _build(verdict="maybe")


# ---------------------------------------------------------------------------
# Averaged judgings (plan §3.5)
# ---------------------------------------------------------------------------
def test_two_judgings_average_points_and_every_problem_field():
    second = _judging({
        "acme-dev": _issues(),                                          # 10.0 (first: 9.4)
        "logico-ops": _issues(("missed_row", "high"), ("stock_phrasing", "low")),   # 8.25 (first: 7.5)
        "initech-data": _issues(("generic", "low")),                    # 9.7 (first: 10.0)
    })
    second["initech-data"]["checklist"] = {"D2a": "N", "D4b": "N"}
    second["logico-ops"]["missed_rows"] = ["R12"]
    cp = _build(judgings=[[("v1-dev", _judging())], [("again", second)]])
    assert cp["comparable"]["judgings"] == 2 and cp["settings"]["judge_tags"] == [["v1-dev"], ["again"]]
    assert cp["settings"]["judge_backends"] == ["claude-cli", "claude-cli"]
    logico = _row(cp, "logico-ops")
    assert logico["points_each"] == [7.5, 8.25] and logico["points"] == pytest.approx(7.875)
    assert logico["problems"]["missed_row"] == {"n": 1.5, "low": 0.5, "high": 1.0, "lost": 1.875}
    assert logico["missed_row_ids"] == ["R3", "R10", "R12"]  # every id any judging charged
    acme = _row(cp, "acme-dev")
    assert acme["points_each"] == [9.4, 10.0] and acme["problems"]["generic"]["n"] == 0.5
    assert _row(cp, "initech-data")["checklist"] == {"D2a": "mixed", "D4b": "N"}


def test_averaging_refuses_a_letter_missing_from_one_judging():
    second = _judging()
    del second["initech-data"]
    with pytest.raises(ec.CheckpointError, match="initech-data is missing from judging again"):
        _build(judgings=[[("v1-dev", _judging())], [("again", second)]])


def test_averaging_refuses_judgings_of_different_letters():
    second = _judging()
    second["acme-dev"]["words"] = 999
    with pytest.raises(ec.CheckpointError, match="disagree on words"):
        _build(judgings=[[("v1-dev", _judging())], [("again", second)]])


# ---------------------------------------------------------------------------
# Append-only file and the parent rule (plan D1, D5)
# ---------------------------------------------------------------------------
def test_append_only_ids_run_in_order_and_earlier_lines_stay_byte_for_byte(tmp_path):
    path = tmp_path / "v2" / "checkpoints.jsonl"
    ec.append(path, _build(ec.load(path)))
    first = path.read_bytes()
    ec.append(path, _build(ec.load(path), experiment="E1", kept=False))
    ec.append(path, _build(ec.load(path), experiment="baseline", replaces="c0", note="corrected c0"))
    assert path.read_bytes().startswith(first)
    cps = ec.load(path)
    assert [c["id"] for c in cps] == ["c0", "c1", "c2"]
    assert cps[2]["replaces"] == "c0" and cps[1]["replaces"] is None
    assert len(path.read_text(encoding="utf-8").splitlines()) == 3


def test_append_refuses_a_stale_id_and_load_refuses_an_edited_file(tmp_path):
    path = tmp_path / "checkpoints.jsonl"
    stale = _build()
    ec.append(path, _build())
    with pytest.raises(ec.CheckpointError, match="would not be the next id"):
        ec.append(path, stale)
    path.write_text(path.read_text().replace('"id": "c0"', '"id": "c7"'))
    with pytest.raises(ec.CheckpointError, match="the file was edited"):
        ec.load(path)


def test_replaces_must_name_an_existing_checkpoint():
    with pytest.raises(ec.CheckpointError, match="--replaces c4: no such"):
        _build(replaces="c4")


def test_default_parent_is_the_last_kept_checkpoint():
    c0 = _build()
    c1 = _build([c0], experiment="E1", kept=False)
    assert c1["parent"] == "c0"
    c2 = _build([c0, c1], experiment="E2", kept=True)
    assert c2["parent"] == "c0"  # not the failed c1
    c3 = _build([c0, c1, c2], experiment="E3", kept=True)
    assert c3["parent"] == "c2"


def test_an_explicit_parent_that_is_not_kept_is_refused():
    c0 = _build()
    c1 = _build([c0], experiment="E1", kept=False)
    with pytest.raises(ec.CheckpointError, match="--parent c1 is not kept"):
        _build([c0, c1], parent="c1")
    with pytest.raises(ec.CheckpointError, match="--parent c9: no such"):
        _build([c0, c1], parent="c9")
    assert _build([c0, c1], parent="c0")["parent"] == "c0"


def test_an_aa_checkpoints_parent_is_the_one_it_repeats():
    c0 = _build()
    c1 = _build([c0], experiment="E1", kept=True)
    again = [[("v1-dev-again", _judging())]]  # a fresh judging: the same tag again is refused (step 2)
    aa = _build([c0, c1], aa_of="c0", kept=False, judgings=again)
    assert aa["parent"] == "c0" and aa["aa_of"] == "c0"
    with pytest.raises(ec.CheckpointError, match="parent is the one it repeats"):
        _build([c0, c1], aa_of="c0", parent="c1", kept=False, judgings=again)
    # an A/A is never a default parent, even when marked kept
    aa_kept = _build([c0], aa_of="c0", kept=True, judgings=again)
    assert _build([c0, aa_kept], experiment="E2")["parent"] == "c0"


def test_default_parent_is_of_the_same_kind():
    c0 = _build()
    m1 = ec.build_measure_checkpoint([c0], experiment="E1", values={"G": 5}, commit="abc1234", kept=True,
                                     created="2026-11-25T10:00:00")
    assert m1["parent"] is None
    assert _build([c0, m1], experiment="E2")["parent"] == "c0"


# ---------------------------------------------------------------------------
# Measure checkpoints (plan §1)
# ---------------------------------------------------------------------------
def test_measure_checkpoint_holds_only_its_named_numbers(tmp_path):
    values = ec.parse_values(["G=5", "U=2", "cost_usd=1.31"])
    cp = ec.build_measure_checkpoint([], experiment="E1", values=values, commit="abc1234", kept=False,
                                     verdict="fail", rule="G at most 2", note="arm B",
                                     created="2026-11-25T10:00:00")
    assert cp["kind"] == "measure" and cp["letters"] == [] and cp["measures"] == {"G": 5.0, "U": 2.0,
                                                                                  "cost_usd": 1.31}
    ec.append(tmp_path / "c.jsonl", cp)
    with pytest.raises(ec.CheckpointError, match="NAME=NUMBER"):
        ec.parse_values(["G5"])
    with pytest.raises(ec.CheckpointError, match="not a number"):
        ec.parse_values(["G=five"])
    with pytest.raises(ec.CheckpointError, match="at least one --value"):
        ec.build_measure_checkpoint([], experiment="E1", values={}, commit="x", kept=False)


def test_build_does_not_touch_its_inputs():
    judgings = [[("v1-dev", _judging())]]
    run_meta = _run()
    before = copy.deepcopy((judgings, run_meta))
    _build(judgings=judgings, run_meta=run_meta)
    assert (judgings, run_meta) == before


# ---------------------------------------------------------------------------
# Run settings given by hand (plan D13)
# ---------------------------------------------------------------------------
def test_given_by_hand_is_recorded_and_checked():
    cp = _build(given_by_hand=["thinking", "commit"])
    assert cp["settings"]["given_by_hand"] == ["commit", "thinking"]  # fixed order
    assert _build()["settings"]["given_by_hand"] == []
    with pytest.raises(ec.CheckpointError, match="not settings that can be typed in"):
        _build(given_by_hand=["models"])
    cp["settings"]["given_by_hand"] = ["typed by Liam"]
    with pytest.raises(ec.CheckpointError, match="privacy guard: settings.given_by_hand"):
        ec.check_allowlist(cp)


def test_unknown_commit_and_thinking_stay_unknown():
    cp = _build(commit=None, thinking={})
    assert cp["settings"]["commit"] is None and cp["settings"]["thinking"] == {}


# ---------------------------------------------------------------------------
# A/A checkpoints (plan §3.3, D14)
# ---------------------------------------------------------------------------
def test_an_aa_must_be_comparable_with_what_it_repeats():
    c0 = _build()
    with pytest.raises(ec.CheckpointError, match="comparable.scorer_sha256"):
        _build([c0], aa_of="c0", kept=False, scorer_sha256="e" * 64, judgings=[[("again", _judging())]])


def test_a_judge_only_aa_needs_a_fresh_judging():
    c0 = _build()
    with pytest.raises(ec.CheckpointError, match=r"needs a fresh judging; \['v1-dev'\]"):
        _build([c0], aa_of="c0", kept=False)
    # a writer + judge A/A (another run) may reuse a tag name: its letters are new
    other = _run(run_id="dev-fake-v2")
    j = _judging(run="dev-fake-v2")
    assert _build([c0], aa_of="c0", kept=False, run_meta=other, judgings=[[("v1-dev", j)]])["aa_of"] == "c0"


# ---------------------------------------------------------------------------
# Hand-made checkpoints for pairing and stats: allowlisted rows with chosen numbers
# ---------------------------------------------------------------------------
def _problems_row(missed=0.0, generic=0.0):
    out = {m: {"n": 0, "low": 0, "high": 0, "lost": 0.0} for m in lp.METRICS}
    out["missed_row"] = {"n": missed, "low": 0, "high": missed, "lost": missed * lp.VALUES["missed_row"][1]}
    out["generic"] = {"n": generic, "low": generic, "high": 0, "lost": generic * lp.VALUES["generic"][0]}
    return out


def _letter(key, points, *, relevant=True, seconds=100.0, drafts=1, think=7000, missed=0, generic=0,
            model="gemini-pro"):
    task = {"model": model, "calls": 1, "in": 6000, "cached": 1000, "out": 1000, "think": think, "cost_usd": 0.0,
            "ms": int(seconds * 500)}
    task["cost_usd"] = round(ec.task_cost_at(task, PRICES), 6)
    return {"key": key, "relevant": relevant, "status": "done", "clean": True, "drafts": drafts, "seconds": seconds,
            "cost_usd": task["cost_usd"], "by_task": {"generate_letter": task}, "words": 300, "stock_hits": 5,
            "points": points, "points_each": [] if points is None else [points],
            "problems": {} if points is None else _problems_row(missed, generic), "missed_row_ids": [],
            "checklist": {}}


def _cp(cid, letters, *, run="r1", aa_of=None, parent=None, kept=True, judgings=1, tags=None, split="dev",
        prices=None, **settings):
    cp = {"id": cid, "created": "2026-11-25T10:00:00", "kind": "letters", "experiment": "baseline", "note": "",
          "parent": parent, "kept": kept, "verdict": "none", "rule": "", "aa_of": aa_of, "replaces": None,
          "comparable": {"set": "v2", "split": split, "judge": "v1", "scorer_sha256": SHA, "judgings": judgings},
          "settings": {"run": run, "judge_tags": tags or [[f"tag-{cid}"]] * judgings,
                       "judge_backends": ["claude-cli"] * judgings, "commit": "abc1234", "engine": "agent",
                       "models": {"strong": "gemini-pro"}, "thinking": dict(THINKING),
                       "prices_seen": {"gemini-pro": list(prices or PRICES["gemini-pro"])}, "given_by_hand": [],
                       **settings},
          "totals": {}, "letters": letters, "measures": {}}
    ec.check_allowlist(cp)  # the fixtures are real checkpoints, not shapes the guard would refuse
    return cp


K = ("k1", "k2", "k3", "k4")
REL = {"k1": True, "k2": True, "k3": False, "k4": None}


def _letters(points, **kw):
    return [_letter(k, p, relevant=REL[k], **kw) for k, p in zip(K, points)]


A = _cp("c0", _letters([8, 7, 9, 6]))
AA = _cp("c1", _letters([8.5, 7, 9, 5.5]), aa_of="c0", parent="c0", kept=False)  # same run: judge-only
B = _cp("c2", _letters([9, 7, 8, 8], seconds=120.0, think=9000), run="r2", parent="c0")


# ---------------------------------------------------------------------------
# Pairing (plan §3.2)
# ---------------------------------------------------------------------------
def test_pairing_lists_one_sided_ads_and_skips_failed_letters():
    a = _cp("c0", _letters([8, 7, 9, 6]) + [_letter("only-a", 5)])
    b = _cp("c1", [_letter("k1", 9), _letter("k2", None), _letter("k3", 8, relevant=True), _letter("k4", 7),
                   _letter("only-b", 6)], run="r2")
    pg = ec.pair(a, b)
    assert [k for k, _, _ in pg["pairs"]] == ["k1", "k3", "k4"]
    assert pg["failed"] == ["k2"] and pg["only_a"] == ["only-a"] and pg["only_b"] == ["only-b"]
    assert [k for k, _, _ in pg["relevant"]] == ["k1"]  # relevant on both sides only
    assert pg["relevant_disagree"] == ["k3", "k4"]


def test_comparable_mismatch_is_refused_and_names_the_field():
    with pytest.raises(ec.CheckpointError, match="comparable.split is 'dev' vs 'test'"):
        ec.check_comparable(A, _cp("c3", _letters([1, 2, 3, 4]), split="test"))
    with pytest.raises(ec.CheckpointError, match="comparable.judgings is 1 vs 2.*judge the other side again"):
        ec.check_comparable(A, _cp("c3", _letters([1, 2, 3, 4]), judgings=2))
    m = ec.build_measure_checkpoint([], experiment="E1", values={"G": 5}, commit="abc1234", kept=True,
                                    created="2026-11-25T10:00:00")
    with pytest.raises(ec.CheckpointError, match="can't be compared"):
        ec.check_comparable(A, m)


def test_differing_settings_are_listed_not_refused():
    b = _cp("c3", _letters([8, 7, 9, 6]), run="r3", engine="workflow",
            thinking={"strong": "low", "mid": "medium", "small": "low"}, given_by_hand=["thinking"])
    diff = {f: (x, y) for f, x, y in ec.settings_diff(A, b)}
    assert diff["engine"] == ("agent", "workflow") and diff["thinking.strong"] == ("high", "low")
    assert diff["run"] == ("r1", "r3") and diff["given_by_hand"] == ([], ["thinking"])
    assert "thinking.mid" not in diff and "models.strong" not in diff
    assert ec.compare([A, b], A, b, PRICES)["settings_diff"] == ec.settings_diff(A, b)


# ---------------------------------------------------------------------------
# Stats (plan §3.3): hand-worked numbers
# ---------------------------------------------------------------------------
def test_paired_difference_sd_and_up_same_down():
    st = ec.diff_stats(ec.pair(A, B)["pairs"], "points", PRICES)
    # A 8 7 9 6, B 9 7 8 8: differences +1 0 -1 +2
    assert st["n"] == 4 and st["a"] == 7.5 and st["b"] == 8.0 and st["diff"] == 0.5
    assert st["sd"] == pytest.approx((5 / 3) ** 0.5)  # deviations .5 -.5 -1.5 1.5, squares sum 5, n - 1 = 3
    assert (st["up"], st["same"], st["down"]) == (2, 1, 1)
    assert ec._sd([1.0]) is None


def test_t_and_the_labels_on_hand_worked_numbers():
    cmp = ec.compare([A, AA, B], A, B, PRICES)
    pts = cmp["groups"]["all"]["points"]
    # A/A differences +0.5 0 0 -0.5: s = sqrt(0.5 / 3); T = 2 s / sqrt(4) = s
    s = (0.5 / 3) ** 0.5
    assert pts["noise"]["s"] == pytest.approx(s) and pts["noise"]["T"] == pytest.approx(s)
    assert pts["noise"]["label"] == ec.BEYOND  # 0.5 > 0.408
    rel = cmp["groups"]["relevant"]["points"]
    assert rel["n"] == 2 and rel["diff"] == 0.5  # k1 +1, k2 0
    assert rel["noise"]["T"] == pytest.approx(2 * s / 2 ** 0.5)
    assert rel["noise"]["label"] == ec.WITHIN  # 0.5 < 0.577
    assert pts["noise"]["ref"] == "c1 vs c0"


# ---------------------------------------------------------------------------
# Which A/A is the noise reference (plan §3.3, §3.5, D14)
# ---------------------------------------------------------------------------
def test_aa_kind_same_run_is_judge_only_other_run_is_writer_and_judge():
    wj = _cp("c3", _letters([8, 7, 9, 6]), run="r9", aa_of="c0", parent="c0", kept=False)
    assert ec.aa_kind(AA, A) == ec.JUDGE_ONLY and ec.aa_kind(wj, A) == ec.WRITER_JUDGE


def test_a_judge_only_reference_is_provisional_and_gives_nothing_for_cost_time_or_drafts():
    g = ec.compare([A, AA, B], A, B, PRICES)["groups"]["all"]
    assert g["points"]["noise"]["provisional"] is True
    assert "provisional: judge noise only, writer noise not included" in ec.label_text(g["points"]["noise"])
    for m in ("cost_today", "cost_usd", "seconds", "drafts"):
        assert g[m]["noise"]["label"] == ec.NO_REF and g[m]["noise"]["T"] is None, m  # never T = 0
        assert ec.label_text(g[m]["noise"]) == ec.NO_REF


def test_no_aa_gives_no_reference():
    g = ec.compare([A, B], A, B, PRICES)["groups"]["all"]
    assert all(g[m]["noise"]["label"] == ec.NO_REF for m, _ in ec.MEASURES)


def test_a_writer_and_judge_reference_is_preferred_and_covers_cost_and_time():
    wj = _cp("c3", _letters([7.5, 7, 9.5, 6], seconds=110.0), run="r9", aa_of="c0", parent="c0", kept=False)
    cps = [A, AA, B, wj]
    refs = ec.noise_refs(cps, A["comparable"], PRICES)
    assert refs[ec.JUDGE]["aa"] == "c3" and refs[ec.WRITER]["aa"] == "c3"
    g = ec.compare(cps, A, B, PRICES)["groups"]["all"]
    assert g["points"]["noise"]["provisional"] is False and g["points"]["noise"]["ref"] == "c3 vs c0"
    # the writer + judge A/A's seconds: all +10, so s = 0: still no reference, never T = 0
    assert g["seconds"]["noise"]["label"] == ec.NO_REF and g["seconds"]["noise"]["ref"] == "c3 vs c0"
    assert "c3 vs c0 never moved on this measure" in ec.label_text(g["seconds"]["noise"])


def test_a_writer_and_judge_reference_labels_cost():
    wj = _cp("c3", [_letter(k, 8, relevant=REL[k], think=t) for k, t in zip(K, (6000, 8000, 7000, 7500))],
             run="r9", aa_of="c0", parent="c0", kept=False)
    g = ec.compare([A, wj, B], A, B, PRICES)["groups"]["all"]
    cost = g["cost_today"]
    # B's letters think 2000 more tokens each: +$0.024 a letter at $12 / 1M
    assert cost["diff"] == pytest.approx(0.024) and cost["noise"]["label"] == ec.BEYOND
    # A/A think differences -1000 +1000 0 +500 -> cost differences x $12 / 1M
    s = ec._sd([-0.012, 0.012, 0.0, 0.006])
    assert cost["noise"]["s"] == pytest.approx(s) and cost["noise"]["T"] == pytest.approx(s)


def test_a_judge_measure_whose_aa_never_moved_has_no_reference():
    g = ec.compare([A, AA, B], A, B, PRICES)["groups"]["all"]
    assert g["untrue.n"]["noise"]["label"] == ec.NO_REF and g["untrue.n"]["noise"]["ref"] == "c1 vs c0"


def test_an_averaged_comparison_falls_back_to_a_single_judging_reference_as_conservative():
    a2 = _cp("c3", _letters([8, 7, 9, 6]), judgings=2, run="r5")
    b2 = _cp("c4", _letters([9, 7, 8, 8]), judgings=2, run="r6")
    g = ec.compare([A, AA, B, a2, b2], a2, b2, PRICES)["groups"]["all"]
    assert g["points"]["noise"]["conservative"] is True and g["points"]["noise"]["ref"] == "c1 vs c0"
    assert "conservative: reference from single judgings" in ec.label_text(g["points"]["noise"])
    # a 2-judging A/A, once it exists, is used instead and is not marked
    aa2 = _cp("c5", _letters([8.5, 7, 9, 6]), judgings=2, run="r5", aa_of="c3", parent="c3", kept=False)
    g = ec.compare([A, AA, B, a2, b2, aa2], a2, b2, PRICES)["groups"]["all"]
    assert g["points"]["noise"]["conservative"] is False and g["points"]["noise"]["ref"] == "c5 vs c3"
    # a single-judging comparison never borrows a less noisy averaged reference
    assert ec.noise_refs([a2, aa2], A["comparable"], PRICES)[ec.JUDGE] is None


def test_a_replaced_aa_is_not_a_reference():
    fix = dict(_cp("c3", _letters([8, 7, 9, 6]), run="r1"), replaces="c1")
    assert ec.noise_refs([A, AA, B, fix], A["comparable"], PRICES)[ec.JUDGE] is None


# ---------------------------------------------------------------------------
# Cost at one price table (plan §3.4, D7)
# ---------------------------------------------------------------------------
def test_cost_is_recomputed_at_todays_prices_and_a_change_is_noted():
    row = A["letters"][0]
    assert ec.cost_at(row, PRICES) == pytest.approx(row["cost_usd"])  # 5000 x 2 + 1000 x 0.2 + 8000 x 12
    doubled = dict(PRICES, **{"gemini-pro": (4.0, 24.0, 0.4)})
    assert ec.cost_at(row, doubled) == pytest.approx(2 * row["cost_usd"])
    cmp = ec.compare([A, B], A, B, doubled)
    st = cmp["groups"]["all"]
    assert st["cost_today"]["a"] == pytest.approx(2 * st["cost_usd"]["a"])
    assert cmp["price_changes"] == [("c0", "gemini-pro", [2.0, 12.0, 0.2], [4.0, 24.0, 0.4]),
                                    ("c2", "gemini-pro", [2.0, 12.0, 0.2], [4.0, 24.0, 0.4])]
    assert ec.compare([A, B], A, B, PRICES)["price_changes"] == []
    assert ec.cost_at(row, {}) is None  # no price today: unknown, never guessed


def test_a_price_change_between_two_checkpoints_is_listed():
    b = _cp("c3", _letters([9, 7, 8, 8]), run="r3", prices=(4.0, 24.0, 0.4))
    assert ("prices_seen.gemini-pro", [2.0, 12.0, 0.2], [4.0, 24.0, 0.4]) in ec.settings_diff(A, b)


def test_task_table_per_letter_at_todays_prices():
    rows = ec.task_table(ec.pair(A, B)["pairs"], PRICES)
    assert [r["task"] for r in rows] == ["generate_letter"]
    r = rows[0]
    assert r["a_cost"] == pytest.approx(A["letters"][0]["cost_usd"])
    assert r["b_cost"] == pytest.approx(B["letters"][0]["cost_usd"])
    assert r["a_s"] == pytest.approx(50.0) and r["b_s"] == pytest.approx(60.0) and r["a_calls"] == 1


# ---------------------------------------------------------------------------
# compare with one or two ids (D8) and measure checkpoints
# ---------------------------------------------------------------------------
def test_one_id_compares_with_its_parent():
    a, b = ec.resolve_pair([A, AA, B], "c2")
    assert (a["id"], b["id"]) == ("c0", "c2")
    a, b = ec.resolve_pair([A, AA, B], "c1", "c2")
    assert (a["id"], b["id"]) == ("c1", "c2")
    with pytest.raises(ec.CheckpointError, match="c0 has no parent"):
        ec.resolve_pair([A], "c0")
    with pytest.raises(ec.CheckpointError, match="no checkpoint c9"):
        ec.resolve_pair([A], "c0", "c9")


def test_measure_checkpoints_compare_on_shared_measures_only():
    m1 = ec.build_measure_checkpoint([], experiment="E1", values={"G": 5, "U": 2}, commit="abc1234", kept=True,
                                     created="2026-11-25T10:00:00")
    m2 = ec.build_measure_checkpoint([m1], experiment="E1", values={"G": 3, "S": 162}, commit="abc1234",
                                     kept=True, created="2026-11-25T10:00:00")
    cmp = ec.compare([m1, m2], m1, m2, PRICES)
    assert cmp["measures"] == [("G", 5.0, 3.0, -2.0)] and cmp["only_a"] == ["U"] and cmp["only_b"] == ["S"]
