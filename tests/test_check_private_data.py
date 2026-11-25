"""The private-data guard blocks private data before a commit exists.

docs/private-data-guard-plan.md. Every test builds a throwaway git repo under tmp_path with
a FAKE list, scrubs GIT_* from the environment and points git at an empty global config, so
the real repo, the owner's real list and the owner's git settings are never touched. The
checker and installer run as subprocesses, the way the hooks run them. No network.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CHECKER = ROOT / "scripts" / "check_private_data.py"
INSTALLER = ROOT / "scripts" / "install_hooks.py"

# Fake private data only (CLAUDE.md PUBLIC REPO).
PHONE = "0400 000 000"
PHRASE = "Liam Chen's secret phrase"
STREET = "12 Example Street"
FAKE_LIST = f"""\
# fake list for tests
{PHONE}
{PHRASE}

{STREET}
ab
"""
# list line numbers of the entries above
PHONE_LINE, PHRASE_LINE, STREET_LINE, SHORT_LINE = 2, 3, 5, 6


@pytest.fixture
def env(tmp_path):
    e = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    empty = tmp_path / "empty.gitconfig"
    empty.write_text("", encoding="utf-8")
    e["GIT_CONFIG_GLOBAL"] = str(empty)
    e["GIT_CONFIG_NOSYSTEM"] = "1"
    # the hooks look for `python` on PATH: make it this interpreter
    e["PATH"] = os.path.dirname(sys.executable) + os.pathsep + e.get("PATH", "")
    return e


def git(repo: Path, env, *args, check=True):
    return subprocess.run(
        ["git", *args], cwd=repo, env=env, capture_output=True, text=True, check=check,
    )


@pytest.fixture
def repo(tmp_path, env):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, env, "init", "-q", "-b", "main")
    for key, value in [
        ("user.name", "Test Bot"), ("user.email", "bot@example.com"),
        ("commit.gpgsign", "false"), ("core.autocrlf", "false"),
    ]:
        git(r, env, "config", key, value)
    (r / "README.md").write_text("hello\n", encoding="utf-8")
    git(r, env, "add", "README.md")
    git(r, env, "commit", "-q", "-m", "init")
    return r


@pytest.fixture
def fake_list(tmp_path):
    p = tmp_path / "fake-list.txt"
    p.write_text(FAKE_LIST, encoding="utf-8")
    return p


def check(repo, env, *args):
    proc = subprocess.run(
        [sys.executable, str(CHECKER), *args], cwd=repo, env=env,
        capture_output=True, text=True, check=False,
    )
    return proc.returncode, proc.stdout + proc.stderr


def stage(repo, env, name, content):
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    git(repo, env, "add", "-f", name)


# ---------------------------------------------------------------- staged content

def test_clean_diff_passes(repo, env, fake_list):
    stage(repo, env, "notes.txt", "nothing private here\n")
    code, out = check(repo, env, "--staged", "--list", str(fake_list))
    assert code == 0, out


def test_listed_string_on_added_line_is_blocked(repo, env, fake_list):
    stage(repo, env, "tests/sample.py", "x = 1\nmsg = \"Liam Chen's secret phrase\"\n")
    code, out = check(repo, env, "--staged", "--list", str(fake_list))
    assert code == 1
    assert f"tests/sample.py:2 matches the private list, line {PHRASE_LINE}" in out


def test_removed_line_is_not_flagged(repo, env, fake_list):
    stage(repo, env, "old.txt", f"keep\n{PHRASE}\n")
    git(repo, env, "commit", "-q", "-m", "old")         # no hooks in this repo
    stage(repo, env, "old.txt", "keep\n")
    code, out = check(repo, env, "--staged", "--list", str(fake_list))
    assert code == 0, out


def test_line_numbers_follow_the_new_file(repo, env, fake_list):
    stage(repo, env, "a.txt", "one\ntwo\nthree\nfour\n")
    git(repo, env, "commit", "-q", "-m", "a")
    stage(repo, env, "a.txt", f"one\ntwo\nthree\nfour\nfive\n{STREET}\n")
    code, out = check(repo, env, "--staged", "--list", str(fake_list))
    assert code == 1
    assert f"a.txt:6 matches the private list, line {STREET_LINE}" in out


def test_case_and_whitespace_differences_still_match(repo, env, fake_list):
    stage(repo, env, "a.txt", "LIAM   chen's  SECRET\tphrase\n")
    code, out = check(repo, env, "--staged", "--list", str(fake_list))
    assert code == 1
    assert f"line {PHRASE_LINE}" in out


@pytest.mark.parametrize("written", ["0400000000", "0400-000-000", "(0400) 000.000", "0400 000 000"])
def test_phone_matches_with_or_without_separators(repo, env, fake_list, written):
    stage(repo, env, "contact.txt", f"call me on {written} please\n")
    code, out = check(repo, env, "--staged", "--list", str(fake_list))
    assert code == 1, written
    assert f"line {PHONE_LINE}" in out


def test_output_never_contains_the_matched_text(repo, env, fake_list):
    stage(repo, env, "a.txt", f"{PHRASE}\n{PHONE}\n{STREET}\n")
    code, out = check(repo, env, "--staged", "--list", str(fake_list))
    assert code == 1
    low = out.casefold()
    for secret in ("secret phrase", "0400", "example street"):
        assert secret not in low


def test_file_name_matching_an_entry_is_blocked_and_hidden(repo, env, fake_list):
    stage(repo, env, f"docs/{STREET}.txt", "fine\n")
    code, out = check(repo, env, "--staged", "--list", str(fake_list))
    assert code == 1
    assert f"a staged file NAME matches the private list, line {STREET_LINE}" in out
    assert "example street" not in out.casefold()


def test_commit_of_only_deletions_passes(repo, env, fake_list):
    git(repo, env, "rm", "-q", "README.md")
    code, out = check(repo, env, "--staged", "--list", str(fake_list))
    assert code == 0, out


# ---------------------------------------------------------------- file types

@pytest.mark.parametrize("name", ["app.db", "shot.png", "letter.docx", "cv.pdf", "empty.sqlite"])
def test_database_image_and_document_files_are_blocked(repo, env, fake_list, name):
    stage(repo, env, name, b"")          # even empty: the extension alone blocks it
    code, out = check(repo, env, "--staged", "--list", str(fake_list))
    assert code == 1
    assert f"{name}: binary file" in out


def test_any_binary_file_is_blocked(repo, env, fake_list):
    stage(repo, env, "blob.bin", b"\x00\x01\x02 binary \x00")
    code, out = check(repo, env, "--staged", "--list", str(fake_list))
    assert code == 1
    assert "blob.bin: binary file" in out


@pytest.mark.parametrize("name", ["private-data.txt", "sub/dir/Private-Data.txt"])
def test_the_private_list_itself_is_blocked(repo, env, fake_list, name):
    stage(repo, env, name, "# only a comment\n")
    code, out = check(repo, env, "--staged", "--list", str(fake_list))
    assert code == 1
    assert "the private list itself must never be committed" in out


# ---------------------------------------------------------------- the list

def test_missing_list_blocks(repo, env, tmp_path):
    stage(repo, env, "a.txt", "fine\n")
    code, out = check(repo, env, "--staged", "--list", str(tmp_path / "nope.txt"))
    assert code == 2
    assert "could not run" in out and "missing" in out


def test_default_list_is_in_the_git_common_dir(repo, env):
    stage(repo, env, "a.txt", f"{STREET}\n")
    code, out = check(repo, env, "--staged")                    # no list yet: fail closed
    assert code == 2
    (repo / ".git" / "private-data.txt").write_text(FAKE_LIST, encoding="utf-8")
    code, out = check(repo, env, "--staged")
    assert code == 1
    assert f"line {STREET_LINE}" in out


def test_default_list_is_shared_by_worktrees(repo, env, tmp_path):
    (repo / ".git" / "private-data.txt").write_text(FAKE_LIST, encoding="utf-8")
    wt = tmp_path / "wt"
    git(repo, env, "worktree", "add", "-q", "-b", "side", str(wt))
    stage(wt, env, "a.txt", f"{PHRASE}\n")
    code, out = check(wt, env, "--staged")
    assert code == 1
    assert f"line {PHRASE_LINE}" in out


def test_empty_list_passes_with_a_warning(repo, env, tmp_path):
    empty = tmp_path / "empty-list.txt"
    empty.write_text("# nothing yet\n\n", encoding="utf-8")
    stage(repo, env, "a.txt", "fine\n")
    code, out = check(repo, env, "--staged", "--list", str(empty))
    assert code == 0
    assert "has no entries yet" in out


def test_empty_list_still_blocks_binary_files(repo, env, tmp_path):
    empty = tmp_path / "empty-list.txt"
    empty.write_text("", encoding="utf-8")
    stage(repo, env, "shot.png", b"\x89PNG\x00")
    code, _ = check(repo, env, "--staged", "--list", str(empty))
    assert code == 1


def test_short_entry_is_skipped_with_a_warning(repo, env, fake_list):
    stage(repo, env, "a.txt", "about ab and abc\n")
    code, out = check(repo, env, "--staged", "--list", str(fake_list))
    assert code == 0
    assert f"list line {SHORT_LINE} is shorter than 4 characters" in out


# ---------------------------------------------------------------- commit message

def test_commit_message_is_checked(repo, env, fake_list, tmp_path):
    msg = tmp_path / "MSG"
    msg.write_text(f"Fix: thing\n\nAsk {STREET} for details\n", encoding="utf-8")
    code, out = check(repo, env, "--message", str(msg), "--list", str(fake_list))
    assert code == 1
    assert f"commit message line 3 matches the private list, line {STREET_LINE}" in out


def test_commit_message_comments_and_verbose_diff_are_skipped(repo, env, fake_list, tmp_path):
    msg = tmp_path / "MSG"
    msg.write_text(
        f"Fix: thing\n# {PHRASE}\n"
        "# ------------------------ >8 ------------------------\n"
        f"+{PHONE}\n",
        encoding="utf-8",
    )
    code, out = check(repo, env, "--message", str(msg), "--list", str(fake_list))
    assert code == 0, out


# ---------------------------------------------------------------- installer + real commits

def install(repo, env, *args, check=True):
    return subprocess.run(
        [sys.executable, str(INSTALLER), "--repo", str(repo), *args], env=env,
        capture_output=True, text=True, check=check,
    )


def test_installer_writes_hooks_with_lf_endings_and_a_template_list(repo, env):
    install(repo, env)
    hooks = repo / ".git" / "hooks"
    for name in ("pre-commit", "commit-msg", "check_private_data.py"):
        assert (hooks / name).is_file(), name
        assert b"\r\n" not in (hooks / name).read_bytes(), name
    template = (repo / ".git" / "private-data.txt").read_text(encoding="utf-8")
    entries = [ln for ln in template.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    assert entries == []                       # comments only: no data, no fake entries


def test_installer_never_overwrites_an_existing_list(repo, env):
    lst = repo / ".git" / "private-data.txt"
    lst.write_text(FAKE_LIST, encoding="utf-8")
    install(repo, env)
    assert lst.read_text(encoding="utf-8") == FAKE_LIST


def test_installer_refuses_a_foreign_hook_unless_forced(repo, env):
    foreign = repo / ".git" / "hooks" / "pre-commit"
    foreign.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
    proc = install(repo, env, check=False)
    assert proc.returncode != 0
    assert foreign.read_text(encoding="utf-8") == "#!/bin/sh\necho mine\n"
    install(repo, env, "--force")
    assert "private-data guard" in foreign.read_text(encoding="utf-8")


def test_installer_check_spots_a_stale_install(repo, env):
    assert install(repo, env, "--check", check=False).returncode == 1     # nothing installed
    install(repo, env)
    assert install(repo, env, "--check", check=False).returncode == 0
    checker = repo / ".git" / "hooks" / "check_private_data.py"
    checker.write_text(checker.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
    proc = install(repo, env, "--check", check=False)
    assert proc.returncode == 1 and "out of date" in proc.stderr


def test_real_commits_are_blocked_or_allowed_by_the_installed_hooks(repo, env):
    install(repo, env)
    (repo / ".git" / "private-data.txt").write_text(FAKE_LIST, encoding="utf-8")
    head = git(repo, env, "rev-parse", "HEAD").stdout

    stage(repo, env, "a.txt", f"{PHONE}\n")
    proc = git(repo, env, "commit", "-q", "-m", "leak", check=False)
    assert proc.returncode != 0
    assert "commit BLOCKED" in proc.stderr and "0400" not in proc.stderr
    assert git(repo, env, "rev-parse", "HEAD").stdout == head

    stage(repo, env, "a.txt", "nothing private\n")
    proc = git(repo, env, "commit", "-q", "-m", f"see {STREET}", check=False)
    assert proc.returncode != 0                                   # commit-msg hook
    assert git(repo, env, "rev-parse", "HEAD").stdout == head

    proc = git(repo, env, "commit", "-q", "-m", "clean", check=False)
    assert proc.returncode == 0, proc.stderr
    assert git(repo, env, "rev-parse", "HEAD").stdout != head


def test_hook_blocks_when_the_list_is_missing(repo, env):
    install(repo, env)
    (repo / ".git" / "private-data.txt").unlink()
    stage(repo, env, "a.txt", "fine\n")
    proc = git(repo, env, "commit", "-q", "-m", "x", check=False)
    assert proc.returncode != 0
    assert "could not run" in proc.stderr


# ---------------------------------------------------------------- can't reach GitHub

def test_git_never_stages_the_list_inside_dot_git(repo, env):
    # git skips paths inside .git/ silently (exit 0, nothing staged), even with -f / -A
    (repo / ".git" / "private-data.txt").write_text(FAKE_LIST, encoding="utf-8")
    git(repo, env, "add", "-f", ".git/private-data.txt", check=False)
    git(repo, env, "add", "-f", "-A")
    assert git(repo, env, "diff", "--cached", "--name-only").stdout == ""
    assert git(repo, env, "ls-files").stdout.split() == ["README.md"]


@pytest.mark.parametrize("path", ["private-data.txt", "docs/private-data.txt", "a/b/private-data.txt"])
def test_gitignore_ignores_the_list_name_anywhere(path):
    proc = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", path], cwd=ROOT, capture_output=True, check=False,
    )
    assert proc.returncode == 0, f"{path} is not ignored by .gitignore"


def test_claude_settings_deny_the_list_and_no_verify():
    deny = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))["permissions"]["deny"]
    assert "Read(**/private-data.txt)" in deny
    assert "Bash(git commit*--no-verify*)" in deny
    assert "PowerShell(git commit*--no-verify*)" in deny
