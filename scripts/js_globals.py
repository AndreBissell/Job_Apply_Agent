"""Map the side panel's shared globals, and check the load-order contract.

    python scripts/js_globals.py           # print the map: each script's top-level names,
                                           # and which other scripts use them
    python scripts/js_globals.py --check   # exit 1 if the contract is broken

The side panel (extension/sidebar.html) loads several CLASSIC scripts that share one
global scope, so the only contract between them is their load order. This reads that
order from sidebar.html and scans each script as text (a small JavaScript lexer: comments,
strings, template literals and regex literals are skipped, so words inside them don't
count). No browser, no server, no network.

--check fails on:
  1. a top-level name declared in two panel scripts (a duplicate `const`/`let` is a
     SyntaxError that stops the whole later script from running);
  2. a name a panel script uses that no panel script declares and that isn't a browser
     built-in (a ReferenceError waiting to happen). For dashboard.js the name must be
     declared in a script loaded BEFORE it, because it runs last and uses the panel's
     names at load time;
  3. one of the functions injected into Seek tabs (pageCollectJobLinks, pageScrapeDetail,
     seekProfileExtract) using any name it doesn't declare itself: chrome.scripting
     serialises them, so a panel global would be a ReferenceError inside the Seek tab.

Name resolution is per file, not per scope: a name declared anywhere in a file (a local,
a parameter) counts as declared for the whole file. That can miss a bug (a local hides a
missing global); it can't invent one. The load e2e (tests/e2e/sidebar_load_e2e.py)
covers what this can't: load-time order and the temporal dead zone.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXTENSION = ROOT / "extension"
HTML = EXTENSION / "sidebar.html"
INJECTED = ("pageCollectJobLinks", "pageScrapeDetail", "seekProfileExtract")
LAST_SCRIPT = "dashboard.js"

KEYWORDS = set("""
    async await break case catch class const continue debugger default delete do else export
    extends false finally for function if import in instanceof let new null of return static
    super switch this throw true try typeof undefined var void while with yield get set
""".split())

# Browser and JavaScript globals the panel may use. A new built-in the code starts using
# goes here; a panel name never does.
BUILTINS = set("""
    AbortController Array Blob Boolean CSS CustomEvent Date DOMParser Error Event EventSource
    File FileReader FormData HTMLElement Infinity Intl JSON Map Math MutationObserver NaN Node
    NodeFilter Number Object Promise Proxy Reflect RegExp Set String Symbol TextEncoder
    TypeError URL URLSearchParams WeakMap WeakSet alert arguments atob btoa cancelAnimationFrame
    chrome clearInterval clearTimeout confirm console crypto customElements decodeURIComponent
    document encodeURIComponent fetch getComputedStyle globalThis history isFinite isNaN
    localStorage location navigator parseFloat parseInt performance prompt queueMicrotask
    requestAnimationFrame sessionStorage setInterval setTimeout structuredClone window
""".split())

_PUNCT = sorted("""
    >>>= ... === !== **= <<= >>= >>> &&= ||= ??= => == != <= >= && || ?? ?. ++ -- += -= *= /=
    %= &= |= ^= ** << >> { } ( ) [ ] ; , < > + - * / % & | ^ ! ~ ? : = . @ #
""".split(), key=len, reverse=True)
_IDENT = re.compile(r"[A-Za-z_$][\w$]*")
_NUMBER = re.compile(r"\d[\w.]*|\.\d[\w]*")
# After these a `/` starts a regex, not a division.
_REGEX_AFTER_WORDS = {"return", "typeof", "case", "do", "else", "in", "of", "new", "delete",
                      "void", "throw", "instanceof", "yield", "await"}


class Tok:
    __slots__ = ("kind", "value", "line", "depth")

    def __init__(self, kind: str, value: str, line: int) -> None:
        self.kind, self.value, self.line, self.depth = kind, value, line, 0

    def __repr__(self) -> str:
        return f"{self.kind}:{self.value}@{self.line}"


def lex(src: str) -> list[Tok]:
    """Tokens of a JavaScript file. Strings and regexes become one opaque token each; a
    template literal's text is skipped but the code inside each ${...} is tokenised."""
    toks: list[Tok] = []
    i, n, line = 0, len(src), 1
    braces: list[str] = []  # '{' or '${', so a '}' knows whether it resumes a template

    def regex_allowed() -> bool:
        if not toks:
            return True
        t = toks[-1]
        if t.kind in ("str", "num", "re"):
            return False
        if t.kind == "id":
            return t.value in _REGEX_AFTER_WORDS
        return t.value not in (")", "]", "}")

    def template(i: int) -> int:
        """Scan template text from i (just after ` or a closing }) to the end or to ${."""
        nonlocal line
        while i < n:
            c = src[i]
            if c == "\\":
                i += 2
                continue
            if c == "\n":
                line += 1
            if c == "`":
                toks.append(Tok("str", "`", line))
                return i + 1
            if src.startswith("${", i):
                toks.append(Tok("p", "${", line))
                braces.append("${")
                return i + 2
            i += 1
        raise SyntaxError("unterminated template literal")

    while i < n:
        c = src[i]
        if c == "\n":
            line += 1
            i += 1
        elif c.isspace():
            i += 1
        elif src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            line += src.count("\n", i, j)
            i = j + 2
        elif c in "'\"":
            j = i + 1
            while src[j] != c:
                j += 2 if src[j] == "\\" else 1
            toks.append(Tok("str", c, line))
            i = j + 1
        elif c == "`":
            i = template(i + 1)
        elif c == "}" and braces and braces[-1] == "${":
            braces.pop()
            toks.append(Tok("p", "}", line))
            i = template(i + 1)
        elif c == "/" and regex_allowed():
            j, in_class = i + 1, False
            while True:
                d = src[j]
                if d == "\\":
                    j += 2
                    continue
                if d == "[":
                    in_class = True
                elif d == "]":
                    in_class = False
                elif d == "/" and not in_class:
                    break
                elif d == "\n":
                    raise SyntaxError(f"line {line}: unterminated regex")
                j += 1
            j += 1
            while j < n and (src[j].isalnum()):
                j += 1
            toks.append(Tok("re", "/", line))
            i = j
        elif m := _IDENT.match(src, i):
            toks.append(Tok("id", m.group(), line))
            i = m.end()
        elif m := _NUMBER.match(src, i):
            toks.append(Tok("num", m.group(), line))
            i = m.end()
        else:
            for p in _PUNCT:
                if src.startswith(p, i):
                    if p == "{":
                        braces.append("{")
                    elif p == "}":
                        braces.pop()
                    toks.append(Tok("p", p, line))
                    i += len(p)
                    break
            else:
                raise SyntaxError(f"line {line}: unexpected {c!r}")
    depth = 0
    for t in toks:
        if t.kind == "p" and t.value in (")", "]", "}"):
            depth -= 1
        t.depth = depth
        if t.kind == "p" and t.value in ("(", "[", "{", "${"):
            depth += 1
    if depth != 0:
        raise SyntaxError(f"unbalanced brackets (depth {depth} at end)")
    return toks


def _match(toks: list[Tok], i: int) -> int:
    """Index of the bracket closing the one at i."""
    d = toks[i].depth
    for j in range(i + 1, len(toks)):
        if toks[j].depth == d and toks[j].value in (")", "]", "}"):
            return j
    raise SyntaxError(f"no closing bracket for {toks[i]}")


def _open_of(toks: list[Tok], j: int) -> int:
    """Index of the bracket opening the one closing at j."""
    d = toks[j].depth
    for i in range(j - 1, -1, -1):
        if toks[i].depth == d and toks[i].value in ("(", "[", "{", "${"):
            return i
    raise SyntaxError(f"no opening bracket for {toks[j]}")


def _skip_expr(toks: list[Tok], i: int, end: int) -> int:
    """From i, skip a default-value expression up to the next ',' at its depth, or end."""
    d = toks[i].depth
    while i < end and not (toks[i].depth == d and toks[i].value in (",", ")", "]", "}")):
        i += 1
    return i


def _pattern(toks: list[Tok], i: int, end: int, names: set[str], skipped: set[int]) -> int:
    """Parse one binding pattern at i (ident, {..} or [..]), adding the bound names.
    Default-value expressions are left as uses. Returns the index after the pattern."""
    t = toks[i]
    if t.value == "...":
        return _pattern(toks, i + 1, end, names, skipped)
    if t.kind == "id":
        names.add(t.value)
        skipped.add(i)
        return i + 1
    if t.value in ("{", "["):
        close = _match(toks, i)
        j = i + 1
        while j < close:
            if toks[j].value == ",":
                j += 1
                continue
            if t.value == "{" and toks[j].kind == "id" and toks[j + 1].value == ":":
                skipped.add(j)  # a key, not a binding
                j = _pattern(toks, j + 2, close, names, skipped)
            elif t.value == "{" and toks[j].value == "[":  # computed key: an expression
                j = _match(toks, j) + 2
                j = _pattern(toks, j, close, names, skipped)
            else:
                j = _pattern(toks, j, close, names, skipped)
            if j < close and toks[j].value == "=":
                j = _skip_expr(toks, j + 1, close)
        return close + 1
    return i + 1


def _param_list(toks: list[Tok], open_i: int, names: set[str], skipped: set[int]) -> None:
    close = _match(toks, open_i)
    j = open_i + 1
    while j < close:
        if toks[j].value == ",":
            j += 1
            continue
        j = _pattern(toks, j, close, names, skipped)
        if j < close and toks[j].value == "=":
            j = _skip_expr(toks, j + 1, close)


class Script:
    def __init__(self, path: Path) -> None:
        self.path, self.name = path, path.name
        self.toks = lex(path.read_text(encoding="utf-8-sig"))
        self.top: dict[str, int] = {}          # top-level name -> line
        self.top_dupes: list[str] = []
        self.declared: set[str] = set()        # every name bound anywhere in the file
        self.functions: dict[str, tuple[int, int]] = {}  # top-level function -> token span
        self._skip: set[int] = set()           # token indexes that are bindings or keys
        self._scan()
        self.free = self._free(0, len(self.toks))

    def _top(self, name: str, line: int) -> None:
        if name in self.top:
            self.top_dupes.append(name)
        self.top[name] = line

    def _scan(self) -> None:
        toks, skip = self.toks, self._skip
        for i, t in enumerate(toks):
            if t.kind != "id":
                continue
            prev = toks[i - 1].value if i else ""
            if t.value in ("function", "class"):
                k = i + 1
                if k < len(toks) and toks[k].value == "*":
                    k += 1
                if toks[k].kind == "id" and toks[k].value not in ("extends",):
                    self.declared.add(toks[k].value)
                    skip.add(k)
                    if t.depth == 0 and prev != "=":
                        self._top(toks[k].value, toks[k].line)
                    k += 1
                if t.value == "function" and toks[k].value == "(":
                    _param_list(toks, k, self.declared, skip)
                    if t.depth == 0 and toks[k - 1].kind == "id":
                        body = _match(toks, _match(toks, k) + 1)
                        self.functions[toks[k - 1].value] = (i, body)
            elif t.value in ("const", "let", "var") and prev != ".":
                j = i + 1
                while True:
                    names: set[str] = set()
                    j = _pattern(toks, j, len(toks), names, skip)
                    self.declared |= names
                    if t.depth == 0:
                        for nm in names:
                            self._top(nm, t.line)
                    if j < len(toks) and toks[j].value == "=":
                        j = self._skip_initializer(j + 1, t.depth)
                    if j < len(toks) and toks[j].value == "," and toks[j].depth == t.depth:
                        j += 1
                        continue
                    break
            elif t.value == "catch" and toks[i + 1].value == "(":
                _param_list(toks, i + 1, self.declared, skip)
        # Arrow functions: `x => ...` and `(a, {b}) => ...`
        for i, t in enumerate(toks):
            if t.value == "=>" and t.kind == "p":
                before = toks[i - 1]
                if before.kind == "id":
                    self.declared.add(before.value)
                    skip.add(i - 1)
                elif before.value == ")":
                    _param_list(toks, _open_of(toks, i - 1), self.declared, skip)
        # Object-literal keys and method shorthand: `{ key: v }`, `{ m() {...} }`
        for i, t in enumerate(toks):
            if t.kind != "id" or i == 0 or i + 1 >= len(toks):
                continue
            prev, nxt = toks[i - 1].value, toks[i + 1].value
            if prev in ("{", ",") and nxt == ":" and self._in_object(i):
                skip.add(i)
            elif prev in ("{", ",", "}") and nxt == "(" and self._in_object(i):
                close = _match(toks, i + 1)
                if close + 1 < len(toks) and toks[close + 1].value == "{":
                    skip.add(i)
                    _param_list(toks, i + 1, self.declared, skip)

    def _in_object(self, i: int) -> bool:
        """Is token i directly inside a {...} that is an object literal (not a block)?"""
        d = self.toks[i].depth
        for j in range(i - 1, -1, -1):
            if self.toks[j].depth == d - 1 and self.toks[j].value in ("{", "(", "[", "${"):
                if self.toks[j].value != "{":
                    return False
                before = self.toks[j - 1] if j else None
                if before is None:
                    return False
                if before.kind == "p" and before.value in (")", "=>"):
                    return False  # a function body or arrow body block
                if before.kind == "id" and before.value in ("else", "try", "finally", "do"):
                    return False
                return True
        return False

    def _skip_initializer(self, j: int, depth: int) -> int:
        toks = self.toks
        while j < len(toks):
            t = toks[j]
            if t.depth == depth and t.value in (",", ";"):
                return j
            if t.depth < depth:
                return j
            if (t.depth == depth and t.kind == "id" and t.value in ("const", "let", "var", "function")
                    and toks[j - 1].line < t.line):
                return j
            j += 1
        return j

    def _free(self, start: int, end: int, declared: set[str] | None = None) -> dict[str, int]:
        """Names used in toks[start:end] that aren't declared (file-wide by default)."""
        declared = self.declared if declared is None else declared
        out: dict[str, int] = {}
        toks = self.toks
        for i in range(start, end):
            t = toks[i]
            if t.kind != "id" or i in self._skip or t.value in KEYWORDS:
                continue
            if i and toks[i - 1].value in (".", "?.", "#"):
                continue
            if i and toks[i - 1].value == "case" and toks[i + 1].value == ":":
                continue
            if t.value in declared:
                continue
            out.setdefault(t.value, t.line)
        return out

    def injected_free(self, fn: str) -> dict[str, int]:
        """Names fn uses that it doesn't declare inside itself (built-ins excluded)."""
        a, b = self.functions[fn]
        inner: set[str] = set()
        for name in self.declared:
            # A name counts as declared inside fn only if one of its binding tokens is there.
            if any(a <= k <= b and self.toks[k].value == name for k in self._skip):
                inner.add(name)
        return {k: v for k, v in self._free(a, b + 1, inner).items() if k not in BUILTINS}


def panel_scripts() -> list[str]:
    html = HTML.read_text(encoding="utf-8")
    return re.findall(r'<script\s+src="([^"]+)"\s*>\s*</script>', html)


def load() -> list[Script]:
    return [Script(EXTENSION / name) for name in panel_scripts()]


def problems(scripts: list[Script]) -> list[str]:
    out: list[str] = []
    order = [s.name for s in scripts]
    if "sidebar.js" not in order or order[-1] != LAST_SCRIPT:
        out.append(f"sidebar.html script order looks wrong: {order}")
    owner: dict[str, str] = {}
    for s in scripts:
        for name in s.top_dupes:
            out.append(f"{s.name}: top-level {name!r} declared twice")
        for name in s.top:
            if name in owner:
                out.append(f"top-level {name!r} declared in both {owner[name]} and {s.name}")
            owner.setdefault(name, s.name)
    for idx, s in enumerate(scripts):
        before = {n for e in scripts[: idx + 1] for n in e.top}
        for name, line in sorted(s.free.items()):
            if name in BUILTINS:
                continue
            if name not in owner:
                out.append(f"{s.name}:{line}: {name!r} is declared by no panel script")
            elif s.name == LAST_SCRIPT and name not in before:
                out.append(f"{s.name}:{line}: {name!r} comes from {owner[name]}, which loads after it")
    found = {fn: [s for s in scripts if fn in s.functions] for fn in INJECTED}
    for fn, where in found.items():
        if len(where) != 1:
            out.append(f"injected function {fn} found in {len(where)} scripts (expected 1)")
            continue
        for name, line in sorted(where[0].injected_free(fn).items()):
            note = f"a panel global from {owner[name]}" if name in owner else "not declared inside it"
            out.append(f"{where[0].name}:{line}: injected {fn} uses {name!r} ({note})")
    return out


def report(scripts: list[Script]) -> str:
    owner = {n: s.name for s in scripts for n in s.top}
    lines = ["# Side-panel scripts, in load order (sidebar.html)", ""]
    for s in scripts:
        lines.append(f"## {s.name} ({len(s.top)} top-level names)")
        lines.append(", ".join(s.top) or "(none)")
        lines.append("")
    lines += ["# Names used outside the script that declares them", "",
              "| Name | Declared in (line) | Used by |", "|---|---|---|"]
    rows = []
    for s in scripts:
        for name, line in s.top.items():
            users = [o.name for o in scripts if o is not s and name in o.free]
            if users:
                rows.append(f"| `{name}` | {s.name}:{line} | {', '.join(users)} |")
    lines += rows
    lines += ["", "# Injected functions (must use only their own names and built-ins)", ""]
    for fn in INJECTED:
        where = [s for s in scripts if fn in s.functions]
        lines.append(f"- `{fn}`: {', '.join(s.name for s in where) or 'NOT FOUND'}")
    unknown = sorted({n for s in scripts for n in s.free if n not in owner and n not in BUILTINS})
    if unknown:
        lines += ["", f"Unresolved names: {', '.join(unknown)}"]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if the load-order contract is broken")
    args = ap.parse_args()
    scripts = load()
    if not args.check:
        print(report(scripts))
        return 0
    found = problems(scripts)
    for p in found:
        print("FAIL", p)
    names = sum(len(s.top) for s in scripts)
    print(f"{len(scripts)} panel scripts, {names} top-level names: "
          + ("OK" if not found else f"{len(found)} problem(s)"))
    return 1 if found else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
