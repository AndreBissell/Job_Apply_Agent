"""Human-in-the-loop Seek scrape (Cloudflare-aware).

Opens a VISIBLE browser THROUGH THE MANDATORY PROXY, navigates a Seek search, and
lets YOU solve any Cloudflare "Just a moment..." challenge by hand. When a
challenge appears the script beeps, raises the window, and waits for you to press
Enter; then it extracts listings using the SAME selectors/parse logic as the
automated scraper.

Because it pauses for your keypress and shows a real browser window, run it
YOURSELF in a terminal where you can see the window — not headless/CI. Windscribe
(the proxy/VPN) must be CONNECTED.

    python scripts/scrape_interactive.py --keywords "software engineer" --location "Brisbane QLD"
    python scripts/scrape_interactive.py --keywords "data analyst" --details 3
    python scripts/scrape_interactive.py --use-saved-searches --save-db

By default it PRINTS what it found and caches the rendered search HTML to
dev_data/ (so selectors can be verified) WITHOUT writing the database. Pass
--save-db to upsert results into job_listings.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, ".")

from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import SavedSearch  # noqa: E402
from app.scraper import selectors  # noqa: E402
from app.scraper.browser import launch_browser  # noqa: E402
from app.scraper.detail import scrape_details  # noqa: E402
from app.scraper.interactive import make_interactive_navigator  # noqa: E402
from app.scraper.run import _upsert_listing  # noqa: E402
from app.scraper.search import scrape_search  # noqa: E402

DEV_DATA = Path("dev_data")


def _cache_html(name: str, html: str) -> None:
    DEV_DATA.mkdir(parents=True, exist_ok=True)
    path = DEV_DATA / name
    path.write_text(html, encoding="utf-8")
    print(f"  cached {path}  ({len(html):,} bytes)")


def _resolve_searches(args, session) -> list:
    """Either the active saved searches, or a single ad-hoc search from the CLI."""
    if args.use_saved_searches:
        rows = list(
            session.scalars(select(SavedSearch).where(SavedSearch.is_active.is_(True)))
        )
        if not rows:
            print("No active saved searches in the DB — falling back to --keywords.")
        else:
            return rows
    # Ad-hoc: a lightweight stand-in with the attributes scrape_search reads.
    return [
        SimpleNamespace(
            label=args.keywords,
            keywords=args.keywords,
            location=args.location,
            work_type=args.work_type,
        )
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--keywords", default="software engineer")
    ap.add_argument("--location", default="Brisbane QLD")
    ap.add_argument("--work-type", default=None)
    ap.add_argument("--use-saved-searches", action="store_true",
                    help="Scrape the active rows in saved_searches instead of --keywords.")
    ap.add_argument("--max-pages", type=int, default=2,
                    help="Max search-results pages to walk per search (default 2 for testing).")
    ap.add_argument("--details", type=int, default=0,
                    help="Also open up to N detail pages per search to capture descriptions "
                         "(default 0 = list only). Each pauses for any challenge.")
    ap.add_argument("--save-db", action="store_true",
                    help="Upsert results into job_listings (default: print only).")
    args = ap.parse_args()

    navigate = make_interactive_navigator()
    session = SessionLocal()
    total_found = 0
    total_inserted = 0

    try:
        searches = _resolve_searches(args, session)

        with launch_browser(headless=False) as context:
            page = context.new_page()

            for ss in searches:
                label = ss.label or ss.keywords or "search"
                print(f"\n=== Search: {label} ===")

                listings = scrape_search(
                    page, ss, session, max_pages=args.max_pages, navigate=navigate
                )

                # Cache whatever page we ended on — invaluable for verifying/fixing
                # selectors against the REAL rendered DOM the first time through.
                try:
                    _cache_html("search_page.html", page.content())
                except Exception as exc:  # noqa: BLE001
                    print(f"  (could not cache search HTML: {exc!r})")

                total_found += len(listings)
                print(f"  Parsed {len(listings)} listing(s) with {selectors.JOB_CARD!r}.")
                if not listings:
                    print("  Nothing parsed. If you DID see results in the window, the "
                          "selectors are off — inspect dev_data/search_page.html and fix "
                          "app/scraper/selectors.py.")
                    continue

                for listing in listings[:20]:
                    print(f"    [{listing.source_job_id}] {listing.title} "
                          f"— {listing.company or '?'} — {listing.location or '?'}")

                if args.details > 0:
                    print(f"\n  Opening up to {args.details} detail page(s)...")
                    scrape_details(page, listings[:args.details], navigate=navigate)
                    for listing in listings[:args.details]:
                        chars = len(listing.raw_description or "")
                        print(f"    [{listing.source_job_id}] description: {chars} chars")

                if args.save_db:
                    inserted = 0
                    for listing in listings:
                        try:
                            if _upsert_listing(session, listing):
                                inserted += 1
                        except Exception as exc:  # noqa: BLE001
                            print(f"    upsert {listing.source_job_id} failed: {exc!r}")
                    session.commit()
                    total_inserted += inserted
                    print(f"  Upserted {inserted} new of {len(listings)} into job_listings.")

            print("\n" + "-" * 72)
            print(f"Total parsed: {total_found}"
                  + (f" | inserted: {total_inserted}" if args.save_db else " | (print-only, not saved)"))
            input("Press Enter to close the browser and exit... ")

        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())