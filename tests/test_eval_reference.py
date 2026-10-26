"""scripts/eval_reference.py: the pattern rules that correct the reference checklist, and
applying confirmed corrections. Fake requirement rows only; no DB, no LLM."""
import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load():
    saved_env = {k: os.environ.get(k) for k in ("DATABASE_URL", "APP_ENV")}
    saved_path = list(sys.path)
    try:
        spec = importlib.util.spec_from_file_location("eval_reference_under_test",
                                                      ROOT / "scripts" / "eval_reference.py")
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


ref = _load()


def row(text, importance="essential", role="headline", status="partial",
        evidence=("experience:1#s1",), rid="R1"):
    return {"id": rid, "text": text, "importance": importance, "letter_role": role,
            "status": status, "evidence": list(evidence)}


INTERN = (3, ["Software Engineering Intern"])


@pytest.mark.parametrize("months,years,want", [
    (3, 2, "gap"), (11, 2, "gap"), (12, 2, "partial"), (36, 5, "partial"),
    (23, 2, "partial"), (24, 2, "supported"), (72, 5, "supported"),
])
def test_years_ratio(months, years, want):
    assert ref.years_status(months, years) == want


@pytest.mark.parametrize("text", [
    "5+ years of commercial software development experience",
    "At least 2-3 years' experience in software development",
    "2–5 years' experience in quality assurance",
    "Around 3+ years’ experience in software engineering, support or a similar role",
])
def test_years_ask_against_an_internship_is_a_gap_that_keeps_its_evidence(text):
    change = ref.rule_years(row(text, evidence=["experience:1#s1", "skill:3"]), *INTERN)
    assert change == {"status": "gap", "evidence": [],
                      "related_evidence": ["experience:1#s1", "skill:3"]}


def test_a_range_is_judged_on_its_lower_number():
    # 18 months against "2-3 years" is 0.75 of 2 years: partial, already the status
    assert ref.rule_years(row("2-3 years' experience with Python"), 18, ["Developer"]) is None
    assert ref.rule_years(row("2-3 years' experience with Python", status="gap"), 18, ["Developer"]) \
        == {"status": "partial"}


def test_enough_years_lifts_to_supported_but_not_without_evidence():
    assert ref.rule_years(row("2+ years' experience"), 30, ["Developer"]) == {"status": "supported"}
    assert ref.rule_years(row("2+ years' experience", status="gap", evidence=()), 30, ["Developer"]) is None


def test_years_that_are_one_branch_of_an_or_are_left_alone():
    text = "A degree in software development with project experience, or 1-2 years' commercial experience"
    assert ref.rule_years(row(text, status="supported"), *INTERN) is None


def test_eligibility_rows_are_never_touched():
    assert ref.rule_years(row("Available for a 2 years contract", role="not_for_letter"), *INTERN) is None


def test_seniority_goes_by_job_titles_held():
    text = "For senior and lead positions: owned a production platform end to end"
    assert ref.rule_years(row(text), *INTERN)["status"] == "gap"
    assert ref.rule_years(row(text), 12, ["Senior Software Engineer"]) == {"status": "supported"}


@pytest.mark.parametrize("text", [
    "Ability to work independently and collaboratively with the Lead Developer and wider team",
    "Work alongside senior engineers on real problems",
    "Lead the design of new features",
])
def test_a_senior_colleague_or_the_verb_lead_is_not_a_seniority_ask(text):
    assert ref.rule_years(row(text, status="supported"), *INTERN) is None


def test_support_rule_drops_non_technical_evidence():
    r = row("Talk to clients on the phone in a Help Desk environment",
            evidence=["experience:1#s4", "experience:2#s1", "experience:2"])
    assert ref.rule_support(r, ["experience:2"]) == {"evidence": ["experience:1#s4"]}
    only = row("Provide IT support to staff", evidence=["experience:2#s1"])
    assert ref.rule_support(only, ["experience:2"]) == {"status": "gap", "evidence": []}
    # experience:20 is not under experience:2
    assert ref.rule_support(row("IT support", evidence=["experience:20#s1"]), ["experience:2"]) is None
    # soft skills may cite any job
    assert ref.rule_support(row("Strong communication", evidence=["experience:2#s2"]), ["experience:2"]) is None


def test_duty_rule_only_takes_essential_letter_facing_gaps_worded_as_duties():
    duty = row("Interrogate Acme's live pricing systems", status="gap", evidence=())
    assert ref.rule_duty(duty) == {"importance": "important"}
    assert ref.rule_duty(row("Angular 2+ (primary framework)", status="gap", evidence=())) is None
    assert ref.rule_duty({**duty, "importance": "important"}) is None
    assert ref.rule_duty({**duty, "status": "partial"}) is None
    assert ref.rule_duty({**duty, "letter_role": "implied"}) is None


def test_apply_changes_only_accepted_rows_and_keeps_the_original():
    reqs = [row("5+ years", rid="R1"), row("Python", rid="R2", status="supported")]
    proposals = [
        {"req_id": "R1", "set": {"status": "gap", "evidence": []}, "was": {"status": "partial",
         "evidence": ["experience:1#s1"]}, "sources": [{"pattern": "a"}], "decision": "yes"},
        {"req_id": "R2", "set": {"status": "partial"}, "was": {"status": "supported"},
         "sources": [{"pattern": "a"}], "decision": "no"},
    ]
    out = ref.apply(reqs, proposals)
    assert out[0]["status"] == "gap" and out[0]["stage3"]["status"] == "partial"
    assert out[0]["corrections"] == [{"pattern": "a"}]
    assert out[1] == reqs[1]
    assert reqs[0]["status"] == "partial"  # the source rows are not mutated


def test_counts_leave_related_evidence_out_of_backing():
    rows = [
        {**row("5+ years", rid="R1", status="gap", evidence=()), "related_evidence": ["skill:1"]},
        row("Python", rid="R2", status="supported", evidence=["experience:1#s1"]),
        row("SQL", rid="R3", importance="important", role="mention", evidence=["skill:2"]),
    ]
    c = ref.counts(rows)
    assert (c["supported"], c["partial"], c["gap"]) == (1, 1, 1)
    assert c["listing_only"] == 1  # R3 only; R1's related skill is not backing
    assert c["must_cover"] == 1 and c["must_have_gaps"] == 1
    assert c["m2_weight_total"] == 4 + 2


def test_two_sources_that_disagree_are_refused():
    p = {"status": "gap"}
    ref._merge(p, {"status": "gap", "evidence": []}, "x")
    with pytest.raises(SystemExit):
        ref._merge(p, {"status": "partial"}, "x")


def test_keep_decisions_only_when_the_change_is_identical():
    old = [{"id": "1:R1", "set": {"status": "gap"}, "decision": "yes"},
           {"id": "1:R2", "set": {"status": "gap"}, "decision": "no"}]
    new = [{"id": "1:R1", "set": {"status": "gap"}, "decision": None},
           {"id": "1:R2", "set": {"status": "partial"}, "decision": None}]
    out = ref.keep_decisions(new, old)
    assert [p["decision"] for p in out] == ["yes", None]


def _split_and_stage3():
    split = {"k1": {"job_id": 7, "side": "dev", "scenario": 6, "title": "Developer", "company": "Acme"}}
    stage3 = {"k1": {"run_id": 1, "requirements": [
        row("At least 2-3 years' experience in software development, including C# and HTML",
            evidence=["experience:1#s2", "skill:5"], rid="R1"),
        row("Python", rid="R2", status="supported"),
    ]}}
    return split, stage3


def test_an_owner_split_replaces_the_pattern_and_adds_the_row_after_its_parent():
    split, stage3 = _split_and_stage3()
    inputs = {"non_technical_evidence": [], "no_change": [], "corrections": [{
        "job_id": 7, "id": "R1", "owner": "split", "why": "two asks in one line",
        "set": {"text": "At least 2-3 years' experience in software development", "status": "gap",
                "evidence": [], "related_evidence": ["experience:1#s2"]},
        "add": [{"id": "R1b", "text": "C# and HTML", "importance": "essential", "letter_role": "headline",
                 "status": "supported", "evidence": ["experience:1#s2"]}]}]}
    [p] = ref.propose(split, stage3, inputs, (3, ["Intern"]))
    assert p["set"]["related_evidence"] == ["experience:1#s2"]  # the owner's, not the pattern's
    assert p["sources"][0]["replaced_by_owner"] and "owner" in p["sources"][1]
    p["decision"] = "yes"
    out = ref.apply(stage3["k1"]["requirements"], [p])
    assert [r["id"] for r in out] == ["R1", "R1b", "R2"]
    assert out[0]["status"] == "gap" and out[0]["stage3"]["status"] == "partial"
    assert out[1]["split_from"] == "R1" and out[1]["status"] == "supported"
    assert ref.counts(out)["m2_weight_total"] == 4 + 4  # R1b and R2; the years row is a gap


def test_a_split_row_must_be_complete_and_use_a_new_id():
    split, stage3 = _split_and_stage3()
    bad = {"id": "R2", "text": "x", "importance": "essential", "letter_role": "headline",
           "status": "supported", "evidence": []}
    inputs = {"non_technical_evidence": [], "no_change": [], "corrections": [
        {"job_id": 7, "id": "R1", "owner": "x", "why": "x", "set": {}, "add": [bad]}]}
    with pytest.raises(SystemExit):
        ref.propose(split, stage3, inputs, (3, ["Intern"]))


def test_text_may_only_change_in_a_split():
    split, stage3 = _split_and_stage3()
    inputs = {"non_technical_evidence": [], "no_change": [], "corrections": [
        {"job_id": 7, "id": "R2", "owner": "x", "why": "x", "set": {"text": "Python 3"}}]}
    with pytest.raises(SystemExit):
        ref.propose(split, stage3, inputs, (3, ["Intern"]))
