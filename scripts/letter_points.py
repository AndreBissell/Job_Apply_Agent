"""The points score for one cover letter (owner, 2026-11-03; evals/results/v2/points-severity-guide.md).

A letter starts at 10 and loses points per problem; the floor is 0. Each problem is low or high.
The judge finds most problems and sets their level (evals/grading-standard-v2.md §7); code finds
the rest (missed rows from the judge's row answers, stock phrasing from letter_metrics' count, and
too long from the judge's filler list plus the word count) and does every sum here, so the judge
never sees what a problem costs. Fit is a separate score and is never deducted.

Pure functions, no LLM and no database: grading_panel.py builds the problem lists, this scores them.
The owner's labels (evals/work/v2/points-draft/labels.json) go through the same `score`, so the
owner's points and the judge's points are computed the same way.
"""

from __future__ import annotations

START = 10.0
# metric -> (low, high) points lost (owner, 2026-11-03). PARKED (owner, 2026-11-04): the weak-example
# and mix-up values and the cap are to be revisited for the best penalty formula.
VALUES = {
    "untrue": (1.0, 2.5),
    "missed_row": (0.75, 1.5),
    "mix_up": (0.5, 1.5),
    "weak_example": (0.5, 1.0),
    "no_personal_reason": (0.5, 1.0),
    "too_long": (0.5, 1.0),
    "generic": (0.3, 0.6),
    "stock_phrasing": (0.25, 0.5),
}
METRICS = tuple(VALUES)
LEVELS = ("low", "high")
# Counted per instance; these two are capped: only the CAP highest instances in a letter are charged
# (two highs = high + high). Untrue claims and missed rows are uncapped.
CAPPED = ("weak_example", "mix_up")
CAP = 2
PER_INSTANCE = ("untrue", "missed_row", *CAPPED)
ONE_PER_LETTER = ("generic", "no_personal_reason", "too_long", "stock_phrasing")
# Code-set levels
STOCK_LOW, STOCK_HIGH = 9, 12  # stock-phrase hits: 9-11 low, 12+ high (letter_metrics.STOCK_LIMIT is 8)
FILLER_HIGH = 3  # filler sentences: 1-2 low, 3+ high
MAX_WORDS = 340  # over it is too long, high (style_lint.MAX_WORDS)


def stock_level(hits: int) -> str | None:
    return None if hits < STOCK_LOW else ("low" if hits < STOCK_HIGH else "high")


def too_long_level(filler: int, words: int) -> str | None:
    if words > MAX_WORDS or filler >= FILLER_HIGH:
        return "high"
    return "low" if filler else None


def missed_level(must_cover: bool) -> str:
    """A missed row's level comes from the answer key, with no override: must-cover = high."""
    return "high" if must_cover else "low"


def _check(issue: dict) -> None:
    if issue.get("metric") not in VALUES:
        raise ValueError(f"unknown metric {issue.get('metric')!r}")
    if issue.get("level") not in LEVELS:
        raise ValueError(f"{issue['metric']}: level must be low or high, not {issue.get('level')!r}")


def present(issues: list[dict]) -> list[dict]:
    """The issues that are problems at all (a level of none, or no level, is not a problem)."""
    return [i for i in issues if i.get("level") not in (None, "none")]


def charged(issues: list[dict]) -> list[dict]:
    """The issues that cost points: every one present, except that a capped metric keeps only its
    CAP highest instances. One-per-letter metrics must appear at most once."""
    issues = present(issues)
    for i in issues:
        _check(i)
    for m in ONE_PER_LETTER:
        if sum(i["metric"] == m for i in issues) > 1:
            raise ValueError(f"{m} is one per letter")
    out = [i for i in issues if i["metric"] not in CAPPED]
    for m in CAPPED:
        mine = sorted((i for i in issues if i["metric"] == m), key=lambda i: i["level"] != "high")
        out += mine[:CAP]
    return out


def cost(issue: dict) -> float:
    low, high = VALUES[issue["metric"]]
    return high if issue["level"] == "high" else low


def score(issues: list[dict]) -> float:
    """10 minus every charged issue's cost, floor 0, rounded to 2 places."""
    return round(max(0.0, START - sum(cost(i) for i in charged(issues))), 2)


def by_metric(issues: list[dict]) -> dict[str, list[str]]:
    """metric -> the charged levels, high first (an empty list: the letter doesn't have it)."""
    got = charged(issues)
    return {m: sorted((i["level"] for i in got if i["metric"] == m), key=lambda l: l != "high") for m in METRICS}


def label_issues(letter: dict) -> list[dict]:
    """The owner's labels for one letter (labels.json `letters[key]`) as issues."""
    out = [{"metric": "untrue", "level": u["level"], "quote": u.get("quote", ""), "why": u.get("why", "")}
           for u in letter.get("untrue", [])]
    for i in letter.get("issues", []):
        if i.get("level") in (None, "none"):
            continue
        out.append({k: v for k, v in i.items() if k in ("metric", "level", "row", "note", "hits")})
    return out
