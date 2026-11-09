"""Freeze the eval set's ads and profile so every later run reads the same inputs
(docs/eval-set-plan.md, stage 2).

Writes evals/sets/v2/, built once and only ever added to:
    ads/seek-<id>.json   the candidate ads, in letter_lab.py snapshot's shape
    profile.db           real.db with every job-side row removed: the frozen profile
    MANIFEST.json        where it came from + a SHA-256 of every file

real.db is only read. The files are marked read-only; `--verify` checks every hash
and that the folder holds exactly the manifest's ads. No LLM calls.

    python scripts/eval_freeze.py                # freeze the ads in candidates.csv (refuses if the set exists)
    python scripts/eval_freeze.py --verify       # check nothing has changed
    python scripts/eval_freeze.py --add 210 211  # append reserve ads (real.db job ids); never rewrites one
"""
from __future__ import annotations

import csv
import datetime
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SET_NAME = "v2"
SET_DIR = ROOT / "evals" / "sets" / SET_NAME
SOURCE_DB = ROOT / "real.db"
CANDIDATES = ROOT / "evals" / "candidates" / "candidates.csv"
DRAW_SEED = 20261020  # scripts/eval_draw.py

# The columns letter_lab.py's snapshot writes and its prepare loads (tests pin the match).
SNAPSHOT_COLUMNS = (
    "source", "source_job_id", "url", "title", "company", "location", "work_type",
    "salary", "classification", "subclassification", "raw_description",
)
# Deleted from the profile copy. job_listings cascades to job_skills, matches,
# cover_letters, letter_runs (+ steps), job_screening_questions and evidence items.
JOB_SIDE_DELETES = ("job_listings", "saved_searches", "llm_usage")
MUST_BE_EMPTY = (
    "job_listings", "job_skills", "matches", "cover_letters", "letter_runs",
    "letter_run_steps", "job_screening_questions", "evidence_bundle_items",
    "saved_searches", "llm_usage",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_only(path: Path) -> None:
    os.chmod(path, stat.S_IREAD)


def _writable(path: Path) -> None:
    os.chmod(path, stat.S_IREAD | stat.S_IWRITE)


def _git_commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    except OSError:
        return None
    return out.stdout.strip() or None


def _now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def read_candidates(path: Path) -> dict[int, dict]:
    """real.db job id -> its candidates.csv row (only the draw's columns are kept)."""
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    out = {}
    for r in rows:
        jid = int(r["job_id"])
        if jid in out:
            raise SystemExit(f"job_id {jid} is in {path.name} twice")
        out[jid] = {"drawn_scenario": int(r["scenario"]), "real_db_score": int(r["score"])}
    return out


def _ad_rows(src: sqlite3.Connection, job_ids: list[int]) -> list[dict]:
    src.row_factory = sqlite3.Row
    marks = ",".join("?" * len(job_ids))
    rows = src.execute(
        f"SELECT id, date_scraped, expired_detected_at, {', '.join(SNAPSHOT_COLUMNS)} "
        f"FROM job_listings WHERE id IN ({marks})", job_ids,
    ).fetchall()
    found = {r["id"]: dict(r) for r in rows}
    missing = sorted(set(job_ids) - set(found))
    if missing:
        raise SystemExit(f"not in the source DB: job ids {missing}")
    bad = [jid for jid, r in found.items()
           if r["source"] != "seek" or len(r["raw_description"] or "") < 200 or r["expired_detected_at"]]
    if bad:
        raise SystemExit(f"not usable (not Seek, under 200 characters of ad text, or closed): {sorted(bad)}")
    return [found[jid] for jid in sorted(found)]


def _write_ads(rows: list[dict], ads_dir: Path, src_name: str, today: str,
               drawn: dict[int, dict]) -> list[dict]:
    """One JSON per ad in snapshot's shape; returns their manifest entries."""
    entries = []
    for row in rows:
        data = {c: row[c] for c in SNAPSHOT_COLUMNS}
        data["snapshot_from"] = src_name
        data["snapshot_at"] = today
        key = f"{row['source']}-{row['source_job_id']}"
        path = ads_dir / f"{key}.json"
        if path.exists():
            raise SystemExit(f"{key} is already frozen; frozen ads are never rewritten")
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        _read_only(path)
        entries.append({
            "key": key, "file": f"ads/{path.name}", "sha256": sha256(path),
            "real_db_job_id": row["id"], "title": row["title"], "company": row["company"],
            "date_scraped": row["date_scraped"], **drawn.get(row["id"], {}), "frozen_at": _now(),
        })
    return entries


def _freeze_profile(src: sqlite3.Connection, out: Path) -> dict:
    """A copy of the source DB with every job-side row removed, vacuumed."""
    dst = sqlite3.connect(out)
    try:
        src.backup(dst)  # consistent even while the API server has real.db open
        dst.execute("PRAGMA foreign_keys = ON")  # the cascades are DB-level ON DELETE CASCADE
        for table in JOB_SIDE_DELETES:
            dst.execute(f"DELETE FROM {table}")
        dst.commit()
        left = {t: dst.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in MUST_BE_EMPTY}
        if any(left.values()):
            raise SystemExit(f"profile copy still holds job-side rows: {left}")
        dst.execute("VACUUM")  # deleted ad text must not linger in free pages
        profiles = dst.execute("SELECT count(*), max(profile_revised_at) FROM profiles").fetchone()
        revision = dst.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    finally:
        dst.close()
    if profiles[0] != 1:
        raise SystemExit(f"expected exactly one profile in the source DB, found {profiles[0]}")
    _read_only(out)
    return {"file": "profile.db", "sha256": sha256(out), "alembic_revision": revision,
            "profile_revised_at": profiles[1]}


def freeze(source_db: Path, drawn: dict[int, dict], set_dir: Path) -> dict:
    """Build the set in a temp folder beside set_dir, then rename it into place."""
    if set_dir.exists():
        raise SystemExit(f"{set_dir} already exists; it is frozen. Use --add for reserve ads.")
    tmp = set_dir.with_name(f".{set_dir.name}-building")
    if tmp.exists():
        shutil.rmtree(tmp, onerror=lambda fn, p, _e: (_writable(Path(p)), fn(p)))
    (tmp / "ads").mkdir(parents=True)
    src = sqlite3.connect(f"file:{source_db.as_posix()}?mode=ro", uri=True)
    try:
        rows = _ad_rows(src, sorted(drawn))
        today = datetime.date.today().isoformat()
        ads = _write_ads(rows, tmp / "ads", source_db.name, today, drawn)
        profile = _freeze_profile(src, tmp / "profile.db")
    finally:
        src.close()
    manifest = {
        "set": set_dir.name, "frozen_at": _now(), "source_db": source_db.name,
        "git_commit": _git_commit(), "candidates_seed": DRAW_SEED,
        "profile": profile, "ads": ads,
    }
    _write_manifest(tmp, manifest)
    tmp.rename(set_dir)
    return manifest


def add(source_db: Path, job_ids: list[int], set_dir: Path) -> list[dict]:
    """Append ads to a frozen set. The set must verify first; existing ads are never touched."""
    problems = verify(set_dir)
    if problems:
        raise SystemExit("the frozen set does not verify, so nothing was added:\n  " + "\n  ".join(problems))
    manifest = json.loads((set_dir / "MANIFEST.json").read_text(encoding="utf-8"))
    have = {a["real_db_job_id"] for a in manifest["ads"]}
    already = sorted(set(job_ids) & have)
    if already:
        raise SystemExit(f"already frozen: job ids {already}")
    src = sqlite3.connect(f"file:{source_db.as_posix()}?mode=ro", uri=True)
    try:
        rows = _ad_rows(src, sorted(set(job_ids)))
    finally:
        src.close()
    new = _write_ads(rows, set_dir / "ads", source_db.name, datetime.date.today().isoformat(), {})
    manifest["ads"] = sorted(manifest["ads"] + new, key=lambda a: a["real_db_job_id"])
    _write_manifest(set_dir, manifest)
    return new


def _write_manifest(set_dir: Path, manifest: dict) -> None:
    path = set_dir / "MANIFEST.json"
    if path.exists():
        _writable(path)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _read_only(path)


def verify(set_dir: Path) -> list[str]:
    """Every problem found; an empty list means the set is exactly as frozen."""
    path = set_dir / "MANIFEST.json"
    if not path.exists():
        return [f"no manifest at {path}"]
    manifest = json.loads(path.read_text(encoding="utf-8"))
    problems = []
    listed = {a["file"] for a in manifest["ads"]} | {manifest["profile"]["file"]}
    for entry in [manifest["profile"], *manifest["ads"]]:
        f = set_dir / entry["file"]
        if not f.exists():
            problems.append(f"missing: {entry['file']}")
        elif sha256(f) != entry["sha256"]:
            problems.append(f"changed since freezing: {entry['file']}")
    on_disk = {f"ads/{p.name}" for p in (set_dir / "ads").glob("*")} | (
        {"profile.db"} if (set_dir / "profile.db").exists() else set())
    problems += [f"not in the manifest: {f}" for f in sorted(on_disk - listed)]
    return problems


def main(argv: list[str]) -> int:
    if argv[:1] == ["--verify"]:
        problems = verify(SET_DIR)
        if problems:
            print(f"{SET_DIR.relative_to(ROOT)} FAILED verification:\n  " + "\n  ".join(problems))
            return 1
        m = json.loads((SET_DIR / "MANIFEST.json").read_text(encoding="utf-8"))
        print(f"OK: {SET_DIR.relative_to(ROOT)} holds {len(m['ads'])} ads + profile.db, "
              f"every hash matches (frozen {m['frozen_at']})")
        return 0
    if argv[:1] == ["--add"]:
        ids = [int(a) for a in argv[1:]]
        if not ids:
            print("--add needs real.db job ids")
            return 1
        new = add(SOURCE_DB, ids, SET_DIR)
        print(f"Added {len(new)} ads to {SET_DIR.relative_to(ROOT)}: {', '.join(a['key'] for a in new)}")
        return 0
    if argv:
        print(__doc__)
        return 1
    m = freeze(SOURCE_DB, read_candidates(CANDIDATES), SET_DIR)
    print(f"Froze {len(m['ads'])} ads + profile.db into {SET_DIR.relative_to(ROOT)} "
          f"(alembic {m['profile']['alembic_revision']}, commit {(m['git_commit'] or '?')[:7]})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
