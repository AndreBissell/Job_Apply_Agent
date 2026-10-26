"""scripts/letter_lab.py grade-sheet (docs/letter-metrics-plan.md §4): the owner's blind form and
--unblind. Fake letters and ads in a temp folder; no DB, no LLM."""
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
        spec = importlib.util.spec_from_file_location("letter_lab_grade_sheet_under_test",
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
RUN = "dev-agent-secret-name"
KEYS = ("acme-dev", "acme-data", "logico-ops")
FIELDS = ("key", "send_readiness", "untrue", "untrue_text", "generic", "missed_important", "weak_example",
          "sounds_ai", "no_real_reason", "too_long", "note")


@pytest.fixture
def work(tmp_path, monkeypatch):
    runs, frozen = tmp_path / "work" / "runs", tmp_path / "sets" / "v2"
    (runs / RUN / "letters").mkdir(parents=True)
    (frozen / "ads").mkdir(parents=True)
    results = [{"key": k, "title": "Junior Developer"} for k in KEYS] + [{"key": "acme-bad", "error": "boom"}]
    (runs / RUN / "run.json").write_text(json.dumps({"run_id": RUN, "results": results}), encoding="utf-8")
    for k in KEYS:
        (runs / RUN / "letters" / f"{k}.txt").write_text(
            f"Dear Acme team,\n\nAt LogiCo I built Python <pipelines> for {k}.\n\nKind regards,\nLiam Chen",
            encoding="utf-8")
        (frozen / "ads" / f"{k}.json").write_text(json.dumps({
            "title": "Junior Developer", "company": "Acme & Co", "location": "Brisbane",
            "raw_description": f"Acme wants Python for {k}."}), encoding="utf-8")
    monkeypatch.setattr(lab, "RUNS_DIR", runs)
    monkeypatch.setattr(lab, "FROZEN_DIR", frozen)
    return tmp_path / "work"


def sheet(run=RUN, only=None, new=False, seed=1):
    return lab.cmd_grade_sheet(argparse.Namespace(run_id=run, only=only, new=new, seed=seed, unblind=None))


def unblind(sheet_id):
    return lab.cmd_grade_sheet(argparse.Namespace(run_id=None, only=None, new=False, seed=None, unblind=sheet_id))


def the_sheet(work):
    (key,) = sorted((work / "grading-keys").glob("*.json"))
    return json.loads(key.read_text(encoding="utf-8"))


def write_grades(path, rows):
    """As Excel's "CSV UTF-8" saves it: with a BOM."""
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows({f: r.get(f, "") for f in FIELDS} for r in rows)


def test_owner_fields_are_the_ones_the_sheet_writes():
    assert lab._grading_panel().OWNER_FIELDS == FIELDS


def test_the_sheet_is_blind_shuffled_and_its_key_kept_apart(work):
    assert sheet() == 0
    key = the_sheet(work)
    folder = work / "grading" / key["sheet"]
    assert key["run"] == RUN and sorted(key["keys"]) == sorted(KEYS)  # the failed letter isn't on it
    assert sorted(p.name for p in folder.iterdir()) == ["grades-owner.csv", "sheet.html"]
    html = (folder / "sheet.html").read_text(encoding="utf-8")
    assert RUN not in html and "secret" not in html
    assert "&lt;pipelines&gt;" in html and "Acme &amp; Co" in html  # escaped, so a letter can't break the page
    assert all(html.index(k) < html.index(nxt) for k, nxt in zip(key["keys"], key["keys"][1:]))
    with (folder / "grades-owner.csv").open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    assert tuple(rows[0]) == FIELDS
    assert [r[0] for r in rows[1:]] == key["keys"]  # row n = letter n
    assert all(not any(r[1:]) for r in rows[1:])


def test_a_second_sheet_for_a_run_needs_new_and_never_reuses_a_folder(work):
    assert sheet() == 0
    first = the_sheet(work)["sheet"]
    csv_path = work / "grading" / first / "grades-owner.csv"
    write_grades(csv_path, [{"key": KEYS[0], "send_readiness": "3", "untrue": "N"}])
    started = csv_path.read_bytes()
    assert sheet() == 1
    assert sheet(new=True, seed=1) == 0  # the same seed would pick the same id: it must pick another
    assert len(list((work / "grading").iterdir())) == 2
    assert csv_path.read_bytes() == started


def test_only_takes_a_subset_and_refuses_an_unknown_key(work):
    assert sheet(only=["acme-nope"]) == 1
    assert sheet(only=[KEYS[1]]) == 0
    assert the_sheet(work)["keys"] == [KEYS[1]]


def test_v1_has_no_grade_sheet(work, monkeypatch):
    monkeypatch.setattr(lab, "FROZEN_DIR", None)
    assert sheet() == 1


def test_unblind_copies_graded_rows_and_names_the_started_ones(work, capsys):
    sheet()
    key = the_sheet(work)
    a, b, c = key["keys"]
    write_grades(work / "grading" / key["sheet"] / "grades-owner.csv", [
        {"key": a, "send_readiness": "3", "untrue": "N", "generic": "Y"},
        {"key": b, "send_readiness": "", "untrue": "Y", "untrue_text": "led a team"},
        {"key": c},
    ])
    assert unblind(key["sheet"]) == 0
    assert "not copied, started but no send_readiness: " + b in capsys.readouterr().out
    graded = lab._grading_panel().read_owner(work / "runs" / RUN / "grades-owner.csv")
    assert list(graded) == [a] and graded[a]["generic"] == "Y"


def test_unblind_never_changes_a_row_graded_differently_and_merges_another_sheet(work):
    sheet(only=[KEYS[0]])
    first = the_sheet(work)["sheet"]
    write_grades(work / "grading" / first / "grades-owner.csv", [{"key": KEYS[0], "send_readiness": "2", "untrue": "N"}])
    assert unblind(first) == 0
    assert sheet(only=[KEYS[0], KEYS[1]], new=True, seed=2) == 0
    (second,) = [p.name for p in (work / "grading").iterdir() if p.name != first]
    path = work / "grading" / second / "grades-owner.csv"
    write_grades(path, [{"key": KEYS[0], "send_readiness": "4", "untrue": "N"},
                        {"key": KEYS[1], "send_readiness": "3", "untrue": "N"}])
    dst = work / "runs" / RUN / "grades-owner.csv"
    before = dst.read_bytes()
    assert unblind(second) == 1  # KEYS[0] was graded 2 already
    assert dst.read_bytes() == before
    write_grades(path, [{"key": KEYS[0], "send_readiness": "2", "untrue": "N"},  # same grade: fine
                        {"key": KEYS[1], "send_readiness": "3", "untrue": "N"}])
    assert unblind(second) == 0
    graded = lab._grading_panel().read_owner(dst)
    assert {k: r["send_readiness"] for k, r in graded.items()} == {KEYS[0]: "2", KEYS[1]: "3"}


def test_unblind_refuses_changed_columns_or_bad_values(work):
    sheet()
    key = the_sheet(work)
    path = work / "grading" / key["sheet"] / "grades-owner.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows([["key", "send_readiness"], [key["keys"][0], "3"]])
    assert unblind(key["sheet"]) == 1
    write_grades(path, [{"key": key["keys"][0], "send_readiness": "5", "untrue": "N"}])
    with pytest.raises(SystemExit, match="send_readiness must be 1-4"):
        unblind(key["sheet"])
    assert not (work / "runs" / RUN / "grades-owner.csv").exists()
    assert unblind("S0000") == 1
