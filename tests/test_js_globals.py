"""The side panel's load-order contract (scripts/js_globals.py --check), run in CI.

The panel is several classic scripts sharing one global scope (extension/sidebar.html).
This fails on a top-level name declared twice, a name no panel script declares, a name
dashboard.js needs from a script that loads after it, or an injected Seek-tab function
using a panel global. Text only: no browser, no network.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import js_globals  # noqa: E402


def test_panel_scripts_keep_the_load_order_contract():
    scripts = js_globals.load()
    assert [s.name for s in scripts][-2:] == ["sidebar.js", "dashboard.js"]
    assert js_globals.problems(scripts) == []


def test_contract_is_not_vacuous():
    scripts = js_globals.load()
    by_name = {n: s.name for s in scripts for n in s.top}
    # dashboard.js's known needs (its header comment) must still be found as declarations.
    for name in ("PROFILE_ID", "mk", "tierOf", "scanBtn", "TRASH_SVG", "typingIn"):
        assert name in by_name, name
    for fn in js_globals.INJECTED:
        assert sum(fn in s.functions for s in scripts) == 1, fn
