"""scripts/letter_lab.py --set: the default (v1) is unchanged, and a frozen set (v2) is
prepared only from its frozen ads and profile.

The prepare tests run letter_lab in a subprocess with the stub LLM (no network, nothing
spent), against a frozen set built in tmp_path: never real.db, evals/eval.db or evals/sets.
"""
import hashlib
import importlib.util
import json
import os
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.models import Base, Experience, JobListing, Profile

ROOT = Path(__file__).resolve().parent.parent
AD_TEXT = "Graduate developer role. " * 20


def _load(name: str, file: str):
    """Import a script by path, then undo letter_lab's env and sys.path edits."""
    saved_env = {k: os.environ.get(k) for k in ("DATABASE_URL", "APP_ENV")}
    saved_path = list(sys.path)
    try:
        spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / file)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        url_at_import = os.environ.get("DATABASE_URL")
    finally:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        sys.path[:] = saved_path
    return mod, url_at_import


lab, URL_AT_IMPORT = _load("letter_lab_sets_under_test", "letter_lab.py")
ef, _ = _load("eval_freeze_for_letter_lab_sets", "eval_freeze.py")


# ---------------------------------------------------------------------------
# The default is unchanged
# ---------------------------------------------------------------------------
def test_importing_still_points_at_the_v1_eval_db():
    assert URL_AT_IMPORT == f"sqlite:///{(ROOT / 'evals' / 'eval.db').as_posix()}"
    assert lab.EVAL_DB == ROOT / "evals" / "eval.db"
    assert lab.SET_FILE == ROOT / "evals" / "set.json"
    assert lab.RUNS_DIR == ROOT / "evals" / "runs"
    assert lab.RESULTS_DIR == ROOT / "evals" / "results"
    assert lab.FROZEN_DIR is None


@pytest.mark.parametrize("argv", [
    ["snapshot"], ["prepare"], ["run"], ["read", "r"], ["report", "r"], ["loop-report", "r"],
    ["cost-report", "r"], ["analyze"], ["analysis-report", "r"], ["plant", "r"],
])
def test_every_command_takes_set_and_defaults_to_v1(argv):
    parser = lab.build_parser()
    assert parser.parse_args(argv).set == "v1"
    assert parser.parse_args([*argv, "--set", "v2"]).set == "v2"


def test_v1_paths_are_the_old_ones_and_v2_has_its_own():
    assert lab.SETS["v1"] == {"eval_db": lab.EVAL_DB, "set_file": lab.SET_FILE, "runs": lab.RUNS_DIR,
                              "results": lab.RESULTS_DIR, "frozen": None}
    v2 = lab.SETS["v2"]
    work = ROOT / "evals" / "work" / "v2"
    assert (v2["eval_db"], v2["set_file"], v2["runs"]) == (work / "eval.db", work / "set.json", work / "runs")
    assert v2["results"] == ROOT / "evals" / "results" / "v2"
    assert v2["frozen"] == ROOT / "evals" / "sets" / "v2"


def test_use_set_refuses_once_app_db_points_elsewhere():
    """In this test session app.db is already connected to the test DB, which is exactly
    the case where eval spend would be logged to the wrong place."""
    import app.db  # noqa: F401  (make sure it is imported)

    before = (lab.EVAL_DB, os.environ.get("DATABASE_URL"))
    with pytest.raises(SystemExit, match="refusing"):
        lab._use_set("v2")
    assert (lab.EVAL_DB, os.environ.get("DATABASE_URL")) == before


# ---------------------------------------------------------------------------
# prepare --set v2, in a subprocess with the stub LLM
# ---------------------------------------------------------------------------
STUB = {
    "extract": {"employer_name": None, "hard_skills": ["Python"], "soft_skills": [], "qualifications": [],
                "experience": [], "seniority": "graduate", "key_responsibilities": ["Build things"],
                "summary": "A graduate role."},
    "employer_name": {"employer_name": None},
    "match": {"score": 77, "reasoning": "Fits.", "gaps": []},
}

# Load letter_lab by path, point its v2 paths at tmp folders, run main().
DRIVER = """
import importlib.util, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("letter_lab", Path("scripts") / "letter_lab.py")
lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)
work, frozen = Path(sys.argv[1]), Path(sys.argv[2])
lab.SETS["v2"] = {"eval_db": work / "eval.db", "set_file": work / "set.json", "runs": work / "runs",
                  "results": work / "results", "frozen": frozen}
raise SystemExit(lab.main(sys.argv[3:]))
"""


@pytest.fixture()
def frozen(tmp_path):
    """A frozen set of two ads (seek-9001, seek-9002) whose profile.db is at alembic head."""
    source = tmp_path / "source.db"
    engine = create_engine(f"sqlite:///{source.as_posix()}")
    Base.metadata.create_all(engine)
    head = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_current_head()
    with Session(engine) as db:
        db.add(Profile(id=1, name="Frozen Tester", email="t@example.com", password_hash="x"))
        db.add(Experience(user_id=1, experience_type="work", title="Intern"))
        for i in (1, 2, 3):
            db.add(JobListing(id=i, source="seek", source_job_id=f"900{i}", url=f"https://example.com/{i}",
                              title=f"Job {i}", company=f"Co {i}", raw_description=f"UNIQUE-AD-{i} " + AD_TEXT))
        db.commit()
        db.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        db.execute(text("INSERT INTO alembic_version VALUES (:h)"), {"h": head})
        db.commit()
    engine.dispose()
    set_dir = tmp_path / "sets" / "v2"
    ef.freeze(source, {i: {"drawn_scenario": 1, "real_db_score": 80} for i in (1, 2)}, set_dir)
    return set_dir


@pytest.fixture()
def run(tmp_path):
    responses = tmp_path / "stub.json"
    responses.write_text(json.dumps(STUB), encoding="utf-8")
    log = tmp_path / "stub.jsonl"
    work = tmp_path / "work" / "v2"

    def _run(frozen_dir, *argv):
        env = {**os.environ, "LLM_PROVIDER": "stub", "LLM_STUB_RESPONSES": str(responses),
               "LLM_STUB_LOG": str(log), "PYTHONIOENCODING": "utf-8"}
        env.pop("DATABASE_URL", None)
        out = subprocess.run([sys.executable, "-c", DRIVER, str(work), str(frozen_dir), *argv],
                             cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=180)
        return out.returncode, out.stdout + out.stderr

    def calls():
        if not log.exists():
            return []
        return [json.loads(line)["task"] for line in log.read_text(encoding="utf-8").splitlines() if line]

    _run.work, _run.calls = work, calls
    return _run


def _scores(db_path: Path) -> dict[str, int | None]:
    con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT j.source_job_id, m.score FROM job_listings j "
                           "LEFT JOIN matches m ON m.job_id = j.id ORDER BY j.source_job_id").fetchall()
    finally:
        con.close()
    return dict(rows)


def _hash_tree(folder: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(folder.rglob("*")):
        if p.is_file():
            h.update(p.name.encode() + p.read_bytes())
    return h.hexdigest()


def test_prepare_v2_builds_from_the_frozen_set_and_picks_up_where_it_left_off(frozen, run):
    before = _hash_tree(frozen)
    code, out = run(frozen, "prepare", "--set", "v2")
    assert code == 0, out
    db = run.work / "eval.db"
    assert _scores(db) == {"9001": 77, "9002": 77}  # the two frozen ads only, never ad 3
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    assert con.execute("SELECT name FROM profiles").fetchall() == [("Frozen Tester",)]
    # The stub writes no llm_usage rows, but the scores above and the eval budget here went
    # through app.db's one engine, the same one usage logging and the budget guard use.
    prefs = json.loads(con.execute("SELECT preferences FROM profiles").fetchone()[0])
    assert prefs["llm_daily_budget_usd"] == lab.EVAL_DAILY_BUDGET_USD
    con.close()
    assert json.loads((run.work / "set.json").read_text(encoding="utf-8")) == {"jobs": ["seek-9001", "seek-9002"]}
    assert sorted(run.calls()) == ["extract", "extract", "match", "match"]
    assert _hash_tree(frozen) == before and ef.verify(frozen) == []  # inputs untouched

    code, out = run(frozen, "prepare", "--set", "v2")  # second run: nothing left to score
    assert code == 0, out
    assert "Using the existing" in out and len(run.calls()) == 4


def test_prepare_v2_only_scores_just_those_ads(frozen, run):
    code, out = run(frozen, "prepare", "--set", "v2", "--only", "seek-9002")
    assert code == 0, out
    assert _scores(run.work / "eval.db") == {"9001": None, "9002": 77}  # both loaded, one scored
    code, out = run(frozen, "prepare", "--set", "v2")
    assert code == 0, out
    assert _scores(run.work / "eval.db") == {"9001": 77, "9002": 77}
    assert sorted(run.calls()) == ["extract", "extract", "match", "match"]  # 9002 not scored twice


def test_prepare_v2_refuses_a_set_that_fails_verification(frozen, run):
    ad = frozen / "ads" / "seek-9001.json"
    os.chmod(ad, stat.S_IREAD | stat.S_IWRITE)
    ad.write_text(ad.read_text(encoding="utf-8").replace("UNIQUE-AD-1", "EDITED"), encoding="utf-8")
    code, out = run(frozen, "prepare", "--set", "v2")
    assert code == 1 and "FAILED verification" in out and "seek-9001" in out
    assert not (run.work / "eval.db").exists() and run.calls() == []


@pytest.mark.parametrize("argv, says", [
    (["snapshot", "--set", "v2"], "frozen"),
    (["prepare", "--set", "v2", "--profile-db", "real.db"], "don't apply"),
    (["prepare", "--set", "v2", "--reselect"], "don't apply"),
    (["prepare", "--set", "v2", "--only", "seek-1234"], "Not in the frozen set"),
    (["prepare", "--only", "seek-9001"], "frozen set"),  # v1 refuses the v2-only options
    (["prepare", "--rebuild"], "frozen set"),
])
def test_refusals(frozen, run, argv, says):
    code, out = run(frozen, *argv)
    assert code == 1 and says in out, out
    assert not (run.work / "eval.db").exists() and run.calls() == []


def test_analyze_only_refuses_a_key_not_in_the_set(frozen, run):
    assert run(frozen, "prepare", "--set", "v2")[0] == 0
    code, out = run(frozen, "analyze", "--set", "v2", "--only", "seek-1234")
    assert code == 1 and "Not in" in out and "seek-1234" in out
    assert len(run.calls()) == 4  # only prepare's calls


# ---------------------------------------------------------------------------
# --split dev|test: one side of the frozen set's split.json
# ---------------------------------------------------------------------------
def _write_split(set_dir: Path, dev: list[str], test: list[str], manifest_sha: str | None = None) -> None:
    sha = manifest_sha or ef.sha256(set_dir / "MANIFEST.json")
    (set_dir / "split.json").write_text(json.dumps({
        "manifest_sha256": sha, "dev": [{"key": k} for k in dev], "test": [{"key": k} for k in test],
    }), encoding="utf-8")


@pytest.fixture()
def v2_paths(frozen, tmp_path, monkeypatch):
    """The module's v2 paths, pointed at the tmp frozen set, with a set.json of both ads."""
    set_file = tmp_path / "set.json"
    set_file.write_text(json.dumps({"jobs": ["seek-9001", "seek-9002"]}), encoding="utf-8")
    monkeypatch.setattr(lab, "FROZEN_DIR", frozen)
    monkeypatch.setattr(lab, "SET_FILE", set_file)
    return frozen


def _args(split=None, only=None):
    return lab.argparse.Namespace(split=split, only=only)


def test_split_picks_one_side_and_only_narrows_within_it(v2_paths):
    _write_split(v2_paths, dev=["seek-9002"], test=["seek-9001"])
    assert lab._set_keys(_args()) == ["seek-9001", "seek-9002"]
    assert lab._set_keys(_args("dev")) == ["seek-9002"]
    assert lab._set_keys(_args("test", ["seek-9001"])) == ["seek-9001"]
    with pytest.raises(SystemExit, match="Not in the dev split: seek-9001"):
        lab._set_keys(_args("dev", ["seek-9001"]))


@pytest.mark.parametrize("setup, says", [
    (lambda d: None, "No .*split.json yet"),
    (lambda d: _write_split(d, ["seek-9001"], [], manifest_sha="0" * 64), "another MANIFEST.json"),
    (lambda d: _write_split(d, ["seek-9001", "seek-7777"], []), "not in .*seek-7777"),
])
def test_split_refusals(v2_paths, setup, says):
    setup(v2_paths)
    with pytest.raises(SystemExit, match=says):
        lab._set_keys(_args("dev"))


def test_split_needs_a_frozen_set(monkeypatch, tmp_path):
    set_file = tmp_path / "set.json"
    set_file.write_text(json.dumps({"jobs": ["seek-1"]}), encoding="utf-8")
    monkeypatch.setattr(lab, "FROZEN_DIR", None)
    monkeypatch.setattr(lab, "SET_FILE", set_file)
    with pytest.raises(SystemExit, match="needs a frozen set"):
        lab._set_keys(_args("dev"))


def test_v2_run_needs_split_or_only_and_resume_keeps_its_split(frozen, run):
    _write_split(frozen, dev=["seek-9002"], test=["seek-9001"])
    assert run(frozen, "prepare", "--set", "v2")[0] == 0
    code, out = run(frozen, "run", "--set", "v2")
    assert code == 1 and "needs --split" in out
    old = run.work / "runs" / "r1"
    old.mkdir(parents=True)
    (old / "run.json").write_text(json.dumps({"split": "test", "results": []}), encoding="utf-8")
    code, out = run(frozen, "run", "--set", "v2", "--split", "dev", "--label", "r1", "--resume")
    assert code == 1 and "same --split" in out
    assert len(run.calls()) == 4  # only prepare's calls: no letter was written
