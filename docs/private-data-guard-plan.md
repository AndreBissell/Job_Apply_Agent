# Plan: a pre-commit guard against private data

Status: DONE 2026-11-24 (built, full suite green, installed, owner's manual check passed).
Approved by the owner 2026-11-24 ("make a detailed plan and implement it; you decide").
Branch `ai-files-fix`. Decisions D1-D8 below were made by Claude at the owner's request.

## Goal

A commit that adds a known piece of the owner's private data, a database / image / PDF file, or
the private list itself is **blocked before it exists**, whoever makes it (the owner, Claude
Code, VS Code's commit button), on any branch and in any worktree. This backs the PUBLIC REPO
rule in CLAUDE.md with a check, so the rule no longer rests only on someone reading
`git diff --cached`.

## Context: what exists today (checked 2026-11-24)

- **The rule is text only.** CLAUDE.md lines 22-39 (PUBLIC REPO) list what must never be
  committed and ask for a staged-diff check before every commit.
- **`.gitignore` blocks known file locations** (real.db, app.db, .env, screenshots*/, evals
  working folders, reports/, backups/, dev_data/). It cannot see file contents, and
  `git add -f` overrides it.
- **No content check anywhere.** No active git hooks (only git's `.sample` files), no
  `core.hooksPath`, no pre-commit config, no secret scanner. CI
  (`.github/workflows/tests.yml`) runs pytest and `node --check` only, and runs after a push,
  which is too late for a public repo.
- **The incident behind the rule was a content leak**: real CV text inside
  `tests/test_style_lint.py`, a normal tracked file. Nothing today would catch a repeat.
- **Worktrees share one git folder and one hooks folder.** `git rev-parse --git-common-dir` is
  `C:/Users/andre/Job_Apply_Agent/.git` from both checkouts (`Job_Apply_Agent` on
  `cost_perform_analysis`, `Job_Apply_Agent-ai-files` on `ai-files-fix`); hooks run from
  `<common>/hooks`. The main checkout's branch does not have this branch's files.
- **No binary files are tracked**: no .png/.jpg/.gif/.webp/.pdf/.db/.sqlite in `git ls-files`.
- **Git for Windows runs hooks through its own `sh`**; `python` on that PATH is currently the
  main checkout's `.venv` (3.11.9). Git is 2.45.2 (`--path-format=absolute` needs 2.31+).
- **Baseline: `python -m pytest` = 2104 passed** (2026-11-24, before any code change).

## Decisions

- **D1: hooks are installed into git's shared hooks folder, checker included.**
  `scripts/install_hooks.py` copies `hooks/pre-commit`, `hooks/commit-msg` and
  `scripts/check_private_data.py` into `git rev-parse --git-path hooks`. The sources are
  tracked and reviewed like code; the installed copies make the guard work on every branch
  and worktree at once, including branches that don't have these files yet. (A tracked
  `core.hooksPath` was rejected: a checkout on a branch without the folder silently runs no
  hook.) `install_hooks.py --check` reports a stale or missing install.
- **D2: fail closed.** No working Python, no list file, a git error, or an unreadable list all
  block the commit with a message saying how to fix it. An empty list (no entries yet) is
  allowed with a warning, so the file-type and list-file checks still run.
- **D3: commit messages are checked too** (`commit-msg` hook, same list; git's `#` comment
  lines skipped).
- **D4: Claude Code may not run `git commit --no-verify`** (deny rules in
  `.claude/settings.json`, Bash and PowerShell forms). Claude's own rules already forbid it;
  this is the enforced copy.
- **D5: the matched text is never printed.** A hit prints `file:line` and the list's line
  number only, so a private string never reaches a terminal log or Claude's context. A file
  NAME that matches an entry is reported without the name.
- **D6: only added lines are checked**, so removed lines and the already-accepted history
  don't trip it. Paths of added/renamed files are checked as well.
- **D7: the list lives at `<git common dir>/private-data.txt`.** Git refuses to track anything
  inside `.git/`, and every worktree reads the same file. Belt and braces: `.gitignore` ignores
  `private-data.txt` at any depth, and the checker blocks staging any file with that name.
  Claude is denied reading it (`.claude/settings.json`). The installer creates a comments-only
  template if it is missing and never overwrites an existing list.
- **D8: matching rules.** Case-insensitive; runs of whitespace count as one space. An entry
  with 6+ digits making up most of its characters (phone, ID numbers) is also matched with
  spaces, dashes, dots, slashes, brackets and `+` removed from both sides, so `0400 000 000`
  matches `0400000000`. Entries shorter than 4 characters are skipped with a warning (they
  would match everywhere).

## Constraints: what not to touch

- No app, extension, API or database code changes.
- Standard library only, no new dependencies, no network calls.
- No history rewrite; the accepted items in CLAUDE.md stay as they are.
- Tests and this plan use fake data only ("Liam Chen", "0400 000 000", @example.com). Tests
  never read the real list: every test uses a temporary repo and its own list.
- Claude never reads the real `private-data.txt`; the owner writes it.

## Steps

1. `scripts/check_private_data.py`: `--staged` (pre-commit) and `--message FILE` (commit-msg),
   `--list PATH` to override the default, exit 0 = pass, 1 = blocked, 2 = could not run.
2. `hooks/pre-commit`, `hooks/commit-msg`: POSIX sh shims; find a working Python (`python`,
   `py`, `python3`, version-checked), run the installed checker beside them, block if none.
3. `scripts/install_hooks.py`: install (LF line endings), refuse to overwrite a hook it didn't
   write unless `--force`, create the list template, `--check` for staleness.
4. `.gitignore` (`private-data.txt`), `.claude/settings.json` (deny reading the list, deny
   `--no-verify`), CLAUDE.md PUBLIC REPO (one bullet naming the guard).
5. `tests/test_check_private_data.py` (temporary git repos, fake lists, `GIT_*` env scrubbed).
6. Full suite; install on this machine; smoke-test a real commit in a scratch repo.

## Done when

- [x] Baseline: full `python -m pytest` count recorded BEFORE any code change (2104 passed).
- [x] Checker, hooks and installer in place (Steps 1-3).
- [x] New tests (39 in `tests/test_check_private_data.py`), each in a temporary git repo with a fake list: blocks a listed string on an
      added line; ignores the same string on a removed line; case and whitespace differences
      still match; a phone number matches with or without spaces; a staged .db and a staged
      .png are blocked; a staged `private-data.txt` is blocked; a matching file name is
      blocked without being printed; a clean diff passes; the output never contains the
      matched text; a missing list blocks; an empty list passes with a warning; the commit
      message is checked; the installer installs, writes the template, never overwrites a
      list, refuses a foreign hook, and `--check` spots a stale install; a real `git commit`
      is blocked / allowed by the installed hooks; git never stages `.git/private-data.txt`
      and `.gitignore` ignores `private-data.txt`.
- [x] The ENTIRE test suite passes: 2143 passed (2104 baseline + 39 new). No existing test
      changed: `git status` shows no existing file under tests/ modified.
- [x] CLAUDE.md PUBLIC REPO names the guard; `.claude/settings.json` denies reading the list
      and `--no-verify`; `.gitignore` ignores `private-data.txt`.
- [x] Installed on this machine (`C:/Users/andre/Job_Apply_Agent/.git/hooks`, shared by both
      worktrees); `install_hooks.py --check` passes; the template list was created.
- [x] Manual check (owner): fill in the real list, stage a scratch file holding one entry, see
      the commit blocked with only a line number shown, then unstage and delete the file.
      PASSED 2026-11-24: `addd.txt:1 matches the private list, line 12`, no text shown.

## Decision log

- 2026-11-24: Option A (local pre-commit hook with an owner-held list) chosen over a CI check
  (runs after the push, too late for a public repo) and gitleaks (knows key formats, not the
  owner's own data). Known keys and ids go on the list instead.
- 2026-11-24: the owner asked Claude to settle D1-D8 and build it in one go.
- 2026-11-24, built: git does NOT error on `git add .git/private-data.txt`; it exits 0 and
  silently stages nothing (also with `-f` and `-A`). The test checks that nothing is staged.
- 2026-11-24, built: Claude's deny rules verified live: a Bash command naming the list file
  and a Read of the list were both refused by the permission settings.
- Binary detection widened from a fixed extension list to "anything git treats as binary,
  plus database / image / document extensions" (a real letter could be a .docx).
