"""Install the private-data guard's git hooks (docs/private-data-guard-plan.md).

    python scripts/install_hooks.py            # install / update the hooks, create the list
    python scripts/install_hooks.py --check    # exit 1 if the install is missing or stale
    ... --repo PATH                            # another repository (tests)
    ... --force                                # replace a pre-commit / commit-msg hook
                                               # this script didn't write

Run once per clone, and again after hooks/ or scripts/check_private_data.py change (the
`--check` says when). It copies hooks/pre-commit, hooks/commit-msg and
scripts/check_private_data.py into git's hooks folder (`git rev-parse --git-path hooks`).
That folder is shared by every worktree of the clone, so the guard covers all of them, on
every branch, including branches that don't have these files yet.

It also creates the private list, `<git common dir>/private-data.txt`, as a comments-only
template if it is missing. It never overwrites or reads an existing list: the owner fills
it in. Inside .git/ git can never track it; .gitignore ignores the name anywhere else too.
Standard library only, no network.
"""

from __future__ import annotations

import argparse
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKER = "private-data guard (installed by scripts/install_hooks.py"
LIST_NAME = "private-data.txt"

# installed name -> source in this repo
SOURCES = {
    "pre-commit": ROOT / "hooks" / "pre-commit",
    "commit-msg": ROOT / "hooks" / "commit-msg",
    "check_private_data.py": ROOT / "scripts" / "check_private_data.py",
}
HOOKS = ("pre-commit", "commit-msg")

LIST_TEMPLATE = """\
# Private list for the private-data guard (docs/private-data-guard-plan.md).
#
# One entry per line. A commit that ADDS a line containing any entry is blocked.
# Matching ignores case and extra spaces; a number (phone, ID) also matches with its
# spaces, dashes and dots removed. Lines starting with # are comments. Entries shorter
# than 4 characters are skipped.
#
# This file lives inside .git/, so git can never commit it, and every worktree of this
# clone uses it. It is never printed: a blocked commit shows only this file's line number.
#
# Add, for example (these are fake):
#   0400 000 000
#   12 Example Street
#   liam.chen@example.com
#   a distinctive sentence from your CV or a cover letter
#
# Do NOT list your name or commit email (allowed in the repo, see CLAUDE.md PUBLIC REPO).
"""


def _git_path(repo: Path, *args: str) -> Path:
    proc = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", *args],
        cwd=repo, capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        sys.exit(f"install_hooks: not a git repository? {proc.stderr.strip()}")
    return Path(proc.stdout.strip())


def hooks_dir(repo: Path) -> Path:
    return _git_path(repo, "--git-path", "hooks")


def list_path(repo: Path) -> Path:
    return _git_path(repo, "--git-common-dir") / LIST_NAME


def _lf_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def check(repo: Path) -> list[str]:
    """Problems with the install; empty = up to date."""
    problems: list[str] = []
    target = hooks_dir(repo)
    for name, src in SOURCES.items():
        dest = target / name
        if not dest.is_file():
            problems.append(f"{dest} is missing")
        elif _lf_text(dest) != _lf_text(src):
            problems.append(f"{dest} is out of date with {src.relative_to(ROOT).as_posix()}")
    if not list_path(repo).is_file():
        problems.append(f"{list_path(repo)} is missing")
    return problems


def install(repo: Path, force: bool = False) -> list[str]:
    """Install the hooks and the list template. Returns what it did."""
    target = hooks_dir(repo)
    target.mkdir(parents=True, exist_ok=True)

    for name in HOOKS:
        dest = target / name
        if dest.is_file() and MARKER not in dest.read_text(encoding="utf-8", errors="replace") and not force:
            sys.exit(
                f"install_hooks: {dest} exists and was not written by this script.\n"
                "  Merge it by hand, or re-run with --force to replace it."
            )

    done: list[str] = []
    for name, src in SOURCES.items():
        dest = target / name
        # LF endings: core.autocrlf may have checked the sources out with CRLF, and a
        # "#!/bin/sh\r" first line doesn't run.
        with open(dest, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(_lf_text(src))
        dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        done.append(f"installed {dest}")

    lst = list_path(repo)
    if lst.exists():
        done.append(f"kept the existing private list {lst} (not read)")
    else:
        with open(lst, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(LIST_TEMPLATE)
        done.append(f"created the private list template {lst}: add your entries to it")
    return done


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--check", action="store_true", help="report a missing or stale install")
    ap.add_argument("--force", action="store_true", help="replace hooks this script didn't write")
    ap.add_argument("--repo", type=Path, default=ROOT, help="repository to install into")
    args = ap.parse_args(argv)

    if args.check:
        problems = check(args.repo)
        for p in problems:
            print(f"install_hooks: {p}", file=sys.stderr)
        if problems:
            print("  Run: python scripts/install_hooks.py", file=sys.stderr)
            return 1
        print("install_hooks: up to date")
        return 0

    for line in install(args.repo, force=args.force):
        print(f"install_hooks: {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
