"""Component 2: Seek job-detail scraper.

For each new listing from Component 1, visit its ``/job/{id}`` page and capture
the full ad body as ``raw_description``. The description container
(``selectors.DETAIL_DESCRIPTION``) is scoped to the ad text itself, so taking its
inner text naturally excludes the page's noise sections (employer questions,
sign-in prompt, featured-jobs sidebar, safety/bank-details notice).

This is where the bulk of request volume lives, so a randomised delay is inserted
*between* detail visits.
"""

from __future__ import annotations

import logging
import random
import time

from app.scraper import selectors
from app.scraper.search import ScrapedListing

logger = logging.getLogger(__name__)

# Randomised politeness delay between detail-page visits (seconds).
DEFAULT_DELAY_RANGE = (2.0, 5.0)


def extract_description(page) -> str | None:
    """Return the cleaned ad body text from a loaded detail page, or ``None``.

    Any selectors listed in ``DETAIL_NOISE_WITHIN`` are removed from the container
    before reading text (defence-in-depth — normally the container is already
    clean, so this is a no-op).
    """
    container = page.query_selector(selectors.DETAIL_DESCRIPTION)
    if container is None:
        logger.warning(
            "Description container not found (selector %s).",
            selectors.DETAIL_DESCRIPTION,
        )
        return None

    # Strip any in-container noise nodes before reading text.
    for noise_css in selectors.DETAIL_NOISE_WITHIN:
        for node in container.query_selector_all(noise_css):
            try:
                node.evaluate("el => el.remove()")
            except Exception:
                logger.debug("Failed to strip noise node %s (continuing).", noise_css)

    text = (container.inner_text() or "").strip()
    return text or None


def scrape_detail(page, listing: ScrapedListing) -> ScrapedListing:
    """Visit one listing's detail page and populate ``raw_description`` in place."""
    logger.info("Detail %s: %s", listing.source_job_id, listing.url)
    page.goto(listing.url, wait_until="domcontentloaded")

    try:
        page.wait_for_selector(selectors.DETAIL_DESCRIPTION, timeout=15_000)
    except Exception:  # PlaywrightTimeoutError
        logger.warning(
            "Job %s: description container never appeared - leaving raw_description NULL.",
            listing.source_job_id,
        )
        return listing

    listing.raw_description = extract_description(page)
    if not listing.raw_description:
        logger.warning("Job %s: empty description after extraction.", listing.source_job_id)
    return listing


def scrape_details(
    page,
    listings: list[ScrapedListing],
    *,
    delay_range: tuple[float, float] = DEFAULT_DELAY_RANGE,
) -> list[ScrapedListing]:
    """Scrape detail pages for ``listings`` (already capped by the orchestrator).

    A randomised delay is applied *between* visits (not before the first, not
    after the last). Per-listing errors are logged and skipped so one bad page
    doesn't abort the batch.
    """
    for index, listing in enumerate(listings):
        if index > 0:
            delay = random.uniform(*delay_range)
            logger.debug("Sleeping %.1fs before next detail page.", delay)
            time.sleep(delay)

        try:
            scrape_detail(page, listing)
        except Exception:  # noqa: BLE001 - log and continue to the next listing
            logger.exception("Job %s: unexpected error scraping detail page.",
                             listing.source_job_id)

    return listings
