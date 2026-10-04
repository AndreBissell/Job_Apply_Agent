"""Export the Quick Apply question bank to reports/question-bank.md (Phase 9a).

    python scripts/export_question_bank.py                 # test env (app.db), the default
    python scripts/export_question_bank.py --env real      # real.db
    python scripts/export_question_bank.py --out my.md     # another file
    python scripts/export_question_bank.py --stdout        # print instead of writing

The Markdown has the shape of docs/quick-apply-samples.md (jobs with their questions in
form order, the Seek library ids seen, the kinds seen, what still needs review), so the
samples doc and the Phase 9c eval set can be kept current without copy-pasting HTML.

The bank holds questions only, never answers. This script only SELECTs: it never
commits, and it does not run migrations. If DATABASE_URL is already set it wins over
``--env``. The output is gitignored (reports/). No LLM calls, no network.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATABASES = {"real": "real.db", "test": "app.db"}
OUT = ROOT / "reports" / "question-bank.md"

MAX_OPTIONS = 12   # more than this and the list is cut
SHOWN_OPTIONS = 10
_TYPE_LABELS = {"single": "single", "dropdown": "dropdown", "text": "free text", "multi": "multi (checkboxes)"}
_SHOWN_ID = re.compile(r"^AU_Q_(?:\d+|[0-9A-Fa-f]{32})_V_\d+$")  # library or generated; not employer ids


def _cell(text: object) -> str:
    """One table cell: single line, pipes escaped."""
    return re.sub(r"\s+", " ", str(text)).replace("|", "\\|").strip()


def _loads(raw: str | None, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except ValueError:
        return default


def _options_cell(options: list[str]) -> str:
    if not options:
        return "—"
    if len(options) > MAX_OPTIONS:
        return _cell(" / ".join(options[:SHOWN_OPTIONS]) + f" … ({len(options)} options)")
    return _cell(" / ".join(options))


def _kind_label(row, *, bold_assisted: bool = True) -> str:
    params = _loads(row.parameters, {})
    if row.kind == "user":
        topic = params.get("topic")
        label = f"user ({topic.replace('_', ' ')})" if topic else "user"
    elif row.kind == "assisted":
        label = f"assisted ({row.strategy})" if row.strategy else "assisted"
        if bold_assisted:
            label = f"**{label}**"
    else:
        label = "unsorted (needs the model)"
    if row.status == "confirmed":
        label += " ✓"
    if row.classified_by == "user":
        label += " (you corrected this)"
    return label


def _question_cell(row, seek_question_id: str | None) -> str:
    text = _cell(row.text)
    if seek_question_id and _SHOWN_ID.match(seek_question_id):
        text += f" (`{seek_question_id}`)"
    return text


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def render(db, *, today: datetime.date | None = None) -> str:
    """The whole bank as Markdown. Only SELECTs from ``db``."""
    from sqlalchemy import select

    from app.models import JobListing, JobScreeningQuestion, ScreeningQuestion

    today = today or datetime.date.today()
    questions = db.scalars(select(ScreeningQuestion).order_by(ScreeningQuestion.id)).all()
    links = db.scalars(
        select(JobScreeningQuestion).order_by(JobScreeningQuestion.job_id, JobScreeningQuestion.position)
    ).all()
    job_ids = sorted({link.job_id for link in links})
    jobs = {j.id: j for j in db.scalars(select(JobListing).where(JobListing.id.in_(job_ids))).all()} if job_ids else {}

    by_job: dict[int, list] = {}
    for link in links:
        by_job.setdefault(link.job_id, []).append(link)
    # newest capture first; the job id breaks ties so the order is stable
    job_order = sorted(
        by_job,
        key=lambda jid: (max(l.last_seen_at for l in by_job[jid]).replace(tzinfo=None), jid),
        reverse=True,
    )

    n_kind = Counter(q.kind for q in questions)
    n_status = Counter(q.status for q in questions)
    lines = [
        "# Seek Quick Apply: question bank export",
        "",
        f"Generated {today.isoformat()}. {_plural(len(questions), 'question')} in the bank, "
        f"{_plural(len(by_job), 'job')} with captured forms. "
        f"By kind: {n_kind.get('user', 0)} user, {n_kind.get('assisted', 0)} assisted, "
        f"{n_kind.get('unknown', 0)} unknown. "
        f"By status: {n_status.get('new', 0)} new, {n_status.get('confirmed', 0)} confirmed.",
        "",
        "The bank holds questions only (text, type, option labels), never answers.",
        "",
        "## Jobs",
        "",
    ]
    if not job_order:
        lines += ["No job has a captured question form yet.", ""]
    for jid in job_order:
        job = jobs.get(jid)
        title = _cell(job.title) if job else f"job row {jid}"
        company = _cell(job.company) if job and job.company else "company unknown"
        src_id = job.source_job_id if job else str(jid)
        lines += [
            f"### {title}, {company} (job {src_id})",
            "",
            "| # | Question (id) | Type | Options | Kind |",
            "|---|---|---|---|---|",
        ]
        for n, link in enumerate(sorted(by_job[jid], key=lambda l: l.position), start=1):
            q = link.question
            opts = _loads(q.options, [])
            lines.append(
                f"| {n} | {_question_cell(q, link.seek_question_id)} | "
                f"{_TYPE_LABELS.get(q.input_type, _cell(q.input_type))} | {_options_cell(opts)} | "
                f"{_cell(_kind_label(q))} |"
            )
        lines.append("")

    lines += ["## Seek standard-library ids seen", ""]
    library = sorted(
        (q for q in questions if q.library_id),
        key=lambda q: (-(q.times_seen or 0), q.id),
    )
    if library:
        lines += ["| Id | Question | Kind | Jobs |", "|---|---|---|---|"]
        for q in library:
            ident = f"{q.library_id}_V_{q.library_version}" if q.library_version else q.library_id
            lines.append(
                f"| `{ident}` | {_cell(q.text)} | {_cell(_kind_label(q, bold_assisted=False))} | {q.times_seen or 0} |"
            )
    else:
        lines.append("None yet.")
    lines.append("")

    topics = Counter(_loads(q.parameters, {}).get("topic") or "other" for q in questions if q.kind == "user")
    strategies = Counter(q.strategy or "other" for q in questions if q.kind == "assisted")
    unsorted = [q for q in questions if q.kind == "unknown"]
    topic_text = ", ".join(f"{t} ({n})" for t, n in sorted(topics.items(), key=lambda kv: (-kv[1], kv[0]))) or "none yet"
    strat_text = ", ".join(f"{s} ({n})" for s, n in sorted(strategies.items(), key=lambda kv: (-kv[1], kv[0]))) or "none yet"
    unsorted_text = "; ".join(f"\"{_cell(q.text)}\"" for q in unsorted) or "none"
    lines += [
        "## Question kinds seen so far",
        "",
        f"- `user` (distinct questions by topic): {topic_text}.",
        f"- `assisted` (distinct questions by strategy): {strat_text}.",
        f"- unsorted (needs the model): {unsorted_text}.",
        "",
    ]

    review = sorted(
        (q for q in questions if q.status == "new"),
        key=lambda q: (0 if q.kind == "unknown" else 1, -(q.times_seen or 0), q.id),
    )
    lines += ["## Needs review (status new)", "", f"{len(review)} question(s) not yet confirmed.", ""]
    for q in review:
        tag = "unsorted" if q.kind == "unknown" else _kind_label(q, bold_assisted=False)
        lines.append(f"- {_cell(q.text)} ({tag})")
    if review:
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--env", choices=sorted(DATABASES), default="test",
                        help="which database to read (default test); ignored if DATABASE_URL is set")
    parser.add_argument("--out", default=str(OUT), help="output file (default reports/question-bank.md)")
    parser.add_argument("--stdout", action="store_true", help="print the report instead of writing it")
    args = parser.parse_args(argv)

    # Before any app import: db.py reads DATABASE_URL at import time.
    if not os.environ.get("DATABASE_URL"):
        os.environ["DATABASE_URL"] = f"sqlite:///{(ROOT / DATABASES[args.env]).as_posix()}"
    sys.path.insert(0, str(ROOT))

    from sqlalchemy import create_engine
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.orm import Session

    # Own engine from the URL (not app.db's): reads only, never commits, no migrations.
    engine = create_engine(os.environ["DATABASE_URL"])
    try:
        with Session(engine) as db:
            text = render(db)
    except OperationalError as exc:
        print(f"could not read the question bank ({exc.orig}). Has the DB been migrated to head?", file=sys.stderr)
        return 1
    finally:
        engine.dispose()

    if args.stdout:
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
        print(text, end="")
        return 0
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out} ({text.count(chr(10))} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
