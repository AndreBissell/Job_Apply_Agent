"""Print the API's flattened route table, one sorted line per route (refactor safety net).

    python scripts/dump_routes.py                                   # print it
    python scripts/dump_routes.py > tests/fixtures/route_table.txt  # refresh the fixture

Written for the app/api/main.py split (docs/refactor-9-10-plan.md, step 9.0).
tests/test_route_table.py compares this output with tests/fixtures/route_table.txt,
so moving an endpoint into a router must leave every line unchanged.

Line format:  METHODS path | endpoint function name | response model | dependencies
              MOUNT path
- The endpoint's MODULE is left out on purpose, so a pure move doesn't change its line.
- Dependencies are written as module.qualname, so swapping one get_db for another shows.

Only imports the app: no server, no database query, no LLM call, no network.
"""

from __future__ import annotations

import re
import sys

sys.path.insert(0, ".")

from starlette.routing import Mount  # noqa: E402

# FastAPI 0.137 includes routers lazily: app.routes holds _IncludedRouter wrappers,
# and effective_candidates() (a PRIVATE API) yields the routes inside them. If a
# FastAPI upgrade removes it, this import fails loudly and the test says why.
try:
    from fastapi.routing import _IncludedRouter  # noqa: E402
except ImportError as exc:  # pragma: no cover - only after a FastAPI upgrade
    raise ImportError(
        "fastapi.routing._IncludedRouter is gone (FastAPI upgrade?). Update flatten() in "
        "scripts/dump_routes.py to the new way of listing included routes."
    ) from exc


def flatten(routes) -> list:
    """Every route the app answers, in matching order, with routers expanded."""
    out: list = []
    for route in routes:
        if isinstance(route, _IncludedRouter):
            out.extend(flatten(route.effective_candidates()))
        else:
            out.append(route)
    return out


def _type_name(tp) -> str:
    """A response model without module paths (a moved model keeps its line)."""
    if tp is None:
        return "-"
    if isinstance(tp, type) and not getattr(tp, "__args__", None):
        return tp.__qualname__
    return re.sub(r"\b(?:\w+\.)+(?=\w)", "", repr(tp))


def _dependencies(dependant) -> list[str]:
    """module.qualname of every dependency, depth first."""
    names: list[str] = []
    for dep in dependant.dependencies:
        call = dep.call
        names.append(f"{getattr(call, '__module__', '?')}.{getattr(call, '__qualname__', repr(call))}")
        names.extend(_dependencies(dep))
    return names


def route_lines(app) -> list[str]:
    lines: list[str] = []
    for route in flatten(app.routes):
        if isinstance(route, Mount):
            lines.append(f"MOUNT {route.path}")
        elif hasattr(route, "dependant"):  # APIRoute, or a router's route context
            methods = ",".join(sorted(route.methods))
            deps = ", ".join(_dependencies(route.dependant)) or "-"
            lines.append(
                f"{methods} {route.path} | {route.endpoint.__name__} | "
                f"{_type_name(route.response_model)} | {deps}"
            )
        else:  # the docs routes (/openapi.json, /docs, ...): plain Starlette routes
            methods = ",".join(sorted(getattr(route, "methods", None) or [])) or "-"
            name = getattr(getattr(route, "endpoint", None), "__name__", "-")
            lines.append(f"{methods} {route.path} | {name} | - | -")
    return sorted(lines)


def main() -> None:
    from app.api.main import app

    print("\n".join(route_lines(app)))


if __name__ == "__main__":
    main()
