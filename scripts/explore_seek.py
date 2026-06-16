"""ONE-SHOT DEV TOOL — cache real Seek pages for offline selector development.

Run this ONCE to fetch a single search-results page and a single job-detail page,
saving their *rendered* HTML to ``dev_data/``. From then on, develop and test the
extraction logic against those cached files — NOT against live Seek. Only re-run
this (and only the page that changed) if the cached HTML proves insufficient.

    python scripts/explore_seek.py
    python scripts/explore_seek.py --keywords "data analyst" --location "Sydney NSW"
    python scripts/explore_seek.py --job-url https://www.seek.com.au/job/12345678

This is intentionally minimal and is NOT part of the app pipeline. ``dev_data/`` is
gitignored.

While doing this one fetch, manually confirm against the cached HTML:
  * The job-card container + per-field selectors (see app/scraper/selectors.py).
  * The URL param that sorts by listing date (assumed sortmode=ListedDate).
  * The description container on /job/{id}, and that the noise sections (employer
    questions, sign-in prompt, featured-jobs sidebar, safety notice) sit OUTSIDE it.
  * au.seek.com/robots.txt — note anything relevant to /jobs and /job/ paths.
Then correct app/scraper/selectors.py if any assumption was wrong.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, ".")

from app.scraper import selectors  # noqa: E402
from app.scraper.browser import launch_browser  # noqa: E402

DEV_DATA = Path("dev_data")


def _save(path: Path, html: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    print(f"  saved {path}  ({len(html):,} bytes)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keywords", default="software engineer")
    parser.add_argument("--location", default="Brisbane QLD")
    parser.add_argument(
        "--job-url",
        default=None,
        help="Specific /job/{id} URL to cache. If omitted, the first result on the "
        "search page is used.",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run with a visible browser window (useful to watch what loads).",
    )
    args = parser.parse_args()

    search_url = selectors.build_search_url(
        keywords=args.keywords, location=args.location, work_type=None
    )

    print("NOTE: this makes a small number of LIVE requests to Seek (one search "
          "page + one detail page). Run it sparingly.")
    print(f"Search URL: {search_url}")

    with launch_browser(headless=not args.headed) as context:
        page = context.new_page()

        # --- robots.txt (fetched THROUGH the proxied browser, never agent-side) --
        # Networking policy: all target-site traffic must go via the proxy, so we
        # read robots.txt with the same proxied browser rather than WebFetch.
        robots_url = f"{selectors.SEEK_BASE}/robots.txt"
        print(f"robots.txt: {robots_url}")
        try:
            page.goto(robots_url, wait_until="domcontentloaded")
            robots_text = page.inner_text("body")
            _save(DEV_DATA / "robots.txt", robots_text)
        except Exception as exc:  # noqa: BLE001
            print(f"  WARNING: could not fetch robots.txt: {exc!r}")

        # --- Search results page ------------------------------------------
        page.goto(search_url, wait_until="domcontentloaded")
        try:
            page.wait_for_selector(selectors.JOB_CARD, timeout=15_000)
        except Exception:
            print("  WARNING: job-card selector did not match — the cached HTML "
                  "will still be saved so you can inspect the real structure.")
        _save(DEV_DATA / "search_page.html", page.content())

        # --- Pick a detail URL --------------------------------------------
        job_url = args.job_url
        if job_url is None:
            link = page.query_selector(selectors.CARD_TITLE_LINK)
            if link is not None:
                href = link.get_attribute("href")
                if href:
                    job_url = href if href.startswith("http") else f"{selectors.SEEK_BASE}{href}"
        if job_url is None:
            print("  Could not determine a job detail URL from the search page; "
                  "pass --job-url explicitly to cache a detail page.")
            return 1

        # --- Job detail page ----------------------------------------------
        print(f"Detail URL: {job_url}")
        page.goto(job_url, wait_until="domcontentloaded")
        try:
            page.wait_for_selector(selectors.DETAIL_DESCRIPTION, timeout=15_000)
        except Exception:
            print("  WARNING: description selector did not match — saving anyway "
                  "for inspection.")
        _save(DEV_DATA / "job_detail.html", page.content())

    print("\nDone. Now inspect dev_data/*.html and reconcile app/scraper/selectors.py.")
    print("Also check https://www.seek.com.au/robots.txt before running the scraper.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
