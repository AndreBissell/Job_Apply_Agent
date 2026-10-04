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
  // Seek's own taxonomy on the detail page, e.g. "Developers/Programmers" and
  // "Information & Communication Technology". UNVERIFIED against the live DOM —
  // readJsonLdJobPosting() is the primary source and these are only the
  // fallback, so a wrong guess here costs nothing. Confirm and fix on a real
  // job page when convenient.
  DETAIL_SUBCLASSIFICATION: '[data-automation="job-detail-classifications"]',
  DETAIL_CLASSIFICATION:    '[data-automation="job-detail-classification"]',
  // Employer / location / work type on the detail page. Same status as the two
  // above: UNVERIFIED fallbacks behind readJsonLdJobPosting(), and the backend
  // additionally recovers the employer name from the ad text, so a wrong guess
  // here costs nothing. Confirm on a real job page when convenient.
  DETAIL_COMPANY:   '[data-automation="advertiser-name"]',
  DETAIL_LOCATION:  '[data-automation="job-detail-location"]',
  DETAIL_WORK_TYPE: '[data-automation="job-detail-work-type"]',

  // Quick Apply "Answer employer questions" step (/job/{id}/apply/role-requirements).
  // Settled from 5 outerHTML samples the user copied from their own browser
  // (docs/quick-apply-samples.md), NOT yet seen by the extension on a live page.
  // Read-only: the extension never clicks, fills or submits anything here.
  APPLY_PROGRESS_NAV:   'nav[aria-label="Progress bar"]',
  APPLY_CURRENT_STEP:   '[aria-current="step"]',
  APPLY_JOB_HEADER:     '[data-automation="job-header"]',
  // Every answer field is name="questionnaire.<questionId>"; group by name.
  QUESTION_FIELDS:      '[name^="questionnaire."]',
  QUESTION_NAME_PREFIX: 'questionnaire.',
  // Seek's own session-replay mask (the user's name in the header): never read.
  PERSONAL_DATA_MASK:   '[data-adora-mask]',
};

// Extract the Seek numeric job id from a /job/{id} href or path.
function extractJobId(href) {
  const match = href && href.match(/\/job\/(\d+)/);
  return match ? match[1] : null;
}

// The job id of a Quick Apply page (/job/{id}/apply/...), else null. The id is in
// the URL only; the questions markup doesn't carry it.
function extractApplyJobId(path) {
  const match = path && path.match(/^\/job\/(\d+)\/apply(?:\/|$)/);
  return match ? match[1] : null;
}
