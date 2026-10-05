"""Quick Apply question-help eval (docs/cover-letter-loop-plan.md §10.1, Phase 9c).

    python scripts/screening_eval.py build-set               # write evals/screening/set.json (draft labels)
    #    ... the user adjudicates the labels in set.json ...
    python scripts/screening_eval.py sort                    # layers 1-4, no model: kind / strategy accuracy
    python scripts/screening_eval.py labels                  # no model: every "you have it" has a resolving pointer
    python scripts/screening_eval.py sort --model --confirm-spend     # + layer 5 (small) on assisted leftovers
    python scripts/screening_eval.py drafts --confirm-spend           # mid-tier drafts + the planted test
    python scripts/screening_eval.py report                  # -> evals/results/screening-<run>.md

Everything runs on a SCRATCH copy of evals/eval.db (the real profile, read-only source)
at evals/runs/<run>/screening.db, migrated to head. real.db and eval.db are never written.
In that copy each eval job's cover letter is set to its latest pipeline run's final draft,
so the job gets `full` help: eval.db's own cover_letters rows predate the eval runs (or are
missing), and question help is gated on "the letter came from this run".

Model calls only with --confirm-spend; without it the LLM provider is the test stub, so a
stray call fails instead of costing money. A `user` question is never sent to a model:
one that layers 1-4 leave unsorted is reported as a FAILURE ("would reach the model"),
not sent. --max-usd (default 1.00) stops the command once its own spend reaches it.

Privacy: the run folder (scratch DB, drafts.md with the answers) is gitignored with the
rest of evals/runs/. evals/screening/set.json (questions + labels, no answers) and the
summary in evals/results/ are meant to be committed.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVALS = ROOT / "evals"
SET_FILE = EVALS / "screening" / "set.json"
RUNS_DIR = EVALS / "runs"
RESULTS_DIR = EVALS / "results"
EVAL_DB = EVALS / "eval.db"
FIXTURE = ROOT / "tests" / "fixtures" / "quick_apply_samples.json"
TEST_BANK_DB = ROOT / "app.db"  # the test env's bank, read-only
PROFILE_ID = 1
DEFAULT_RUN = "screening-v1"
EVAL_DAILY_BUDGET_USD = 20.0  # scratch copy only, as letter_lab

# --------------------------------------------------------------------------------------
# The synthetic items (not seen on Seek; written for this eval). Job ids are eval.db's.
# --------------------------------------------------------------------------------------
_DRAFTS = [
    # id, job_id, text, strategy, skill
    ("D1", 4, "Describe your experience with SQL.", "free_text_describe", "SQL"),
    ("D2", 17, "How many years of experience do you have with React?", "years_skill_text", "React"),
    ("D3", 5, "Please describe your experience with C# and .NET.", "free_text_describe", "C# and .NET"),
    ("D4", 42, "How many years of Java experience do you have?", "years_skill_text", "Java"),
    ("D5", 29, "Tell us about your experience building and consuming REST APIs.", "free_text_describe", "REST APIs"),
    ("D6", 24, "Describe your experience with Microsoft Power Platform.", "free_text_describe",
     "Microsoft Power Platform"),
    ("D7", 44, "How many years of experience do you have with Python?", "years_skill_text", "Python"),
    ("D8", 37, "Briefly describe a project where you used machine learning.", "free_text_describe",
     "machine learning"),
    ("D9", 17, "How many years of experience you have with building application on React Native with "
               "Expo, targeting web, iOS and Android ?", "years_skill_text", "React Native with Expo"),
    ("D10", 24, "Describe your experience with Azure DevOps.", "free_text_describe", "Azure DevOps"),
]
# The planted test: the whole question is about a skill the profile has no trace of, so the
# writer is told "NOT IN PROFILE (never claim)". Pass = no claim of it, no duration, not
# covered "yes". build-set checks nothing; `drafts` skips one the profile turns out to have.
_PLANTED = [
    ("P1", 2, "Describe your experience with Kubernetes.", "free_text_describe", "Kubernetes"),
    ("P2", 45, "How many years of experience do you have with Salesforce?", "years_skill_text", "Salesforce"),
    ("P3", 17, "Describe your experience with Flutter.", "free_text_describe", "Flutter"),
    ("P4", 29, "How many years of Terraform experience do you have?", "years_skill_text", "Terraform"),
]
# `user` questions in wordings the samples don't have. Sorting only; none is ever sent.
_USER_TRAPS = [
    ("U1", "Why do you want to work for us?", "motivation", False),
    ("U2", "What is your notice period?", "notice", False),
    ("U3", "Do you hold a current driver's licence?", "legal", True),
    ("U4", "Please include a link to your portfolio or GitHub profile.", "source", True),
    ("U5", "When would you be available for an interview?", "notice", True),
    ("U6", "Are you willing to undergo a police check?", "legal", False),
    ("U7", "What are your hourly rate expectations?", "salary", False),
]
_USER_TRAP_NOTE = ("Synthetic. Expected `user`, but the layer-3 keyword filter probably has no "
                   "pattern for this wording, so it would fall through to layer 5. Topic is a guess.")


# --------------------------------------------------------------------------------------
# Setup (env BEFORE any app import)
# --------------------------------------------------------------------------------------
def _run_dir(run: str) -> Path:
    return RUNS_DIR / run


def _scratch_db(run: str) -> Path:
    return _run_dir(run) / "screening.db"


def _point_app_at(db_path: Path, spend: bool) -> None:
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
    os.environ["APP_ENV"] = "test"
    if not spend:
        os.environ["LLM_PROVIDER"] = "stub"  # no canned file: any call raises, nothing is spent
        os.environ.pop("LLM_STUB_RESPONSES", None)
    sys.path.insert(0, str(ROOT))


def _load_set() -> dict:
    if not SET_FILE.exists():
        raise SystemExit(f"{SET_FILE} not found: run `build-set` first")
    return json.loads(SET_FILE.read_text(encoding="utf-8"))


def _prepare_scratch(run: str, fresh: bool) -> list[int]:
    """Copy eval.db, migrate, point each job's letter at its latest pipeline run. Returns
    the job ids that now get `full` help. Runs after _point_app_at."""
    path = _scratch_db(run)
    path.parent.mkdir(parents=True, exist_ok=True)
    if fresh or not path.exists():
        if not EVAL_DB.exists():
            raise SystemExit(f"{EVAL_DB} not found (letter_lab.py prepare builds it)")
        shutil.copyfile(EVAL_DB, path)
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")

    from sqlalchemy import select

    from app.db import SessionLocal
    from app.llm.letter.state import LetterState
    from app.models import CoverLetter, LetterRun, Match
    from app.preferences import set_preferences
    from app.screening import assist

    full = []
    with SessionLocal() as db:
        set_preferences(db, PROFILE_ID, {"llm_daily_budget_usd": EVAL_DAILY_BUDGET_USD,
                                         "screening_question_help_enabled": True})
        for match in db.scalars(select(Match).where(Match.user_id == PROFILE_ID)).all():
            run_row = db.scalars(
                select(LetterRun).where(LetterRun.match_id == match.id, LetterRun.engine.in_(("agent", "workflow")),
                                        LetterRun.status.in_(("done", "budget_stopped", "failed")),
                                        LetterRun.final_draft_version.is_not(None))
                .order_by(LetterRun.id.desc())
            ).first()
            if run_row is None:
                continue
            state = LetterState.model_validate_json(run_row.state)
            draft = next((d for d in state.drafts if d.version == run_row.final_draft_version), None)
            if draft is None:
                continue
            letter = db.scalar(select(CoverLetter).where(CoverLetter.match_id == match.id))
            if letter is None:
                db.add(CoverLetter(match_id=match.id, generated_content=draft.text, status="draft"))
            else:
                letter.generated_content = draft.text
            db.commit()
            if assist.help_for_job(db, match.job_id, PROFILE_ID)[0] == assist.FULL:
                full.append(match.job_id)
    return sorted(full)


def _spend_since(since: datetime.datetime) -> float:
    from sqlalchemy import func, select

    from app.db import SessionLocal
    from app.models import LlmUsage

    with SessionLocal() as db:
        return float(db.scalar(select(func.coalesce(func.sum(LlmUsage.cost_usd), 0.0))
                               .where(LlmUsage.created_at >= since)) or 0.0)


# --------------------------------------------------------------------------------------
# build-set
# --------------------------------------------------------------------------------------
def _item(**kw) -> dict:
    base = {"id": None, "source": None, "text": None, "input_type": "text", "options": [],
            "seek_question_id": None, "expected": None, "use": ["sort"], "job_id": None,
            "unsure": False, "note": ""}
    return {**base, **kw}


def cmd_build_set(args) -> int:
    if SET_FILE.exists() and not args.force:
        raise SystemExit(f"{SET_FILE} exists (it may hold your adjudicated labels): use --force to rebuild")
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    items: list[dict] = []
    seen_texts: set[str] = set()
    for s in fixture["samples"]:
        for n, q in enumerate(s["questions"], start=1):
            exp = dict(q["expected"])
            note = ""
            if exp["kind"] == "unknown":  # S5 Q4: layers 1-4 leave it; the truth label is below
                exp = {"kind": "assisted", "strategy": "years_skill_text",
                       "parameters": {"skill": "React Native with Expo"}}
                note = "Layers 1-4 leave this unsorted (expected); layer 5 should give this label."
            items.append(_item(
                id=f"{s['id']}-q{n}", source=s["id"], text=q["text"], input_type=q["input_type"],
                options=[o["label"] for o in q["options"]], seek_question_id=q["seek_question_id"],
                expected={"kind": exp["kind"], "strategy": exp["strategy"], "parameters": exp.get("parameters") or {}},
                note=note,
            ))
            seen_texts.add(" ".join(q["text"].split()).lower())

    if TEST_BANK_DB.exists():
        con = sqlite3.connect(f"file:{TEST_BANK_DB.as_posix()}?mode=ro", uri=True)
        try:
            rows = con.execute("SELECT id, library_id, library_version, text, input_type, options, kind, strategy, "
                               "parameters, classified_by FROM screening_questions ORDER BY id").fetchall()
        except sqlite3.OperationalError:
            rows = []
        for rid, lib, ver, text, input_type, options, kind, strategy, params, by in rows:
            if " ".join(text.split()).lower() in seen_texts:
                continue
            seek_id = f"{lib}_V_{ver}" if lib else f"indirect_bank_{rid}"
            if kind == "unknown":
                expected = {"kind": "assisted", "strategy": "skill_in_role_yes_no",
                            "parameters": {"skill": "Microsoft Azure DevOps"}}
                unsure, note = True, ("Test bank, unsorted. A yes/no 'experience using X' question: there is no "
                                      "plain skill yes/no strategy, and skill_in_role_yes_no only counts work "
                                      "roles, which is stricter than the question. Check the strategy.")
            else:
                expected = {"kind": kind, "strategy": strategy, "parameters": json.loads(params or "{}")}
                unsure, note = False, f"Test bank; label = what layers 2-4 gave it ({by}). Check it."
            items.append(_item(id=f"bank-{rid}", source="test bank", text=text, input_type=input_type,
                               options=json.loads(options or "[]"), seek_question_id=seek_id,
                               expected=expected, unsure=unsure, note=note))

    for iid, job_id, text, strategy, skill in _DRAFTS:
        items.append(_item(id=iid, source="synthetic", text=text, job_id=job_id, use=["sort", "draft"],
                           expected={"kind": "assisted", "strategy": strategy, "parameters": {"skill": skill}},
                           note="Synthetic open-ended question on an eval.db job, for the draft eval."))
    for iid, job_id, text, strategy, skill in _PLANTED:
        items.append(_item(id=iid, source="synthetic", text=text, job_id=job_id, use=["sort", "planted"],
                           expected={"kind": "assisted", "strategy": strategy, "parameters": {"skill": skill}},
                           note="Planted: the profile has no trace of the skill. The draft must not claim it."))
    for iid, text, topic, unsure in _USER_TRAPS:
        items.append(_item(id=iid, source="synthetic", text=text,
                           expected={"kind": "user", "strategy": "user", "parameters": {"topic": topic}},
                           unsure=unsure, note=_USER_TRAP_NOTE if unsure else "Synthetic `user` wording."))

    SET_FILE.parent.mkdir(parents=True, exist_ok=True)
    SET_FILE.write_text(json.dumps({
        "_note": ("Phase 9c eval set. `expected` is the TRUE label (kind, strategy, parameters) whatever layer "
                  "sorts it. Adjudicate: fix any wrong label, then set `adjudicated` to true. Items with "
                  "`unsure: true` need a look most. `use`: sort / draft / planted. job_id = an evals/eval.db "
                  "job. No answers are stored here."),
        "adjudicated": False,
        "items": items,
    }, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    n_unsure = sum(1 for i in items if i["unsure"])
    print(f"wrote {SET_FILE.relative_to(ROOT)}: {len(items)} items ({n_unsure} marked unsure)")
    return 0


# --------------------------------------------------------------------------------------
# sort
# --------------------------------------------------------------------------------------
def _subject(params: dict) -> str | None:
    v = (params or {}).get("skill") or (params or {}).get("role")
    return " ".join(v.lower().split()) if v else None


def _compare(got_kind, got_strategy, got_params, exp: dict) -> dict:
    kind_ok = got_kind == exp["kind"]
    strat_ok = got_strategy == exp["strategy"]
    want_subject = _subject(exp.get("parameters"))
    subject_ok = want_subject is None or _subject(got_params) == want_subject
    return {"kind_ok": kind_ok, "strategy_ok": strat_ok, "subject_ok": subject_ok}


def cmd_sort(args) -> int:
    data = _load_set()
    spend = bool(args.model)
    if spend and not args.confirm_spend:
        raise SystemExit("--model calls the small model: add --confirm-spend")
    _point_app_at(_scratch_db(args.run), spend)
    if spend:
        _prepare_scratch(args.run, args.fresh)
    from app.screening.sort import sort_question

    rows = []
    for it in data["items"]:
        s = sort_question(it["seek_question_id"] or f"indirect_{it['id']}", it["text"], it["input_type"], it["options"])
        row = {"id": it["id"], "source": it["source"], "expected": it["expected"], "layer": None,
               "got": None, "unsure": it["unsure"]}
        if s is not None:
            row["layer"] = s.classified_by
            row["got"] = {"kind": s.kind, "strategy": s.strategy, "parameters": s.parameters}
            row.update(_compare(s.kind, s.strategy, s.parameters, it["expected"]))
        rows.append(row)

    leftovers = [r for r in rows if r["layer"] is None]
    if spend:
        _layer5(args, data, leftovers)
    out = {"run": args.run, "adjudicated": data.get("adjudicated", False), "model": spend, "rows": rows}
    _run_dir(args.run).mkdir(parents=True, exist_ok=True)
    (_run_dir(args.run) / "sort.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    for line in _sort_summary(out):
        print(line)
    return 0


def _layer5(args, data, leftovers: list[dict]) -> None:
    """Layer 5 on leftovers labelled `assisted` ONLY. A `user` leftover is the failure the
    eval reports and is never sent."""
    import json as _json

    from app.models import ScreeningQuestion
    from app.screening import classify
    from app.db import SessionLocal

    by_id = {it["id"]: it for it in data["items"]}
    start = datetime.datetime.now(datetime.timezone.utc)
    with SessionLocal() as db:
        for row in leftovers:
            it = by_id[row["id"]]
            if it["expected"]["kind"] != "assisted":
                row["not_sent"] = "labelled user: never sent"
                continue
            if _spend_since(start) >= args.max_usd:
                row["not_sent"] = "stopped at --max-usd"
                continue
            q = ScreeningQuestion(text=it["text"], input_type=it["input_type"],
                                  options=_json.dumps(it["options"]) if it["options"] else None, kind="unknown")
            ok = classify._ask_model(db, q, None)  # the production prompt; q is transient, never stored
            row["layer"] = "model"
            if ok:
                row["got"] = {"kind": q.kind, "strategy": q.strategy, "parameters": _json.loads(q.parameters or "{}")}
                row.update(_compare(q.kind, q.strategy, row["got"]["parameters"], it["expected"]))
            else:
                row["got"] = None
                row.update({"kind_ok": False, "strategy_ok": False, "subject_ok": False, "model_rejected": True})
    print(f"layer 5 spend: ${_spend_since(start):.4f}")


def _pct(n: int, d: int) -> str:
    return f"{n}/{d}" + (f" ({100 * n / d:.0f}%)" if d else "")


def _sort_summary(out: dict) -> list[str]:
    rows = out["rows"]
    sorted_rows = [r for r in rows if r.get("got")]
    by_layer: dict[str, int] = {}
    for r in rows:
        by_layer[r["layer"] or "unsorted"] = by_layer.get(r["layer"] or "unsorted", 0) + 1
    user_left = [r for r in rows if r["layer"] is None and r["expected"]["kind"] == "user"]
    user_to_model = [r for r in rows if r["layer"] == "model" and r["expected"]["kind"] == "user"]
    assisted_left = [r for r in rows if r["layer"] is None and r["expected"]["kind"] == "assisted"]
    lines = [
        f"items: {len(rows)} (labels adjudicated: {out['adjudicated']}; layer 5 run: {out['model']})",
        "by layer: " + ", ".join(f"{k} {v}" for k, v in sorted(by_layer.items())),
        f"kind accuracy (sorted items): {_pct(sum(r['kind_ok'] for r in sorted_rows), len(sorted_rows))}",
        f"strategy accuracy (sorted items): {_pct(sum(r['strategy_ok'] for r in sorted_rows), len(sorted_rows))}",
        f"subject (skill/role) match: {_pct(sum(r['subject_ok'] for r in sorted_rows), len(sorted_rows))}",
        f"FAILURES - `user` questions that would reach the model: {len(user_left) + len(user_to_model)}"
        + (f" ({', '.join(r['id'] for r in user_left + user_to_model)})" if user_left or user_to_model else ""),
        f"assisted questions left for layer 5: {len(assisted_left)}"
        + (f" ({', '.join(r['id'] for r in assisted_left)})" if assisted_left else ""),
    ]
    wrong = [r for r in sorted_rows if not (r["kind_ok"] and r["strategy_ok"] and r["subject_ok"])]
    for r in wrong:
        lines.append(f"  wrong: {r['id']} ({r['layer']}): got {r['got']} expected {r['expected']}")
    return lines


# --------------------------------------------------------------------------------------
# labels (no model): every "you have it" has a resolving pointer
# --------------------------------------------------------------------------------------
def _capture_items(db, job_id: int, items: list[dict]) -> dict[str, int]:
    """Capture ``items`` onto the job's form and force each bank row to its label (so no
    layer-5 call can happen). Returns item id -> bank id."""
    from app.models import ScreeningQuestion
    from app.screening import bank

    captured = [bank.CapturedQuestion(
        seek_question_id=(it["seek_question_id"] or f"indirect_eval_{it['id']}").replace("-", "_"),
        field_name="questionnaire." + (it["seek_question_id"] or f"indirect_eval_{it['id']}").replace("-", "_"),
        text=it["text"], input_type=it["input_type"],
        options=[bank.CapturedOption(f"{it['id']}_{n}", label) for n, label in enumerate(it["options"])],
    ) for it in items]
    views = bank.record_job_questions(db, job_id, captured)
    ids = {}
    for it, v in zip(items, views):
        row = db.get(ScreeningQuestion, v["bank_id"])
        exp = it["expected"]
        row.kind, row.strategy = exp["kind"], exp["strategy"]
        row.parameters = json.dumps(exp.get("parameters") or {})
        row.classified_by = "user"
        ids[it["id"]] = row.id
    db.commit()
    return ids


def cmd_labels(args) -> int:
    data = _load_set()
    _point_app_at(_scratch_db(args.run), spend=False)
    full = _prepare_scratch(args.run, args.fresh)
    from app.db import SessionLocal
    from app.llm.letter.state import ProfileIndex
    from app.screening import assist

    items = [it for it in data["items"] if it["source"] != "test bank" or it["input_type"] != "text"]
    checks = {"items": 0, "pointers": 0, "unresolved": [], "have_without_evidence": [],
              "bracket_without_dates": [], "user_with_help": [], "jobs": full}
    with SessionLocal() as db:
        for job_id in full:
            _capture_items(db, job_id, items)
            out = assist.assist_job(db, job_id, PROFILE_ID)
            index = ProfileIndex(assist._load_profile(db, PROFILE_ID))
            for q in out["questions"]:
                a = q["assist"]
                checks["items"] += 1
                if q["kind"] == "user":
                    if a is not None or q.get("draftable"):
                        checks["user_with_help"].append((job_id, q["text"][:60]))
                    continue
                if a is None:
                    continue
                for item in [*a["have"]["evidence"], *a["have"]["not_counted"],
                             *(e for o in a["options"] for e in o["evidence"])]:
                    checks["pointers"] += 1
                    if index.resolve(item["pointer"]) is None:
                        checks["unresolved"].append((job_id, q["text"][:60], item["pointer"]))
                for o in a["options"]:
                    if o["tag"] in ("wanted_have", "have") and not o["evidence"]:
                        checks["have_without_evidence"].append((job_id, q["text"][:40], o["label"]))
                if a["profile_option"] and not (a["have"]["evidence"] and a["have"]["years"]["work_months"]):
                    checks["bracket_without_dates"].append((job_id, q["text"][:60], a["profile_option"]))
    _run_dir(args.run).mkdir(parents=True, exist_ok=True)
    (_run_dir(args.run) / "labels.json").write_text(json.dumps(checks, indent=1, ensure_ascii=False),
                                                    encoding="utf-8")
    bad = len(checks["unresolved"]) + len(checks["have_without_evidence"]) + len(checks["bracket_without_dates"]) \
        + len(checks["user_with_help"])
    print(f"jobs with full help in the scratch copy: {len(full)} {full}")
    print(f"question views checked: {checks['items']}; evidence pointers checked: {checks['pointers']}")
    print(f"problems: {bad} (unresolved {len(checks['unresolved'])}, 'have' without evidence "
          f"{len(checks['have_without_evidence'])}, bracket without dates {len(checks['bracket_without_dates'])}, "
          f"`user` with help {len(checks['user_with_help'])})")
    return 1 if bad else 0


# --------------------------------------------------------------------------------------
# drafts (paid)
# --------------------------------------------------------------------------------------
def _planted_verdict(answer: str, covered: str, skill: str) -> list[str]:
    from app.screening import assist, drafts

    problems = []
    if covered == "yes":
        problems.append("covered: yes")
    if drafts.stated_durations(answer):
        problems.append(f"states a duration: {drafts.stated_durations(answer)}")
    issues = drafts.code_issues(answer, [], ceiling_months=None, skill=skill, missing_labels=[skill])
    problems.extend(issues)
    return problems


def cmd_drafts(args) -> int:
    data = _load_set()
    if not args.confirm_spend:
        raise SystemExit("drafts calls the mid model: add --confirm-spend")
    if not data.get("adjudicated"):
        print("NOTE: set.json is not marked adjudicated yet.")
    _point_app_at(_scratch_db(args.run), spend=True)
    full = set(_prepare_scratch(args.run, args.fresh))
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import LlmUsage
    from app.screening import assist, drafts

    start = datetime.datetime.now(datetime.timezone.utc)
    results = []
    with SessionLocal() as db:
        profile = assist._load_profile(db, PROFILE_ID)
        for it in [i for i in data["items"] if {"draft", "planted"} & set(i["use"])]:
            res = {"id": it["id"], "use": "planted" if "planted" in it["use"] else "draft", "job_id": it["job_id"],
                   "skill": (it["expected"].get("parameters") or {}).get("skill"), "text": it["text"]}
            if it["job_id"] not in full:
                res["skipped"] = "job has no full-pipeline letter in the scratch copy"
                results.append(res)
                continue
            if res["use"] == "planted" and not assist._missing_parts(profile, res["skill"]):
                res["skipped"] = "the profile has this skill: not a planted case"
                results.append(res)
                continue
            if _spend_since(start) >= args.max_usd:
                res["skipped"] = "stopped at --max-usd"
                results.append(res)
                continue
            ids = _capture_items(db, it["job_id"], [it])
            before = datetime.datetime.now(datetime.timezone.utc)
            try:
                view = drafts.draft_question(db, it["job_id"], ids[it["id"]], PROFILE_ID)["draft"]
            except Exception as exc:  # report, keep going
                res["error"] = f"{type(exc).__name__}: {exc}"
                results.append(res)
                continue
            usage = db.scalars(select(LlmUsage).where(LlmUsage.created_at >= before,
                                                      LlmUsage.task == drafts.TASK)).all()
            res.update(view)
            res["cost_usd"] = round(float(sum(u.cost_usd or 0 for u in usage)), 5)
            res["years_flag"] = any("more than your profile's dates support" in i for i in view["issues"])
            if res["use"] == "planted":
                res["planted_problems"] = _planted_verdict(view["answer"] or "", view["covered"], res["skill"])
            results.append(res)
            print(f"{it['id']}: covered={view['covered']} verified={view['verified']} "
                  f"issues={len(view['issues'])} ${res['cost_usd']:.4f}")
    run_dir = _run_dir(args.run)
    (run_dir / "drafts.json").write_text(json.dumps(results, indent=1, ensure_ascii=False, default=str),
                                         encoding="utf-8")
    _write_drafts_md(run_dir, results)
    print(f"spend: ${_spend_since(start):.4f}; answers in {(run_dir / 'drafts.md').relative_to(ROOT)}")
    return 0


def _write_drafts_md(run_dir: Path, results: list[dict]) -> None:
    lines = ["# Quick Apply drafts (private: the real profile's answers; gitignored)", ""]
    for r in results:
        lines.append(f"## {r['id']} ({r['use']}, job {r['job_id']}): {r['text']}")
        if r.get("skipped") or r.get("error"):
            lines += [f"_{r.get('skipped') or r.get('error')}_", ""]
            continue
        lines.append(f"covered **{r['covered']}**, verified **{r['verified']}**, "
                     f"years ceiling {r['years_months']} months, ${r['cost_usd']:.4f}")
        lines += ["", "> " + (r["answer"] or "(no answer)").replace("\n", "\n> "), ""]
        if r.get("note"):
            lines.append(f"Note: {r['note']}")
        for i in r["issues"]:
            lines.append(f"- issue: {i}")
        for p in r.get("planted_problems") or []:
            lines.append(f"- PLANTED FAIL: {p}")
        lines.append("")
    (run_dir / "drafts.md").write_text("\n".join(lines), encoding="utf-8")


# --------------------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------------------
def cmd_report(args) -> int:
    run_dir = _run_dir(args.run)
    lines = [f"# Quick Apply question help eval: {args.run}", "",
             f"_Generated {datetime.date.today().isoformat()} by `scripts/screening_eval.py report`. "
             "Numbers only; the answers stay in the gitignored run folder._", ""]
    sort_file, labels_file, drafts_file = (run_dir / f for f in ("sort.json", "labels.json", "drafts.json"))
    if sort_file.exists():
        lines += ["## Sorting", ""] + [f"- {s}" for s in _sort_summary(json.loads(sort_file.read_text("utf-8")))] + [""]
    if labels_file.exists():
        c = json.loads(labels_file.read_text("utf-8"))
        lines += ["## Labels (no model)", "",
                  f"- jobs with full help in the scratch copy: {len(c['jobs'])}",
                  f"- question views checked: {c['items']}; evidence pointers checked: {c['pointers']}",
                  f"- unresolved pointers: {len(c['unresolved'])}; 'you have it' without evidence: "
                  f"{len(c['have_without_evidence'])}; bracket without dated work: {len(c['bracket_without_dates'])}; "
                  f"`user` questions given help: {len(c['user_with_help'])}", ""]
    if drafts_file.exists():
        rs = json.loads(drafts_file.read_text("utf-8"))
        done = [r for r in rs if "covered" in r]
        d = [r for r in done if r["use"] == "draft"]
        p = [r for r in done if r["use"] == "planted"]
        cost = sum(float(r.get("cost_usd") or 0) for r in done)
        lines += ["## Drafts (mid tier)", "",
                  f"- drafted: {len(d)} questions; skipped/errors: {len(rs) - len(done)}",
                  f"- verified (passed every code check): {_pct(sum(r['verified'] for r in d), len(d))}",
                  f"- covered yes / partly / no: {sum(r['covered'] == 'yes' for r in d)} / "
                  f"{sum(r['covered'] == 'partly' for r in d)} / {sum(r['covered'] == 'no' for r in d)}",
                  f"- flagged (any issue): {sum(bool(r['issues']) for r in d)}; years above the dates: "
                  f"{sum(r['years_flag'] for r in d)}",
                  f"- planted (no trace of the skill): {_pct(sum(not r['planted_problems'] for r in p), len(p))} "
                  "made no claim of it, no duration, not covered 'yes'",
                  f"- spend: ${cost:.4f} (${cost / max(len(done), 1):.4f} per draft)", ""]
        # The stored answers re-checked with TODAY's code checks (no model): a check added
        # after the run shows up here without paying for a new one.
        _point_app_at(_scratch_db(args.run), spend=False)
        from app.screening import drafts as drafts_mod

        rescored = [r for r in d if r["answer"] and drafts_mod.code_issues(
            r["answer"], [], ceiling_months=r["years_months"], skill=r["skill"], missing_labels=[])]
        meta = [r["id"] for r in d if r["answer"] and any(
            i.startswith("talks to you") for i in drafts_mod.code_issues(
                r["answer"], [], ceiling_months=None, skill=r["skill"], missing_labels=[]))]
        lines += [f"- re-checked with today's code checks (no model): {len(rescored)} of "
                  f"{sum(bool(r['answer']) for r in d)} answers would now be flagged; a note for the candidate "
                  f"inside the answer: {len(meta)}" + (f" ({', '.join(meta)})" if meta else ""), ""]
        kinds: dict[str, int] = {}
        for r in d:
            for i in r["issues"]:
                key = ("years above the dates" if "dates support" in i else "missing part mentioned"
                       if i.startswith("mentions ") else "listed skill as experience" if "listed skill" in i
                       else "pointer/quote (verify)")
                kinds[key] = kinds.get(key, 0) + 1
        if kinds:
            lines += ["Issue kinds: " + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())), ""]
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"{args.run}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["build-set", "sort", "labels", "drafts", "report"])
    ap.add_argument("--run", default=DEFAULT_RUN)
    ap.add_argument("--force", action="store_true", help="build-set: overwrite set.json")
    ap.add_argument("--fresh", action="store_true", help="re-copy eval.db into the scratch DB")
    ap.add_argument("--model", action="store_true", help="sort: also run layer 5 (small model)")
    ap.add_argument("--confirm-spend", action="store_true", help="allow model calls")
    ap.add_argument("--max-usd", type=float, default=1.0)
    args = ap.parse_args()
    return {"build-set": cmd_build_set, "sort": cmd_sort, "labels": cmd_labels, "drafts": cmd_drafts,
            "report": cmd_report}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
