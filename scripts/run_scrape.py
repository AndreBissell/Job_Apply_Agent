"""CLI entrypoint for the Seek scraper.

    python scripts/run_scrape.py                 # production: cap 20/search
    python scripts/run_scrape.py --max-new 50    # raise the per-search cap
    python scripts/run_scrape.py --limit 5       # dev/test: a handful of requests
    python scripts/run_scrape.py --headed        # watch the browser

``--max-new`` overrides the per-search production cap (default 20).
``--limit`` is a separate, smaller dev/test cap. If both are set, the smaller wins.

Run ``scripts/explore_seek.py`` and reconcile ``app/scraper/selectors.py`` BEFORE
running this against live Seek.
"""

from __future__ import annotations

import argparse
import logging
import sys

sys.path.insert(0, ".")

from app.scraper.run import DEFAULT_MAX_NEW_PER_SEARCH, run_daily_scrape  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Seek scraper.")
    parser.add_argument(
        "--max-new",
        type=int,
        default=DEFAULT_MAX_NEW_PER_SEARCH,
        help=f"Per-search production cap (default {DEFAULT_MAX_NEW_PER_SEARCH}).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Smaller dev/test cap on detail pages per search. Smaller of "
        "--limit/--max-new wins. Use e.g. 5 for a live pipeline test.",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run with a visible browser window.",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="DEBUG-level logging."
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    summary = run_daily_scrape(
        max_new_per_search=args.max_new,
        limit=args.limit,
        headless=not args.headed,
    )

    # Non-zero exit if any per-search errors were recorded.
    had_errors = any(r.errors for r in summary.per_search)
    return 1 if had_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
