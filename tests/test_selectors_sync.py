"""The extension's Seek selectors and the dormant scraper's copy must agree.

extension/selectors.js is the extension's single source (the content script and the
side panel both load it; the panel passes the values into injected functions as
`args`). app/scraper/selectors.py is the scraper's copy and can't import JS, so this
test fails when the two drift apart. No AI call and no network: both files are only
read as text (the Python one with `ast`, so app.scraper is never imported).
"""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXTENSION = ROOT / "extension"
JS_FILE = EXTENSION / "selectors.js"
PY_FILE = ROOT / "app" / "scraper" / "selectors.py"

# If the shared set shrinks below this, the test would be passing by matching nothing.
EXPECTED_SHARED = {
    "JOB_CARD", "CARD_TITLE_LINK", "CARD_COMPANY", "CARD_LOCATION", "CARD_WORK_TYPE",
    "CARD_SALARY", "DETAIL_DESCRIPTION", "DETAIL_TITLE", "DETAIL_COMPANY",
    "DETAIL_LOCATION", "DETAIL_WORK_TYPE", "DETAIL_APPLY_BUTTON",
}


def _js_selectors() -> dict[str, str]:
    text = JS_FILE.read_text(encoding="utf-8")
    return dict(re.findall(r"^\s*([A-Z_]+):\s*'([^']*)'", text, flags=re.MULTILINE))


def _py_selectors() -> dict[str, str]:
    tree = ast.parse(PY_FILE.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            out[node.targets[0].id] = node.value.value
    return out


def test_shared_selectors_are_found():
    shared = set(_js_selectors()) & set(_py_selectors())
    assert EXPECTED_SHARED <= shared, f"missing from the shared set: {EXPECTED_SHARED - shared}"


def test_js_and_python_selectors_agree():
    js, py = _js_selectors(), _py_selectors()
    differing = {k: (js[k], py[k]) for k in set(js) & set(py) if js[k] != py[k]}
    assert not differing, f"selectors.js vs selectors.py differ (js, py): {differing}"


def test_sidebar_has_no_hardcoded_detail_selectors():
    # pageScrapeDetail gets these from SELECTORS via executeScript `args`. Every
    # extension script is scanned, not just sidebar.js: the panel is split across
    # several sidebar_*.js files, and checking one file would pass while checking nothing.
    scripts = sorted(p for p in EXTENSION.glob("*.js") if p.name != JS_FILE.name)
    texts = {p.name: p.read_text(encoding="utf-8") for p in scripts}
    # Not vacuous: the file holding the scan's injected scraper is among those checked.
    assert any("function pageScrapeDetail(" in t for t in texts.values()), sorted(texts)
    for name, text in texts.items():
        assert "jobAdDetails" not in text, f"hardcoded detail selector in {name}"
        assert "job-detail-title" not in text, f"hardcoded detail selector in {name}"
