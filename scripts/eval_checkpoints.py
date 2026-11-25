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
import math
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
SETTINGS_KEYS = ("run", "judge_tags", "judge_backends", "commit", "engine", "models", "thinking", "prices_seen",
                 "given_by_hand")
# Settings the owner may type in for a run that did not save them (D13); `given_by_hand` names them.
HAND_SETTINGS = ("commit", "thinking")
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
    hand = s.get("given_by_hand", [])
    if not isinstance(hand, list) or any(h not in HAND_SETTINGS for h in hand):
        _fail("settings.given_by_hand", f"{hand!r} must be a list of {HAND_SETTINGS}")

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
                             allow_test: bool = False, created: str | None = None,
                             given_by_hand: tuple | list = ()) -> dict:
    """A `letters` checkpoint from a run's run.json, its judging(s) and its llm_usage rows.

    judgings: one list per judging (D11), each a list of (tag, judge-<tag>.json) halves.
    usage: llm_usage rows of the run's letter runs (run_id, task, tier, model, tokens, cost, ms).
    ads: letter_lab._v2_ads(), key -> {"side", "relevant", ...}.
    commit / thinking: what the run used; None / {} = unknown (D13: never today's values).
    given_by_hand: which of them the owner typed in (D13), not read from run.json.
    Raises CheckpointError on every refusal of plan §3.1; builds every row from the allowlist."""
    bad = [h for h in given_by_hand if h not in HAND_SETTINGS]
    if bad:
        raise CheckpointError(f"given_by_hand: {bad} are not settings that can be typed in {HAND_SETTINGS}")
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
            "given_by_hand": [h for h in HAND_SETTINGS if h in given_by_hand],
        },
        "totals": {"letters": len(letters), "cost_usd": round(sum(x["cost_usd"] for x in letters), 6),
                   "seconds": round(sum(x["seconds"] for x in letters), 1),
                   "points_mean": _mean([x["points"] for x in judged]) if judged else None},
        "letters": letters,
        "measures": {},
    }
    if aa_of is not None:
        _check_aa(cp, next(c for c in existing if c["id"] == aa_of))
    check_allowlist(cp)
    return cp


def _check_aa(cp: dict, orig: dict) -> None:
    """An A/A repeats `orig` with no change, so it must be comparable with it; and a judge-only A/A
    (the same run) must be a fresh judging, or every judge difference is 0 and the noise looks nil."""
    for f in COMPARABLE_KEYS:
        if cp["comparable"].get(f) != orig["comparable"].get(f):
            raise CheckpointError(f"an A/A must match the checkpoint it repeats: comparable.{f} is "
                                  f"{cp['comparable'].get(f)!r}, {orig['id']} has {orig['comparable'].get(f)!r}")
    s, o = cp["settings"], orig["settings"]
    if s["run"] == o.get("run"):
        shared = {t for g in s["judge_tags"] for t in g} & {t for g in o.get("judge_tags", []) for t in g}
        if shared:
            raise CheckpointError(f"a judge-only A/A (same run as {orig['id']}) needs a fresh judging; "
                                  f"{sorted(shared)} judged {orig['id']} already")


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
# Pairing and stats (plan §3.2 - §3.5). Pure: checkpoints and a price table in, numbers out.
# ---------------------------------------------------------------------------
# Who produces a measure decides which A/A can be its noise reference (D14): the judge's measures
# (points, problems) move when the same letters are judged again; the writer's (cost, time, drafts)
# only move when new letters are written.
JUDGE, WRITER = "judge", "writer"
MEASURES: tuple[tuple[str, str], ...] = (
    ("points", JUDGE), ("cost_today", WRITER), ("cost_usd", WRITER), ("seconds", WRITER), ("drafts", WRITER),
    *((f"{m}.{f}", JUDGE) for m in lp.METRICS for f in ("n", "lost")),
)
MEASURE_KIND = dict(MEASURES)
WITHIN, BEYOND, NO_REF = "within noise", "beyond noise", "no noise reference yet"
JUDGE_ONLY, WRITER_JUDGE = "judge-only", "writer+judge"
_EPS = 1e-9


def task_cost_at(task: dict, prices: dict) -> float | None:
    """One task's cost at a price table (USD per 1M tokens: input, output, cached input). Input
    tokens include the cached ones; thinking bills as output (client.estimate_cost_usd's rule).
    None when the table has no price for the model: a cost is never guessed."""
    price = prices.get(task["model"])
    if price is None:
        return None
    p_in, p_out, p_cached = price
    fresh = max(0, task["in"] - task["cached"])
    return (fresh * p_in + task["cached"] * p_cached + (task["out"] + task["think"]) * p_out) / 1_000_000


def cost_at(row: dict, prices: dict) -> float | None:
    """A letter's cost recomputed from its by_task tokens at `prices` (plan §3.4)."""
    costs = [task_cost_at(t, prices) for t in row["by_task"].values()]
    return None if any(c is None for c in costs) else round(sum(costs), 6)


def value(row: dict, measure: str, prices: dict) -> float | None:
    if measure == "cost_today":
        return cost_at(row, prices)
    if "." in measure:
        metric, field = measure.split(".")
        p = row["problems"].get(metric)
        return None if p is None else p[field]
    return row.get(measure)


def _sd(values: list[float]) -> float | None:
    """Sample standard deviation (n - 1), as plan §9.0's 0.65 was measured."""
    if len(values) < 2:
        return None
    m = sum(values) / len(values)
    return (sum((v - m) ** 2 for v in values) / (len(values) - 1)) ** 0.5


def check_comparable(a: dict, b: dict) -> None:
    """§3.2: `comparable` must match exactly, else refuse and name the field."""
    if a["kind"] != b["kind"]:
        raise CheckpointError(f"{a['id']} is a {a['kind']} checkpoint and {b['id']} a {b['kind']} one; "
                              "they can't be compared")
    for f in COMPARABLE_KEYS:
        x, y = a["comparable"].get(f), b["comparable"].get(f)
        if x != y:
            hint = (" (an average and a single judging mix two noise levels: judge the other side again and "
                    "average it, plan §3.5)") if f == "judgings" else ""
            raise CheckpointError(f"{a['id']} and {b['id']} are not comparable: comparable.{f} is {x!r} vs {y!r}"
                                  + hint)


def pair(a: dict, b: dict) -> dict:
    """Letters paired by ad key (§3.2). A letter with no draft (no points) on either side is not
    paired; ads on one side only are listed and left out. `relevant` = relevant on both sides."""
    ra, rb = {r["key"]: r for r in a["letters"]}, {r["key"]: r for r in b["letters"]}
    pairs, failed = [], []
    for key, x in ra.items():
        if key in rb:
            y = rb[key]
            if x["points"] is None or y["points"] is None:
                failed.append(key)
            else:
                pairs.append((key, x, y))
    return {
        "pairs": pairs,
        "relevant": [p for p in pairs if p[1]["relevant"] is True and p[2]["relevant"] is True],
        "relevant_disagree": [k for k, x, y in pairs if x["relevant"] != y["relevant"]],
        "only_a": [k for k in ra if k not in rb], "only_b": [k for k in rb if k not in ra],
        "failed": failed,
    }


def diff_stats(pairs: list[tuple], measure: str, prices: dict) -> dict:
    """Mean of A and B, the mean paired difference (B - A), its SD, and ads up / same / down,
    over the pairs where both sides have the measure."""
    vals = [(value(x, measure, prices), value(y, measure, prices)) for _, x, y in pairs]
    vals = [(p, q) for p, q in vals if p is not None and q is not None]
    d = [q - p for p, q in vals]
    n = len(vals)
    return {
        "n": n,
        "a": sum(p for p, _ in vals) / n if n else None,
        "b": sum(q for _, q in vals) / n if n else None,
        "diff": sum(d) / n if n else None,
        "sd": _sd(d),
        "up": sum(1 for x in d if x > _EPS), "same": sum(1 for x in d if abs(x) <= _EPS),
        "down": sum(1 for x in d if x < -_EPS),
    }


def _replaced(checkpoints: list[dict]) -> set[str]:
    return {c["replaces"] for c in checkpoints if c.get("replaces")}


def aa_kind(aa: dict, orig: dict) -> str:
    """D14: the same run judged again = judge-only; new letters with the same settings = writer + judge."""
    return JUDGE_ONLY if aa["settings"].get("run") == orig["settings"].get("run") else WRITER_JUDGE


def aa_pairs(checkpoints: list[dict]) -> list[dict]:
    """Every A/A pair, oldest first, with its kind. A checkpoint a later one replaces is skipped."""
    by_id, gone = {c["id"]: c for c in checkpoints}, _replaced(checkpoints)
    return [{"aa": c, "of": by_id[c["aa_of"]], "kind": aa_kind(c, by_id[c["aa_of"]])}
            for c in checkpoints if c.get("aa_of") and c["id"] not in gone and c["aa_of"] in by_id]


def noise_refs(checkpoints: list[dict], comparable: dict, prices: dict) -> dict:
    """§3.3 / §3.5: the A/A pair each kind of measure is labelled against.

    Judge measures: the most recent writer + judge A/A, else the most recent judge-only one; each
    preferring the same `judgings` and falling back to a single-judging one ("conservative").
    Writer measures: a writer + judge A/A only (a judge-only A/A's letters are the same, so its
    cost / time / drafts differences are all 0). Returns {JUDGE: ref | None, WRITER: ref | None},
    a ref = {"aa", "of", "kind", "conservative", "n", "sd": {measure: s}}."""
    def match(c: dict) -> str | None:
        rest = [f for f in COMPARABLE_KEYS if f != "judgings"]
        if any(c["comparable"].get(f) != comparable.get(f) for f in rest):
            return None
        if c["comparable"].get("judgings") == comparable.get("judgings"):
            return "exact"
        if c["comparable"].get("judgings") == 1:
            return "conservative"  # a single-judging reference for an average: its T is larger
        return None  # a reference less noisy than the comparison would over-claim

    found = {}
    for p in reversed(aa_pairs(checkpoints)):
        how = match(p["aa"])
        if how is not None:
            found.setdefault((p["kind"], how), p)

    def ref(p: dict | None, conservative: bool) -> dict | None:
        if p is None:
            return None
        pairs = pair(p["of"], p["aa"])["pairs"]
        return {"aa": p["aa"]["id"], "of": p["of"]["id"], "kind": p["kind"], "conservative": conservative,
                "n": len(pairs), "sd": {m: diff_stats(pairs, m, prices)["sd"] for m, _ in MEASURES}}

    judge = next(((found[k], k[1] == "conservative") for k in
                  ((WRITER_JUDGE, "exact"), (WRITER_JUDGE, "conservative"), (JUDGE_ONLY, "exact"),
                   (JUDGE_ONLY, "conservative")) if k in found), (None, False))
    # The writer's measures don't depend on how often the letters were judged: no conservative mark.
    writer = found.get((WRITER_JUDGE, "exact")) or found.get((WRITER_JUDGE, "conservative"))
    return {JUDGE: ref(*judge), WRITER: ref(writer, False)}


def noise(stats: dict, measure: str, refs: dict) -> dict:
    """The noise label of one mean difference: T = 2 s / sqrt(n), n = this comparison's pairs.
    s = 0 (the A/A never moved on this measure) gives no reference, never T = 0: 17 letters that
    did not move say little about what a new letter would do (Decision log, step 2)."""
    ref = refs[MEASURE_KIND[measure]]
    out = {"label": NO_REF, "T": None, "s": None, "ref": None, "kind": None, "provisional": False,
           "conservative": False}
    if ref is None or not stats["n"]:
        return out
    s = ref["sd"].get(measure)
    out |= {"ref": f"{ref['aa']} vs {ref['of']}", "kind": ref["kind"], "s": s}
    if not s:
        return out
    t = 2 * s / stats["n"] ** 0.5
    return out | {"label": BEYOND if abs(stats["diff"]) > t else WITHIN, "T": t,
                  "provisional": ref["kind"] == JUDGE_ONLY, "conservative": ref["conservative"]}


def label_text(nz: dict) -> str:
    """The label as the reports print it (§3.3, §3.5)."""
    text = nz["label"]
    if nz["label"] == NO_REF and nz["ref"] is not None:
        text += f" ({nz['ref']} never moved on this measure)"
    if nz["provisional"]:
        text += "; provisional: judge noise only, writer noise not included"
    if nz["conservative"]:
        text += "; conservative: reference from single judgings"
    return text


def task_table(pairs: list[tuple], prices: dict) -> list[dict]:
    """§4.4: per llm_usage task, $ / letter at today's prices and s / letter, A and B, over the
    paired letters (a task a letter didn't run counts 0 for it)."""
    n = len(pairs) or 1
    tasks = sorted({t for _, x, y in pairs for r in (x, y) for t in r["by_task"]})
    out = []
    for task in tasks:
        row = {"task": task}
        for side, i in (("a", 1), ("b", 2)):
            ts = [p[i]["by_task"].get(task) for p in pairs]
            costs = [task_cost_at(t, prices) if t else 0.0 for t in ts]
            row[f"{side}_cost"] = None if any(c is None for c in costs) else sum(costs) / n
            row[f"{side}_s"] = sum(t["ms"] for t in ts if t) / 1000 / n
            row[f"{side}_calls"] = sum(t["calls"] for t in ts if t) / n
        out.append(row)
    return sorted(out, key=lambda r: -(r["b_cost"] or 0))


def settings_diff(a: dict, b: dict) -> list[tuple[str, object, object]]:
    """§4.2: every `settings` field that differs, one tier / model at a time. Listed, never refused."""
    sa, sb = a["settings"], b["settings"]
    out = []
    for f in ("run", "engine", "commit", "judge_tags", "judge_backends", "given_by_hand"):
        if sa.get(f) != sb.get(f):
            out.append((f, sa.get(f), sb.get(f)))
    for f in ("models", "thinking"):
        for tier in TIERS:
            x, y = sa.get(f, {}).get(tier), sb.get(f, {}).get(tier)
            if x != y:
                out.append((f"{f}.{tier}", x, y))
    pa, pb = sa.get("prices_seen", {}), sb.get("prices_seen", {})
    for m in sorted(set(pa) & set(pb)):
        if pa[m] != pb[m]:
            out.append((f"prices_seen.{m}", pa[m], pb[m]))
    return out


def price_changes(cps: list[dict], prices: dict) -> list[tuple[str, str, list, list | None]]:
    """(checkpoint, model, price then, price today) for every model whose price differs from today's
    table: a price change must never look like an optimisation (D7)."""
    out = []
    for c in cps:
        for m, p in sorted(c["settings"].get("prices_seen", {}).items()):
            now = list(prices[m]) if m in prices else None
            if now != list(p):
                out.append((c["id"], m, list(p), now))
    return out


def resolve_pair(checkpoints: list[dict], a_id: str, b_id: str | None = None) -> tuple[dict, dict]:
    """D8: two ids = A and B; one id = that checkpoint (B) against its parent (A)."""
    by_id = {c["id"]: c for c in checkpoints}
    for i in (a_id, b_id):
        if i is not None and i not in by_id:
            raise CheckpointError(f"no checkpoint {i}")
    if b_id is not None:
        return by_id[a_id], by_id[b_id]
    b = by_id[a_id]
    if b["parent"] is None:
        raise CheckpointError(f"{a_id} has no parent; name the checkpoint to compare it with")
    return by_id[b["parent"]], b


def compare(checkpoints: list[dict], a: dict, b: dict, prices: dict) -> dict:
    """Everything the compare report shows (§4), as numbers. Refuses on a `comparable` mismatch."""
    check_comparable(a, b)
    if a["kind"] == "measure":
        shared = [m for m in a["measures"] if m in b["measures"]]
        return {"kind": "measure", "a": a, "b": b,
                "measures": [(m, a["measures"][m], b["measures"][m], b["measures"][m] - a["measures"][m])
                             for m in shared],
                "only_a": [m for m in a["measures"] if m not in b["measures"]],
                "only_b": [m for m in b["measures"] if m not in a["measures"]],
                "settings_diff": settings_diff(a, b)}
    pg = pair(a, b)
    refs = noise_refs(checkpoints, a["comparable"], prices)
    groups = {}
    for name, pairs in (("all", pg["pairs"]), ("relevant", pg["relevant"])):
        groups[name] = {}
        for m, _ in MEASURES:
            st = diff_stats(pairs, m, prices)
            groups[name][m] = st | {"noise": noise(st, m, refs)}
    return {"kind": "letters", "a": a, "b": b, "pairing": pg, "refs": refs, "groups": groups,
            "tasks": task_table(pg["pairs"], prices), "settings_diff": settings_diff(a, b),
            "price_changes": price_changes([a, b], prices)}


# ---------------------------------------------------------------------------
# The compare report (plan §4). Markdown rendered from compare(); the JSONL is the only source,
# so the report is never edited by hand. It prints numbers, ad keys, row ids, settings and the
# owner's own note / rule, nothing else (those are all a checkpoint holds).
# ---------------------------------------------------------------------------
VERTEX_LINE = "judged on Vertex: Gemini trial credits, ends 2026-12-30"
UNKNOWN = "unknown"
DASH = "—"
PROVISIONAL_MARK, CONSERVATIVE_MARK = "†", "‡"
MEASURE_NAMES = {"points": "Points (0-10)", "cost_today": "$ / letter, today's prices",
                 "cost_usd": "$ / letter, as recorded", "seconds": "Seconds / letter", "drafts": "Drafts / letter",
                 **{f"{m}.n": f"{m}: instances / letter" for m in lp.METRICS},
                 **{f"{m}.lost": f"{m}: points lost / letter" for m in lp.METRICS}}
HEADLINE = ("points", "cost_today", "cost_usd", "seconds")
GROUP_NAMES = {"all": "all", "relevant": "relevant"}


def fmt(measure: str, v: float | None, signed: bool = False) -> str:
    """One number the way the reports print it: $ to 4 places, seconds whole, the rest to 2."""
    if v is None:
        return DASH
    if measure in ("cost_today", "cost_usd"):
        body = f"${abs(v):.4f}"
        return ("+" if v >= 0 else "-") + body if signed else body
    spec = ".0f" if measure == "seconds" else ".2f"
    return f"{v:+{spec}}" if signed else f"{v:{spec}}"


def _esc(text) -> str:
    """Safe inside a markdown table cell (the owner's note / rule may hold a | or a line break)."""
    return str(text).replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


def noise_cell(nz: dict, measure: str) -> str:
    """A noise label for a table cell. The provisional / conservative texts are footnote marks
    (PROVISIONAL_MARK, CONSERVATIVE_MARK) explained under the table, so they aren't repeated per row."""
    if nz["label"] == NO_REF:
        return NO_REF + (f" ({nz['ref']} never moved)" if nz["ref"] else "")
    marks = (PROVISIONAL_MARK if nz["provisional"] else "") + (CONSERVATIVE_MARK if nz["conservative"] else "")
    return f"{nz['label']} (T {fmt(measure, nz['T'])}){marks}"


def footnotes(noises: list[dict]) -> list[str]:
    """The lines explaining the marks noise_cell used in one table (none = no lines)."""
    out = []
    prov = next((nz for nz in noises if nz["provisional"]), None)
    cons = next((nz for nz in noises if nz["conservative"]), None)
    if prov:
        out.append(f"{PROVISIONAL_MARK} provisional: judge noise only, writer noise not included "
                   f"(reference {prov['ref']}, the same letters judged again).")
    if cons:
        out.append(f"{CONSERVATIVE_MARK} conservative: reference from single judgings (reference {cons['ref']}; "
                   "its T is larger, so it can only under-claim).")
    return [""] + out if out else []


def _tags(cp: dict) -> str:
    judgings = [" + ".join(f"`{t}`" for t in g) for g in cp["settings"].get("judge_tags", [])]
    if not judgings:
        return UNKNOWN
    return judgings[0] if len(judgings) == 1 else " and ".join(judgings) + ", averaged"


def who(cp: dict) -> str:
    """One line naming a checkpoint: id, experiment, run, judging."""
    if cp["kind"] == "measure":
        return f"**{cp['id']}** ({cp['experiment']}, measure checkpoint)"
    aa = f", repeat of {cp['aa_of']}" if cp.get("aa_of") else ""
    return (f"**{cp['id']}** ({cp['experiment']}{aa}): run `{cp['settings'].get('run')}`, judged {_tags(cp)}")


def _yes(v) -> str:
    return {True: "yes", False: "no"}.get(v, DASH)


def _vertex_lines(cps: list[dict]) -> list[str]:
    return [f"- {c['id']}: {VERTEX_LINE}." for c in cps if "vertex" in c["settings"].get("judge_backends", [])]


def render_headline(cmp: dict) -> list[str]:
    """§4.1, also what the terminal prints."""
    a, b = cmp["a"], cmp["b"]
    if cmp["kind"] == "measure":
        lines = ["## 1. Headline", "", f"A = {who(a)}. B = {who(b)}.", "",
                 f"| Measure | A ({a['id']}) | B ({b['id']}) | B − A |", "|---|---|---|---|"]
        lines += [f"| {_esc(m)} | {x:g} | {y:g} | {d:+g} |" for m, x, y, d in cmp["measures"]]
        if not cmp["measures"]:
            lines.append(f"| (no measure on both sides) | {DASH} | {DASH} | {DASH} |")
        return lines + ["", "Measure checkpoints have no repeat check, so these differences carry no noise label."]
    lines = ["## 1. Headline", "", f"A = {who(a)}.  ", f"B = {who(b)}.", "",
             f"| Measure | Ads | A ({a['id']}) | B ({b['id']}) | B − A | Noise | Ads up / same / down |",
             "|---|---|---|---|---|---|---|"]
    noises = []
    for m in HEADLINE:
        for g in ("all", "relevant"):
            st = cmp["groups"][g][m]
            noises.append(st["noise"])
            lines.append(f"| {MEASURE_NAMES[m] if g == 'all' else ''} | {GROUP_NAMES[g]} {st['n']} | "
                         f"{fmt(m, st['a'])} | {fmt(m, st['b'])} | {fmt(m, st['diff'], True)} | "
                         f"{noise_cell(st['noise'], m)} | {st['up']} / {st['same']} / {st['down']} |")
    return lines + footnotes(noises)


def _setting_cells(cp: dict) -> dict[str, str]:
    """Every setting as the report prints it: unknown when the run did not save it and nobody typed
    it in (D13), "(typed in)" when the owner did."""
    s = cp["settings"]
    hand = s.get("given_by_hand", [])

    def show(v, field=None):
        if v is None or v == "":
            return UNKNOWN
        return f"`{v}`" + (" (typed in)" if field in hand else "")

    backends = s.get("judge_backends", [])
    out = {"run": show(s.get("run")), "engine": show(s.get("engine")), "commit": show(s.get("commit"), "commit"),
           "judge": f"`{cp['comparable'].get('judge')}`: {_tags(cp)} on " + (
               " / ".join(f"`{x}`" for x in backends) if backends else UNKNOWN)}
    for tier in TIERS:
        out[f"model, {tier}"] = show(s.get("models", {}).get(tier))
    for tier in TIERS:
        out[f"thinking, {tier}"] = show(s.get("thinking", {}).get(tier), "thinking")
    for m, p in sorted(s.get("prices_seen", {}).items()):
        out[f"price `{m}`"] = f"${p[0]:.2f} / ${p[1]:.2f} / ${p[2]:.3f}"
    return out


def _what_changed(cmp: dict) -> list[str]:
    a, b = cmp["a"], cmp["b"]
    lines = ["## 2. What changed", "",
             f"| | A ({a['id']}) | B ({b['id']}) |", "|---|---|---|"]
    for label, f in (("Experiment", "experiment"), ("Created", "created"), ("Note", "note"),
                     ("Kept", "kept"), ("Verdict", "verdict"), ("Rule (written before the run)", "rule"),
                     ("Parent", "parent"), ("Repeats (A/A of)", "aa_of"), ("Replaces", "replaces")):
        cell = [(_yes(c[f]) if f == "kept" else _esc(c[f]) if c[f] not in (None, "") else DASH) for c in (a, b)]
        lines.append(f"| {label} | {cell[0]} | {cell[1]} |")
    if cmp["kind"] == "measure":
        sa, sb = (_esc(c["settings"].get("commit") or UNKNOWN) for c in (a, b))
        return lines + ["", "| Setting | A | B | Differs |", "|---|---|---|---|",
                        f"| commit | {sa} | {sb} | {'yes' if sa != sb else ''} |"]
    ca, cb = _setting_cells(a), _setting_cells(b)
    lines += ["", "Settings (prices are $ per 1M tokens: input / output / cached input; *unknown* = the run "
              "did not save it and it was not typed in):", "",
              "| Setting | A | B | Differs |", "|---|---|---|---|"]
    for f in [*ca, *(f for f in cb if f not in ca)]:
        x, y = ca.get(f, "not used"), cb.get(f, "not used")
        lines.append(f"| {f} | {x} | {y} | {'yes' if x != y else ''} |")
    c = a["comparable"]
    lines += ["", f"Comparable (must match, and does): set `{c.get('set')}`, split `{c.get('split')}`, judge "
              f"`{c.get('judge')}`, scorer `{(c.get('scorer_sha256') or '')[:12]}`, {c.get('judgings')} judging(s) "
              "per letter."]
    vertex = _vertex_lines([a, b])
    if vertex:
        lines += [""] + vertex
    lines.append("")
    if cmp["price_changes"]:
        lines.append("Prices that differ from today's (costs \"at today's prices\" use today's):")
        for cid, m, then, now in cmp["price_changes"]:
            today = (f"${now[0]:.2f} / ${now[1]:.2f} / ${now[2]:.3f}" if now else
                     "not in today's table, so its cost at today's prices is unknown")
            lines.append(f"- {cid}, `{m}`: ${then[0]:.2f} / ${then[1]:.2f} / ${then[2]:.3f} then; today {today}.")
    else:
        lines.append("Prices: every model both runs used costs the same today as when it ran.")
    return lines


def _problems_section(cmp: dict) -> list[str]:
    lines = ["## 3. Problems per letter", "",
             "Each problem type the judge charges (`scripts/letter_points.py`): instances and points lost per "
             "letter. Better = fewer points lost on that letter.", ""]
    for g in ("all", "relevant"):
        n = cmp["groups"][g]["points"]["n"]
        lines += [f"### {'All paired ads' if g == 'all' else 'Relevant ads'} ({n})", "",
                  "| Problem | Instances A | B | B − A | Points lost A | B | B − A | Noise (points lost) | "
                  "Letters better / same / worse |", "|---|---|---|---|---|---|---|---|---|"]
        noises = []
        for m in lp.METRICS:
            sn, sl = cmp["groups"][g][f"{m}.n"], cmp["groups"][g][f"{m}.lost"]
            noises.append(sl["noise"])
            lines.append(f"| `{m}` | {fmt('n', sn['a'])} | {fmt('n', sn['b'])} | {fmt('n', sn['diff'], True)} | "
                         f"{fmt('lost', sl['a'])} | {fmt('lost', sl['b'])} | {fmt('lost', sl['diff'], True)} | "
                         f"{noise_cell(sl['noise'], f'{m}.lost')} | {sl['down']} / {sl['same']} / {sl['up']} |")
        lines += footnotes(noises) + [""]
    return lines


def _tasks_section(cmp: dict) -> list[str]:
    n = len(cmp["pairing"]["pairs"])
    lines = ["## 4. Cost and time by step", "",
             f"Per `llm_usage` task, per letter over the {n} paired letters, at today's prices. Seconds are the "
             "time inside that step's model calls; a letter's wall-clock time (headline) also counts code and "
             "waits.", "",
             "| Step | Calls / letter A → B | $ A | $ B | $ B − A | Seconds A | Seconds B | Seconds B − A |",
             "|---|---|---|---|---|---|---|---|"]
    tot = {"a_cost": 0.0, "b_cost": 0.0, "a_s": 0.0, "b_s": 0.0}
    for r in cmp["tasks"]:
        d = None if r["a_cost"] is None or r["b_cost"] is None else r["b_cost"] - r["a_cost"]
        lines.append(f"| `{r['task']}` | {r['a_calls']:.1f} → {r['b_calls']:.1f} | {fmt('cost_today', r['a_cost'])} | "
                     f"{fmt('cost_today', r['b_cost'])} | {fmt('cost_today', d, True)} | {r['a_s']:.0f} | "
                     f"{r['b_s']:.0f} | {r['b_s'] - r['a_s']:+.0f} |")
        for f in tot:
            tot[f] = None if tot[f] is None or r[f] is None else tot[f] + r[f]
    if not cmp["tasks"]:
        lines.append(f"| (no model calls recorded) | {DASH} | {DASH} | {DASH} | {DASH} | {DASH} | {DASH} | {DASH} |")
    else:
        d = None if tot["a_cost"] is None or tot["b_cost"] is None else tot["b_cost"] - tot["a_cost"]
        lines.append(f"| **all steps** | | {fmt('cost_today', tot['a_cost'])} | {fmt('cost_today', tot['b_cost'])} | "
                     f"{fmt('cost_today', d, True)} | {tot['a_s']:.0f} | {tot['b_s']:.0f} | "
                     f"{tot['b_s'] - tot['a_s']:+.0f} |")
    return lines


def _pts(row: dict) -> str:
    each = row["points_each"]
    return f"{row['points']:.2f}" + (f" ({', '.join(f'{p:g}' for p in each)})" if len(each) > 1 else "")


def letter_changes(x: dict, y: dict) -> str:
    """What moved on one letter: problem instances (B − A) and the missed row ids gained / lost."""
    parts = []
    for m in lp.METRICS:
        d = round(y["problems"].get(m, {}).get("n", 0) - x["problems"].get(m, {}).get("n", 0), 2)
        if d:
            parts.append(f"`{m}` {d:+g}")
    rows_a, rows_b = set(x["missed_row_ids"]), set(y["missed_row_ids"])
    moved = [f"+{r}" for r in sorted(rows_b - rows_a, key=_row_num)] + \
            [f"-{r}" for r in sorted(rows_a - rows_b, key=_row_num)]
    text = ", ".join(parts)
    if moved:
        text += ("; " if text else "") + "missed rows " + " ".join(moved)
    return text or DASH


def _per_letter_section(cmp: dict, prices: dict) -> list[str]:
    lines = ["## 5. Per letter", "",
             "One row per paired ad, the letter that fell most first. Points in brackets are each judging's "
             "score when a checkpoint averages several. $ at today's prices.", "",
             "| Ad | Relevant | Drafts A → B | Points A → B | Δ | $ A → B | Seconds A → B | What changed |",
             "|---|---|---|---|---|---|---|---|"]
    rows = sorted(cmp["pairing"]["pairs"], key=lambda p: (p[2]["points"] - p[1]["points"], p[0]))
    for key, x, y in rows:
        rel = _yes(x["relevant"]) if x["relevant"] == y["relevant"] else f"{_yes(x['relevant'])} → {_yes(y['relevant'])}"
        drafts = f"{x['drafts'] if x['drafts'] is not None else DASH} → {y['drafts'] if y['drafts'] is not None else DASH}"
        lines.append(f"| `{key}` | {rel} | {drafts} | {_pts(x)} → {_pts(y)} | {y['points'] - x['points']:+.2f} | "
                     f"{fmt('cost_today', cost_at(x, prices))} → {fmt('cost_today', cost_at(y, prices))} | "
                     f"{fmt('seconds', x['seconds'])} → {fmt('seconds', y['seconds'])} | {letter_changes(x, y)} |")
    if not rows:
        lines.append("| (no paired ads) | | | | | | | |")
    return lines


def _left_out_section(cmp: dict, prices: dict) -> list[str]:
    a, b, pg = cmp["a"], cmp["b"], cmp["pairing"]
    unpriced = [k for k, x, y in pg["pairs"] if cost_at(x, prices) is None or cost_at(y, prices) is None]

    def keys(ks):
        return ", ".join(f"`{k}`" for k in ks) if ks else "none"

    return ["## 6. Left out", "",
            f"- Only in A ({a['id']}), not paired: {keys(pg['only_a'])}",
            f"- Only in B ({b['id']}), not paired: {keys(pg['only_b'])}",
            f"- No draft on one side (a failed letter), not paired: {keys(pg['failed'])}",
            f"- Relevant on one side only (in *all*, not in *relevant*): {keys(pg['relevant_disagree'])}",
            f"- Cost at today's prices unknown (a model today's table doesn't list), left out of that row: "
            f"{keys(unpriced)}"]


def _ref_line(ref: dict | None, what: str, missing: str) -> str:
    if ref is None:
        return f"- {what}: {missing}"
    kind = ("judge-only: the same letters judged again, so writer noise is not in it" if ref["kind"] == JUDGE_ONLY
            else "writer + judge: new letters written with the same settings, then judged")
    cons = "; conservative: a single-judging reference for averaged checkpoints" if ref["conservative"] else ""
    return f"- {what}: repeat check **{ref['aa']} vs {ref['of']}** ({kind}; {ref['n']} paired letters{cons})."


def _how_to_read(cmp: dict) -> list[str]:
    refs = cmp["refs"]
    return ["## 7. How to read this", "",
            "- **Noise threshold T = 2 × s ÷ √n.** s is how much each letter's number moved between two "
            "checkpoints that should match (a repeat check, A/A); n is the number of paired ads in the row. "
            "A difference bigger than T is *beyond noise*; otherwise *within noise*.",
            _ref_line(refs[JUDGE], "Judge measures (points, problems)", "no repeat check yet, so no noise label."),
            _ref_line(refs[WRITER], "Writer measures (cost, time, drafts)",
                      "no noise reference yet: that needs a writer + judge repeat (new letters written with the "
                      "same settings), such as E3's control arm."),
            "- A measure the repeat check never moved (s = 0) shows *no noise reference yet*, never T = 0.",
            "- **No conclusion from one letter.** The per-letter table says where to look; the means decide.",
            "- Cost at today's prices is recomputed from each letter's tokens with `client.PRICES` (thinking "
            "billed as output, cached input at the cached price); *as recorded* is what was logged at the time."]


def report_name(a: dict, b: dict) -> str:
    return f"compare-{a['id']}-{b['id']}.md"


def render_compare(cmp: dict, prices: dict, set_name: str = "v2") -> str:
    """The whole compare report (§4), sections in the plan's order."""
    a, b = cmp["a"], cmp["b"]
    lines = [f"# Compare {a['id']} → {b['id']}", "",
             f"Written by `letter_lab.py compare --set {set_name} {a['id']} {b['id']}` from "
             f"`evals/results/{set_name}/checkpoints.jsonl`. Don't edit it by hand: run the command again.", ""]
    lines += render_headline(cmp) + [""] + _what_changed(cmp)
    if cmp["kind"] == "measure":
        only = [f"- Only in A ({a['id']}): {', '.join(cmp['only_a']) or 'none'}",
                f"- Only in B ({b['id']}): {', '.join(cmp['only_b']) or 'none'}"]
        return "\n".join(lines + ["", "## Left out", ""] + only) + "\n"
    lines += [""] + _problems_section(cmp) + _tasks_section(cmp) + [""] + _per_letter_section(cmp, prices)
    lines += [""] + _left_out_section(cmp, prices) + [""] + _how_to_read(cmp)
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# The history report and the checkpoints list (plan §5, §2)
# ---------------------------------------------------------------------------
HISTORY_MEASURES = ("points", "cost_today", "seconds", "missed_row.n", "untrue.n")
HISTORY_NAMES = {"points": "Points", "cost_today": "$ / letter", "seconds": "Seconds / letter",
                 "missed_row.n": "Missed rows / letter", "untrue.n": "Untrue / letter"}


def summary(cp: dict, prices: dict) -> dict:
    """One letters checkpoint's per-letter means. Cost and time over every letter (a failed letter
    cost money too); points and problems over the judged ones. Cost at today's prices is unknown
    if any letter's is."""
    rows = cp["letters"]
    judged = [r for r in rows if r["points"] is not None]

    def mean(vs):
        vs = [v for v in vs if v is not None]
        return sum(vs) / len(vs) if vs else None

    costs = [cost_at(r, prices) for r in rows]
    out = {"letters": len(rows), "judged": len(judged),
           "points": mean([r["points"] for r in judged]),
           "cost_today": None if not rows or any(c is None for c in costs) else sum(costs) / len(rows),
           "cost_usd": mean([r["cost_usd"] for r in rows]),
           "seconds": mean([r["seconds"] for r in rows])}
    for m in lp.METRICS:
        out[f"{m}.n"] = mean([r["problems"].get(m, {}).get("n") for r in judged])
    return out


def kept_chain(cps: list[dict]) -> list[dict]:
    """Kept letters checkpoints, oldest first: no A/A (it repeats, it changes nothing) and none that a
    later checkpoint replaces (the correction stands in its place)."""
    gone = _replaced(cps)
    return [c for c in cps if c["kind"] == "letters" and c["kept"] and not c.get("aa_of") and c["id"] not in gone]


def _diff_cell(cps: list[dict], a: dict | None, b: dict, measure: str, prices: dict) -> tuple[str, dict | None]:
    """One "B − A (noise label)" cell of the history tables, over all paired ads."""
    if a is None:
        return DASH, None
    try:
        st = compare(cps, a, b, prices)["groups"]["all"][measure]
    except CheckpointError as e:
        why = str(e).split(": ", 1)[-1].split(" (")[0]
        return f"not comparable ({_esc(why)})", None
    return f"{fmt(measure, st['diff'], True)}, {noise_cell(st['noise'], measure)}", st["noise"]


def _change_table(cps: list[dict], rows: list[tuple[dict, dict | None]], prices: dict, against: str) -> list[str]:
    lines = [f"| Checkpoint | {against} | " + " | ".join(HISTORY_NAMES[m] for m in HISTORY_MEASURES) + " |",
             "|---|---|" + "---|" * len(HISTORY_MEASURES)]
    noises = []
    for b, a in rows:
        cells = []
        for m in HISTORY_MEASURES:
            text, nz = _diff_cell(cps, a, b, m, prices)
            cells.append(text)
            if nz:
                noises.append(nz)
        lines.append(f"| {b['id']} | {a['id'] if a else DASH} | " + " | ".join(cells) + " |")
    return lines + footnotes(noises)


def _nice_max(x: float) -> float:
    """A round chart ceiling a little above the largest value."""
    x = max(x, 1e-9) * 1.25
    step = 10 ** (math.floor(math.log10(x)) - 1)
    return round(math.ceil(x / step) * step, 10)


def mermaid_chart(title: str, y_label: str, points: list[tuple[str, float | None]],
                  y_max: float | None = None) -> list[str]:
    """A GitHub Mermaid xychart-beta line chart (plan §5.2). Points without a value are left out."""
    pts = [(i, v) for i, v in points if v is not None]
    if not pts:
        return [f"*{title}: no values yet.*"]
    top = y_max if y_max is not None else _nice_max(max(v for _, v in pts))
    return ["```mermaid", "xychart-beta", f'    title "{title}"',
            "    x-axis [" + ", ".join(f'"{i}"' for i, _ in pts) + "]",
            f'    y-axis "{y_label}" 0 --> {top:g}',
            "    line [" + ", ".join(f"{round(v, 4):g}" for _, v in pts) + "]", "```"]


def _judge_cell(cp: dict) -> str:
    backends = cp["settings"].get("judge_backends", [])
    return f"`{cp['comparable'].get('judge')}` ({', '.join(backends) or UNKNOWN})"


def render_history(cps: list[dict], prices: dict, set_name: str = "v2") -> str:
    """The history report (plan §5): kept chain, trends, not kept, measure checkpoints, noise refs."""
    by_id, gone = {c["id"]: c for c in cps}, _replaced(cps)
    chain = kept_chain(cps)
    sums = {c["id"]: summary(c, prices) for c in cps if c["kind"] == "letters"}
    lines = [f"# Experiment history (eval set {set_name})", "",
             f"Written by `letter_lab.py history --set {set_name}` from `evals/results/{set_name}/checkpoints.jsonl`. "
             "Don't edit it by hand: run the command again. $ are at today's prices (`client.PRICES`); "
             "differences are over the ads both checkpoints share, with their noise label (a `compare` "
             "report gives the detail).", ""]

    # 1. Kept chain
    lines += ["## 1. Kept chain", "",
              "Every kept checkpoint, oldest first: what is in use now is the last row.", ""]
    if not chain:
        lines.append("No kept letters checkpoint yet.")
    else:
        lines += ["| Checkpoint | Date | Experiment | Note | Judge | Letters | " +
                  " | ".join(HISTORY_NAMES[m] for m in HISTORY_MEASURES) + " |",
                  "|---|---|---|---|---|---|" + "---|" * len(HISTORY_MEASURES)]
        for c in chain:
            s = sums[c["id"]]
            lines.append(f"| {c['id']} | {c['created'][:10]} | {_esc(c['experiment'])} | {_esc(c['note']) or DASH} | "
                         f"{_judge_cell(c)} | {s['letters']} | " +
                         " | ".join(fmt(m, s[m]) for m in HISTORY_MEASURES) + " |")
        first = chain[0]
        lines += ["", "Change from the parent:", ""]
        lines += _change_table(cps, [(c, by_id.get(c["parent"])) for c in chain], prices, "Parent")
        lines += ["", f"Change from {first['id']}, the first kept checkpoint:", ""]
        lines += _change_table(cps, [(c, first if c is not first else None) for c in chain], prices, "From")
    vertex = _vertex_lines([c for c in cps if c["kind"] == "letters" and c["id"] not in gone])
    if vertex:
        lines += [""] + vertex
    changes = price_changes([c for c in cps if c["kind"] == "letters"], prices)
    if changes:
        lines += ["", "Prices that differ from today's (every $ above is at today's):"]
        for cid, m, then, now in changes:
            today = (f"${now[0]:.2f} / ${now[1]:.2f} / ${now[2]:.3f}" if now else "not in today's table (unknown)")
            lines.append(f"- {cid}, `{m}`: ${then[0]:.2f} / ${then[1]:.2f} / ${then[2]:.3f} then; today {today}.")

    # 2. Trends
    lines += ["", "## 2. Trends", "",
              "The kept chain as three line charts (GitHub draws them; VS Code's preview shows the code unless a "
              "Mermaid extension is installed). The table above has the same numbers.", ""]
    for m, title, y, top in (("points", "Points per letter", "Points (0-10)", 10),
                             ("cost_today", "Cost per letter at today's prices", "USD", None),
                             ("seconds", "Seconds per letter", "Seconds", None)):
        lines += mermaid_chart(title, y, [(c["id"], sums[c["id"]][m]) for c in chain], top) + [""]

    # 3. Not kept
    lines += ["## 3. Not kept", "",
              "Experiments that were tried and not adopted, against their parent (failures stay on record, D4).", ""]
    dropped = [c for c in cps if c["kind"] == "letters" and not c["kept"] and not c.get("aa_of")
               and c["id"] not in gone]
    if not dropped:
        lines.append("None yet.")
    else:
        lines += ["| Checkpoint | Date | Experiment | Verdict | Note | Parent | " +
                  " | ".join(HISTORY_NAMES[m] for m in HISTORY_MEASURES) + " |",
                  "|---|---|---|---|---|---|" + "---|" * len(HISTORY_MEASURES)]
        noises = []
        for c in dropped:
            cells = []
            for m in HISTORY_MEASURES:
                text, nz = _diff_cell(cps, by_id.get(c["parent"]), c, m, prices)
                cells.append(text)
                noises += [nz] if nz else []
            lines.append(f"| {c['id']} | {c['created'][:10]} | {_esc(c['experiment'])} | {c['verdict']} | "
                         f"{_esc(c['note']) or DASH} | {c['parent'] or DASH} | " + " | ".join(cells) + " |")
        lines += footnotes(noises)

    # 4. Measure checkpoints
    lines += ["", "## 4. Measure checkpoints", "",
              "Experiments measured without writing letters (E1-style), one table per experiment.", ""]
    measures = [c for c in cps if c["kind"] == "measure" and c["id"] not in gone]
    if not measures:
        lines.append("None yet.")
    for exp in dict.fromkeys(c["experiment"] for c in measures):
        group = [c for c in measures if c["experiment"] == exp]
        names = list(dict.fromkeys(m for c in group for m in c["measures"]))
        lines += [f"### {_esc(exp)}", "",
                  "| Checkpoint | Date | Kept | Verdict | Note | " + " | ".join(_esc(n) for n in names) + " |",
                  "|---|---|---|---|---|" + "---|" * len(names)]
        for c in group:
            vals = [f"{c['measures'][n]:g}" if n in c["measures"] else DASH for n in names]
            lines.append(f"| {c['id']} | {c['created'][:10]} | {_yes(c['kept'])} | {c['verdict']} | "
                         f"{_esc(c['note']) or DASH} | " + " | ".join(vals) + " |")
        lines.append("")

    # 5. Noise references
    lines += ["", "## 5. Noise references", "",
              "Each repeat check (A/A): how much a letter's number moved between two checkpoints that should match "
              "(s, the standard deviation of the per-letter differences). `compare` uses the most recent one that "
              "fits (plan §3.3). *Never moved* = s is 0, which gives no reference.", ""]
    pairs = aa_pairs(cps)
    if not pairs:
        lines.append("No repeat check yet.")
    else:
        heads = []
        cols = []
        for p in pairs:
            n = len(pair(p["of"], p["aa"])["pairs"])
            heads.append(f"{p['aa']['id']} vs {p['of']['id']} ({p['kind']}, {n} letters, "
                         f"{p['aa']['comparable'].get('judgings')} judging)")
            cols.append((p, pair(p["of"], p["aa"])["pairs"]))
        lines += ["| Measure | " + " | ".join(heads) + " |", "|---|" + "---|" * len(heads)]
        for m, kind in MEASURES:
            cells = []
            for p, prs in cols:
                if kind == WRITER and p["kind"] == JUDGE_ONLY:
                    cells.append("not a reference (same letters)")
                    continue
                sd = diff_stats(prs, m, prices)["sd"]
                cells.append(DASH if sd is None else "0 (never moved)" if not sd else fmt(m, sd))
            lines.append(f"| {MEASURE_NAMES[m]} | " + " | ".join(cells) + " |")
    return "\n".join(lines).rstrip("\n") + "\n"


def render_list(cps: list[dict], prices: dict) -> str:
    """`checkpoints` (plan §2): one row per checkpoint."""
    replaced_by = {c["replaces"]: c["id"] for c in cps if c.get("replaces")}
    lines = ["| Checkpoint | Date | Kind | Experiment | Kept | Verdict | Parent | Repeats | Points | $ / letter | "
             "Seconds / letter | Note |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for c in cps:
        cid = c["id"] + (f" (replaced by {replaced_by[c['id']]})" if c["id"] in replaced_by else "")
        note = _esc(c["note"]) or DASH
        if c["kind"] == "measure":
            vals = ", ".join(f"{k}={v:g}" for k, v in c["measures"].items())
            cells = [DASH, DASH, DASH]
            note = f"{vals}; {note}" if note != DASH else vals
        else:
            s = summary(c, prices)
            cells = [fmt("points", s["points"]), fmt("cost_today", s["cost_today"]), fmt("seconds", s["seconds"])]
        lines.append(f"| {cid} | {c['created'][:10]} | {c['kind']} | {_esc(c['experiment'])} | {_yes(c['kept'])} | "
                     f"{c['verdict']} | {c['parent'] or DASH} | {c.get('aa_of') or DASH} | " + " | ".join(cells) +
                     f" | {note} |")
    if not cps:
        return "No checkpoints yet.\n"
    return "\n".join(lines) + "\n"


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
