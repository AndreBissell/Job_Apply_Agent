"""Write the to-work-on list to reports/to-work-on.md (plan §5.9).

    python scripts/gap_report.py real              # real.db
    python scripts/gap_report.py test              # app.db
    python scripts/gap_report.py real --days 30    # a shorter window
    python scripts/gap_report.py real --stdout     # print instead of writing the file

The list is every skill you said "No" to in an ask_user question, ranked by how
many distinct ads asked for it in the last 90 days, then by how many of those
rated it essential. The skills at the top are the ones worth learning. Clear an
item with POST /gaps/{id}/clear, or by adding the skill to your profile.

The file is gitignored: it is personal profile data. No LLM calls, no Seek requests.
"""

from __future__ import annotations

import argparse
import datetime
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATABASES = {"real": "real.db", "test": "app.db", "eval": "evals/eval.db"}
OUT = ROOT / "reports" / "to-work-on.md"


def render(items: list, *, days: int, env: str, today: datetime.date) -> str:
    """The report as Markdown. ``items`` are ``gaps.WorkItem``s, already ranked."""
    lines = [
        "# To work on",
        "",
        f"Skills you said you don't have, ranked by how many ads asked for them in the last "
        f"{days} days ({env}, {today.isoformat()}).",
        "",
    ]
    if not items:
        lines.append("Nothing yet. A skill lands here when you answer No to a question before a cover letter.")
        return "\n".join(lines) + "\n"
    lines += [
        f"| # | Skill | Ads ({days} days) | Essential | All time | Said no | Recent ads |",
        "|---|---|---|---|---|---|---|",
    ]
    for n, i in enumerate(items, start=1):
        said = i.said_no_at.date().isoformat() if i.said_no_at else ""
        titles = "; ".join(t.replace("|", "/") for t in i.recent_titles) or "none yet"
        lines.append(
            f"| {n} | {i.label.replace('|', '/')} | {i.recent} | {i.essential} | {i.total} | {said} | {titles} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("env", choices=sorted(DATABASES))
    parser.add_argument("--days", type=int, default=90, help="the ranking window (default 90)")
    parser.add_argument("--profile-id", type=int, default=1)
    parser.add_argument("--stdout", action="store_true", help="print the report instead of writing it")
    args = parser.parse_args()

    # Before any app import: db.py reads DATABASE_URL at import time.
    os.environ["DATABASE_URL"] = f"sqlite:///{(ROOT / DATABASES[args.env]).as_posix()}"
    sys.path.insert(0, str(ROOT))

    # Bring the DB to the current schema first, as run_api.py does on startup.
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")

    from app import gaps
    from app.db import SessionLocal

    with SessionLocal() as db:
        items = gaps.to_work_on(db, args.profile_id, days=args.days)
    text = render(items, days=args.days, env=args.env, today=datetime.date.today())
    if args.stdout:
        print(text)
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({len(items)} item(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
