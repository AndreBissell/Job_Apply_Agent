"""Scraper orchestration.

Ties Component 1 (search) and Component 2 (detail) together over the active
saved searches, applies the per-search cap, upserts results into ``job_listings``,
and returns a run summary.

Capping (the key behaviour)
---------------------------
Component 1 may surface hundreds of new listings on a fresh saved search. We only
process ``max_new_per_search`` detail pages per search per run. The remainder is
left untouched — because those listings are still absent from ``job_listings``,
Component 1 surfaces them again next run, so the backlog drains over consecutive
runs (e.g. 531 new -> 20/run -> caught up in ~27 runs, after which daily volume is
just genuinely-new listings).

``--limit`` (dev/test) is a *separate, smaller* cap; when both apply the smaller
wins. It exists so an end-to-end live test is a handful of requests, not hundreds.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import JobListing, SavedSearch
from app.scraper.browser import launch_browser
from app.scraper.detail import DEFAULT_DELAY_RANGE, scrape_details
from app.scraper.search import SOURCE, ScrapedListing, scrape_search

logger = logging.getLogger(__name__)

DEFAULT_MAX_NEW_PER_SEARCH = 20


@dataclass
class SearchResult:
    label: str
    found_new: int = 0
    processed: int = 0
    deferred: int = 0
    inserted: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class RunSummary:
    searches_run: int = 0
    per_search: list[SearchResult] = field(default_factory=list)

    @property
    def total_new(self) -> int:
        return sum(r.found_new for r in self.per_search)

    @property
    def total_processed(self) -> int:
        return sum(r.processed for r in self.per_search)

    @property
    def total_deferred(self) -> int:
        return sum(r.deferred for r in self.per_search)

    @property
    def total_inserted(self) -> int:
        return sum(r.inserted for r in self.per_search)

    def log(self) -> None:
        logger.info("=== Scrape summary ===")
        logger.info("Searches run: %d", self.searches_run)
        for r in self.per_search:
            logger.info(
                "  [%s] new=%d processed=%d deferred=%d inserted=%d errors=%d",
                r.label, r.found_new, r.processed, r.deferred, r.inserted,
                len(r.errors),
            )
            for err in r.errors:
                logger.info("      error: %s", err)
        logger.info(
            "Totals: new=%d processed=%d deferred=%d inserted=%d",
            self.total_new, self.total_processed, self.total_deferred,
            self.total_inserted,
        )


def _upsert_listing(session: Session, listing: ScrapedListing) -> bool:
    """Insert ``listing`` into job_listings if not already present. Returns inserted?

    No "update existing" path yet: unprocessed/deferred listings are simply retried
    on a future run rather than updated in place.
    """
    exists = session.scalar(
        select(JobListing.id).where(
            JobListing.source == SOURCE,
            JobListing.source_job_id == listing.source_job_id,
        )
    )
    if exists is not None:
        return False

    session.add(
        JobListing(
            source=SOURCE,
            source_job_id=listing.source_job_id,
            url=listing.url,
            title=listing.title,
            company=listing.company,
            location=listing.location,
            classification=listing.classification,
            subclassification=listing.subclassification,
            work_type=listing.work_type,
            salary=listing.salary,
            raw_description=listing.raw_description,
            # close_date / start_date intentionally left NULL (rarely present).
        )
    )
    return True


def run_daily_scrape(
    *,
    max_new_per_search: int = DEFAULT_MAX_NEW_PER_SEARCH,
    limit: int | None = None,
    headless: bool = True,
    delay_range: tuple[float, float] = DEFAULT_DELAY_RANGE,
    session: Session | None = None,
) -> RunSummary:
    """Run the full scrape over all active saved searches.

    Parameters
    ----------
    max_new_per_search :
        Production safeguard: at most this many detail pages per search per run.
    limit :
        Optional smaller dev/test cap. When set, the *effective* cap is
        ``min(max_new_per_search, limit)``.
    headless :
        Passed to the browser launcher.
    session :
        Optional existing session (mainly for tests). If omitted, one is created.
    """
    effective_cap = max_new_per_search
    if limit is not None:
        effective_cap = min(effective_cap, limit)
    logger.info(
        "Starting scrape: max_new_per_search=%d limit=%s -> effective cap=%d",
        max_new_per_search, limit, effective_cap,
    )

    owns_session = session is None
    session = session or SessionLocal()
    summary = RunSummary()

    try:
        searches = list(
            session.scalars(
                select(SavedSearch).where(SavedSearch.is_active.is_(True))
            )
        )
        if not searches:
            logger.info("No active saved searches - nothing to do.")
            return summary

        with launch_browser(headless=headless) as context:
            page = context.new_page()

            for ss in searches:
                summary.searches_run += 1
                label = ss.label or ss.keywords or f"search#{ss.id}"
                result = SearchResult(label=label)

                try:
                    new_listings = scrape_search(page, ss, session)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Search '%s': failed during listing scrape.", label)
                    result.errors.append(f"search failed: {exc!r}")
                    summary.per_search.append(result)
                    continue

                result.found_new = len(new_listings)
                to_process = new_listings[:effective_cap]
                result.deferred = len(new_listings) - len(to_process)
                logger.info(
                    "Search '%s': %d new, processing %d, deferring %d.",
                    label, result.found_new, len(to_process), result.deferred,
                )

                scrape_details(page, to_process, delay_range=delay_range)
                result.processed = len(to_process)

                for listing in to_process:
                    try:
                        if _upsert_listing(session, listing):
                            result.inserted += 1
                    except Exception as exc:  # noqa: BLE001
                        logger.exception(
                            "Job %s: failed to upsert.", listing.source_job_id
                        )
                        result.errors.append(
                            f"upsert {listing.source_job_id}: {exc!r}"
                        )

                session.commit()
                summary.per_search.append(result)

        return summary
    finally:
        summary.log()
        if owns_session:
            session.close()
