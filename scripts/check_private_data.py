"""Block a commit that would add the owner's private data (docs/private-data-guard-plan.md).

    python scripts/check_private_data.py --staged          # pre-commit: the staged changes
    python scripts/check_private_data.py --message FILE    # commit-msg: the commit message
    ... --list PATH                                        # another list (tests)

Normally run by the git hooks that scripts/install_hooks.py installs, not by hand.

The private list is `<git common dir>/private-data.txt` (inside .git/, so git can never
track it, and every worktree shares it): one entry per line, `#` starts a comment line. The
owner writes it; nothing in the repo reads it except this checker.

Blocks (exit 1):
  - an ADDED line in the staged diff (or a commit-message line) containing a list entry.
    Case-insensitive, runs of whitespace count as one space; an entry that is mostly digits
    (phone, ID numbers) also matches with spaces, dashes, dots, slashes, brackets and `+`
    removed, so "0400 000 000" matches "0400000000";
  - an added or renamed file whose PATH contains an entry (reported without the path);
  - an added or renamed binary file (git's own binary test, or a database / image /
    document extension): screenshots, databases and letters are never committed;
  - any staged file named private-data.txt.
Cannot run (exit 2, also blocks): no list file, an unreadable list, a git error.

The matched text is NEVER printed, only `file:line` and the list's line number, so a
private string can't reach a terminal log or an AI assistant's context. Only added lines
are checked, so removed lines and history already accepted don't trip it. The list only
knows what is on it: reading `git diff --cached` before committing is still the rule.
Standard library only, no network.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

LIST_NAME = "private-data.txt"
MIN_ENTRY_LEN = 4
MIN_DIGITS = 6

# Binary files git might not flag on its own (empty or tiny files): always blocked.
BLOCKED_EXTENSIONS = {
    ".db", ".sqlite", ".sqlite3",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".heic",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".odt", ".zip",
}

_WS = re.compile(r"\s+")
_SEPARATORS = re.compile(r"[\s\-./()+]")
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_SCISSORS = "------------------------ >8 ------------------------"

EXIT_OK, EXIT_BLOCKED, EXIT_CANNOT_RUN = 0, 1, 2


class CannotRun(Exception):
    """The check could not run; the commit is blocked (fail closed)."""


@dataclass(frozen=True)
class Entry:
    line_no: int     # the entry's line in the list file, the only thing ever printed
    text: str        # normalised: casefolded, whitespace collapsed
    digits: str      # separators removed, for number-like entries; "" otherwise


@dataclass(frozen=True)
class Finding:
    message: str


# ---------------------------------------------------------------- the list

def _norm(s: str) -> str:
    return _WS.sub(" ", s.casefold()).strip()


def _digits_form(s: str) -> str:
    return _SEPARATORS.sub("", s.casefold())


def _is_number_like(raw: str) -> bool:
    compact = _SEPARATORS.sub("", raw)
    digits = sum(c.isdigit() for c in compact)
    return digits >= MIN_DIGITS and digits * 2 > len(compact)


def load_entries(path: Path) -> tuple[list[Entry], list[str]]:
    """Read the list. Returns (entries, warnings); raises CannotRun if it can't be read."""
    if not path.is_file():
        raise CannotRun(
            f"the private list is missing: {path}\n"
            "  Run `python scripts/install_hooks.py` to create it, then add your entries."
        )
    try:
        raw_lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise CannotRun(f"the private list can't be read ({type(exc).__name__}): {path}") from exc

    entries: list[Entry] = []
    warnings: list[str] = []
    for i, raw in enumerate(raw_lines, start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        text = _norm(stripped)
        if len(text) < MIN_ENTRY_LEN:
            warnings.append(f"list line {i} is shorter than {MIN_ENTRY_LEN} characters; skipped")
            continue
        digits = _digits_form(stripped) if _is_number_like(stripped) else ""
        entries.append(Entry(line_no=i, text=text, digits=digits))
    return entries, warnings


def matching_entries(text: str, entries: list[Entry]) -> list[Entry]:
    norm = _norm(text)
    digits = _digits_form(text)
    return [e for e in entries if e.text in norm or (e.digits and e.digits in digits)]


# ---------------------------------------------------------------- git

def _git(repo: Path, *args: str) -> bytes:
    try:
        proc = subprocess.run(
            ["git", "-c", "core.quotepath=false", *args],
            cwd=repo, capture_output=True, check=False,
        )
    except OSError as exc:
        raise CannotRun(f"git could not be started: {exc}") from exc
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise CannotRun(f"`git {' '.join(args[:2])}` failed: {err}")
    return proc.stdout


def default_list_path(repo: Path) -> Path:
    out = _git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return Path(out.decode("utf-8", "replace").strip()) / LIST_NAME


def _staged_paths(repo: Path) -> list[tuple[str, str]]:
    """(status letter, path) for every staged change except deletions."""
    out = _git(repo, "diff", "--cached", "--name-status", "-z", "--diff-filter=ACMR").decode(
        "utf-8", "replace"
    )
    parts = out.split("\0")
    result: list[tuple[str, str]] = []
    i = 0
    while i < len(parts) and parts[i]:
        status = parts[i][0]
        if status in "RC":            # R100\0old\0new
            result.append((status, parts[i + 2]))
            i += 3
        else:                         # A\0path
            result.append((status, parts[i + 1]))
            i += 2
    return result


def _binary_paths(repo: Path) -> set[str]:
    """Staged paths git itself treats as binary (numstat shows '-\t-')."""
    out = _git(repo, "diff", "--cached", "--numstat", "-z", "--diff-filter=ACMR").decode(
        "utf-8", "replace"
    )
    parts = out.split("\0")
    binary: set[str] = set()
    i = 0
    while i < len(parts) and parts[i]:
        added, deleted, rest = parts[i].split("\t", 2)
        if rest:                      # plain change: "a\td\tpath"
            path, i = rest, i + 1
        else:                         # rename/copy: "a\td\t" \0 old \0 new
            path, i = parts[i + 2], i + 3
        if added == "-" and deleted == "-":
            binary.add(path)
    return binary


def _added_lines(repo: Path):
    """Yield (path, new line number, text) for every added line in the staged diff."""
    out = _git(
        repo, "diff", "--cached", "-U0", "--no-color", "--no-ext-diff", "--diff-filter=ACMR",
        "--src-prefix=a/", "--dst-prefix=b/",
    ).decode("utf-8", "replace")
    path = None
    line_no = 0
    in_hunk = False
    for line in out.splitlines():
        if line.startswith("diff --git "):
            path, in_hunk = None, False
            continue
        if not in_hunk:
            if line.startswith("+++ "):
                target = line[4:].strip().strip('"')
                path = target[2:] if target.startswith("b/") else None
            elif (m := _HUNK.match(line)) and path is not None:
                in_hunk, line_no = True, int(m.group(1))
            continue
        if line.startswith("@@"):
            m = _HUNK.match(line)
            line_no = int(m.group(1)) if m else line_no
        elif line.startswith("+"):
            yield path, line_no, line[1:]
            line_no += 1
        # "-" lines and "\ No newline at end of file" don't move the new-file counter


# ---------------------------------------------------------------- the checks

def check_staged(repo: Path, entries: list[Entry]) -> list[Finding]:
    findings: list[Finding] = []
    staged = _staged_paths(repo)
    binary = _binary_paths(repo)

    hidden: set[str] = set()
    for _status, path in staged:
        for e in matching_entries(path, entries):
            hidden.add(path)
            findings.append(Finding(f"a staged file NAME matches the private list, line {e.line_no}"))

    def shown(path: str) -> str:
        return "<file name hidden: it matches the private list>" if path in hidden else path

    for status, path in staged:
        name = path.rsplit("/", 1)[-1]
        if name.casefold() == LIST_NAME:
            findings.append(Finding(f"{shown(path)}: the private list itself must never be committed"))
            continue
        if status in "ACR" and (path in binary or Path(name).suffix.casefold() in BLOCKED_EXTENSIONS):
            findings.append(Finding(
                f"{shown(path)}: binary file (screenshots, databases, documents and other "
                "binary files are never committed)"
            ))

    for path, line_no, text in _added_lines(repo):
        for e in matching_entries(text, entries):
            findings.append(Finding(f"{shown(path)}:{line_no} matches the private list, line {e.line_no}"))
    return findings


def check_message(message_file: Path, entries: list[Entry]) -> list[Finding]:
    try:
        text = message_file.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise CannotRun(f"the commit message can't be read: {exc}") from exc
    findings: list[Finding] = []
    for i, line in enumerate(text.splitlines(), start=1):
        if _SCISSORS in line:
            break                     # `commit -v`: below this is the diff, checked already
        if line.startswith("#"):
            continue
        for e in matching_entries(line, entries):
            findings.append(Finding(f"commit message line {i} matches the private list, line {e.line_no}"))
    return findings


# ---------------------------------------------------------------- entry point

def _say(msg: str) -> None:
    print(f"private-data guard: {msg}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--staged", action="store_true", help="check the staged changes")
    mode.add_argument("--message", type=Path, metavar="FILE", help="check a commit message file")
    ap.add_argument("--list", type=Path, metavar="PATH", help="private list (default: in .git)")
    args = ap.parse_args(argv)

    repo = Path(os.getcwd())
    try:
        list_path = args.list or default_list_path(repo)
        entries, warnings = load_entries(list_path)
        for w in warnings:
            _say(f"warning: {w}")
        if not entries:
            _say(f"warning: the private list has no entries yet ({list_path}); "
                 "only file-type checks ran")
        findings = check_staged(repo, entries) if args.staged else check_message(args.message, entries)
    except CannotRun as exc:
        _say(f"commit BLOCKED, the check could not run: {exc}")
        return EXIT_CANNOT_RUN

    if not findings:
        return EXIT_OK
    _say("commit BLOCKED.")
    for f in findings:
        print(f"  {f.message}", file=sys.stderr)
    print(
        "The matched text is not shown. Remove it, `git add` again, and commit again.\n"
        "If a hit is wrong, change the entry in the private list (the line number above).",
        file=sys.stderr,
    )
    return EXIT_BLOCKED


if __name__ == "__main__":
    sys.exit(main())
