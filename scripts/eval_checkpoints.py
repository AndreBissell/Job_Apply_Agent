"""Experiment checkpoints and their comparison (docs/experiment-checkpoints-plan.md, v1.0).

A checkpoint records one finished experiment, once: a letter run plus its judging(s), or a few
measured numbers. Checkpoints are appended to the committed evals/results/v2/checkpoints.jsonl and
never edited (a correction is a new checkpoint with `replaces`). `compare` and `history` read them
back and write markdown reports.

Pure functions, no LLM, no database and no judge call: `letter_lab.py` (the checkpoint, checkpoints,
compare and history subcommands) reads the run files and `llm_usage` and hands them in here.

PUBLIC REPO. A checkpoint holds numbers, ad keys, row ids (R10), checklist ids and settings only.
Every row is BUILT from an allowlist of fields, never copied from a run or judge record and pruned:
run.json's results carry the ad's title and company, and the judge records quote the letter in
`issues`, `problems`, `note`, `claims`, `quote_problems` and `checklist_doubts`. `check_allowlist`
runs on every checkpoint before it is written and also checks each value's form, so a text field
that slipped into an allowed key is refused too.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import letter_points as lp  # noqa: E402  the points scorer: stdlib only

CHECKPOINTS_FILE = "checkpoints.jsonl"
KINDS = ("letters", "measure")
VERDICTS = ("pass", "fail", "none")


class CheckpointError(Exception):
    """A refusal (plan §3.1): nothing is written, and the message says why."""


# ---------------------------------------------------------------------------
# The allowlist (plan §1). A test pins these sets; a new field is added here on purpose or not at all.
# ---------------------------------------------------------------------------
TOP_KEYS = ("id", "created", "kind", "experiment", "note", "parent", "kept", "verdict", "rule", "aa_of",
            "replaces", "comparable", "settings", "totals", "letters", "measures")
COMPARABLE_KEYS = ("set", "split", "judge", "scorer_sha256", "judgings")
SETTINGS_KEYS = ("run", "judge_tags", "judge_backends", "commit", "engine", "models", "thinking", "prices_seen")
TOTALS_KEYS = ("letters", "cost_usd", "seconds", "points_mean")
LETTER_KEYS = ("key", "relevant", "status", "clean", "drafts", "seconds", "cost_usd", "by_task", "words",
               "stock_hits", "points", "points_each", "problems", "missed_row_ids", "checklist")
TASK_KEYS = ("model", "calls", "in", "cached", "out", "think", "cost_usd", "ms")
PROBLEM_KEYS = ("n", "low", "high", "lost")
TIERS = ("strong", "mid", "small")

# The form every short string value must have. None of them allows a space, so a sentence (a title,
# a quote, a judge's note) can't pass as an ad key, a status or a model name.
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:+-]{0,79}")
_ROW = re.compile(r"R\d{1,3}")
_CHECK_ID = re.compile(r"D\d{1,2}[a-z]?")
_CHECK_VALUES = ("Y", "N", "mixed")
_CREATED = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d")
_SHA = re.compile(r"[0-9a-f]{64}")
_CP_ID = re.compile(r"c\d+")


def _fail(where: str, why: str):
    raise CheckpointError(f"privacy guard: {where}: {why}")


def _keys(d, allowed: tuple, where: str, exact: bool = False) -> None:
    if not isinstance(d, dict):
        _fail(where, "must be an object")
    extra = set(d) - set(allowed)
    if extra:
        _fail(where, f"key(s) outside the allowlist: {sorted(extra)}")
    if exact and set(d) != set(allowed):
        _fail(where, f"missing key(s): {sorted(set(allowed) - set(d))}")


def _name(v, where: str, pattern: re.Pattern = _NAME, nullable: bool = False) -> None:
    if v is None and nullable:
        return
    if not isinstance(v, str) or not pattern.fullmatch(v):
        _fail(where, f"{v!r} is not a plain name (letters, digits, - _ . : + only)")


def _number(v, where: str, nullable: bool = False) -> None:
    if v is None and nullable:
        return
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        _fail(where, f"{v!r} is not a number")


def _flag(v, where: str, nullable: bool = False) -> None:
    if v is None and nullable:
        return
    if not isinstance(v, bool):
        _fail(where, f"{v!r} is not true / false")


def check_allowlist(cp: dict) -> None:
    """Refuse a checkpoint with any key outside the allowlist, or any value not of its field's form.
    Free text is allowed only in `note` and `rule`, which the owner types on the command line."""
    _keys(cp, TOP_KEYS, "checkpoint", exact=True)
    _name(cp["id"], "id", _CP_ID)
    if not isinstance(cp["created"], str) or not _CREATED.fullmatch(cp["created"]):
        _fail("created", f"{cp['created']!r} is not YYYY-MM-DDTHH:MM:SS")
    if cp["kind"] not in KINDS:
        _fail("kind", repr(cp["kind"]))
    _name(cp["experiment"], "experiment")
    for f in ("note", "rule"):
        if not isinstance(cp[f], str):
            _fail(f, "must be text")
    for f in ("parent", "aa_of", "replaces"):
        _name(cp[f], f, _CP_ID, nullable=True)
    _flag(cp["kept"], "kept")
    if cp["verdict"] not in VERDICTS:
        _fail("verdict", repr(cp["verdict"]))

    c = cp["comparable"]
    _keys(c, COMPARABLE_KEYS, "comparable")
    for f in ("set", "split", "judge"):
        if f in c:
            _name(c[f], f"comparable.{f}", nullable=True)
    if "scorer_sha256" in c:
        _name(c["scorer_sha256"], "comparable.scorer_sha256", _SHA)
    if "judgings" in c:
        _number(c["judgings"], "comparable.judgings")

    s = cp["settings"]
    _keys(s, SETTINGS_KEYS, "settings")
    for f in ("run", "commit", "engine"):
        if f in s:
            _name(s[f], f"settings.{f}", nullable=True)
    for i, judging in enumerate(s.get("judge_tags", [])):
        if not isinstance(judging, list) or not judging:
            _fail(f"settings.judge_tags[{i}]", "must be a non-empty list")
        for tag in judging:
            _name(tag, f"settings.judge_tags[{i}]")
    for i, b in enumerate(s.get("judge_backends", [])):
        _name(b, f"settings.judge_backends[{i}]")
    for f in ("models", "thinking"):
        _keys(s.get(f, {}), TIERS, f"settings.{f}")
        for tier, v in s.get(f, {}).items():
            _name(v, f"settings.{f}.{tier}", nullable=True)
    for model, price in s.get("prices_seen", {}).items():
        _name(model, "settings.prices_seen")
        if not isinstance(price, list) or len(price) != 3:
            _fail(f"settings.prices_seen.{model}", "must be [input, output, cached]")
        for p in price:
            _number(p, f"settings.prices_seen.{model}")

    _keys(cp["totals"], TOTALS_KEYS, "totals")
    for f, v in cp["totals"].items():
        _number(v, f"totals.{f}", nullable=True)

    if not isinstance(cp["letters"], list):
        _fail("letters", "must be a list")
    for row in cp["letters"]:
        _check_letter(row)

    if not isinstance(cp["measures"], dict):
        _fail("measures", "must be an object")
    for name, v in cp["measures"].items():
        _name(name, "measures")
        _number(v, f"measures.{name}")


def _check_letter(row: dict) -> None:
    _keys(row, LETTER_KEYS, "letter", exact=True)
    _name(row["key"], "letter.key")
    where = f"letter {row['key']}"
    _flag(row["relevant"], f"{where}.relevant", nullable=True)
    _name(row["status"], f"{where}.status", nullable=True)
    _flag(row["clean"], f"{where}.clean", nullable=True)
    for f in ("drafts", "seconds", "cost_usd", "words", "stock_hits", "points"):
        _number(row[f], f"{where}.{f}", nullable=True)
    for task, t in row["by_task"].items():
        _name(task, f"{where}.by_task")
        _keys(t, TASK_KEYS, f"{where}.by_task.{task}", exact=True)
        _name(t["model"], f"{where}.by_task.{task}.model")
        for f in TASK_KEYS[1:]:
            _number(t[f], f"{where}.by_task.{task}.{f}")
    if not isinstance(row["points_each"], list):
        _fail(f"{where}.points_each", "must be a list")
    for p in row["points_each"]:
        _number(p, f"{where}.points_each")
    for metric, p in row["problems"].items():
        if metric not in lp.METRICS:
            _fail(f"{where}.problems", f"{metric!r} is not a letter_points metric")
        _keys(p, PROBLEM_KEYS, f"{where}.problems.{metric}", exact=True)
        for f in PROBLEM_KEYS:
            _number(p[f], f"{where}.problems.{metric}.{f}")
    for r in row["missed_row_ids"]:
        _name(r, f"{where}.missed_row_ids", _ROW)
    for cid, v in row["checklist"].items():
        _name(cid, f"{where}.checklist", _CHECK_ID)
        if v not in _CHECK_VALUES:
            _fail(f"{where}.checklist.{cid}", f"{v!r} is not one of {_CHECK_VALUES}")


# ---------------------------------------------------------------------------
# Building a checkpoint (plan §1, §3.1, §3.5)
# ---------------------------------------------------------------------------
def _row_num(row_id: str) -> int:
    return int(row_id[1:]) if _ROW.fullmatch(row_id) else 10**6


def resolve_parent(existing: list[dict], kind: str, parent: str | None, aa_of: str | None) -> str | None:
    """D5: the last kept checkpoint of the same kind, never a failed one. An A/A checkpoint's parent
    is the checkpoint it repeats. A/A checkpoints are never a default parent: they repeat, not change."""
    by_id = {c["id"]: c for c in existing}
    if aa_of is not None:
        if aa_of not in by_id:
            raise CheckpointError(f"--aa-of {aa_of}: no such checkpoint")
        if by_id[aa_of]["kind"] != "letters" or kind != "letters":
            raise CheckpointError("an A/A checkpoint repeats a letters checkpoint")
        if parent is not None and parent != aa_of:
            raise CheckpointError(f"an A/A checkpoint's parent is the one it repeats ({aa_of}), not {parent}")
        return aa_of
    if parent is not None:
        if parent not in by_id:
            raise CheckpointError(f"--parent {parent}: no such checkpoint")
        if not by_id[parent]["kept"]:
            raise CheckpointError(f"--parent {parent} is not kept; a new checkpoint is measured against what is "
                                  "in use (plan D5)")
        return parent
    kept = [c for c in existing if c["kept"] and c["kind"] == kind and c["aa_of"] is None]
    return kept[-1]["id"] if kept else None


def _common(existing: list[dict], *, kind: str, experiment: str, note: str, kept: bool, verdict: str, rule: str,
            parent: str | None, aa_of: str | None, replaces: str | None, created: str | None) -> dict:
    if verdict not in VERDICTS:
        raise CheckpointError(f"verdict must be one of {VERDICTS}, not {verdict!r}")
    if verdict != "none" and not rule.strip():
        raise CheckpointError("a pass / fail verdict needs the --rule it was judged against (plan D10)")
    if replaces is not None and replaces not in {c["id"] for c in existing}:
        raise CheckpointError(f"--replaces {replaces}: no such checkpoint")
    return {
        "id": f"c{len(existing)}",
        "created": created or datetime.datetime.now().isoformat(timespec="seconds"),
        "kind": kind, "experiment": experiment, "note": note,
        "parent": resolve_parent(existing, kind, parent, aa_of),
        "kept": kept, "verdict": verdict, "rule": rule, "aa_of": aa_of, "replaces": replaces,
    }


def _join(judging: list[tuple[str, dict]], run_id: str, frozen: dict) -> tuple[dict, str]:
    """One judging from one or more tags (halves covering different letters). Checks every record's
    judge block against the frozen judge, and that no two halves cover the same ad. Returns
    (key -> record, backend)."""
    want = frozen["version"]
    joined: dict[str, dict] = {}
    owner: dict[str, str] = {}
    backends = set()
    for tag, data in judging:
        for key, rec in data.items():
            if key in joined:
                raise CheckpointError(f"judge tags {owner[key]} and {tag} both cover {key}; halves joined with a "
                                      "comma must cover different letters")
            if rec.get("run") not in (None, run_id):
                raise CheckpointError(f"judge-{tag}.json's {key} is a letter of run {rec.get('run')}, not {run_id}")
            blocks = rec.get("judge") or []
            if not blocks:
                raise CheckpointError(f"judge-{tag}.json's {key} has no judge block")
            for b in blocks:
                diff = sorted(f for f in set(want) | set(b) if want.get(f) != b.get(f))
                if diff:
                    raise CheckpointError(f"judge-{tag}.json's {key} was not judged by frozen judge "
                                          f"{frozen['name']}: {', '.join(diff)} differ")
                backends.add(b["backend"])
            joined[key], owner[key] = rec, tag
    if len(backends) != 1:
        raise CheckpointError(f"one judging must use one backend, not {sorted(backends)}")
    return joined, backends.pop()


def _readd(rec: dict, tag: str, key: str) -> dict[str, list[str]]:
    """D12: re-add the saved problem list with the scorer; it must give the saved points (and the
    saved per-metric levels). Returns the scorer's by_metric."""
    try:
        points, by_metric = lp.score(rec["issues"]), lp.by_metric(rec["issues"])
    except (KeyError, ValueError) as e:
        raise CheckpointError(f"judge-{tag}.json's {key}: the scorer can't read its problem list ({e})") from e
    if points != rec.get("points"):
        raise CheckpointError(f"judge-{tag}.json's {key}: the scorer gives {points}, the file says "
                              f"{rec.get('points')}; scripts/letter_points.py has changed since it was judged (D12)")
    if "by_metric" in rec and by_metric != rec["by_metric"]:
        raise CheckpointError(f"judge-{tag}.json's {key}: the scorer's problem levels differ from the file's (D12)")
    return by_metric


def _problems(by_metric: dict[str, list[str]]) -> dict[str, dict]:
    out = {}
    for m in lp.METRICS:
        levels = by_metric.get(m, [])
        low, high = levels.count("low"), levels.count("high")
        out[m] = {"n": low + high, "low": low, "high": high,
                  "lost": round(low * lp.VALUES[m][0] + high * lp.VALUES[m][1], 4)}
    return out


def _merge_checklists(checklists: list[dict]) -> dict[str, str]:
    """One Y/N per checklist item. With several judgings: their answer when they all agree, else
    "mixed" (the plan does not average a Y/N; Decision log 2026-11-25)."""
    out = {}
    for cid in sorted({cid for c in checklists for cid in c}):
        answers = {c.get(cid) for c in checklists}
        out[cid] = answers.pop() if len(answers) == 1 else "mixed"
    return out


def _mean(values: list[float], places: int = 4) -> float:
    return round(sum(values) / len(values), places)


def _by_task(rows: list[dict]) -> dict[str, dict]:
    """llm_usage rows of one letter -> task -> summed tokens, cost and time (letter_lab._cost_rows,
    per letter instead of per run)."""
    out: dict[str, dict] = {}
    for u in rows:
        t = out.setdefault(u["task"], {"model": u["model"], "calls": 0, "in": 0, "cached": 0, "out": 0,
                                       "think": 0, "cost_usd": 0.0, "ms": 0})
        if t["model"] != u["model"]:
            raise CheckpointError(f"task {u['task']} used two models in one letter ({t['model']}, {u['model']}); "
                                  "a checkpoint row keeps one model per task")
        t["calls"] += 1
        for f, src in (("in", "input_tokens"), ("cached", "cached_tokens"), ("out", "output_tokens"),
                       ("think", "thinking_tokens"), ("ms", "duration_ms")):
            t[f] += int(u.get(src) or 0)
        t["cost_usd"] += float(u.get("cost_usd") or 0)
    for t in out.values():
        t["cost_usd"] = round(t["cost_usd"], 6)
    return dict(sorted(out.items()))


def build_letters_checkpoint(existing: list[dict], *, run_meta: dict, judgings: list[list[tuple[str, dict]]],
                             frozen_judge: dict, usage: list[dict], ads: dict[str, dict], scorer_sha256: str,
                             prices: dict, thinking: dict, commit: str, set_name: str, experiment: str,
                             kept: bool, note: str = "", verdict: str = "none", rule: str = "",
                             parent: str | None = None, aa_of: str | None = None, replaces: str | None = None,
                             allow_test: bool = False, created: str | None = None) -> dict:
    """A `letters` checkpoint from a run's run.json, its judging(s) and its llm_usage rows.

    judgings: one list per judging (D11), each a list of (tag, judge-<tag>.json) halves.
    usage: llm_usage rows of the run's letter runs (run_id, task, tier, model, tokens, cost, ms).
    ads: letter_lab._v2_ads(), key -> {"side", "relevant", ...}.
    Raises CheckpointError on every refusal of plan §3.1; builds every row from the allowlist."""
    run_id = run_meta["run_id"]
    results = run_meta["results"]
    if not judgings:
        raise CheckpointError("a letters checkpoint needs at least one --judge")
    test_keys = [r["key"] for r in results if ads.get(r["key"], {}).get("side") == "test"]
    if (run_meta.get("split") == "test" or test_keys) and not allow_test:
        raise CheckpointError(f"{run_id} has test-split letters ({len(test_keys)}); the test split stays closed "
                              "until the end of the project. Give --allow-test only for that final run")

    joined, backends = [], []
    for judging in judgings:
        j, backend = _join(judging, run_id, frozen_judge)
        joined.append(j)
        backends.append(backend)
    run_keys = {r["key"] for r in results}
    for judging, j in zip(judgings, joined):
        stray = sorted(set(j) - run_keys)
        if stray:
            raise CheckpointError(f"judging {'+'.join(t for t, _ in judging)} covers {stray[0]}, which is not a "
                                  f"letter of {run_id}")

    usage_by_run: dict[int, list[dict]] = {}
    for u in usage:
        usage_by_run.setdefault(u["run_id"], []).append(u)

    letters = []
    for r in results:
        key = r["key"]
        has_draft = "error" not in r and (r.get("drafts") or 0) > 0
        recs = [(judging, j[key]) for judging, j in zip(judgings, joined) if key in j]
        if recs and len(recs) != len(joined):
            missing = next('+'.join(t for t, _ in g) for g, j in zip(judgings, joined) if key not in j)
            raise CheckpointError(f"{key} is missing from judging {missing}; an average must be over the same "
                                  "judgings for every letter (plan §3.1)")
        if has_draft and not recs:
            raise CheckpointError(f"{key} has a draft but no judging covers it")
        by_task = _by_task(usage_by_run.get(r.get("letter_run_id"), []))
        row = {
            "key": key, "relevant": ads.get(key, {}).get("relevant"),
            "status": r.get("status") or ("error" if "error" in r else None),
            "clean": r.get("clean"), "drafts": r.get("drafts"),
            "seconds": round(float(r.get("seconds") or 0), 1),
            "cost_usd": round(sum(t["cost_usd"] for t in by_task.values()), 6),
            "by_task": by_task,
            "words": None, "stock_hits": None, "points": None, "points_each": [], "problems": {},
            "missed_row_ids": [], "checklist": {},
        }
        if recs:
            each, problems, missed, checklists = [], [], set(), []
            for judging, rec in recs:
                tag = next(t for t, data in judging if key in data)
                problems.append(_problems(_readd(rec, tag, key)))
                each.append(float(rec["points"]))
                missed |= {str(m) for m in rec.get("missed_rows", [])}
                checklists.append(rec.get("checklist", {}))
            for f in ("words", "stock_hits"):  # code-set from the letter itself: the same in every judging
                seen = {rec.get(f) for _, rec in recs}
                if len(seen) != 1:
                    raise CheckpointError(f"{key}: the judgings disagree on {f} ({sorted(map(str, seen))}), so "
                                          "they did not judge the same letter")
                row[f] = seen.pop()
            row["points_each"] = each
            row["points"] = _mean(each)
            row["problems"] = {m: {f: _mean([p[m][f] for p in problems]) for f in PROBLEM_KEYS} for m in lp.METRICS}
            row["missed_row_ids"] = sorted(missed, key=_row_num)
            row["checklist"] = _merge_checklists(checklists)
        letters.append(row)

    judged = [x for x in letters if x["points"] is not None]
    models: dict[str, set] = {}
    for u in usage:
        if u["run_id"] in {r.get("letter_run_id") for r in results}:
            models.setdefault(u["tier"], set()).add(u["model"])
    used = sorted({t["model"] for x in letters for t in x["by_task"].values()})
    cp = _common(existing, kind="letters", experiment=experiment, note=note, kept=kept, verdict=verdict, rule=rule,
                 parent=parent, aa_of=aa_of, replaces=replaces, created=created)
    cp |= {
        "comparable": {"set": set_name, "split": run_meta.get("split"), "judge": frozen_judge["name"],
                       "scorer_sha256": scorer_sha256, "judgings": len(judgings)},
        "settings": {
            "run": run_id, "judge_tags": [[t for t, _ in g] for g in judgings], "judge_backends": backends,
            "commit": commit, "engine": run_meta.get("engine"),
            "models": {t: "+".join(sorted(models[t])) for t in TIERS if t in models},
            "thinking": {t: thinking[t] for t in TIERS if t in thinking},
            "prices_seen": {m: list(prices[m]) for m in used if m in prices},
        },
        "totals": {"letters": len(letters), "cost_usd": round(sum(x["cost_usd"] for x in letters), 6),
                   "seconds": round(sum(x["seconds"] for x in letters), 1),
                   "points_mean": _mean([x["points"] for x in judged]) if judged else None},
        "letters": letters,
        "measures": {},
    }
    check_allowlist(cp)
    return cp


def build_measure_checkpoint(existing: list[dict], *, experiment: str, values: dict[str, float], commit: str,
                             kept: bool, note: str = "", verdict: str = "none", rule: str = "",
                             parent: str | None = None, replaces: str | None = None,
                             created: str | None = None) -> dict:
    """A `measure` checkpoint (E1, E6 and the like): no letters, the experiment's own named numbers."""
    if not values:
        raise CheckpointError("a measure checkpoint needs at least one --value NAME=NUMBER")
    cp = _common(existing, kind="measure", experiment=experiment, note=note, kept=kept, verdict=verdict, rule=rule,
                 parent=parent, aa_of=None, replaces=replaces, created=created)
    cp |= {"comparable": {}, "settings": {"commit": commit}, "totals": {}, "letters": [],
           "measures": {k: float(v) for k, v in values.items()}}
    check_allowlist(cp)
    return cp


def parse_values(pairs: list[str]) -> dict[str, float]:
    """--value NAME=NUMBER, repeated."""
    out = {}
    for p in pairs:
        name, sep, num = p.partition("=")
        if not sep:
            raise CheckpointError(f"--value {p!r}: expected NAME=NUMBER")
        try:
            out[name.strip()] = float(num)
        except ValueError as e:
            raise CheckpointError(f"--value {p!r}: {num!r} is not a number") from e
    return out


# ---------------------------------------------------------------------------
# The JSONL file: append-only (plan D1)
# ---------------------------------------------------------------------------
def dumps(cp: dict) -> str:
    return json.dumps(cp, ensure_ascii=True)


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for i, cp in enumerate(out):
        if cp.get("id") != f"c{i}":
            raise CheckpointError(f"{path.name} line {i + 1} is {cp.get('id')}, expected c{i}: the file was edited")
    return out


def append(path: Path, cp: dict) -> None:
    """Add one line. Never rewrites the file: earlier lines stay byte-for-byte as they were."""
    existing = load(path)
    if cp["id"] != f"c{len(existing)}":
        raise CheckpointError(f"{cp['id']} would not be the next id (c{len(existing)}); rebuild it")
    check_allowlist(cp)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = path.read_bytes() if path.exists() else b""
    with path.open("ab") as fh:
        if raw and not raw.endswith(b"\n"):
            fh.write(b"\n")
        fh.write((dumps(cp) + "\n").encode("utf-8"))


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
