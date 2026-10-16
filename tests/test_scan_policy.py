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

from app.api.profile import PreferencesUpdate

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


def test_job_links_come_only_from_a_page_the_user_opened():
    # pageCollectJobLinks is injected once, into `tab`: either the tab the user has in front
    # of them (the header's Scan Page), or the search tab their Scan more click opened, read by
    # its id (`tabId`), so they can move to another tab while it loads (user, 2026-10-15).
    injections = _find_all(r"injectFn\(\s*(\w+(?:\.\w+)*)\s*,\s*pageCollectJobLinks\b")
    assert [m.group(1) for _, m in injections] == ["tab.id"]
    name, _ = injections[0]
    text = _sources()[name]
    assert re.search(
        r"\[\s*tab\s*\]\s*=\s*await\s+chrome\.tabs\.query\(\s*\{\s*active:\s*true", text
    ), "without a tabId, `tab` must be the user's active tab (chrome.tabs.query({ active: true, ... }))"
    assert re.search(r"\btab\s*=\s*await\s+chrome\.tabs\.get\(\s*tabId\s*\)", text)
    # And it is never handed to the browser any other way (no second, hidden injection).
    uses = _find_all(r"\bpageCollectJobLinks\b")
    assert len(uses) == 2, "pageCollectJobLinks: expected its definition plus one injection"
    # `tabId` only ever comes from Scan more's own new search tab: every other call passes none.
    calls = [m.group(1).strip() for _, m in _find_all(r"(?<!function )\bscanPage\(([^)]*)\)")]
    assert sorted(calls) == ["", "{ tabId: tab.id, onStatus: say }"], calls


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


def test_scan_more_opens_one_search_per_click():
    """The Overview's Scan more buttons (CLAUDE.md, Seek access policy; D7/D8 of
    docs/overview-applied-rework-plan.md as the user updated them 2026-10-15): one per suggested
    search (the top 3). A click opens THAT ONE Seek search in a foreground tab (active: true:
    the user clicked and sees it, so it is the page the user opened), then scans that tab by its
    id with the same scanPage. It never loops over the suggestions or moves on by itself."""
    text = _sources()["dashboard.js"]
    body = text[text.index("async function scanMore("):text.index("function setScanMoreButtons(")]
    calls = [_call_args(body, m.end() - 1) for m in re.finditer(r"chrome\.tabs\.create\(", body)]
    assert len(calls) == 1, "Scan more opens exactly one tab"
    assert re.search(r"\burl:\s*seekSearchUrl\(\s*phrase\s*,\s*where\s*\)", calls[0]), calls[0]
    assert re.search(r"\bactive:\s*true\b", calls[0]), f"the search tab must be in the foreground: {calls[0]}"
    assert re.search(r"scanPage\(\s*\{\s*tabId:\s*tab\.id\b", body), "it scans the tab it opened"
    loop = r"\bfor\s*\(|\bwhile\s*\(|\.forEach\(|\.map\("
    assert not re.search(loop, body), "no loop: one search per click"
    # It runs only from a row's own click, with that row's search; at most 3 rows; and
    # dashboard.js builds a Seek search nowhere else.
    assert len(re.findall(r"\bscanMore\(", text)) == 2, "scanMore: its definition plus one click handler"
    assert re.search(r"addEventListener\(\s*'click',\s*\(\)\s*=>\s*scanMore\(\s*phrase\s*,\s*where\s*\)\s*\)", text)
    pick = text[text.index("function renderSuggestions("):text.index("function renderSearchRow(")]
    assert ".slice(0, 3)" in pick and "scanMore(" not in pick and "chrome.tabs" not in pick
    assert len(re.findall(r"\bseekSearchUrl\(", text)) == 1


# ---------------------------------------------------------------------------
# Availability re-checks (CLAUDE.md, "Availability re-checks"): the side panel re-visits stored
# listings in the background. Same stance as the scan: paced, capped, one page at a time, one hop.
# ---------------------------------------------------------------------------
def _const(name: str) -> int:
    found = _find_all(rf"\bconst\s+{name}\s*=\s*(\d+)\s*;")
    assert len(found) == 1, f"{name} must be defined exactly once, found {len(found)}"
    return int(found[0][1].group(1))


def test_check_delay_is_at_least_the_scan_delay():
    assert _const("CHECK_DELAY_MS") >= _const("SCAN_DELAY_MS") >= 5000


def test_check_session_cap_stays_a_trickle():
    assert 1 <= _const("CHECK_SESSION_CAP") <= 10


def test_backend_daily_cap_is_bounded():
    from app import availability

    assert 1 <= availability.DAILY_CAP <= 30
    assert availability.MISSES_TO_EXPIRE >= 2  # one miss is never enough to flag a job


def test_checks_open_only_background_tabs_for_stored_listings():
    text = _sources()["sidebar_availability.js"]
    calls = [_call_args(text, m.end() - 1) for m in re.finditer(r"chrome\.tabs\.create\(", text)]
    assert len(calls) == 1, "the checker opens its one page in one place"
    assert re.search(r"\bactive:\s*false\b", calls[0]), calls[0]
    assert re.search(r"\burl\b", calls[0])
    # The URL is rebuilt from the listing's own id on Seek's own host (checkUrlFor), never a link
    # read off a page.
    assert "checkUrlFor(job)" in text and "CHECK_HOSTS.includes(u.hostname)" in text


def test_checks_never_read_links_off_a_page():
    """One hop only: the checker injects a function that reads the description and title, and
    nothing else, so no link on a checked page can ever be followed (no second hop)."""
    text = _sources()["sidebar_availability.js"]
    injections = re.findall(r"injectFn\(\s*(\w+(?:\.\w+)*)\s*,\s*(\w+)", text)
    assert injections == [("tab.id", "pageCheckListing")]
    body = text[text.index("async function pageCheckListing"):text.index("function classifyCheck")]
    assert "href" not in body and "querySelectorAll" not in body and "pageCollectJobLinks" not in text


def test_checks_wait_their_turn_behind_the_scan_and_the_shared_pace():
    text = _sources()["sidebar_availability.js"]
    run = text[text.index("async function runAvailabilitySession"):text.index("function armAvailabilityChecks")]
    assert "scanning" in run
    assert run.index("seekPace(CHECK_DELAY_MS)") < run.index("checkOne(job)")
    scan = _sources()["sidebar_scan.js"]
    assert "cancelAvailabilityCheck()" in scan and "seekPace(SCAN_DELAY_MS)" in scan


def test_background_keeps_scan_tabs_across_a_service_worker_restart():
    # Raw text: _strip_comments would read the "/*" in background.js's host patterns as a comment.
    text = (EXT / "background.js").read_text(encoding="utf-8")
    assert "chrome.storage.session" in text and text.count("scanTabsReady.then") >= 4
