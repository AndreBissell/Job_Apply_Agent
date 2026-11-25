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
                                "prices_seen")
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
    aa = _build([c0, c1], aa_of="c0", kept=False)
    assert aa["parent"] == "c0" and aa["aa_of"] == "c0"
    with pytest.raises(ec.CheckpointError, match="parent is the one it repeats"):
        _build([c0, c1], aa_of="c0", parent="c1", kept=False)
    # an A/A is never a default parent, even when marked kept
    aa_kept = _build([c0], aa_of="c0", kept=True)
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
