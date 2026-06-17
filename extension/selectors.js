// Seek DOM selectors for the content script.
//
// IMPORTANT: keep in sync with app/scraper/selectors.py (the Python side mirrors
// these). They are based on Seek's data-automation attributes. If Seek changes
// its DOM and capture stops working, fix the selectors HERE and in the Python
// file together.

const SELECTORS = {
  // Matches normal AND premium/featured cards (data-automation varies:
  // normalJob/premiumJob — data-testid is stable). Verified vs live DOM 2026-06-17.
  JOB_CARD:           '[data-testid="job-card"]',
  CARD_TITLE_LINK:    'a[data-automation="jobTitle"]',
  CARD_COMPANY:       '[data-automation="jobCompany"]',
  CARD_LOCATION:      '[data-automation="jobLocation"]',
  CARD_WORK_TYPE:     '[data-automation="jobWorkType"]',
  CARD_SALARY:        '[data-automation="jobSalary"]',
  DETAIL_DESCRIPTION: '[data-automation="jobAdDetails"]',
  DETAIL_TITLE:       '[data-automation="job-detail-title"]',
};

// Extract the Seek numeric job id from a /job/{id} href or path.
function extractJobId(href) {
  const match = href && href.match(/\/job\/(\d+)/);
  return match ? match[1] : null;
}
