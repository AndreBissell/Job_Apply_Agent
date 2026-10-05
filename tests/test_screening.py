"""Tests for the Quick Apply question bank (plan §10.1, Phase 9a): app/screening/{identity,
sort,bank}.py and app/api/screening.py.

The 22 real sampled questions S1-S5 live in tests/fixtures/quick_apply_samples.json, each
with the sorting recorded in docs/quick-apply-samples.md (kind 'unknown' = needs the model).

No network and no LLM. In-memory SQLite only (get_db is overridden), never real.db / app.db.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.main import app
from app.api.profile_ui import get_db
from app.db import Base
from app.models import JobListing, JobScreeningQuestion, ScreeningQuestion
from app.screening import bank
from app.screening.bank import CapturedOption, CapturedQuestion, CorrectionError
from app.screening.identity import (
    clean_text, fingerprint, id_family, identity_key, normalise_text, parse_library_id,
)
from app.screening.sort import (
    ASSISTED_STRATEGIES, by_keyword, by_library_id, by_template, sort_question,
)

FIXTURE = Path(__file__).parent / "fixtures" / "quick_apply_samples.json"
SAMPLES = json.loads(FIXTURE.read_text(encoding="utf-8"))["samples"]
SAMPLE_BY_ID = {s["id"]: s for s in SAMPLES}
ALL_QUESTIONS = [(s["id"], i, q) for s in SAMPLES for i, q in enumerate(s["questions"])]


def _labels(q: dict) -> list[str]:
    return [o["label"].strip() for o in q["options"]]


def _find(sample_id: str, fragment: str) -> dict:
    return next(q for q in SAMPLE_BY_ID[sample_id]["questions"] if fragment in q["text"])


def _captured(q: dict) -> CapturedQuestion:
    return CapturedQuestion(
        seek_question_id=q["seek_question_id"], field_name=q["field_name"], text=q["text"],
        input_type=q["input_type"], options=[CapturedOption(o["value"], o["label"]) for o in q["options"]],
    )


def _payload(sample_id: str) -> dict:
    return {"questions": [
        {k: q[k] for k in ("seek_question_id", "field_name", "text", "input_type", "options")}
        for q in SAMPLE_BY_ID[sample_id]["questions"]
    ]}


# --- fixtures ----------------------------------------------------------------------
@pytest.fixture()
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(eng, "connect")
    def _fk_on(conn, _):
        if isinstance(conn, sqlite3.Connection):
            conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture()
def client(engine):
    Session = sessionmaker(bind=engine)

    def _db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def db(engine):
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _make_job(db, n: int = 1) -> int:
    job = JobListing(source="seek", source_job_id=f"job-{n}", url=f"https://example.test/job/{n}",
                     title=f"Job {n}")
    db.add(job)
    db.commit()
    return job.id


@pytest.fixture()
def jobs(db):
    """Five job ids, one per sample S1..S5."""
    return {s["id"]: _make_job(db, i) for i, s in enumerate(SAMPLES, start=1)}


def _record_sample(db, jobs, sample_id: str) -> list[dict]:
    qs = [_captured(q) for q in SAMPLE_BY_ID[sample_id]["questions"]]
    return bank.record_job_questions(db, jobs[sample_id], qs)


# --- 1. identity -------------------------------------------------------------------
class TestIdentity:
    @pytest.mark.parametrize("sid, family", [
        ("AU_Q_6_V_10", "library"),
        ("AU_Q_218_V_2", "library"),
        ("AU_Q_D29EC90383D57C663E2D2A43F205280B_V_2", "generated"),
        ("AU_Q_d29ec90383d57c663e2d2a43f205280b_V_12", "generated"),
        ("indirect_3f6c2a10-8b1e-4c55-9d2a-0a1b2c3d4e5f_0", "employer"),
        ("indirect_7d4e9b22-1c3a-4f60-8e11-5a6b7c8d9e0f_a1", "employer"),
        ("something_else", "other"),
        ("AU_Q_6", "other"),
        ("AU_Q_6_V_x", "other"),
        ("", "other"),
    ])
    def test_id_family(self, sid, family):
        assert id_family(sid) == family

    def test_id_family_of_every_fixture_id_is_a_known_shape(self):
        for _, _, q in ALL_QUESTIONS:
            assert id_family(q["seek_question_id"]) in ("library", "generated", "employer")

    def test_parse_library_id(self):
        assert parse_library_id("AU_Q_6_V_10") == ("AU_Q_6", "10")
        assert parse_library_id("AU_Q_136_V_3") == ("AU_Q_136", "3")
        assert parse_library_id("AU_Q_D29EC90383D57C663E2D2A43F205280B_V_2") is None
        assert parse_library_id("indirect_a_b") is None
        assert parse_library_id("junk") is None

    def test_clean_text(self):
        assert clean_text("  Yes   ") == "Yes"
        assert clean_text("What’s “this”?") == 'What\'s "this"?'
        assert clean_text(None) == ""

    def test_normalise_text(self):
        # curly quotes, runs of whitespace, case, and S5's trailing " ?"
        assert normalise_text("What’s   YOUR  Salary?") == "what's your salary"
        assert normalise_text("targeting web, iOS and Android ?") == "targeting web, ios and android"
        assert normalise_text("Why?!  ") == "why"
        assert normalise_text("What’s up") == normalise_text("What's up")

    def test_s5_trailing_space_question_mark_matches_plain_form(self):
        q = _find("S5", "React Native with Expo")
        assert normalise_text(q["text"]) == normalise_text(q["text"].rstrip(" ?"))
        assert normalise_text(q["text"]).endswith("android")

    def test_fingerprint_is_32_hex_and_stable(self):
        fp = fingerprint("Are you happy?", "single", ["Yes", "No"])
        assert re.fullmatch(r"[0-9a-f]{32}", fp)
        assert fp == fingerprint("Are you happy?", "single", ["Yes", "No"])

    def test_fingerprint_insensitive_to_order_spaces_and_case(self):
        base = fingerprint("Are you happy?", "single", ["Yes", "No"])
        assert fingerprint("Are you happy?", "single", ["No", "Yes"]) == base
        assert fingerprint("Are you happy?", "single", ["Yes ", " No "]) == base
        assert fingerprint("ARE YOU HAPPY?", "single", ["yes", "NO"]) == base
        assert fingerprint("Are you happy ?", "single", ["Yes", "No"]) == base

    def test_fingerprint_differs_on_type_text_or_options(self):
        base = fingerprint("Are you happy?", "single", ["Yes", "No"])
        assert fingerprint("Are you happy?", "dropdown", ["Yes", "No"]) != base
        assert fingerprint("Are you sad?", "single", ["Yes", "No"]) != base
        assert fingerprint("Are you happy?", "single", ["Yes", "No", "Maybe"]) != base
        assert fingerprint("Are you happy?", "single", ["Yes", "Nope"]) != base

    def test_identity_key_library_ignores_version_and_text(self):
        a = identity_key("AU_Q_6_V_10", "Right to work?", "dropdown", ["a"])
        b = identity_key("AU_Q_6_V_11", "A reworded question", "dropdown", ["b", "c"])
        assert a == b == "lib:AU_Q_6"

    def test_identity_key_fingerprint_for_generated_and_employer(self):
        gen = identity_key("AU_Q_D29EC90383D57C663E2D2A43F205280B_V_2", "Years as X?", "dropdown", ["1"])
        emp = identity_key("indirect_aaa_bbb", "Why us?", "text", [])
        assert gen.startswith("fp:") and emp.startswith("fp:")
        assert len(gen) == 3 + 32

    def test_two_employer_ids_with_identical_content_share_a_key(self):
        a = identity_key("indirect_11111111-1111-1111-1111-111111111111_0", "Why us?", "text", [])
        b = identity_key("indirect_22222222-2222-2222-2222-222222222222_0", "Why us?", "text", [])
        assert a == b

    def test_generated_ids_of_different_roles_get_different_keys(self):
        a = identity_key("AU_Q_" + "A" * 32 + "_V_2", "How many years' experience do you have as a nurse?", "dropdown", ["1"])
        b = identity_key("AU_Q_" + "B" * 32 + "_V_2", "How many years' experience do you have as a chef?", "dropdown", ["1"])
        assert a != b


# --- 2. sorting --------------------------------------------------------------------
def _expected_id(sample_id, idx, q):
    return f"{sample_id}-Q{idx + 1}"


class TestSortQuestion:
    @pytest.mark.parametrize("sample_id, idx, q", ALL_QUESTIONS,
                             ids=[_expected_id(*t) for t in ALL_QUESTIONS])
    def test_every_sample_sorts_as_recorded(self, sample_id, idx, q):
        got = sort_question(q["seek_question_id"], q["text"], q["input_type"], _labels(q))
        exp = q["expected"]
        if exp["kind"] == "unknown":
            assert got is None
            return
        assert got is not None
        assert got.kind == exp["kind"]
        assert got.strategy == exp["strategy"]
        assert got.classified_by == exp["classified_by"]
        assert got.parameters == exp["parameters"]

    def test_exactly_one_sample_is_unsorted(self):
        unknown = [q for _, _, q in ALL_QUESTIONS if q["expected"]["kind"] == "unknown"]
        assert len(ALL_QUESTIONS) == 22 and len(unknown) == 1
        assert "React Native with Expo" in unknown[0]["text"]

    # layers, one at a time
    def test_layer_library_id(self):
        q = _find("S2", "right to work")
        got = by_library_id(q["seek_question_id"], q["text"])
        assert (got.kind, got.strategy, got.classified_by, got.parameters) == (
            "user", "user", "library_id", {"topic": "work_rights"})

    def test_layer_library_id_skill_parameters_come_from_text(self):
        q = _find("S2", "C# development")
        got = by_library_id(q["seek_question_id"], q["text"])
        assert got.strategy == "skill_in_role_yes_no"
        assert got.parameters == {"skill": "C#", "phrase": "C# development"}

    def test_layer_library_id_ignores_non_library_and_unlisted_ids(self):
        assert by_library_id("AU_Q_6804_V_4", "How many years' experience do you have as a nurse?") is None
        assert by_library_id("indirect_a_b", "Right to work?") is None
        assert by_library_id("AU_Q_" + "A" * 32 + "_V_2", "Salary?") is None

    def test_layer_library_id_does_not_mutate_the_shared_table(self):
        q = _find("S2", "C# development")
        by_library_id(q["seek_question_id"], q["text"])
        again = by_library_id("AU_Q_218_V_9", "Have you worked in a role which requires Python experience?")
        assert again.parameters == {"skill": "Python", "phrase": "Python"}

    def test_layer_keyword(self):
        got = by_keyword(_find("S1", "legally entitled")["text"])
        assert (got.kind, got.strategy, got.classified_by, got.parameters) == (
            "user", "user", "keyword", {"topic": "work_rights"})
        assert by_keyword("What are your salary expectations?").parameters == {"topic": "salary"}
        assert by_keyword("How did you hear about this job?").parameters == {"topic": "source"}

    def test_layer_template_years_role(self):
        q = _find("S4", "Front End React")
        got = by_template(q["text"], q["input_type"], _labels(q))
        assert (got.kind, got.strategy, got.classified_by) == ("assisted", "years_role_bracket", "template")
        assert got.parameters == {"role": "Front End React Developer"}

    def test_layer_template_skill_in_role_single(self):
        got = by_template("Have you worked in a role that requires Python experience?", "single", ["Yes", "No"])
        assert got.strategy == "skill_in_role_yes_no"
        assert got.parameters == {"skill": "Python", "phrase": "Python"}

    def test_layer_template_ignores_unsortable(self):
        q = _find("S5", "React Native with Expo")
        assert by_template(q["text"], q["input_type"], _labels(q)) is None

    # layer order
    def test_library_id_beats_keyword(self):
        q = _find("S2", "right to work")
        assert by_keyword(q["text"]) is not None  # the text alone would be caught by keyword
        got = sort_question(q["seek_question_id"], q["text"], q["input_type"], _labels(q))
        assert got.classified_by == "library_id"

    def test_keyword_beats_template(self):
        # A years-as-role wording that also names a user topic goes to keyword first.
        text = "How many years' experience do you have as a salary analyst?"
        got = sort_question("indirect_a_b", text, "dropdown", ["1", "2"])
        assert got.classified_by == "keyword" and got.parameters == {"topic": "salary"}

    # negatives the keyword layer must not catch
    def test_office_manager_is_template_not_keyword(self):
        text = "How many years' experience do you have as an Office Manager?"
        assert by_keyword(text) is None
        got = sort_question("AU_Q_" + "C" * 32 + "_V_2", text, "dropdown", ["No experience", "1 year"])
        assert (got.kind, got.strategy, got.classified_by) == ("assisted", "years_role_bracket", "template")
        assert got.parameters == {"role": "Office Manager"}

    def test_payroll_officer_is_template_not_keyword(self):
        text = "How many years' experience do you have as a payroll officer?"
        assert by_keyword(text) is None  # "pay" must not match inside "payroll"
        got = sort_question("AU_Q_" + "D" * 32 + "_V_2", text, "dropdown", ["No experience", "1 year"])
        assert got.strategy == "years_role_bracket" and got.parameters == {"role": "payroll officer"}

    def test_microsoft_office_is_unsorted(self):
        assert sort_question("indirect_a_b", "Have you worked with Microsoft Office?", "single", ["Yes", "No"]) is None

    def test_programming_languages_with_non_library_id_is_unsorted(self):
        text = "Which of the following programming languages are you experienced in?"
        assert sort_question("indirect_a_b", text, "multi", ["Python", "Java"]) is None

    # By design (by_template): the years template needs a closed answer list, so a free-text
    # years question or one with no options stays unsorted for the model.
    def test_years_template_does_not_fire_for_text_input(self):
        text = "How many years' experience do you have as a nurse?"
        assert sort_question("indirect_a_b", text, "text", []) is None

    def test_years_template_needs_options(self):
        text = "How many years' experience do you have as a nurse?"
        assert by_template(text, "dropdown", []) is None
        assert by_template(text, "multi", ["1", "2"]) is None

    def test_skill_in_role_template_only_for_single(self):
        text = "Have you worked in a role which requires Python experience?"
        assert by_template(text, "dropdown", ["Yes", "No"]) is None


# --- 3. bank upsert ----------------------------------------------------------------
def _bank_rows(db):
    return db.scalars(select(ScreeningQuestion).order_by(ScreeningQuestion.id)).all()


def _links(db, job_id):
    return db.scalars(select(JobScreeningQuestion).where(JobScreeningQuestion.job_id == job_id)
                      .order_by(JobScreeningQuestion.position)).all()


class TestBankUpsert:
    def test_five_jobs_dedupe_to_distinct_identities(self, db, jobs):
        for sid in ("S1", "S2", "S3", "S4", "S5"):
            _record_sample(db, jobs, sid)
        keys = set()
        for _, _, q in ALL_QUESTIONS:
            keys.add(identity_key(q["seek_question_id"], clean_text(q["text"]), q["input_type"],
                                  [clean_text(o["label"]) for o in q["options"] if clean_text(o["label"])]))
        rows = _bank_rows(db)
        assert len(rows) == len(keys)
        assert len(rows) == 22 - (2 + 1 + 1)  # AU_Q_6 x3, AU_Q_8 x2, AU_Q_13 x2 collapse
        assert len({r.identity_key for r in rows}) == len(rows)

    def test_times_seen_counts_distinct_jobs(self, db, jobs):
        for sid in ("S1", "S2", "S3", "S4", "S5"):
            _record_sample(db, jobs, sid)
        by_key = {r.identity_key: r for r in _bank_rows(db)}
        assert by_key["lib:AU_Q_6"].times_seen == 3   # S2, S3, S4
        assert by_key["lib:AU_Q_8"].times_seen == 2   # S2, S4
        assert by_key["lib:AU_Q_13"].times_seen == 2  # S2, S3
        assert by_key["lib:AU_Q_218"].times_seen == 1

    def test_first_capture_views(self, db, jobs):
        out = _record_sample(db, jobs, "S2")
        assert [v["position"] for v in out] == list(range(6))
        assert all(v["new_to_bank"] for v in out)
        assert [v["sorted_by"] for v in out] == [
            "library_id", "template", "library_id", "library_id", "library_id", "library_id"]
        assert out[0]["library_id"] == "AU_Q_6" and out[0]["library_version"] == "10"
        assert out[1]["parameters"] == {"role": "full stack developer"}

    def test_repeat_in_another_job_is_a_bank_hit(self, db, jobs):
        _record_sample(db, jobs, "S2")
        out = _record_sample(db, jobs, "S3")
        assert [v["sorted_by"] for v in out] == ["bank", "bank"]
        assert [v["new_to_bank"] for v in out] == [False, False]
        assert out[0]["kind"] == "user" and out[0]["classified_by"] == "library_id"

    def test_reposting_the_same_job_changes_nothing(self, db, jobs):
        _record_sample(db, jobs, "S2")
        rows_before = [(r.id, r.times_seen) for r in _bank_rows(db)]
        links_before = [(l.question_id, l.position) for l in _links(db, jobs["S2"])]
        out = _record_sample(db, jobs, "S2")
        assert [(r.id, r.times_seen) for r in _bank_rows(db)] == rows_before
        assert [(l.question_id, l.position) for l in _links(db, jobs["S2"])] == links_before
        assert db.scalar(select(func.count()).select_from(JobScreeningQuestion)) == 6
        assert all(v["sorted_by"] == "bank" and v["new_to_bank"] is False for v in out)

    def test_link_rows_keep_form_details(self, db, jobs):
        _record_sample(db, jobs, "S5")
        links = _links(db, jobs["S5"])
        assert [l.position for l in links] == [0, 1, 2, 3]
        first = links[0]
        src = SAMPLE_BY_ID["S5"]["questions"][0]
        assert first.seek_question_id == src["seek_question_id"]
        assert first.field_name == src["field_name"]
        values = json.loads(first.option_values)
        assert [set(v) for v in values] == [{"value", "label"}] * len(src["options"])
        assert [v["value"] for v in values] == [o["value"] for o in src["options"]]
        # labels trimmed ("Australian Citizen/ ... Citizen " has a trailing space in the source)
        assert [v["label"] for v in values] == [o["label"].strip() for o in src["options"]]
        assert any(o["label"] != o["label"].strip() for o in src["options"])
        # free-text question has no option_values
        assert links[3].option_values is None

    def test_bank_options_store_trimmed_labels_only(self, db, jobs):
        _record_sample(db, jobs, "S5")
        row = next(r for r in _bank_rows(db) if "in-office" in r.text)
        assert json.loads(row.options) == ["Yes", "No"]

    def test_no_answer_field_anywhere(self, db, jobs):
        for sid in ("S1", "S2", "S5"):
            _record_sample(db, jobs, sid)
        banned = {"answer", "answers", "selected", "checked", "value_entered"}
        assert not banned & set(ScreeningQuestion.__table__.columns.keys())
        assert not banned & set(JobScreeningQuestion.__table__.columns.keys())
        for link in db.scalars(select(JobScreeningQuestion)).all():
            for opt in json.loads(link.option_values or "[]"):
                assert set(opt) == {"value", "label"}

    def test_same_question_twice_in_one_capture_is_linked_once(self, db, jobs):
        q = _captured(_find("S2", "right to work"))
        dup = CapturedQuestion(q.seek_question_id, q.field_name, q.text, q.input_type, list(q.options))
        out = bank.record_job_questions(db, jobs["S1"], [q, dup])
        assert len(out) == 1
        assert len(_links(db, jobs["S1"])) == 1
        assert _bank_rows(db)[0].times_seen == 1

    def test_library_row_updates_version_text_and_options(self, db, jobs):
        q10 = CapturedQuestion("AU_Q_6_V_10", "questionnaire.AU_Q_6_V_10", "Right to work?", "dropdown",
                               [CapturedOption("a", "Citizen")])
        q11 = CapturedQuestion("AU_Q_6_V_11", "questionnaire.AU_Q_6_V_11", "What is your right to work?",
                               "dropdown", [CapturedOption("a", "Citizen"), CapturedOption("b", "Visa")])
        bank.record_job_questions(db, jobs["S1"], [q10])
        out = bank.record_job_questions(db, jobs["S2"], [q11])
        rows = _bank_rows(db)
        assert len(rows) == 1
        assert rows[0].library_version == "11"
        assert rows[0].text == "What is your right to work?"
        assert json.loads(rows[0].options) == ["Citizen", "Visa"]
        assert rows[0].times_seen == 2
        assert out[0]["sorted_by"] == "bank"
        # the per-job link keeps the id this form used
        assert _links(db, jobs["S1"])[0].seek_question_id == "AU_Q_6_V_10"
        assert _links(db, jobs["S2"])[0].seek_question_id == "AU_Q_6_V_11"

    def test_unknown_question_stays_unknown_and_new(self, db, jobs):
        out = _record_sample(db, jobs, "S5")
        unknown = out[3]
        assert unknown["kind"] == "unknown" and unknown["status"] == "new"
        assert unknown["classified_by"] is None and unknown["strategy"] is None
        assert unknown["sorted_by"] is None and unknown["new_to_bank"] is True
        # a repeat is still unknown, still not 'bank', still not classified
        again = _record_sample(db, jobs, "S5")[3]
        assert again["kind"] == "unknown" and again["sorted_by"] is None
        assert again["classified_by"] is None and again["new_to_bank"] is False

    def test_sorted_rows_start_as_status_new(self, db, jobs):
        out = _record_sample(db, jobs, "S1")
        assert all(v["status"] == "new" for v in out)

    def test_employer_questions_share_a_row_across_jobs(self, db, jobs):
        # Same text/type/options under two different employer ids -> one bank row.
        a = CapturedQuestion("indirect_aaaaaaaa-0000_0", "questionnaire.indirect_aaaaaaaa-0000_0",
                             "Do you hold a current driver licence?", "single",
                             [CapturedOption("1", "Yes"), CapturedOption("2", "No")])
        b = CapturedQuestion("indirect_bbbbbbbb-1111_0", "questionnaire.indirect_bbbbbbbb-1111_0",
                             "Do you hold a current driver licence?", "single",
                             [CapturedOption("9", "No "), CapturedOption("8", "Yes ")])
        bank.record_job_questions(db, jobs["S1"], [a])
        out = bank.record_job_questions(db, jobs["S2"], [b])
        assert len(_bank_rows(db)) == 1
        assert _bank_rows(db)[0].times_seen == 2
        assert out[0]["new_to_bank"] is False

    def test_job_questions_in_form_order(self, db, jobs):
        _record_sample(db, jobs, "S2")
        got = bank.job_questions(db, jobs["S2"])
        assert [v["text"] for v in got] == [clean_text(q["text"]) for q in SAMPLE_BY_ID["S2"]["questions"]]
        assert [v["position"] for v in got] == list(range(6))

    def test_list_questions_unsorted_first(self, db, jobs):
        _record_sample(db, jobs, "S5")
        _record_sample(db, jobs, "S1")
        listing = bank.list_questions(db)
        kinds = [v["kind"] for v in listing]
        assert kinds[0] == "unknown"
        assert kinds.count("unknown") == 1
        assert bank.list_questions(db, status="confirmed") == []
        assert len(bank.list_questions(db, status="new")) == len(listing)


# --- 4. review ---------------------------------------------------------------------
def _unknown_row(db, jobs) -> ScreeningQuestion:
    _record_sample(db, jobs, "S5")
    return next(r for r in _bank_rows(db) if r.kind == "unknown")


class TestReview:
    def test_confirm_unchanged_keeps_classified_by(self, db, jobs):
        _record_sample(db, jobs, "S2")
        row = next(r for r in _bank_rows(db) if r.identity_key == "lib:AU_Q_6")
        view = bank.review_question(db, row.id, "user", "user")
        assert view["status"] == "confirmed"
        assert view["classified_by"] == "library_id"
        assert view["parameters"] == {"topic": "work_rights"}

    def test_confirm_keyword_row_keeps_keyword(self, db, jobs):
        _record_sample(db, jobs, "S1")
        row = next(r for r in _bank_rows(db) if "legally entitled" in r.text)
        view = bank.review_question(db, row.id, "user", "user")
        assert view["classified_by"] == "keyword" and view["status"] == "confirmed"

    def test_correction_sets_user_and_resets_parameters(self, db, jobs):
        _record_sample(db, jobs, "S1")
        row = next(r for r in _bank_rows(db) if "motivated" in r.text)
        view = bank.review_question(db, row.id, "assisted", "free_text_describe")
        assert (view["kind"], view["strategy"], view["classified_by"], view["status"]) == (
            "assisted", "free_text_describe", "user", "confirmed")
        assert view["parameters"] == {}

    def test_correction_applies_to_a_later_jobs_capture(self, db, jobs):
        row = _unknown_row(db, jobs)
        bank.review_question(db, row.id, "assisted", "years_skill_text", {"skill": "React Native"})
        # the same question on a different job and under a different employer id
        again = _captured(_find("S5", "React Native with Expo"))
        again.seek_question_id = "indirect_ffffffff-0000-0000-0000-000000000000_z9"
        again.field_name = f"questionnaire.{again.seek_question_id}"
        out = bank.record_job_questions(db, jobs["S4"], [again])
        assert out[0]["bank_id"] == row.id and out[0]["new_to_bank"] is False
        assert out[0]["sorted_by"] == "bank"
        assert (out[0]["kind"], out[0]["strategy"], out[0]["classified_by"]) == (
            "assisted", "years_skill_text", "user")
        assert out[0]["parameters"] == {"skill": "React Native"}

    def test_unknown_to_assisted_with_strategy(self, db, jobs):
        row = _unknown_row(db, jobs)
        view = bank.review_question(db, row.id, "assisted", "years_skill_text")
        assert view["kind"] == "assisted" and view["strategy"] == "years_skill_text"
        assert view["classified_by"] == "user" and view["status"] == "confirmed"

    def test_unknown_to_user(self, db, jobs):
        row = _unknown_row(db, jobs)
        view = bank.review_question(db, row.id, "user", None)
        assert (view["kind"], view["strategy"], view["classified_by"]) == ("user", "user", "user")

    def test_kind_user_forces_strategy_user(self, db, jobs):
        row = _unknown_row(db, jobs)
        view = bank.review_question(db, row.id, "user", "years_role_bracket")
        assert view["strategy"] == "user"

    @pytest.mark.parametrize("strategy", [None, "", "nonsense", "user"])
    def test_invalid_assisted_strategy_raises(self, db, jobs, strategy):
        row = _unknown_row(db, jobs)
        with pytest.raises(CorrectionError):
            bank.review_question(db, row.id, "assisted", strategy)
        db.refresh(row)
        assert row.kind == "unknown" and row.status == "new"  # nothing half-applied

    def test_invalid_kind_raises(self, db, jobs):
        row = _unknown_row(db, jobs)
        with pytest.raises(CorrectionError):
            bank.review_question(db, row.id, "unknown", None)

    def test_missing_id_raises_lookup_error(self, db):
        with pytest.raises(LookupError):
            bank.review_question(db, 9999, "user", "user")

    def test_every_assisted_strategy_is_accepted(self, db, jobs):
        row = _unknown_row(db, jobs)
        for strategy in ASSISTED_STRATEGIES:
            assert bank.review_question(db, row.id, "assisted", strategy)["strategy"] == strategy

    def test_corrected_row_is_not_resorted_by_code(self, db, jobs):
        # Keyword would call work rights 'user'; the user says it is assisted.
        _record_sample(db, jobs, "S1")
        row = next(r for r in _bank_rows(db) if "legally entitled" in r.text)
        bank.review_question(db, row.id, "assisted", "free_text_describe")
        again = _record_sample(db, jobs, "S1")
        v = next(x for x in again if x["bank_id"] == row.id)
        assert (v["kind"], v["strategy"], v["classified_by"], v["sorted_by"]) == (
            "assisted", "free_text_describe", "user", "bank")

    def test_corrected_library_row_survives_recapture_with_new_version(self, db, jobs):
        _record_sample(db, jobs, "S2")
        row = next(r for r in _bank_rows(db) if r.identity_key == "lib:AU_Q_8")
        bank.review_question(db, row.id, "assisted", "free_text_describe")
        q = CapturedQuestion("AU_Q_8_V_3", "questionnaire.AU_Q_8_V_3", "What is your salary?", "dropdown",
                             [CapturedOption("1", "$30k")])
        v = bank.record_job_questions(db, jobs["S3"], [q])[0]
        assert v["kind"] == "assisted" and v["classified_by"] == "user"
        assert v["library_version"] == "3" and v["sorted_by"] == "bank"


# --- 5. endpoints ------------------------------------------------------------------
class TestEndpoints:
    def test_post_s2_capture(self, client, jobs):
        r = client.post(f"/jobs/{jobs['S2']}/screening-questions", json=_payload("S2"))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["job_id"] == jobs["S2"]
        qs = body["questions"]
        assert len(qs) == 6
        assert [q["position"] for q in qs] == list(range(6))
        assert [q["kind"] for q in qs] == ["user", "assisted", "assisted", "user", "user", "assisted"]
        assert [q["strategy"] for q in qs] == [
            "user", "years_role_bracket", "skill_in_role_yes_no", "user", "user", "skill_multi_select"]
        assert all(q["sorted_by"] and q["new_to_bank"] for q in qs)

    def test_post_unsorted_question_reports_unknown(self, client, jobs):
        r = client.post(f"/jobs/{jobs['S5']}/screening-questions", json=_payload("S5"))
        assert r.status_code == 200
        last = r.json()["questions"][3]
        assert last["kind"] == "unknown" and last["sorted_by"] is None

    def test_post_twice_is_idempotent(self, client, jobs):
        client.post(f"/jobs/{jobs['S2']}/screening-questions", json=_payload("S2"))
        r = client.post(f"/jobs/{jobs['S2']}/screening-questions", json=_payload("S2"))
        assert r.status_code == 200
        assert all(q["sorted_by"] == "bank" and q["times_seen"] == 1 for q in r.json()["questions"])

    def test_post_unknown_job_is_404(self, client):
        r = client.post("/jobs/99999/screening-questions", json=_payload("S2"))
        assert r.status_code == 404

    @staticmethod
    def _q(**over) -> dict:
        base = {"seek_question_id": "AU_Q_6_V_10", "field_name": "questionnaire.AU_Q_6_V_10",
                "text": "Right to work?", "input_type": "dropdown",
                "options": [{"value": "a", "label": "Citizen"}]}
        base.update(over)
        return base

    @pytest.mark.parametrize("name, body", [
        ("field_name mismatch", {"questions": [{**{
            "seek_question_id": "AU_Q_6_V_10", "text": "Q?", "input_type": "dropdown",
            "options": [{"value": "a", "label": "A"}]}, "field_name": "questionnaire.AU_Q_7_V_10"}]}),
        ("text with options", {"questions": [{
            "seek_question_id": "AU_Q_6_V_10", "field_name": "questionnaire.AU_Q_6_V_10",
            "text": "Q?", "input_type": "text", "options": [{"value": "a", "label": "A"}]}]}),
        ("single with no options", {"questions": [{
            "seek_question_id": "AU_Q_6_V_10", "field_name": "questionnaire.AU_Q_6_V_10",
            "text": "Q?", "input_type": "single", "options": []}]}),
        ("single with blank labels", {"questions": [{
            "seek_question_id": "AU_Q_6_V_10", "field_name": "questionnaire.AU_Q_6_V_10",
            "text": "Q?", "input_type": "single", "options": [{"value": "a", "label": "  "}]}]}),
        ("bad id chars", {"questions": [{
            "seek_question_id": "AU Q/6", "field_name": "questionnaire.AU Q/6",
            "text": "Q?", "input_type": "text", "options": []}]}),
        ("empty list", {"questions": []}),
        ("missing questions", {}),
        ("bad input type", {"questions": [{
            "seek_question_id": "AU_Q_6_V_10", "field_name": "questionnaire.AU_Q_6_V_10",
            "text": "Q?", "input_type": "slider", "options": []}]}),
        ("blank text", {"questions": [{
            "seek_question_id": "AU_Q_6_V_10", "field_name": "questionnaire.AU_Q_6_V_10",
            "text": "   ", "input_type": "text", "options": []}]}),
    ])
    def test_post_validation_is_422(self, client, jobs, name, body):
        r = client.post(f"/jobs/{jobs['S1']}/screening-questions", json=body)
        assert r.status_code == 422, name

    def test_failed_validation_writes_nothing(self, client, jobs, db):
        client.post(f"/jobs/{jobs['S1']}/screening-questions", json={"questions": []})
        assert _bank_rows(db) == []

    def test_get_job_questions_in_form_order(self, client, jobs):
        client.post(f"/jobs/{jobs['S2']}/screening-questions", json=_payload("S2"))
        r = client.get(f"/jobs/{jobs['S2']}/screening-questions")
        assert r.status_code == 200
        qs = r.json()["questions"]
        assert [q["seek_question_id"] for q in qs] == [
            q["seek_question_id"] for q in SAMPLE_BY_ID["S2"]["questions"]]
        assert [q["position"] for q in qs] == list(range(6))

    def test_get_job_questions_empty_and_404(self, client, jobs):
        assert client.get(f"/jobs/{jobs['S1']}/screening-questions").json()["questions"] == []
        assert client.get("/jobs/99999/screening-questions").status_code == 404

    def test_list_bank_unsorted_first_with_vocab(self, client, jobs):
        client.post(f"/jobs/{jobs['S1']}/screening-questions", json=_payload("S1"))
        client.post(f"/jobs/{jobs['S5']}/screening-questions", json=_payload("S5"))
        r = client.get("/screening-questions", params={"status": "new"})
        assert r.status_code == 200
        body = r.json()
        assert body["questions"][0]["kind"] == "unknown"
        assert {"questions", "kinds", "assisted_strategies", "user_topics", "classified_by"} <= set(body)
        assert body["kinds"] == ["user", "assisted"]
        assert body["assisted_strategies"] == list(ASSISTED_STRATEGIES)
        assert "work_rights" in body["user_topics"] and "model" in body["classified_by"]

    def test_list_bank_status_filter_and_bad_status(self, client, jobs):
        client.post(f"/jobs/{jobs['S5']}/screening-questions", json=_payload("S5"))
        qid = client.get("/screening-questions").json()["questions"][0]["bank_id"]
        assert client.patch(f"/screening-questions/{qid}", json={"kind": "user"}).status_code == 200
        new = client.get("/screening-questions", params={"status": "new"}).json()["questions"]
        confirmed = client.get("/screening-questions", params={"status": "confirmed"}).json()["questions"]
        assert qid not in [q["bank_id"] for q in new]
        assert [q["bank_id"] for q in confirmed] == [qid]
        assert client.get("/screening-questions", params={"status": "bogus"}).status_code == 422

    def test_patch_review(self, client, jobs):
        client.post(f"/jobs/{jobs['S5']}/screening-questions", json=_payload("S5"))
        unknown = next(q for q in client.get("/screening-questions").json()["questions"] if q["kind"] == "unknown")
        r = client.patch(f"/screening-questions/{unknown['bank_id']}",
                         json={"kind": "assisted", "strategy": "years_skill_text",
                               "parameters": {"skill": "React Native"}})
        assert r.status_code == 200
        body = r.json()
        assert (body["kind"], body["strategy"], body["classified_by"], body["status"]) == (
            "assisted", "years_skill_text", "user", "confirmed")
        assert body["parameters"] == {"skill": "React Native"}

    def test_patch_missing_is_404(self, client):
        assert client.patch("/screening-questions/9999", json={"kind": "user"}).status_code == 404

    @pytest.mark.parametrize("body", [
        {"kind": "assisted"},
        {"kind": "assisted", "strategy": "nonsense"},
        {"kind": "unknown"},
        {"kind": "bogus"},
        {},
    ])
    def test_patch_invalid_is_422(self, client, jobs, body):
        client.post(f"/jobs/{jobs['S5']}/screening-questions", json=_payload("S5"))
        qid = client.get("/screening-questions").json()["questions"][0]["bank_id"]
        assert client.patch(f"/screening-questions/{qid}", json=body).status_code == 422

    def test_deleting_a_job_cascades_links_but_keeps_bank_rows(self, client, db, jobs):
        client.post(f"/jobs/{jobs['S2']}/screening-questions", json=_payload("S2"))
        client.post(f"/jobs/{jobs['S3']}/screening-questions", json=_payload("S3"))
        assert db.scalar(select(func.count()).select_from(JobScreeningQuestion)) == 8
        bank_count = len(_bank_rows(db))
        job = db.get(JobListing, jobs["S2"])
        db.delete(job)
        db.commit()
        assert _links(db, jobs["S2"]) == []
        assert len(_links(db, jobs["S3"])) == 2  # the other job keeps its links
        assert len(_bank_rows(db)) == bank_count  # the bank outlives the job
