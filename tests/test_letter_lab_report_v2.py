"""scripts/letter_lab.py report --set v2: the headline scorecard (docs/letter-metrics-plan.md §3).
Fake ads, letters, judge answers and profile (Liam Chen, Acme, LogiCo, Initech) in a temp folder;
no DB, no LLM."""
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
        spec = importlib.util.spec_from_file_location("letter_lab_report_v2_under_test",
                                                      ROOT / "scripts" / "letter_lab.py")
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


lab = _load()
RUN = "dev-agent-v1"
ADS = {  # key -> (job_id, would_apply, company, title)
    "acme-dev": (1, "Y", "Acme Pty Ltd", "Secret Title Alpha"),
    "logico-ops": (2, "N", "LogiCo", "Secret Title Beta"),
    "initech-data": (3, "", "Initech", "Secret Title Gamma"),
}
PROFILE_TEXT = "Built Python pipelines for the LogiCo warehouse team every single week for two years"


def _letter(key, company):
    return (f"Dear {company} team,\n\nAt LogiCo I built Python pipelines for the warehouse team. This role is "
            f"exactly the kind of work I want to do next for {key}.\n\nI wrote SQL reports every week.\n\n"
            "Kind regards,\nLiam Chen")


def _ref(key):
    rows = [
        {"id": "R1", "text": "Python", "importance": "essential", "letter_role": "headline", "status": "supported",
         "evidence": ["experience:1#s1"], "skill": "Python"},
        {"id": "R2", "text": "SQL", "importance": "important", "letter_role": "mention", "status": "supported",
         "evidence": ["skill:3"], "skill": "SQL"},
    ]
    return {"key": key, "requirements": rows}


def _judge(fabricated=0):
    return {"send_readiness": 3, "untrue": "N",
            "counts": {"supported": 2, "stretch": 0, "fabricated": fabricated, "employer_fact_not_in_ad": 0,
                       "gap_fabrications": 0},
            "checklist": {d: "Y" for d in ("D2a", "D2b", "D2c", "D3a", "D3b", "D4a", "D5a", "D5b", "D5c", "D6a",
                                           "D6b", "D6c", "D6d", "D6e", "D6f", "D6g", "D6h", "D7a")},
            "requirements": [
                {"id": "R1", "addressed": "Y", "quote": "built Python pipelines", "presented_as": "experience",
                 "evidence_used": ["experience:1#s1"]},
                {"id": "R2", "addressed": "Y", "quote": "I wrote SQL reports", "presented_as": "skill",
                 "evidence_used": ["skill:3"]}]}


@pytest.fixture
def work(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "path", list(sys.path))  # importing eval_reference changes both
    for k in ("DATABASE_URL", "APP_ENV"):
        if k in os.environ:
            monkeypatch.setenv(k, os.environ[k])
        else:
            monkeypatch.delenv(k, raising=False)
    frozen, runs = tmp_path / "sets" / "v2", tmp_path / "work" / "runs"
    (frozen / "ads").mkdir(parents=True)
    (runs / RUN / "letters").mkdir(parents=True)
    (tmp_path / "work" / "reference" / "ads").mkdir(parents=True)
    (frozen / "split.json").write_text(json.dumps({"dev": [
        {"key": k, "job_id": jid, "scenario": 1} for k, (jid, *_) in ADS.items()]}), encoding="utf-8")
    cands = tmp_path / "candidates.csv"
    with cands.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["job_id", "would_apply (Y/N)"])
        w.writerows([jid, would] for jid, would, *_ in ADS.values())
    results = []
    for k, (_, _, company, title) in ADS.items():
        (frozen / "ads" / f"{k}.json").write_text(json.dumps({
            "title": title, "company": company, "raw_description": "We want someone to build Python pipelines."}),
            encoding="utf-8")
        (runs / RUN / "letters" / f"{k}.txt").write_text(_letter(k, company), encoding="utf-8")
        (tmp_path / "work" / "reference" / "ads" / f"{k}.json").write_text(json.dumps(_ref(k)), encoding="utf-8")
        results.append({"key": k, "cost_usd": 0.2, "seconds": 100})
    results.append({"key": "acme-bad", "error": "boom"})
    (runs / RUN / "run.json").write_text(json.dumps({"run_id": RUN, "engine": "agent", "strong_model": "m",
                                                     "created_at": "2026-10-26", "split": "dev",
                                                     "results": results}), encoding="utf-8")
    (runs / RUN / "judge-t1.json").write_text(json.dumps({"acme-dev": _judge(), "logico-ops": _judge(fabricated=1)}),
                                              encoding="utf-8")
    with (runs / RUN / "grades-owner.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=lab._grading_panel().OWNER_FIELDS)
        w.writeheader()
        w.writerow({"key": "logico-ops", "send_readiness": "4", "untrue": "N"})
    monkeypatch.setattr(lab, "FROZEN_DIR", frozen)
    monkeypatch.setattr(lab, "RUNS_DIR", runs)
    monkeypatch.setattr(lab, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(lab, "CANDIDATES", cands)
    monkeypatch.setattr(lab, "PANELS_DIR", tmp_path / "panels")
    monkeypatch.setattr(lab, "_v2_profile_inputs", lambda: {
        "name": "Liam Chen", "evidence": [PROFILE_TEXT], "writing_sample": "The part I enjoyed most was the testing.",
        "skills": ["Python", "SQL"], "org_names": [], "project_titles": [], "exp_kinds": {"1": "work"}, "job_skills": {}})
    return tmp_path


def report(judge=None, picks=None):
    return lab.cmd_report(argparse.Namespace(run_id=RUN, grades="grades.csv", judge=judge, picks=picks))


def outputs(tmp):
    return ((tmp / "results" / f"{RUN}.md").read_text(encoding="utf-8"),
            (tmp / "work" / "runs" / RUN / "report-detail.md").read_text(encoding="utf-8"))


def test_the_committed_file_holds_numbers_and_ad_keys_only(work):
    assert report() == 0
    committed, detail = outputs(work)
    for private in ("Secret Title", "Pty Ltd", "Initech", "warehouse", "Liam", "exactly the kind of", "enjoyed most"):
        assert private not in committed
    assert "Secret Title Alpha" in detail and "exactly the kind of work" in detail
    assert "C4 cross-letter repetition: **1** phrase(s)" in committed


def test_relevant_ads_come_first_and_the_owner_grade_wins(work):
    report()
    committed, _ = outputs(work)
    assert "| Measure | Relevant ads (1) | All ads (3) |" in committed
    table = [ln for ln in committed.splitlines() if ln.startswith("| `")]
    assert [ln.split("`")[1] for ln in table] == ["acme-dev", "initech-data", "logico-ops"]
    assert table[0].startswith("| `acme-dev` | 1 | Y | 3 | judge |")
    assert table[1].startswith("| `initech-data` | 1 | ? | — | — | — |")  # not judged, no label
    assert table[2].startswith("| `logico-ops` | 1 | N | 4 | owner | 1 |")
    assert "| 1. Send-readiness, mean 1-4 | 3.00 (owner 0, judge 1) | 3.50 (owner 1, judge 1) |" in committed
    assert ("| 2. Fabrication-free rate | 100% of 1 judged | 50% of 2 judged; owner: 1 of 1 with nothing untrue |"
            in committed)  # the owner graded logico-ops only, and said N
    assert "| 4. Honest coverage | 100% | 50% |" in committed  # logico-ops has a fabrication
    assert "| 6. Cost per letter | $0.200 | $0.200 |" in committed


def test_the_judge_file_is_picked_or_must_be_named(work):
    runs = work / "work" / "runs" / RUN
    (runs / "judge-t2.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit, match="--judge t1 / t2"):
        report()
    assert report(judge="t2") == 0
    assert "Judge: `t2` (0 letters)" in outputs(work)[0]
    with pytest.raises(SystemExit, match="No judge-t9.json"):
        report(judge="t9")


def test_picks_add_the_win_rate_for_this_run_only(work):
    (work / "panels" / "p1").mkdir(parents=True)
    picks = {"baseline": "dev-base", "new": RUN, "ads": 3, "new_wins": 2, "baseline_wins": 0, "ties": 1,
             "win_rate": 0.833, "sign_test_p": 0.5, "per_ad": {}}
    (work / "panels" / "p1" / "picks.json").write_text(json.dumps(picks), encoding="utf-8")
    assert report(picks="p1") == 0
    assert f"**A/B win rate** of `{RUN}` over `dev-base` (picks `p1`, 3 ads): **83%**" in outputs(work)[0]
    picks["new"] = "other-run"
    (work / "panels" / "p1" / "picks.json").write_text(json.dumps(picks), encoding="utf-8")
    with pytest.raises(SystemExit, match="not dev-agent-v1"):
        report(picks="p1")
