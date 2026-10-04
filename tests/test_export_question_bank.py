"""Tests for scripts/export_question_bank.py: ``render`` over an in-memory bank filled from
tests/fixtures/quick_apply_samples.json (S1..S5), and ``main`` against a temp SQLite file.

No network, no LLM, never real.db / app.db.
"""
from __future__ import annotations

import datetime
import importlib.util
import json
import re
import sqlite3
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import JobListing, JobScreeningQuestion, ScreeningQuestion
from app.screening import bank
from app.screening.bank import CapturedOption, CapturedQuestion

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = json.loads((ROOT / "tests" / "fixtures" / "quick_apply_samples.json").read_text(encoding="utf-8"))["samples"]
TODAY = datetime.date(2026, 10, 4)


def _load_script():
    spec = importlib.util.spec_from_file_location("export_question_bank_under_test",
                                                  ROOT / "scripts" / "export_question_bank.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["export_question_bank_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def script():
    return _load_script()


def _captured(sample_q: dict) -> CapturedQuestion:
    return CapturedQuestion(
        seek_question_id=sample_q["seek_question_id"],
        field_name=sample_q["field_name"],
        text=sample_q["text"],
        input_type=sample_q["input_type"],
        options=[CapturedOption(o["value"], o["label"]) for o in sample_q["options"]],
    )


def fill(session, sample_ids: list[str]) -> dict[str, int]:
    """Create a job per sample and record its questions. Returns sample id -> job row id."""
    ids = {}
    for s in (s for s in SAMPLES if s["id"] in sample_ids):
        job = JobListing(source="seek", source_job_id=s["job_id"], url="u", title=s["title"], company=s["company"])
        session.add(job)
        session.flush()
        bank.record_job_questions(session, job.id, [_captured(q) for q in s["questions"]])
        ids[s["id"]] = job.id
    return ids


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
def db(engine):
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    yield s
    s.close()


def table_rows(md: str, heading: str) -> list[list[str]]:
    """Rows (cells) of the first table under a heading, header and separator dropped."""
    block = md.split(heading, 1)[1]
    rows = []
    for line in block.splitlines()[1:]:
        if line.startswith("#") and rows:
            break
        if line.startswith("|"):
            rows.append([c.strip() for c in re.split(r"(?<!\\)\|", line.strip())[1:-1]])
        elif rows:
            break
    return rows[2:]


class TestRender:
    def test_headings_and_summary(self, db, script):
        fill(db, ["S1", "S2", "S3", "S4", "S5"])
        md = script.render(db, today=TODAY)
        assert md.startswith("# Seek Quick Apply: question bank export\n")
        for h in ("## Jobs", "## Seek standard-library ids seen", "## Question kinds seen so far",
                  "## Needs review (status new)"):
            assert h in md
        assert "Generated 2026-10-04" in md
        assert "5 jobs" in md
        assert "never answers" in md
        n = db.query(ScreeningQuestion).count()
        assert f"{n} questions in the bank" in md
        assert "1 unknown" in md
        assert f"{n} new, 0 confirmed" in md

    def test_job_headings_and_form_order(self, db, script):
        fill(db, ["S1", "S2"])
        md = script.render(db, today=TODAY)
        assert "### IT Trainee, ATSICHS Brisbane (job 94904230)" in md
        assert "### Full Stack Developer, Barrington Group Australia Pty Ltd (job 94900002)" in md
        rows = table_rows(md, "### IT Trainee")
        assert [r[0] for r in rows] == ["1", "2", "3", "4", "5", "6", "7"]
        assert rows[0][1].startswith("Are you legally entitled to work in Australia?")
        assert rows[0][2:] == ["single", "Yes / No", "user (work rights)"]
        assert rows[1][4] == "user (identity)"

    def test_jobs_newest_first(self, db, script):
        ids = fill(db, ["S1", "S2"])
        old = datetime.datetime(2026, 1, 1)
        new = datetime.datetime(2026, 9, 1)
        db.execute(update(JobScreeningQuestion).where(JobScreeningQuestion.job_id == ids["S1"]).values(last_seen_at=new))
        db.execute(update(JobScreeningQuestion).where(JobScreeningQuestion.job_id == ids["S2"]).values(last_seen_at=old))
        db.commit()
        md = script.render(db, today=TODAY)
        assert md.index("### IT Trainee") < md.index("### Full Stack Developer")
        # and the other way round
        db.execute(update(JobScreeningQuestion).where(JobScreeningQuestion.job_id == ids["S1"]).values(last_seen_at=old))
        db.execute(update(JobScreeningQuestion).where(JobScreeningQuestion.job_id == ids["S2"]).values(last_seen_at=new))
        db.commit()
        md = script.render(db, today=TODAY)
        assert md.index("### Full Stack Developer") < md.index("### IT Trainee")

    def test_ids_library_generated_shown_employer_omitted(self, db, script):
        fill(db, ["S1", "S2", "S4", "S5"])
        md = script.render(db, today=TODAY)
        assert "(`AU_Q_6_V_10`)" in md
        assert "(`AU_Q_6804_V_4`)" in md
        assert "`AU_Q_D29EC90383D57C663E2D2A43F205280B_V_2`" in md  # generated id family
        assert "indirect_" not in md
        s1 = table_rows(md, "### IT Trainee")
        assert "`" not in s1[0][1]

    def test_library_table_counts_jobs_once_per_id(self, db, script):
        fill(db, ["S2", "S3", "S4"])
        md = script.render(db, today=TODAY)
        rows = table_rows(md, "## Seek standard-library ids seen")
        assert [r[0] for r in rows].count("`AU_Q_6_V_10`") == 1
        by_id = {r[0]: r for r in rows}
        assert by_id["`AU_Q_6_V_10`"][3] == "3"
        assert by_id["`AU_Q_13_V_2`"][3] == "2"
        assert by_id["`AU_Q_8_V_2`"][3] == "2"
        assert by_id["`AU_Q_6804_V_4`"][3] == "1"
        assert by_id["`AU_Q_6_V_10`"][2] == "user (work rights)"
        assert rows[0][0] == "`AU_Q_6_V_10`"  # most seen first
        # the generated-id years question is not a library id
        assert not any("AU_Q_D29" in r[0] for r in rows)

    def test_long_option_list_is_truncated_and_free_text_dash(self, db, script):
        fill(db, ["S1", "S2", "S5"])
        md = script.render(db, today=TODAY)
        fsd = table_rows(md, "### Full Stack Developer")
        salary = next(r for r in fsd if "expected annual base salary" in r[1])
        assert " … (26 options)" in salary[3]
        assert salary[3].count(" / ") == 9  # first 10 labels shown
        # the 11-option visa list is under the cap and shown whole
        visa = next(r for r in fsd if "right to work" in r[1])
        assert "…" not in visa[3] and visa[3].count(" / ") == 10
        free = next(r for r in table_rows(md, "### IT Trainee") if r[1].startswith("What motivated"))
        assert free[2] == "free text" and free[3] == "—"
        multi = next(r for r in fsd if "programming languages" in r[1])
        assert multi[2] == "multi (checkboxes)"
        assert multi[4] == "**assisted (skill_multi_select)**"

    def test_unsorted_question_listed_and_needs_review_first(self, db, script):
        fill(db, ["S5"])
        md = script.render(db, today=TODAY)
        rows = table_rows(md, "### React Native Developer")
        react = next(r for r in rows if "React Native with Expo" in r[1])
        assert react[2] == "free text" and react[4] == "unsorted (needs the model)"
        kinds = md.split("## Question kinds seen so far")[1].split("## Needs review")[0]
        assert "unsorted (needs the model):" in kinds and "React Native with Expo" in kinds
        review = md.split("## Needs review (status new)")[1].strip().splitlines()
        assert review[0].startswith("4 question(s)")
        bullets = [ln for ln in review if ln.startswith("- ")]
        assert len(bullets) == 4
        assert "React Native with Expo" in bullets[0] and bullets[0].endswith("(unsorted)")

    def test_kinds_bullets_count_topics_and_strategies(self, db, script):
        fill(db, ["S1", "S2", "S3", "S4"])
        md = script.render(db, today=TODAY)
        kinds = md.split("## Question kinds seen so far")[1].split("## Needs review")[0]
        assert "work_rights (2)" in kinds  # S1's wording and AU_Q_6: two distinct questions
        assert "legal (2)" in kinds
        assert "years_role_bracket (2)" in kinds
        assert "skill_multi_select (1)" in kinds
        assert "unsorted (needs the model): none" in kinds

    def test_confirmed_and_corrected_markers(self, db, script):
        fill(db, ["S2"])
        years = db.query(ScreeningQuestion).filter(ScreeningQuestion.library_id == "AU_Q_6804").one()
        csharp = db.query(ScreeningQuestion).filter(ScreeningQuestion.library_id == "AU_Q_218").one()
        bank.review_question(db, years.id, "assisted", "years_role_bracket")  # confirmation
        bank.review_question(db, csharp.id, "user", None)  # correction
        md = script.render(db, today=TODAY)
        rows = table_rows(md, "### Full Stack Developer")
        y = next(r for r in rows if "years' experience" in r[1])
        c = next(r for r in rows if "C# development" in r[1])
        assert y[4] == "**assisted (years_role_bracket)** ✓"
        assert c[4] == "user ✓ (you corrected this)"
        review = md.split("## Needs review (status new)")[1]
        assert "years' experience" not in review and "C# development" not in review
        assert "2 confirmed" in md

    def test_pipes_are_escaped(self, db, script):
        job = JobListing(source="seek", source_job_id="777", url="u", title="A | B", company="Co | Ltd")
        db.add(job)
        db.flush()
        bank.record_job_questions(db, job.id, [CapturedQuestion(
            "indirect_x_1", "questionnaire.indirect_x_1", "Pick one | or the other?", "single",
            [CapturedOption("0", "Red | Blue"), CapturedOption("1", "Green")])])
        md = script.render(db, today=TODAY)
        assert "### A \\| B, Co \\| Ltd (job 777)" in md
        row = next(ln for ln in md.splitlines() if ln.startswith("| 1 | Pick one"))
        assert "Pick one \\| or the other?" in row and "Red \\| Blue / Green" in row
        assert len(re.split(r"(?<!\\)\|", row)) == 7  # 5 cells, nothing split by a stray pipe

    def test_no_answer_data(self, db, script):
        fill(db, ["S1", "S2", "S3", "S4", "S5"])
        md = script.render(db, today=TODAY).lower()
        assert "checked" not in md and "selected" not in md and "answer:" not in md

    def test_empty_bank(self, db, script):
        md = script.render(db, today=TODAY)
        assert "0 questions in the bank" in md
        assert "No job has a captured question form yet." in md
        assert "0 question(s) not yet confirmed." in md

    def test_render_only_reads(self, db, script):
        fill(db, ["S2"])
        before = db.query(ScreeningQuestion).count(), db.query(JobScreeningQuestion).count()
        script.render(db, today=TODAY)
        assert not db.new and not db.dirty
        assert (db.query(ScreeningQuestion).count(), db.query(JobScreeningQuestion).count()) == before


class TestMain:
    def _make_db(self, tmp_path):
        path = tmp_path / "bank.db"
        eng = create_engine(f"sqlite:///{path.as_posix()}")
        Base.metadata.create_all(eng)
        with sessionmaker(bind=eng)() as s:
            fill(s, ["S2", "S3"])
        eng.dispose()
        return path

    def test_stdout(self, tmp_path, monkeypatch, capsys, script):
        path = self._make_db(tmp_path)
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path.as_posix()}")
        assert script.main(["--stdout"]) == 0
        out = capsys.readouterr().out
        assert out.startswith("# Seek Quick Apply: question bank export")
        assert "(`AU_Q_6_V_10`)" in out and "### Full Stack Developer" in out

    def test_writes_file_and_leaves_db_unchanged(self, tmp_path, monkeypatch, capsys, script):
        path = self._make_db(tmp_path)
        before = path.read_bytes()
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path.as_posix()}")
        out = tmp_path / "sub" / "bank.md"
        assert script.main(["--out", str(out)]) == 0
        assert out.read_text(encoding="utf-8").startswith("# Seek Quick Apply")
        assert "wrote" in capsys.readouterr().out
        assert path.read_bytes() == before

    def test_unmigrated_db_is_an_error_not_a_crash(self, tmp_path, monkeypatch, capsys, script):
        empty = tmp_path / "empty.db"
        sqlite3.connect(empty).close()
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{empty.as_posix()}")
        assert script.main(["--stdout"]) == 1
        assert "migrated" in capsys.readouterr().err
