"""The extension's permissions stay as narrow as the features need.

The manifest has "<all_urls>" since 2026-10-14 for one reason only: Chrome refuses
chrome.tabs.captureVisibleTab without it (or activeTab, which needs a toolbar click), and
the Quick Apply "application sent" screenshot is automatic
(docs/centrelink-ease-apply-plan.md). These tests keep that grant from widening what the
extension does: content scripts still run on Seek only, the background worker's backend
relay still reaches only the local backend, the screenshot is only of the asking tab
while it is on screen, and nothing injects scripts outside the scan code.

Text-only checks: no browser, no network.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXT = ROOT / "extension"
MANIFEST = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
BACKGROUND = (EXT / "background.js").read_text(encoding="utf-8")
SEEK = {"https://au.seek.com/*", "https://www.seek.com.au/*"}


def test_permissions_are_the_known_set():
    assert set(MANIFEST["permissions"]) == {"activeTab", "scripting", "sidePanel", "storage"}
    hosts = set(MANIFEST["host_permissions"])
    local = {f"http://{h}:{p}/*" for h in ("localhost", "127.0.0.1") for p in (8000, 8001)}
    assert hosts == SEEK | local | {"<all_urls>"}, hosts


def test_content_scripts_run_on_seek_only():
    for block in MANIFEST["content_scripts"]:
        assert set(block["matches"]) == SEEK, block["matches"]
        assert "exclude_matches" not in block or block["exclude_matches"]


def test_all_urls_is_used_for_the_screenshot_only():
    # captureVisibleTab is called in exactly one place, and only after checking that the
    # tab asking is the active one.
    calls = re.findall(r"captureVisibleTab\(", BACKGROUND)
    assert len(calls) == 1
    handler = BACKGROUND[BACKGROUND.index("'CAPTURE_SCREENSHOT'"):]
    assert "tab.active" in handler and "chrome.tabs.get(sender.tab.id" in handler
    for path in EXT.glob("*.js"):
        if path.name != "background.js":
            assert "captureVisibleTab(" not in path.read_text(encoding="utf-8"), path.name


def test_backend_relay_still_reaches_only_the_local_backend():
    relay = BACKGROUND[BACKGROUND.index("'BACKEND_FETCH'"):BACKGROUND.index("'CAPTURE_SCREENSHOT'")]
    assert "['localhost', '127.0.0.1'].includes(u.hostname)" in relay
    assert "['8000', '8001'].includes(u.port)" in relay
    assert "u.protocol === 'http:'" in relay


def test_scripts_are_injected_only_into_seek_tabs_the_panel_opened():
    # chrome.scripting.executeScript is the one way "<all_urls>" could reach other sites'
    # pages. Two side-panel files use it, both on Seek tabs they opened themselves: the
    # scan (sidebar_scan.js injectFn; the Seek Access Policy tests cover it) and the Seek
    # profile import (sidebar_seek_import.js, one fixed Seek address). No other file.
    users = sorted(p.name for p in EXT.glob("*.js") if "executeScript(" in p.read_text(encoding="utf-8"))
    assert users == ["sidebar_scan.js", "sidebar_seek_import.js"], users
    seek_import = (EXT / "sidebar_seek_import.js").read_text(encoding="utf-8")
    assert len(re.findall(r"executeScript\(", seek_import)) == 1
    assert re.findall(r"chrome\.tabs\.create\(\{\s*url:\s*'([^']+)'", seek_import) == ["https://au.seek.com/profile/me"]
