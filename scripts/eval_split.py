"""Choose the eval set's ads and split them into dev / test (docs/eval-set-plan.md, stage 4).

Reads the frozen ads (evals/sets/v2, scripts/eval_freeze.py) and their tags in
evals/candidates/candidates.csv: `final_scenario` (filled at stage 3) when present, else the
draw's `scenario`; `scenario_ok` = N drops an ad. Every frozen ad needs a row there, and only
frozen ads can be chosen.

Near-duplicate rules (owner, 2026-10-21; the plan's Decision log):
  family = ads at least FAMILY_SIMILARITY alike, joined transitively;
  choose scarcest scenario first, one ad per family unless a scenario would fall short;
  a family with more than one chosen ad goes to dev, whole; the rest is a seeded random
  split within each scenario.

    python scripts/eval_split.py           # print the proposed split (writes nothing)
    python scripts/eval_split.py --write   # save it to evals/sets/v2/split.json (refuses if there)
"""
from __future__ import annotations

import ast
import csv
import datetime
import difflib
import itertools
import json
import random
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import eval_freeze  # noqa: E402  (no side effects on import)

if hasattr(sys.stdout, "reconfigure"):  # Windows consoles are cp1252; Seek titles aren't
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = eval_freeze.ROOT
SET_DIR = eval_freeze.SET_DIR
TAGS = eval_freeze.CANDIDATES
TRACKER = ROOT / "scripts" / "eval_pool.py"
SPLIT_FILE = SET_DIR / "split.json"
SEED = 20261021
FAMILY_SIMILARITY = 0.6  # at freezing, families were 0.79+ and every other pair under 0.5


@dataclass(frozen=True)
class Ad:
    key: str
    job_id: int
    scenario: int
    title: str
    company: str | None


def load_targets(tracker: Path = TRACKER) -> dict[int, tuple[str, int, int]]:
    """eval_pool.py's TARGET (scenario -> (name, dev, test)), parsed, not run: importing the
    tracker sorts the whole of real.db."""
    tree = ast.parse(tracker.read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.Assign)
                and any(getattr(t, "id", None) == "TARGET" for t in n.targets))
    return ast.literal_eval(node.value)


def families(texts: dict[str, str], threshold: float = FAMILY_SIMILARITY) -> list[list[str]]:
    """Groups of 2+ keys whose texts are at least `threshold` alike, joined transitively.

    Compared word by word: on the 49 frozen ads it finds the same families as a character
    comparison, with a wider gap (0.79+ vs 0.48), in about 2 s instead of about 5 minutes."""
    words = {k: re.findall(r"\w+", t.lower()) for k, t in texts.items()}
    parent = {k: k for k in texts}

    def root(k: str) -> str:
        while parent[k] != k:
            k = parent[k]
        return k

    for a, b in itertools.combinations(sorted(texts), 2):
        sm = difflib.SequenceMatcher(None, words[a], words[b], autojunk=False)
        # Both quick ratios are upper bounds on ratio(), so skipping on them is exact.
        if sm.real_quick_ratio() >= threshold and sm.quick_ratio() >= threshold and sm.ratio() >= threshold:
            parent[root(b)] = root(a)
    groups: dict[str, list[str]] = {}
    for k in sorted(texts):
        groups.setdefault(root(k), []).append(k)
    return [g for g in groups.values() if len(g) > 1]


def choose(ads: list[Ad], need: dict[int, int], family_of: dict[str, int],
           rng: random.Random) -> tuple[dict[int, list[Ad]], dict[str, str]]:
    """Up to need[s] ads per scenario: scarcest scenario first, one ad per family unless a
    scenario would otherwise fall short. Returns the chosen ads and a note per ad."""
    pool = {s: sorted((a for a in ads if a.scenario == s), key=lambda a: a.key) for s in need}
    for s in sorted(pool):
        rng.shuffle(pool[s])
    order = sorted(need, key=lambda s: (len(pool[s]) - need[s], s))
    chosen: dict[int, list[Ad]] = {s: [] for s in need}
    skipped: dict[int, list[Ad]] = {s: [] for s in need}
    taken: set[int] = set()
    notes: dict[str, str] = {}
    for s in order:
        for ad in pool[s]:
            fam = family_of.get(ad.key)
            if fam is not None and fam in taken:
                skipped[s].append(ad)
                continue
            if len(chosen[s]) == need[s]:
                notes[ad.key] = "spare"
                continue
            chosen[s].append(ad)
            if fam is not None:
                taken.add(fam)
                notes[ad.key] = "first of its family"
    for s in order:
        for ad in skipped[s]:
            if len(chosen[s]) < need[s]:
                chosen[s].append(ad)
                notes[ad.key] = "second of its family: scenario short"
            else:
                notes[ad.key] = "left out: a family member is already in"
    return chosen, notes


def split(chosen: dict[int, list[Ad]], targets: dict[int, tuple[str, int, int]],
          family_of: dict[str, int], rng: random.Random) -> tuple[list[Ad], list[Ad], list[str]]:
    """Dev and test lists, plus warnings. A family with 2+ chosen ads goes to dev whole."""
    counts: dict[int, int] = {}
    for ad in itertools.chain.from_iterable(chosen.values()):
        fam = family_of.get(ad.key)
        if fam is not None:
            counts[fam] = counts.get(fam, 0) + 1
    to_dev = {f for f, n in counts.items() if n > 1}
    dev: list[Ad] = []
    test: list[Ad] = []
    warnings: list[str] = []
    for s in sorted(chosen):
        _name, d, t = targets[s]
        ads = chosen[s]
        dev_n = d if len(ads) >= d + t else int(len(ads) * d / (d + t) + 0.5)
        forced = [a for a in ads if family_of.get(a.key) in to_dev]
        rest = sorted((a for a in ads if a not in forced), key=lambda a: a.key)
        rng.shuffle(rest)
        if len(forced) > dev_n:
            warnings.append(f"scenario {s}: {len(forced)} family ads forced into dev, over its {dev_n} dev slots")
        room = max(0, dev_n - len(forced))
        dev += forced + rest[:room]
        test += rest[room:]
    return dev, test, warnings


def read_tags(path: Path, frozen: dict[int, dict]) -> list[Ad]:
    """The usable frozen ads with their scenario. Errors on an untagged frozen ad or a tag
    for an ad that isn't frozen."""
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    ok_col = next((c for c in (rows[0] if rows else {}) if c.startswith("scenario_ok")), None)
    tagged = {int(r["job_id"]): r for r in rows}
    unknown = sorted(set(tagged) - set(frozen))
    if unknown:
        raise SystemExit(f"tagged but not frozen (freeze them with eval_freeze.py --add): {unknown}")
    untagged = sorted(set(frozen) - set(tagged))
    if untagged:
        raise SystemExit(f"frozen but not tagged in {path.name}: {untagged}")
    ads = []
    for jid, r in sorted(tagged.items()):
        if ok_col and (r.get(ok_col) or "").strip().upper().startswith("N"):
            continue
        scenario = (r.get("final_scenario") or "").strip() or r["scenario"]
        e = frozen[jid]
        ads.append(Ad(e["key"], jid, int(scenario), e["title"], e["company"]))
    return ads


def plan(set_dir: Path = SET_DIR, tags: Path = TAGS, seed: int = SEED) -> dict:
    problems = eval_freeze.verify(set_dir)
    if problems:
        raise SystemExit("the frozen set does not verify:\n  " + "\n  ".join(problems))
    manifest = json.loads((set_dir / "MANIFEST.json").read_text(encoding="utf-8"))
    frozen = {a["real_db_job_id"]: a for a in manifest["ads"]}
    job_id = {a["key"]: a["real_db_job_id"] for a in manifest["ads"]}
    texts = {a["key"]: json.loads((set_dir / a["file"]).read_text(encoding="utf-8"))["raw_description"]
             for a in manifest["ads"]}
    fams = families(texts)
    family_of = {k: i for i, g in enumerate(fams) for k in g}
    targets = load_targets()
    ads = read_tags(tags, frozen)
    rng = random.Random(seed)
    chosen, notes = choose(ads, {s: d + t for s, (_n, d, t) in targets.items()}, family_of, rng)
    dev, test, warnings = split(chosen, targets, family_of, rng)
    by_key = {a.key: a for a in ads}
    short = {s: d + t - len(chosen[s]) for s, (_n, d, t) in targets.items() if len(chosen[s]) < d + t}

    def row(a: Ad) -> dict:
        return {"key": a.key, "job_id": a.job_id, "scenario": a.scenario, "title": a.title,
                "company": a.company, "note": notes.get(a.key, "")}

    return {
        "set": set_dir.name, "seed": seed, "family_similarity": FAMILY_SIMILARITY,
        "made_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "manifest_sha256": eval_freeze.sha256(set_dir / "MANIFEST.json"),
        "families": [[{"key": k, "job_id": job_id[k], "note": notes.get(k, "not usable")
                       if k in by_key else "dropped (scenario_ok = N)"} for k in g] for g in fams],
        "short": short, "warnings": warnings,
        "dev": [row(a) for a in dev], "test": [row(a) for a in test],
    }


def report(p: dict, targets: dict[int, tuple[str, int, int]]) -> str:
    lines = [f"Split of {p['set']} (seed {p['seed']}): dev {len(p['dev'])}, test {len(p['test'])}", ""]
    for s, (name, d, t) in targets.items():
        nd = sum(1 for r in p["dev"] if r["scenario"] == s)
        nt = sum(1 for r in p["test"] if r["scenario"] == s)
        gap = f"   SHORT by {p['short'][s]}" if s in p["short"] else ""
        lines.append(f"  {s}. {name:37} dev {nd}/{d}  test {nt}/{t}{gap}")
    lines += ["", "Families (near-duplicate ads):"]
    for g in p["families"]:
        lines += [f"  #{m['job_id']:<4} {m['note']}" for m in g] + [""]
    for w in p["warnings"]:
        lines.append(f"WARNING: {w}")
    for side in ("dev", "test"):
        lines.append(f"{side.upper()}:")
        for r in sorted(p[side], key=lambda r: (r["scenario"], r["job_id"])):
            note = f"  [{r['note']}]" if r["note"] else ""
            lines.append(f"  sc{r['scenario']}  #{r['job_id']:<4} {r['title'][:55]} ({r['company']}){note}")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if argv not in ([], ["--write"]):
        print(__doc__)
        return 1
    p = plan()
    print(report(p, load_targets()))
    if argv == ["--write"]:
        if SPLIT_FILE.exists():
            print(f"{SPLIT_FILE.relative_to(ROOT)} already exists; delete it to redo the split.")
            return 1
        SPLIT_FILE.write_text(json.dumps(p, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Wrote {SPLIT_FILE.relative_to(ROOT)}")
    else:
        print("(dry run: nothing written; --write saves it)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
