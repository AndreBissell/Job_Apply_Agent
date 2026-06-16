"""Component 1: Seek search / list scraper.

Given a ``saved_searches`` row, build the search URL, walk results pages, and
return the listings that are NOT already in ``job_listings`` (deduplicated on
``(source='seek', source_job_id)``). Stops paginating as soon as a whole page
contains zero new listings (early-stop: everything beyond is from a prior run).

This component does NOT cap how many new listings it returns — on a fresh saved
search that can be hundreds. Capping happens in orchestration so that capped-out
listings stay "new" and are picked up on a later run.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import JobListing
from app.scraper import selectors

logger = logging.getLogger(__name__)

SOURCE = "seek"

# Seek detail URLs look like /job/12345 (optionally with a slug or query string).
_JOB_ID_RE = re.compile(r"/job/(\d+)")

# Safety bound so a selector/layout change can never spin pagination forever.
MAX_PAGES = 50


@dataclass
class ScrapedListing:
    """Structured fields scraped from one search-result card.

    ``raw_description`` is filled later by Component 2. ``classification`` /
    ``subclassification`` are captured here but currently have NO column in
    ``job_listings`` — see the note in ``run.py`` / PROGRESS.md.
    """

    source_job_id: str
    url: str
    title: str
    company: str | None = None
    location: str | None = None
    classification: str | None = None
    subclassification: str | None = None
    work_type: str | None = None
    salary: str | None = None
    raw_description: str | None = field(default=None)


def _text_or_none(card, css: str) -> str | None:
    """Inner text of the first match for ``css`` within ``card``, else ``None``."""
    el = card.query_selector(css)
    if el is None:
        return None
    text = (el.inner_text() or "").strip()
    return text or None


def _extract_job_id(href: str | None) -> str | None:
    if not href:
        return None
    match = _JOB_ID_RE.search(href)
    return match.group(1) if match else None


def _absolute_url(href: str) -> str:
    if href.startswith("http"):
        return href
    return f"{selectors.SEEK_BASE}{href}"


def parse_card(card) -> ScrapedListing | None:
    """Parse one job card into a ``ScrapedListing``; ``None`` if it's unusable.

    A card with no resolvable job id or title is logged and skipped rather than
    crashing the whole run.
    """
    link = card.query_selector(selectors.CARD_TITLE_LINK)
    if link is None:
        logger.warning("Job card had no title link (selector %s) - skipping",
                       selectors.CARD_TITLE_LINK)
        return None

    href = link.get_attribute("href")
    job_id = _extract_job_id(href)
    if job_id is None:
        logger.warning("Could not extract source_job_id from href=%r - skipping", href)
        return None

    title = (link.inner_text() or "").strip()
    if not title:
        logger.warning("Job %s had an empty title - skipping", job_id)
        return None

    return ScrapedListing(
        source_job_id=job_id,
        url=_absolute_url(href),
        title=title,
        company=_text_or_none(card, selectors.CARD_COMPANY),
        location=_text_or_none(card, selectors.CARD_LOCATION),
        classification=_text_or_none(card, selectors.CARD_CLASSIFICATION),
        subclassification=_text_or_none(card, selectors.CARD_SUBCLASSIFICATION),
        work_type=_text_or_none(card, selectors.CARD_WORK_TYPE),
        salary=_text_or_none(card, selectors.CARD_SALARY),
    )


def _existing_job_ids(session: Session, source_job_ids: list[str]) -> set[str]:
    """Which of ``source_job_ids`` already exist in job_listings for this source."""
    if not source_job_ids:
        return set()
    stmt = select(JobListing.source_job_id).where(
        JobListing.source == SOURCE,
        JobListing.source_job_id.in_(source_job_ids),
    )
    return set(session.scalars(stmt))


def scrape_search(
    page,
    saved_search,
    session: Session,
    *,
    max_pages: int = MAX_PAGES,
) -> list[ScrapedListing]:
    """Walk a saved search's results pages and return the *new* listings.

    Parameters
    ----------
    page :
        A live Playwright ``Page`` (the orchestrator owns the browser lifecycle).
    saved_search :
        A ``SavedSearch`` row (uses ``keywords``, ``location``, ``work_type``).
    session :
        DB session, used to check existing ``(source, source_job_id)`` rows.
    """
    new_listings: list[ScrapedListing] = []
    seen_ids: set[str] = set()  # guard against the same id appearing twice in a run

    for page_num in range(1, max_pages + 1):
        url = selectors.build_search_url(
            keywords=saved_search.keywords,
            location=saved_search.location,
            work_type=saved_search.work_type,
            page=page_num,
        )
        logger.info("Search '%s' page %d: %s",
                    saved_search.label or saved_search.keywords, page_num, url)

        page.goto(url, wait_until="domcontentloaded")

        # Wait for at least one card to render; absence likely means "no more
        # results" (we've paged past the end), which is a clean stop, not an error.
        try:
            page.wait_for_selector(selectors.JOB_CARD, timeout=15_000)
        except Exception:  # PlaywrightTimeoutError, kept broad to avoid import
            logger.info("No job cards on page %d - stopping pagination.", page_num)
            break

        cards = page.query_selector_all(selectors.JOB_CARD)
        if not cards:
            logger.info("Zero cards parsed on page %d - stopping pagination.", page_num)
            break

        listings = [parsed for c in cards if (parsed := parse_card(c)) is not None]
        existing = _existing_job_ids(session, [l.source_job_id for l in listings])

        page_new = 0
        for listing in listings:
            if listing.source_job_id in existing or listing.source_job_id in seen_ids:
                continue
            seen_ids.add(listing.source_job_id)
            new_listings.append(listing)
            page_new += 1

        logger.info("Page %d: %d cards, %d new.", page_num, len(listings), page_new)

        # Early-stop: a full page with nothing new means the rest is old.
        if page_new == 0:
            logger.info("Page %d had no new listings - early stop.", page_num)
            break

    logger.info("Search '%s': %d new listings total.",
                saved_search.label or saved_search.keywords, len(new_listings))
    return new_listings
