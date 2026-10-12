"""No new silent catch-alls in app/ (ARCHITECTURE 5.4 #11).

Every `except Exception` outside the dormant app/scraper/ must either end in a bare
`raise` (nothing is hidden) or carry `# noqa: BLE001 — <reason>` on its `except` line,
saying why swallowing it is deliberate. This is the pattern that once hid a failed
score as a permanent 0 (5.4 #8). Reads source text with `ast`; no AI, no network.
"""

import ast
import re
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"
REASONED_NOQA = re.compile(r"noqa:\s*BLE001\s*[—-]+\s*\S")


def _catches_exception(handler: ast.ExceptHandler) -> bool:
    t = handler.type
    if isinstance(t, ast.Name):
        return t.id == "Exception"
    if isinstance(t, ast.Tuple):
        return any(isinstance(e, ast.Name) and e.id == "Exception" for e in t.elts)
    return False


def _ends_in_bare_raise(handler: ast.ExceptHandler) -> bool:
    last = handler.body[-1]
    return isinstance(last, ast.Raise) and last.exc is None


def test_every_broad_except_re_raises_or_says_why():
    offenders = []
    for path in sorted(APP.rglob("*.py")):
        if "scraper" in path.relative_to(APP).parts:
            continue
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        for node in ast.walk(ast.parse(source)):
            if not (isinstance(node, ast.ExceptHandler) and _catches_exception(node)):
                continue
            if _ends_in_bare_raise(node) or REASONED_NOQA.search(lines[node.lineno - 1]):
                continue
            offenders.append(f"{path.relative_to(APP.parent)}:{node.lineno}")
    assert not offenders, (
        "except Exception that neither ends in a bare `raise` nor has "
        "`# noqa: BLE001 — <reason>`: " + ", ".join(offenders)
    )
