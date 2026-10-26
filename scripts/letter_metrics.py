"""Metrics for v2 letters (docs/letter-metrics-plan.md §3). Pure functions: no files, no DB and no
LLM; letter_lab's `report` gathers the inputs (letters, ads, profile text, skill names, the judge's
answers, the reference checklist) and calls these.

- Code checks, free and deterministic: G1 no stuffing, C3 ad echo, profile copy and writing-sample
  copy, C4 cross-letter repetition, C5 style warnings, C6 cost and time, C7 nearest-letter similarity.
- From the judge's answers (judge-<tag>.json) and the reference checklist: M1-M4, honest coverage
  and the recruiter checklist score. `scorecard` averages a group of letters for the headline.

The thresholds marked "a guess" stay as written until the first real letters exist (plan §8).
"""
from __future__ import annotations

import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.llm.letter.tools.check_requirements import quote_in_letter
from app.llm.letter.tools.style_lint import (
    MAX_WORDS,
    SIGN_OFF_RE,
    body_paragraphs,
    lint,
    word_count,
)
from app.llm.prefilter import normalise_skill

STUFF_RUN = 5  # G1: a comma list of this many skill terms is stuffing (a guess)
MAX_SKILL_MENTIONS = 2  # G1: a skill named more often than this is stuffing
AD_ECHO_N = 6  # C3: runs of 6+ words copied from the ad
PROFILE_COPY_N = 8  # C3: runs of 8+ words copied from the profile (panel 4's measure)
# C3: runs of 6+ words copied from the writing sample, kept apart from profile copy so the voice
# review can see each move (owner, 2026-10-26); 6 because the stock lines already seen copied
# from the sample are about 6 words long.
SAMPLE_COPY_N = 6
REPEAT_N = (4, 5, 6)  # C4: phrase lengths
REPEAT_MIN_LETTERS = 3  # C4: in this many letters of a run
NEAR_TEMPLATE = 0.10  # C7: closer than this to another letter is a near-template (a guess)

_LONGEST_SKILL = 5  # words; longer skill names aren't looked for inside a letter
# Words in an employer's name that are also ordinary words: C4 doesn't drop a phrase for these.
_NAME_STOPWORDS = {"the", "and", "of", "for", "a", "an", "in", "at", "to", "&"}

_WORD_RE = re.compile(r"[a-z0-9+#]+(?:'[a-z]+)?")
_SENTENCE_RE = re.compile(r"(?<=[.!?;:])\s+|\n+")
_JOIN_RE = re.compile(r"\s+(?:and|or|&)\s+", re.IGNORECASE)
_LEADING_JOIN_RE = re.compile(r"^(?:and|or|&)\s+", re.IGNORECASE)


def words(text: str) -> list[str]:
    """Lower-case words, split the way normalise_skill splits (".-_/" are spaces), so "Node.js"
    is "node js" here and in the skill list alike. Keeps "c++", "c#" and "don't" whole."""
    return _WORD_RE.findall(re.sub(r"[.\-_/]", " ", text.lower().replace("’", "'")))


def body(letter: str) -> str:
    """The letter without its greeting and sign-off (C3, C4, C7 measure the body only)."""
    paras = body_paragraphs(letter)
    if paras and (m := SIGN_OFF_RE.search(paras[-1])):  # a sign-off with no blank line before it
        paras[-1] = paras[-1][:m.start()].rstrip()
    return "\n\n".join(p for p in paras if p)


# ---------------------------------------------------------------------------
# G1: no stuffing
# ---------------------------------------------------------------------------
def skill_vocab(names) -> frozenset[str]:
    """Known skill names (the profile's, the ad's job_skills, the reference rows' `skill`),
    normalised so case and spelling variants match."""
    return frozenset(s for n in names if n and (s := normalise_skill(n)))


def _skill_spans(tokens: list[str], vocab: frozenset[str]) -> list[tuple[int, int, str]]:
    """(start, end, skill) for each skill named in `tokens`, longest match first, left to right."""
    out, i = [], 0
    while i < len(tokens):
        for n in range(min(_LONGEST_SKILL, len(tokens) - i), 0, -1):
            skill = normalise_skill(" ".join(t.removesuffix("'s") for t in tokens[i:i + n]))
            if skill in vocab:
                out.append((i, i + n, skill))
                i += n
                break
        else:
            i += 1
    return out


def _is_skill(item: str, vocab: frozenset[str]) -> bool:
    return normalise_skill(" ".join(words(item))) in vocab


def _edge_skill(item: str, vocab: frozenset[str], end: bool) -> bool:
    """Does `item` end (or start) with a skill? A list's first item often has words before
    its skill ("I have used Python"), and its last item words after ("React in production")."""
    toks = words(item)
    for n in range(1, min(_LONGEST_SKILL, len(toks)) + 1):
        if normalise_skill(" ".join(toks[-n:] if end else toks[:n])) in vocab:
            return True
    return False


def _list_items(sentence: str, vocab: frozenset[str]) -> list[str]:
    items = []
    for part in sentence.split(","):
        part = _LEADING_JOIN_RE.sub("", part.strip(" \t()[]\"'"))
        if _is_skill(part, vocab) or not _JOIN_RE.search(part):
            items.append(part)
        else:  # "AWS and React": two items, unless the whole thing is one skill name
            items.extend(p.strip(" \t()[]\"'") for p in _JOIN_RE.split(part))
    return items


def longest_skill_list(text: str, vocab: frozenset[str]) -> list[str]:
    """The longest run of comma-separated skill terms in any one sentence."""
    best: list[str] = []
    for sentence in _SENTENCE_RE.split(text):
        items = _list_items(sentence, vocab)
        exact = [_is_skill(it, vocab) for it in items]
        i = 0
        while i < len(items):
            if not exact[i]:
                i += 1
                continue
            j = i
            while j < len(items) and exact[j]:
                j += 1
            lo = i - 1 if i > 0 and _edge_skill(items[i - 1], vocab, end=True) else i
            hi = j + 1 if j < len(items) and _edge_skill(items[j], vocab, end=False) else j
            if hi - lo > len(best):
                best = items[lo:hi]
            i = j
    return best


def skill_mentions(text: str, vocab: frozenset[str]) -> Counter:
    return Counter(skill for _, _, skill in _skill_spans(words(text), vocab))


def no_stuffing(body_text: str, vocab: frozenset[str]) -> dict:
    """G1: no list of STUFF_RUN+ skill terms, and no skill named more than MAX_SKILL_MENTIONS times."""
    run = longest_skill_list(body_text, vocab)
    over = {s: n for s, n in skill_mentions(body_text, vocab).most_common() if n > MAX_SKILL_MENTIONS}
    return {"passed": len(run) < STUFF_RUN and not over, "longest_list": len(run), "list": run,
            "over_mentioned": over}


# ---------------------------------------------------------------------------
# C3: ad echo and profile copy
# ---------------------------------------------------------------------------
def copied(body_text: str, sources: list[str], n: int) -> dict:
    """Share of the body's words inside runs of n+ words found verbatim in one of `sources`, and
    those runs. Each source is matched on its own, so a run never spans two of them."""
    toks = words(body_text)
    grams = set()
    for s in sources:
        t = words(s)
        grams.update(tuple(t[i:i + n]) for i in range(len(t) - n + 1))
    covered = [False] * len(toks)
    for i in range(len(toks) - n + 1):
        if tuple(toks[i:i + n]) in grams:
            covered[i:i + n] = [True] * n
    runs, start = [], None
    for i, c in enumerate(covered + [False]):
        if c and start is None:
            start = i
        elif not c and start is not None:
            runs.append(" ".join(toks[start:i]))
            start = None
    return {"share": sum(covered) / len(toks) if toks else 0.0, "runs": runs}


def ad_echo(body_text: str, ad_text: str) -> dict:
    return copied(body_text, [ad_text], AD_ECHO_N)


def profile_copy(body_text: str, profile_texts: list[str]) -> dict:
    """Against the evidence text (summary, experiences, qualifications, skills), not the writing sample."""
    return copied(body_text, profile_texts, PROFILE_COPY_N)


def sample_copy(body_text: str, writing_sample: str | None) -> dict | None:
    return copied(body_text, [writing_sample], SAMPLE_COPY_N) if writing_sample else None


# ---------------------------------------------------------------------------
# C4: cross-letter repetition
# ---------------------------------------------------------------------------
def _inside(short: tuple, long: tuple) -> bool:
    return any(long[i:i + len(short)] == short for i in range(len(long) - len(short) + 1))


def _join(a: tuple, b: tuple, letters: set, tokens: dict) -> tuple | None:
    """a and b overlapped into one phrase, if that phrase is in every one of `letters`."""
    for k in range(min(len(a), len(b)) - 1, 0, -1):
        if a[-k:] == b[:k]:
            joined = a + b[k:]
            if all(_inside(joined, tuple(tokens[key])) for key in letters):
                return joined
    return None


def _merge(phrases: list[tuple], letters: set, tokens: dict) -> list[tuple]:
    """Join overlapping phrases found in the same letters, and drop one inside another."""
    while True:
        step = next(((a, b, b if _inside(a, b) else joined) for a in phrases for b in phrases
                     if a is not b and (_inside(a, b) or (joined := _join(a, b, letters, tokens)))), None)
        if step is None:
            return phrases
        a, b, new = step
        phrases = [p for p in phrases if p is not a and p is not b] + [new]


def repeated_phrases(bodies: dict[str, str], vocab: frozenset[str], names) -> list[dict]:
    """C4: phrases of 4-6 words in REPEAT_MIN_LETTERS+ of a run's letters, skipping any phrase that
    touches a skill term or a name (the candidate's, the employers'). One wording is listed once:
    a phrase inside a longer one found in the same letters is dropped, and overlapping phrases in
    the same letters are joined, so one shared sentence counts once."""
    name_words = {w.removesuffix("'s") for n in names if n for w in words(n)} - _NAME_STOPWORDS
    tokens = {key: words(text) for key, text in bodies.items()}
    seen: dict[tuple, set] = defaultdict(set)
    for key, toks in tokens.items():
        skip = {i for a, b, _ in _skill_spans(toks, vocab) for i in range(a, b)}
        skip |= {i for i, t in enumerate(toks) if t.removesuffix("'s") in name_words}
        for n in REPEAT_N:
            for i in range(len(toks) - n + 1):
                if skip.isdisjoint(range(i, i + n)):
                    seen[tuple(toks[i:i + n])].add(key)
    common = {g: ks for g, ks in seen.items() if len(ks) >= REPEAT_MIN_LETTERS}
    keep = [g for g, ks in common.items()
            if not any(len(h) > len(g) and common[h] == ks and _inside(g, h) for h in common)]
    by_letters: dict[frozenset, list[tuple]] = defaultdict(list)
    for g in keep:
        by_letters[frozenset(common[g])].append(g)
    out = [{"phrase": " ".join(p), "letters": sorted(letters)}
           for letters, phrases in by_letters.items() for p in _merge(phrases, letters, tokens)]
    return sorted(out, key=lambda r: (-len(r["letters"]), r["phrase"]))


# ---------------------------------------------------------------------------
# C5: style warnings, C6: cost and time
# ---------------------------------------------------------------------------
def style_warnings(letter: str) -> dict:
    """C5: style_lint's warnings (gap_led, stock_close, stock_bridge, us_spelling, flat_rhythm,
    i_openers, short) and how many of each kind."""
    warnings = lint(letter)["warnings"]
    return {"warnings": warnings, "kinds": Counter(w.split(":", 1)[0] for w in warnings)}


def cost_time(results: list[dict]) -> dict:
    """C6: cost and time per letter from run.json's results (failed letters left out)."""
    ok = [r for r in results if not r.get("error")]
    costs = [r["cost_usd"] for r in ok if r.get("cost_usd") is not None]
    secs = [r["seconds"] for r in ok if r.get("seconds") is not None]
    return {"letters": len(ok), "total_cost": sum(costs),
            "cost_per_letter": statistics.mean(costs) if costs else None,
            "seconds_per_letter": statistics.mean(secs) if secs else None}


# ---------------------------------------------------------------------------
# C7: nearest-letter similarity
# ---------------------------------------------------------------------------
def edit_distance(a: list, b: list) -> int:
    """Word-level Levenshtein distance: insertions, deletions and substitutions of whole words."""
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def nearest_letters(bodies: dict[str, str]) -> dict[str, dict]:
    """C7: per letter, the closest other letter in the run and the edit distance to it as a share
    of the longer body (0 = the same words, 1 = nothing in common); under NEAR_TEMPLATE is flagged."""
    ids: dict[str, int] = {}  # words as ints: the distance loop compares faster
    toks = {k: [ids.setdefault(w, len(ids)) for w in words(t)] for k, t in bodies.items()}
    best: dict[str, tuple[float, str | None]] = {k: (float("inf"), None) for k in toks}
    keys = sorted(toks)
    for n, a in enumerate(keys):
        for b in keys[n + 1:]:
            longer = max(len(toks[a]), len(toks[b]))
            d = edit_distance(toks[a], toks[b]) / longer if longer else 0.0
            for x, y in ((a, b), (b, a)):
                if d < best[x][0]:
                    best[x] = (d, y)
    return {k: {"nearest": near, "distance": None if near is None else round(d, 4),
                "near_template": near is not None and d < NEAR_TEMPLATE}
            for k, (d, near) in best.items()}


# ---------------------------------------------------------------------------
# M1-M4, honest coverage and the checklist, from one letter's judge entry (judge-<tag>.json)
# ---------------------------------------------------------------------------
EVIDENCE_RANK = {"work": 4, "project": 3, "qualification": 2, "skill": 1, "summary": 0}  # M3
CUSTOM_DIMS, GENERIC_DIMS = ("D2", "D3", "D4", "D7"), ("D5", "D6")
LENGTH_ITEM, LENGTH = "D6f", (250, MAX_WORDS)  # code counts the words; the judge's answer is replaced


def pointer_kind(pointer: str, exp_kinds: dict[str, str]) -> str | None:
    """work / project / qualification / skill / summary, as eval_reference.evidence_catalog derives it.
    `exp_kinds` maps an experience id to work (a job) or project."""
    kind, _, rest = pointer.partition(":")
    if kind == "experience":
        return exp_kinds.get(rest.split("#")[0])
    return "summary" if kind == "profile" else kind


def addressed_rows(letter: str, judge: dict, flags: dict[str, tuple[bool, bool]]) -> set[str]:
    """Rows the letter really addresses: the judge said Y, its quote is in the letter (checked again
    here, in case the letter changed after judging), and a listing-only row is presented as a skill,
    never as experience (plan §3). `flags` is grading_panel._flags: id -> (must_cover, listing_only)."""
    out = set()
    for r in judge["requirements"]:
        if str(r.get("addressed", "")).strip().upper()[:1] != "Y":
            continue
        if not quote_in_letter(r.get("quote", ""), letter):
            continue
        if flags.get(r["id"], (False, False))[1] and r.get("presented_as") != "skill":
            continue
        out.add(r["id"])
    return out


def checklist_scores(checklist: dict, letter: str) -> dict:
    """Each dimension's share of Y; custom = mean of D2, D3, D4, D7; generic = mean of D5, D6;
    the checklist score weights custom 2:1 over generic (plan §3 Layer 2)."""
    yes = {d: str(v).strip().upper()[:1] == "Y" for d, v in checklist.items()}
    yes[LENGTH_ITEM] = LENGTH[0] <= word_count(letter) <= LENGTH[1]
    by_dim: dict[str, list[bool]] = defaultdict(list)
    for item, y in yes.items():
        by_dim[item[:2]].append(y)
    dims = {d: sum(v) / len(v) for d, v in sorted(by_dim.items())}
    custom = statistics.mean(dims[d] for d in CUSTOM_DIMS)
    generic = statistics.mean(dims[d] for d in GENERIC_DIMS)
    return {"dims": dims, "custom": custom, "generic": generic, "checklist": (2 * custom + generic) / 3}


def judged(letter: str, ref: dict, judge: dict, flags: dict[str, tuple[bool, bool]],
           exp_kinds: dict[str, str], weights: dict[str, int]) -> dict:
    """One letter's Layer 1 and 2 numbers. `ref` is its reference checklist, `weights` M2's
    (eval_reference.M2_WEIGHTS). None where a measure has nothing to count on this ad."""
    hit = addressed_rows(letter, judge, flags)
    rows = ref["requirements"]
    musts = [rid for rid, (must, _) in flags.items() if must]
    scored = [r for r in rows if r["status"] in ("supported", "partial") and r["letter_role"] in ("headline", "mention")]
    total = sum(weights[r["importance"]] for r in scored)
    m2 = sum(weights[r["importance"]] for r in scored if r["id"] in hit) / total if total else None

    def rank(pointers) -> int:
        return max((EVIDENCE_RANK.get(pointer_kind(p, exp_kinds), -1) for p in pointers), default=-1)

    used = {r["id"]: r.get("evidence_used", []) for r in judge["requirements"]}
    best = [rank(used.get(r["id"], [])) >= rank(r["evidence"])
            for r in rows if r["letter_role"] == "headline" and r["id"] in hit and r["evidence"]]
    counts = judge["counts"]
    fabricated = counts["fabricated"]
    return {
        "judge_grade": judge["send_readiness"],
        "m1": sum(rid in hit for rid in musts) / len(musts) if musts else None,
        "m2": m2,
        "honest": None if m2 is None else (m2 if fabricated == 0 else 0.0),
        "m3": sum(best) / len(best) if best else None,
        "fabricated": fabricated,
        "gap_fabrications": counts["gap_fabrications"],
        "stretch": counts["stretch"],
        "claims": sum(n for label, n in counts.items() if label != "gap_fabrications"),
        "supported": counts["supported"],
        **checklist_scores(judge["checklist"], letter),
    }


# ---------------------------------------------------------------------------
# The scorecard: one group of letters (relevant ads, or all) -> the headline and diagnostics
# ---------------------------------------------------------------------------
def _mean(values) -> float | None:
    values = [v for v in values if v is not None]
    return statistics.mean(values) if values else None


def scorecard(rows: list[dict]) -> dict:
    """Averages over per-letter rows (letter_lab's report builds them). A row's `grade` is the
    owner's send-readiness where there is one, else the judge's (`grade_by` says which); judge
    measures are None on a letter the judge didn't grade, and are averaged over the ones it did."""
    graded = [r for r in rows if r.get("grade") is not None]
    by_judge = [r for r in rows if r.get("fabricated") is not None]
    claims = sum(r["claims"] for r in by_judge)
    ct = cost_time(rows)
    return {
        "letters": len(rows),
        "send_readiness": _mean(r["grade"] for r in graded),
        "send_ready": sum(r["grade"] >= 3 for r in graded) / len(graded) if graded else None,
        "by_owner": sum(r["grade_by"] == "owner" for r in graded),
        "by_judge": sum(r["grade_by"] == "judge" for r in graded),
        "judged": len(by_judge),
        "fabrication_free": sum(r["fabricated"] == 0 for r in by_judge) / len(by_judge) if by_judge else None,
        **{m: _mean(r.get(m) for r in rows) for m in ("m1", "honest", "checklist", "custom", "generic", "m2", "m3",
                                                     "fabricated", "gap_fabrications", "stretch", "ad_echo",
                                                     "profile_copy", "sample_copy", "warnings")},
        "precision": sum(r["supported"] for r in by_judge) / claims if claims else None,
        "dims": {d: _mean(r["dims"].get(d) for r in by_judge) for d in CUSTOM_DIMS + GENERIC_DIMS},
        "gates": sum(r["gates"] for r in rows),
        "g1": sum(r["g1"] for r in rows),
        "near_templates": sum(r["near_template"] for r in rows),
        "cost_per_letter": ct["cost_per_letter"],
        "seconds_per_letter": ct["seconds_per_letter"],
    }
