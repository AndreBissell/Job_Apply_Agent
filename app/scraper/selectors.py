"""Centralised Seek selectors and URL construction.

WHY THIS FILE EXISTS
--------------------
Selectors below are based on Seek's ``data-automation`` attributes, which are the
most stable hooks the site exposes. BUT they have NOT been verified against live
DOM in this build (per the no-live-scraping constraint). Treat them as a starting
point: run ``scripts/explore_seek.py`` once to cache real pages into ``dev_data/``,
confirm each selector against that cached HTML, and correct anything wrong *here*.
Everything downstream imports from this one module, so a fix here propagates.

Verify during Step 0:
  * The job-card container and per-field selectors on a search results page.
  * The URL parameter that sorts by listing date (assumed ``sortmode=ListedDate``).
  * The description container on a ``/job/{id}`` page, and that it naturally
    excludes the noise sections (employer questions, sign-in prompt, featured-jobs
    sidebar, safety/bank-details notice) because they live outside it.
  * The worktype query-param codes (assumed numeric Seek codes; see WORK_TYPE_CODES).
"""

from __future__ import annotations

from urllib.parse import urlencode

SEEK_BASE = "https://www.seek.com.au"

# --- Search results page ---------------------------------------------------
# A single job card. Seek renders each result as an <article data-automation=...>.
JOB_CARD = '[data-automation="normalJob"]'

# Per-card fields, queried *within* a card element.
CARD_TITLE_LINK = 'a[data-automation="jobTitle"]'        # href -> /job/{id}
CARD_COMPANY = '[data-automation="jobCompany"]'
CARD_LOCATION = '[data-automation="jobLocation"]'
CARD_CLASSIFICATION = '[data-automation="jobClassification"]'
CARD_SUBCLASSIFICATION = '[data-automation="jobSubClassification"]'
CARD_WORK_TYPE = '[data-automation="jobWorkType"]'
CARD_SALARY = '[data-automation="jobSalary"]'            # often absent -> nullable
CARD_LISTING_DATE = '[data-automation="jobListingDate"]'

# --- Job detail page -------------------------------------------------------
# The ad body container. Taking its inner text gives the full description while
# excluding everything rendered outside it (the noise sections).
DETAIL_DESCRIPTION = '[data-automation="jobAdDetails"]'
DETAIL_TITLE = '[data-automation="job-detail-title"]'

# Defence-in-depth: if a future layout nests noise *inside* the description
# container, these selectors are stripped from the extracted node before reading
# text. Empty by default because the container above should already be clean.
DETAIL_NOISE_WITHIN = (
    # e.g. '[data-automation="employerQuestionsSection"]',
)


# --- URL construction ------------------------------------------------------
# Seek's worktype filter uses numeric codes. VERIFY these in Step 0. Unknown /
# unmapped work_type values are simply omitted from the URL (keyword search still
# applies), so a wrong/missing code never breaks the search.
WORK_TYPE_CODES = {
    "full time": "242",
    "full-time": "242",
    "part time": "243",
    "part-time": "243",
    "contract": "244",
    "contract/temp": "244",
    "casual": "245",
    "casual/vacation": "245",
}


def build_search_url(
    *,
    keywords: str | None,
    location: str | None,
    work_type: str | None,
    page: int = 1,
    sort_by_date: bool = True,
) -> str:
    """Build a Seek search URL from saved-search criteria, generically.

    Not hardcoded to any particular keyword/location — every field is optional and
    only added when present. Uses the ``/jobs?keywords=...&where=...`` query form.
    """
    params: dict[str, str] = {}
    if keywords:
        params["keywords"] = keywords
    if location:
        params["where"] = location
    if work_type:
        code = WORK_TYPE_CODES.get(work_type.strip().lower())
        if code:
            params["worktype"] = code
    if sort_by_date:
        params["sortmode"] = "ListedDate"
    if page > 1:
        params["page"] = str(page)

    query = urlencode(params)
    return f"{SEEK_BASE}/jobs?{query}" if query else f"{SEEK_BASE}/jobs"
