"""scripts/eval_freeze.py: the frozen eval set holds exactly the chosen ads, a profile
copy with nothing job-side left in it, and catches any later change.

Builds its own SQLite source DB in tmp_path; never touches real.db or app.db.
"""
import ast
import importlib.util
import json
import os
import stat
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.models import (Base, CoverLetter, Experience, JobListing, JobSkill, LetterRun,
                        LlmUsage, Match, Profile)

ROOT = Path(__file__).resolve().parent.parent
AD_TEXT = "Graduate developer role. " * 20  # over the 200-character usable floor


def _load():
    spec = importlib.util.spec_from_file_location("eval_freeze_under_test", ROOT / "scripts" / "eval_freeze.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["eval_freeze_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


ef = _load()


@pytest.fixture()
def source(tmp_path):
    """A source DB: one profile, three usable ads (1, 2, 3), each with job-side rows."""
    path = tmp_path / "source.db"
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Profile(id=1, name="Test", email="t@example.com", password_hash="x"))
        db.add(Experience(user_id=1, experience_type="work", title="Intern"))
        for i in (1, 2, 3):
            db.add(JobListing(id=i, source="seek", source_job_id=f"900{i}", url=f"https://example.com/{i}",
                              title=f"Job {i}", company=f"Co {i}", raw_description=f"UNIQUE-AD-{i} " + AD_TEXT))
        db.flush()
        for i in (1, 2, 3):
            db.add(JobSkill(job_id=i, name="Python"))
            db.add(Match(id=i, user_id=1, job_id=i, score=80))
        db.flush()
        for i in (1, 2, 3):
            db.add(CoverLetter(match_id=i, generated_content=f"LETTER-{i}"))
            db.add(LetterRun(match_id=i, engine="agent"))
        db.add(LlmUsage(task="match", tier="small", model="m"))
        db.commit()
        db.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        db.execute(text("INSERT INTO alembic_version VALUES ('rev123')"))
        db.commit()
    engine.dispose()
    return path


def _drawn(*ids):
    return {i: {"drawn_scenario": 1, "real_db_score": 80} for i in ids}


def _bytes_under(folder: Path) -> bytes:
    return b"".join(p.read_bytes() for p in folder.rglob("*") if p.is_file())


def test_freezes_only_the_chosen_ads(source, tmp_path):
    set_dir = tmp_path / "sets" / "v2"
    m = ef.freeze(source, _drawn(1, 3), set_dir)
    assert sorted(p.name for p in (set_dir / "ads").iterdir()) == ["seek-9001.json", "seek-9003.json"]
    assert [a["real_db_job_id"] for a in m["ads"]] == [1, 3]
    ad = json.loads((set_dir / "ads" / "seek-9001.json").read_text(encoding="utf-8"))
    assert set(ad) == set(ef.SNAPSHOT_COLUMNS) | {"snapshot_from", "snapshot_at"}
    assert ad["raw_description"].startswith("UNIQUE-AD-1 ")
    # Ad 2 was not chosen: its text is nowhere in the set, profile.db included (VACUUM).
    blob = _bytes_under(set_dir)
    assert b"UNIQUE-AD-2" not in blob
    assert b"LETTER-" not in blob
    assert ef.verify(set_dir) == []
    assert not (tmp_path / "sets" / ".v2-building").exists()


def test_profile_copy_keeps_the_profile_and_no_job_rows(source, tmp_path):
    set_dir = tmp_path / "v2"
    m = ef.freeze(source, _drawn(1), set_dir)
    assert m["profile"]["alembic_revision"] == "rev123"
    import sqlite3
    con = sqlite3.connect(f"file:{(set_dir / 'profile.db').as_posix()}?mode=ro", uri=True)
    try:
        assert con.execute("SELECT name FROM profiles").fetchall() == [("Test",)]
        assert con.execute("SELECT count(*) FROM experiences").fetchone()[0] == 1
        for table in ef.MUST_BE_EMPTY:
            assert con.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0, table
    finally:
        con.close()


def test_source_db_is_not_written(source, tmp_path):
    before = ef.sha256(source)
    ef.freeze(source, _drawn(1, 2), tmp_path / "v2")
    assert ef.sha256(source) == before


def test_files_are_read_only_and_a_second_freeze_refuses(source, tmp_path):
    set_dir = tmp_path / "v2"
    ef.freeze(source, _drawn(1), set_dir)
    for p in [set_dir / "MANIFEST.json", set_dir / "profile.db", *(set_dir / "ads").iterdir()]:
        assert not os.access(p, os.W_OK), p
    with pytest.raises(SystemExit, match="already exists"):
        ef.freeze(source, _drawn(1), set_dir)


def test_verify_catches_a_changed_a_missing_and_an_extra_file(source, tmp_path):
    set_dir = tmp_path / "v2"
    ef.freeze(source, _drawn(1, 2, 3), set_dir)
    changed = set_dir / "ads" / "seek-9001.json"
    os.chmod(changed, stat.S_IREAD | stat.S_IWRITE)
    changed.write_text("{}", encoding="utf-8")
    gone = set_dir / "ads" / "seek-9002.json"
    os.chmod(gone, stat.S_IREAD | stat.S_IWRITE)
    gone.unlink()
    (set_dir / "ads" / "seek-7777.json").write_text("{}", encoding="utf-8")
    assert sorted(ef.verify(set_dir)) == [
        "changed since freezing: ads/seek-9001.json",
        "missing: ads/seek-9002.json",
        "not in the manifest: ads/seek-7777.json",
    ]


def test_add_appends_without_touching_frozen_ads(source, tmp_path):
    set_dir = tmp_path / "v2"
    ef.freeze(source, _drawn(1), set_dir)
    first = ef.sha256(set_dir / "ads" / "seek-9001.json")
    new = ef.add(source, [3], set_dir)
    assert [a["key"] for a in new] == ["seek-9003"]
    m = json.loads((set_dir / "MANIFEST.json").read_text(encoding="utf-8"))
    assert [a["real_db_job_id"] for a in m["ads"]] == [1, 3]
    assert ef.sha256(set_dir / "ads" / "seek-9001.json") == first
    assert ef.verify(set_dir) == []
    with pytest.raises(SystemExit, match="already frozen"):
        ef.add(source, [1], set_dir)


def test_add_refuses_a_set_that_does_not_verify(source, tmp_path):
    set_dir = tmp_path / "v2"
    ef.freeze(source, _drawn(1), set_dir)
    (set_dir / "ads" / "stray.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit, match="does not verify"):
        ef.add(source, [2], set_dir)


def test_refuses_unknown_short_or_closed_ads(source, tmp_path):
    with pytest.raises(SystemExit, match=r"not in the source DB: job ids \[99\]"):
        ef.freeze(source, _drawn(1, 99), tmp_path / "a")
    engine = create_engine(f"sqlite:///{source.as_posix()}")
    with engine.begin() as con:
        con.execute(text("UPDATE job_listings SET raw_description = 'short' WHERE id = 2"))
        con.execute(text("UPDATE job_listings SET expired_detected_at = CURRENT_TIMESTAMP WHERE id = 3"))
    engine.dispose()
    with pytest.raises(SystemExit, match=r"not usable .*\[2, 3\]"):
        ef.freeze(source, _drawn(1, 2, 3), tmp_path / "b")


def test_snapshot_columns_match_letter_lab():
    """The frozen ads must load in letter_lab.py's prepare unchanged (parsed, not
    imported: importing letter_lab points DATABASE_URL at evals/eval.db)."""
    tree = ast.parse((ROOT / "scripts" / "letter_lab.py").read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.Assign)
                and any(getattr(t, "id", None) == "_SNAPSHOT_COLUMNS" for t in n.targets))
    assert tuple(ast.literal_eval(node.value)) == ef.SNAPSHOT_COLUMNS


def test_read_candidates_handles_the_bom_and_rejects_duplicates(tmp_path):
    sheet = tmp_path / "c.csv"
    sheet.write_text("job_id,scenario,score,title\n12,1,95,A\n7,3,60,B\n", encoding="utf-8-sig")
    assert ef.read_candidates(sheet) == {12: {"drawn_scenario": 1, "real_db_score": 95},
                                         7: {"drawn_scenario": 3, "real_db_score": 60}}
    sheet.write_text("job_id,scenario,score\n12,1,95\n12,1,95\n", encoding="utf-8-sig")
    with pytest.raises(SystemExit, match="twice"):
        ef.read_candidates(sheet)
