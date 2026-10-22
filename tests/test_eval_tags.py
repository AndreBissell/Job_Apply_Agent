"""scripts/eval_tags.py (the scenario rule) and scripts/eval_tag.py (writing the final tags
into the candidates sheet without touching the owner's columns). No DB, no LLM."""
import csv
import importlib.util
import os
import sys
from pathlib import Path

import pytest

from app.llm.letter.state import Requirement, UserDecision

ROOT = Path(__file__).resolve().parent.parent


def _load(name, file):
    saved_env = {k: os.environ.get(k) for k in ("DATABASE_URL", "APP_ENV")}
    saved_path = list(sys.path)
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / file)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod  # dataclasses look their module up while the class is built
        spec.loader.exec_module(mod)
    finally:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        sys.path[:] = saved_path
    return mod


tags = _load("eval_tags_under_test", "eval_tags.py")
tag_script = _load("eval_tag_under_test", "eval_tag.py")


def req(i, importance="essential", role="headline", status="supported", evidence=("experience:1#s1",),
        skill="", decided=False):
    return Requirement(id=f"R{i}", text=f"requirement {i}", importance=importance, letter_role=role,
                       status=status, evidence=list(evidence), skill=skill or f"Skill{i}",
                       user_decision=UserDecision(choice="leave_out", answer="remembered", remembered=True)
                       if decided else None)


MUST_GAP = dict(status="gap", evidence=())
LISTED = dict(evidence=("skill:7",))


@pytest.mark.parametrize("score, reqs, expected", [
    (39, [req(1, **MUST_GAP)], 7),                                    # 7 first, whatever else
    (85, [req(1)], 1),
    (90, [req(1), req(2, importance="nice_to_have", status="partial")], 2),
    (90, [req(1, **MUST_GAP), req(2, importance="nice_to_have", status="gap", evidence=())], 3),
    (90, [req(1, **LISTED)], 1),                                      # listing-only never beats 85+
    (90, [req(1, role="not_for_letter", **MUST_GAP)], 1),             # eligibility isn't a must-have gap
    (90, [req(1, importance="important", **MUST_GAP)], 1),            # nor is a non-essential gap
    (75, [req(1, **MUST_GAP), req(2, **LISTED)], 6),                  # borderline before 4 and 3
    (45, [req(1, **MUST_GAP), req(2, **LISTED)], 4),                  # listing-only before 3, no floor
    (55, [req(1, **MUST_GAP)], 3),
    (55, [req(1)], None),
])
def test_first_match_wins_in_the_plan_order(score, reqs, expected):
    assert tag_script.tag(score, reqs, count_decided=True)[0] == expected


def test_listing_floor_is_only_for_the_provisional_tags():
    assert tags.scenario(45, must_gap=True, nice_unsupported=False, listing_only=True) == 4
    assert tags.scenario(45, must_gap=True, nice_unsupported=False, listing_only=True, listing_min_score=60) == 3


def test_listing_floor_option_reaches_the_rule():
    reqs = [req(1, **MUST_GAP), req(2, **LISTED)]
    assert tag_script.tag(45, reqs, True)[0] == 4
    assert tag_script.tag(45, reqs, True, listing_floor=60)[0] == 3
    assert tag_script.tag(45, [req(2, **LISTED)], True, listing_floor=60)[0] is None


def test_a_remembered_no_counts_only_when_asked():
    reqs = [req(1, decided=True, skill="Kotlin", **MUST_GAP)]
    assert not reqs[0].needs_user
    assert tag_script.tag(90, reqs, count_decided=True) == (
        3, 'must-have missing: Kotlin (remembered "no": Kotlin)')
    assert tag_script.tag(90, reqs, count_decided=False)[0] == 1


def test_why_names_the_deciding_claims():
    assert tag_script.tag(90, [req(1, importance="nice_to_have", status="gap", evidence=(), skill="AWS")],
                          True)[1] == "no must-have missing; nice-to-have missing: AWS (gap)"
    assert tag_script.tag(50, [req(1, skill="TypeScript", **LISTED)], True)[1] == "listing-only: TypeScript"
    assert tag_script.tag(88, [req(1, status="partial")], True)[1] == (
        "no must-have missing; every nice-to-have backed (1 essential partial)")


FIELDS = ["job_id", "scenario", "score", "title", "would_apply (Y/N)", "scenario_ok (Y/N)", "comment"]


def _sheet(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def test_write_sheet_fills_only_the_new_columns_and_backs_up(tmp_path):
    sheet = tmp_path / "candidates.csv"
    _sheet(sheet, [
        {"job_id": "12", "scenario": "1", "score": "95", "title": "Dev, \"Graduate\"", "would_apply (Y/N)": "Y",
         "scenario_ok (Y/N)": "", "comment": "café ✓"},
        {"job_id": "13", "scenario": "3", "score": "50", "title": "QA", "would_apply (Y/N)": "",
         "scenario_ok (Y/N)": "N", "comment": ""},
    ])
    original = sheet.read_bytes()
    backup = tag_script.write_sheet(sheet, {
        12: {"final_scenario": 2, "eval_score": 93, "why": "nice-to-have missing: AWS (gap)"},
        13: {"final_scenario": None, "eval_score": None, "why": "not tagged: no score"},
    }, tmp_path / "backups")
    assert backup.read_bytes() == original
    assert sheet.read_bytes().startswith(b"\xef\xbb\xbf")  # Excel still reads it as UTF-8
    with sheet.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert list(rows[0]) == FIELDS + ["final_scenario", "eval_score", "why"]
    assert rows[0]["would_apply (Y/N)"] == "Y" and rows[0]["comment"] == "café ✓"
    assert rows[0]["title"] == 'Dev, "Graduate"' and rows[1]["scenario_ok (Y/N)"] == "N"
    assert (rows[0]["final_scenario"], rows[0]["eval_score"]) == ("2", "93")
    assert (rows[1]["final_scenario"], rows[1]["why"]) == ("none", "not tagged: no score")


def test_write_sheet_leaves_a_locked_sheet_alone(tmp_path, monkeypatch):
    sheet = tmp_path / "candidates.csv"
    _sheet(sheet, [{"job_id": "12", "scenario": "1", "score": "95", "title": "Dev"}])
    original = sheet.read_bytes()

    def locked(*_a):
        raise PermissionError("in use")

    monkeypatch.setattr(tag_script.os, "replace", locked)
    with pytest.raises(SystemExit, match="locked"):
        tag_script.write_sheet(sheet, {12: {"final_scenario": 1, "eval_score": 95, "why": "x"}}, tmp_path / "b")
    assert sheet.read_bytes() == original and not (tmp_path / "candidates.csv.tmp").exists()
