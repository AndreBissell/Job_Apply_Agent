"""Human-in-the-loop navigation for Cloudflare-protected pages.

Seek sits behind Cloudflare, which serves a "Just a moment..." challenge to
traffic it thinks is automated. We do NOT defeat that check — instead we run a
VISIBLE browser (still through the mandatory proxy) and hand control to the human
to solve any challenge themselves. A real person clicking the box is exactly what
the check is asking for; extraction only resumes once the human confirms the real
content is on screen.

The public surface is ``make_interactive_navigator()``, which returns a
``navigate(page, url, ready_selector, *, what) -> bool`` callable. The scraper's
``scrape_search`` / ``scrape_detail`` accept this as their ``navigate=`` hook, so
the *same* parsing code runs whether navigation is automated (default) or
human-gated (interactive). When a challenge appears the human is notified
(terminal beep + the browser window is raised + a console banner) and the run
pauses until they press Enter.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Markers that identify a Cloudflare interstitial rather than real page content.
_CHALLENGE_TITLE_HINTS = ("just a moment", "attention required", "verifying you are human")
_CHALLENGE_MARKERS = (
    "#challenge-form",
    "#cf-chl-widget",
    "script[src*='challenge-platform']",
    "iframe[src*='challenges.cloudflare.com']",
)


def looks_like_challenge(page) -> bool:
    """Best-effort detection of a Cloudflare challenge page.

    Checks the document title and a few stable Cloudflare DOM markers. Kept
    conservative: a false negative just means we treat an empty page as
    "end of results" (a clean stop), never a crash.
    """
    try:
        title = (page.title() or "").strip().lower()
    except Exception:  # noqa: BLE001 - title() can throw mid-navigation
        title = ""
    if any(hint in title for hint in _CHALLENGE_TITLE_HINTS):
        return True
    for selector in _CHALLENGE_MARKERS:
        try:
            if page.query_selector(selector) is not None:
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _beep() -> None:
    """Audible attention signal. Windows MessageBeep, else the terminal bell."""
    try:
        import winsound

        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
    except Exception:  # noqa: BLE001 - non-Windows or no audio
        print("\a", end="", flush=True)


def notify_human(page, what: str) -> None:
    """Raise the browser window and print a loud banner asking for a manual solve."""
    try:
        page.bring_to_front()
    except Exception:  # noqa: BLE001 - best effort
        logger.debug("bring_to_front failed (continuing).")
    _beep()
    print("\n" + "=" * 72)
    print(f"  ACTION NEEDED  —  a challenge is blocking the {what}.")
    print("  In the browser window (now raised), solve the Cloudflare")
    print("  'Just a moment...' / CAPTCHA until you can SEE the real content.")
    print("=" * 72)


def _content_ready(page, ready_selector: str, timeout_ms: int) -> bool:
    try:
        page.wait_for_selector(ready_selector, timeout=timeout_ms)
        return True
    except Exception:  # noqa: BLE001 - PlaywrightTimeoutError, kept broad
        return False


def make_interactive_navigator(*, quick_timeout_ms: int = 8_000, post_timeout_ms: int = 20_000):
    """Build a challenge-aware ``navigate`` callable for the scraper.

    The returned function navigates to ``url`` and:

    * returns ``True`` immediately if ``ready_selector`` appears (no challenge);
    * if a Cloudflare challenge is detected, notifies the human, waits for them to
      solve it and press Enter, then re-checks (looping until solved, skipped, or
      the challenge clears with still no content);
    * if there is no challenge and no content, returns ``False`` so the caller can
      treat it as a clean end-of-results stop (no false alarm).
    """

    def navigate(page, url: str, ready_selector: str, *, what: str = "page") -> bool:
        logger.info("Opening %s: %s", what, url)
        page.goto(url, wait_until="domcontentloaded")

        if _content_ready(page, ready_selector, quick_timeout_ms):
            return True

        if not looks_like_challenge(page):
            # No challenge, no content -> genuinely nothing here (e.g. paged past
            # the last results page). Clean stop, don't bother the human.
            return False

        while True:
            notify_human(page, what)
            answer = input(
                "  Press Enter once you've solved it (or type 's' + Enter to skip this page): "
            ).strip().lower()
            if answer == "s":
                logger.info("Human skipped %s.", what)
                return False
            if _content_ready(page, ready_selector, post_timeout_ms):
                return True
            if not looks_like_challenge(page):
                # Challenge gone but still no expected content — give up on this page.
                print("  Challenge cleared but the expected content isn't here — skipping.")
                return False
            print("  Still seeing a challenge — solve it fully, then press Enter again.")

    return navigate