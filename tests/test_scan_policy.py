"""The Seek scan stays inside the access policy (CLAUDE.md, "SEEK ACCESS POLICY").

Written as a safety net for the main.py / sidebar.js split (docs/refactor-9-10-plan.md,
9.0), and kept: it checks mechanically what a moved block must never change.
- SCAN_DELAY_MS is defined once and is at least 5000 (>= 5 s between pages);
- SCAN_PAGES_CEILING is defined once and is 25, and the API's bound agrees (le=25);
- job links are collected only from the user's own active tab (one hop, never a second);
- the scan opens job pages in background tabs (active: false);
- the detail scraper is injected only into those background tabs.

It reads every extension/*.js as text, so it keeps working whichever file the scan code
lives in. No browser, no network, no AI call.
"""

from __future__ import annotations

import re
from pathlib import Path

from annotated_types import Le

from app.api.main import PreferencesUpdate

ROOT = Path(__file__).resolve().parent.parent
EXT = ROOT / "extension"


def _strip_comments(text: str) -> str:
    """Drop /* */ and // comments (not the // in "https://"), so a comment naming a
    function doesn't count as a use of it."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"(?<![:\\])//[^\n]*", "", text)


def _sources() -> dict[str, str]:
    return {
        p.name: _strip_comments(p.read_text(encoding="utf-8")) for p in sorted(EXT.glob("*.js"))
    }


def _find_all(pattern: str) -> list[tuple[str, re.Match]]:
    return [(name, m) for name, text in _sources().items() for m in re.finditer(pattern, text)]


def _call_args(text: str, open_paren: int) -> str:
    """The text between a call's '(' and its matching ')'."""
    depth = 0
    for i in range(open_paren, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[open_paren + 1:i]
    raise AssertionError("unbalanced call")


def test_scan_delay_is_defined_once_and_at_least_5s():
    found = _find_all(r"\bconst\s+SCAN_DELAY_MS\s*=\s*(\d+)\s*;")
    assert len(found) == 1, f"SCAN_DELAY_MS must be defined exactly once, found {len(found)}"
    assert int(found[0][1].group(1)) >= 5000


def test_scan_ceiling_is_defined_once_and_is_25():
    found = _find_all(r"\bconst\s+SCAN_PAGES_CEILING\s*=\s*(\d+)\s*;")
    assert len(found) == 1, f"SCAN_PAGES_CEILING must be defined exactly once, found {len(found)}"
    assert int(found[0][1].group(1)) == 25


def test_api_caps_scan_pages_at_25():
    field = PreferencesUpdate.model_fields["scan_max_pages"]
    caps = [m.le for m in field.metadata if isinstance(m, Le)]
    assert caps == [25]


def test_job_links_come_only_from_the_users_active_tab():
    # pageCollectJobLinks is injected once, into `tab`: the tab the user has open.
    injections = _find_all(r"injectFn\(\s*(\w+(?:\.\w+)*)\s*,\s*pageCollectJobLinks\b")
    assert [m.group(1) for _, m in injections] == ["tab.id"]
    name, _ = injections[0]
    text = _sources()[name]
    assert re.search(
        r"const\s+\[\s*tab\s*\]\s*=\s*await\s+chrome\.tabs\.query\(\s*\{\s*active:\s*true", text
    ), "`tab` must be the user's active tab (chrome.tabs.query({ active: true, ... }))"
    # And it is never handed to the browser any other way (no second, hidden injection).
    uses = _find_all(r"\bpageCollectJobLinks\b")
    assert len(uses) == 2, "pageCollectJobLinks: expected its definition plus one injection"


def test_scan_opens_job_pages_in_background_tabs():
    calls = []
    for name, text in _sources().items():
        for m in re.finditer(r"chrome\.tabs\.create\(", text):
            args = _call_args(text, m.end() - 1)
            if re.search(r"\burls\[", args):  # the scan's per-link open
                calls.append(args)
    assert calls, "the scan's chrome.tabs.create({ url: urls[i], ... }) was not found"
    for args in calls:
        assert re.search(r"\bactive:\s*false\b", args), f"scan tab must be active: false: {args}"


def test_detail_scraper_is_injected_only_into_scan_tabs():
    injections = _find_all(r"injectFn\(\s*(\w+(?:\.\w+)*)\s*,\s*pageScrapeDetail\b")
    assert injections, "the pageScrapeDetail injection was not found"
    assert {m.group(1) for _, m in injections} == {"bgTab.id"}
    uses = _find_all(r"\bpageScrapeDetail\b")
    assert len(uses) == 1 + len(injections), "pageScrapeDetail is referenced outside its injection"
