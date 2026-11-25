# Seek capture: two live checks

**Put off:** 2026-09-21 (classification) and 2026-10-02 (search-card fields). Moved here from
CLAUDE.md's "Parked fast-follows" on 2026-11-24.
**Waiting on:** a live Seek session the owner drives. Don't guess selectors.

## What

1. **Classification fallback selectors.** `readJsonLdJobPosting()` (the page's schema.org
   JSON-LD block) is the primary source of `job_listings.classification` /
   `subclassification` and should work. The fallback, `SELECTORS.DETAIL_CLASSIFICATION` /
   `DETAIL_SUBCLASSIFICATION` in `extension/selectors.js`, are UNVERIFIED guesses. Open a real
   job page, check `classification` is populated, and fix the selectors if the JSON-LD path
   ever stops covering it.
2. **Search-card fields.** On 2026-10-02 no Seek row had a `location` or `discovered_query`,
   which suggests the search-card capture path (`parseSearchPage` in `content_script.js`) may
   not be firing. Open a search page, check the console, and check new rows in the DB.

## How

Both are read-only checks on pages the owner opens (no scan needed). Query real.db for the
columns before and after, and keep `extension/selectors.js` and `app/scraper/selectors.py`
in sync if a selector changes.
