# Plan: availability checks, fast mode and a value-first queue

Status: DRAFT 2026-11-17, all decisions made; awaiting the owner's final read and go-ahead. No
code written yet. Branch `avaliability-check`.

## Why

The owner uses the app about once a week. Coming back after a few days, two things go wrong:

- **Letters can't be written for good jobs.** Every letter needs the job seen live within
  `FRESH_FOR` (24 h), and after a few days away almost nothing is.
- **Keep Applying can offer closed ads.** It hides only jobs already *flagged* expired
  (`app/api/jobs.py`), not stale ones, so a job with a letter whose ad has closed still shows
  as ready to apply.

What the code does today (`app/availability.py`, `extension/sidebar_availability.js`):

1. **Rate.** The checks were built as a deliberate trickle (2026-10-13): first session 60 s
   after the panel opens, then at most 8 pages (15 s apart) per session, one session per 30 min,
   at most 24 pages per rolling 24 h. That fit a panel left open all day; at weekly use, a
   backlog of 40 stale jobs takes about two days to clear.
2. **Order.** Two tiers. "Blocking" jobs (unscored, or scored at/above the auto-letter score
   with no letter) go first, **newest first, not by score**. Jobs with a letter are only in the
   second "listed" tier (letter first, then score), so they wait behind every blocking job.
3. **A dead zone.** A "listed" job (score 60+, not blocking) is re-checked only after
   `RECHECK_AFTER` = 3 days, but it stops being fresh enough for a letter after 24 h. A job
   below the auto-letter score, seen live 2 days ago, can't get a letter and won't be checked
   for another day. Letter jobs are listed too, so a closed one can sit in Keep Applying for up
   to 3 days. The cause: two numbers that must agree (`FRESH_FOR`, `RECHECK_AFTER`) are
   defined separately.

**Not fixed by this plan:** a closed ad is flagged only on its second miss, a day after the
first (the two-strike rule, kept so a blocked browser can't flag live jobs). After this change
a closed letter job can still show in Keep Applying for about a day. See Follow-ups.

## Decisions (owner, 2026-11-17)

**D1. One queue, ordered by value.** Replaces the blocking / listed tiers. Still excluded, as
today: no stored description, flagged expired, applied, hidden.
1. Scored jobs **with** a cover letter, by match score (highest first), **at any score**. Today
   the 60 floor filters the whole listed tier, so a letter job below 60 is never checked; a
   letter is a stronger sign of intent than the score. Consequence, accepted: any letter job is
   checked before any job without one. A low scorer rarely has a letter (only one asked for by
   hand), but a 78 with a "+ letters" letter before a fresh-scraped 95 is common; see the
   letter-unlock follow-up.
2. Scored jobs **without** a cover letter, by match score. Floor stays `MIN_DISPLAY_SCORE` (60).
3. Unscored jobs last, newest first.

Accepted trade-off: an unscored stale job is checked only when every known job due that day is
done. Scan Page skips jobs it already holds ad text for, so a rescan won't bring it back; only
opening the ad by hand refreshes it.

**D2. Recheck after 24 h, defined as `RECHECK_AFTER = FRESH_FOR`.** One number, so the dead
zone can't come back. `RETRY_BLOCKING_AFTER` (6 h) goes with the blocking tier.
`RECHECK_AFTER_MISS` (1 day, the second-strike visit) is unchanged.

**D3. Fast mode.** Automatic, no button.
- **Goal:** 10 jobs from groups 1 and 2 (D1) seen live within 24 h. Fewer than 10 known
  jobs: all of them. Exact trigger, visit and count rules: P1.
- **Pace:** 5 s between pages (`SCAN_DELAY_MS`, the policy floor, the same as Scan Page).
- **Stops when** 10 jobs are seen live, or after **15 pages**, whichever comes first.
- **At most once an hour** (timestamp in `chrome.storage.local`, like the cooldown).
- Its pages don't count toward the background rate (D4); they do count toward the daily cap (D5).

**D4. Background trickle unchanged in rate:** 15 s apart, 8 pages per session, one session per
30 min (16 pages an hour), same queue as D1.

**D5. Daily cap 24 -> 100** (rolling 24 h, fast + background). Deliberate 4x increase.
Realistic load: fast mode fires about once a day (right after a run all of the top 10 are
checked, so it can't fire again until they go stale together, or a run hit its 15-page cap),
so one burst of up to 15 pages, then 16 an hour. The ceiling, fast mode firing every hour,
is 31 an hour (cap reached in 3-4 hours); the cap and the challenge cooldown bound it.

**Unchanged:** the challenge-page 6 h cooldown and its length (stops fast mode too; D7 widens
what counts toward the 5-in-a-row trigger), the
two-strike rule and its live-page canary, applied jobs never visited or flagged, a scan always
cancels a check, one Seek page at a time (`seekPace`), a stored listing's own `/job/{id}` page
only, never a link found on it.

## The fast-mode rules

**P1 (owner's version, 2026-11-17). Two separate rules:**
- **Counts toward the 10 = seen live within 24 h** (`last_seen_live_at`). A closed ad doesn't
  count; fast mode moves on down the D1 order. So fast mode aims for **10 confirmed-live jobs**,
  not 10 visited ones.
- **Fast mode visits a job only if it was NOT checked or seen live within 24 h**
  (`availability_checked_at` and `last_seen_live_at` both older, the same idea as `_due`).
  - A closed ad checked an hour ago is skipped, not reopened: no loop. Its second strike comes
    a day later, as today.
  - A job captured this morning is never visited: it is already fresh.
- **Fires** when fewer than 10 jobs in the D1 order (groups 1-2) are seen live within 24 h and
  at least one job ahead of the 10th live one is due a visit.
- **Edge case, accepted:** many closed/unknown results mean a run can reach 15 pages before 10
  live. An hour later the next run skips what was visited and goes further down the list.
  Worst case (every page "unknown", e.g. a redirect): 15 pages an hour on nothing, bounded by
  the daily cap. Open: count a run of unknown results toward the cooldown (see O3).

## Further decisions (2026-11-17)

**O1 -> D8 (owner, 2026-11-17). The backend decides: `GET /availability/next` returns the job
and `mode: fast | background`.** It sees every verdict before choosing the next job, so P1
(skip, take the next, stop at 10 live), the 15-page cap and once an hour are all backend rules,
tested in pytest, and a panel bug can't break them. Two panels open share one count.
- **State:** the start time of the last fast run, one timestamp in `profiles.preferences` (no
  migration). The run's page count = checks recorded since that time.
- **A GET never changes data.** Asking `/next` must not start a run: a panel reload or a retry
  would burn the hour. The run starts when the first fast-mode verdict is POSTed.
- The panel keeps only the pacing (5 s / 15 s), pinned by `tests/test_scan_policy.py`.
- The response shape changes (`mode`; `tier` replaced): `tests/fixtures/openapi.json` is
  updated on purpose (`tests/test_route_table.py`).

Rejected: the panel deciding (only the panel would enforce the caps; two panels = two fast
runs); a separate endpoint returning a list of 15 up front (the panel would have to count live
results itself).

**O2 -> D6 (owner, 2026-11-17). Fast mode starts as soon as the panel is ready; background
keeps its 60 s start delay.** The 60 s let a Scan Page click go first; a scan still wins
because it cancels the check in flight (`cancelAvailabilityCheck`). Cost: opening the panel
opens a background Seek tab straight away.

**O3 -> D7 (decided by Claude at the owner's request, 2026-11-17). 5 pages in a row that were
not live, "missing" or "unknown", start the 6 h cooldown; a live page resets the count.**
Today the cooldown starts on a challenge page or 5 "missing" in a row, and "unknown" (a
redirect, a tab error) neither adds to nor resets that count, so a browser Seek redirects
everywhere would never pause.
- One counter, the existing `CHECK_MISS_STREAK_LIMIT` (5), widened: no second counter to drift,
  and a missing/unknown mix is caught.
- Only pages actually opened count: an "unknown" from `checkUrlFor` refusing the URL opened
  nothing and says nothing about Seek.
- Risk, accepted: what a closed Seek ad looks like is still unverified. If closed ads redirect,
  5 closed ads in a row pause checks for 6 h with nothing wrong. The same risk already exists
  for "missing"; a false pause costs an afternoon, a missed block risks the browser. The manual
  checks show what a closed ad produces; tune the limit then.

## Policy and tests that change (on purpose)

The caps are pinned so that changing one takes a test edit and a policy edit, never only a
constant.
- `CLAUDE.md`, Seek Access Policy, "Availability re-checks": add fast mode (automatic, 5 s,
  15 pages, once an hour, top 10 only) and the 100 daily cap.
- `tests/test_scan_policy.py`: `test_backend_daily_cap_is_bounded` (<= 30 -> <= 100); new pins
  for the fast-mode delay (>= `SCAN_DELAY_MS`), page cap (<= 15) and once-an-hour gap.
  `test_check_session_cap_stays_a_trickle` unchanged.
- `tests/test_availability.py`: the blocking/listed tier tests become ordering tests for D1.

## Test plan

- Unit (`tests/test_availability.py`): the D1 order (letter > no letter > unscored; score
  within a group; unscored newest first); exclusions; D2 (a job seen 25 h ago is due, 23 h ago
  is not; a struck job waits a day); fast mode (P1): fires with fewer than 10 seen live; a job
  checked "missing" an hour ago is skipped, not revisited, and the 11th job is taken instead; a
  job captured this morning counts and is never visited; stops at 10 live; daily cap at 100.
- Policy (`tests/test_scan_policy.py`): as above.
- e2e (`tests/e2e/availability_e2e.py`, tabs stubbed, never real Seek): fast mode runs at 5 s,
  stops at 10 checked or 15 pages, doesn't run again within the hour, a scan cancels it, a
  challenge page stops it, background then continues at 15 s.
- **Exact counts, not only bounds.** Each cap test asserts the expected number, e.g. "20 stale
  jobs, none live: fast mode opens exactly 15 pages", so a loop that stops early (say at the
  background cap of 8) fails too. A test that only checks "never more than 15" would pass it.
- **D7 cooldown:** 5 non-live pages in a row (mixed missing/unknown) start it; a live page
  in between resets the count; an unknown from a refused URL (no page opened) doesn't count.
- **The backend guard holds whatever the panel does:** after 100 checks in 24 h,
  `/availability/next` offers nothing in either mode.

## Follow-ups (out of scope, logged so they aren't lost)

- **First-strike display.** Mark or hide a Keep Applying job on its first "missing" result
  (not flagged, just "may have closed"), so a closed ad doesn't look ready for a day.
- **Letter-unlock target.** Under D1 fast mode spends its pages on jobs that already have a
  letter; with 10+ of those it unlocks no new letters. If that bites, add a second target
  ("at least N jobs without a letter checked").

## Manual checks (owner, at the end, in their own Chrome)

- [ ] After a few days away, open the panel: fast mode starts, the console shows 5 s spacing
      and it stops within 15 pages.
- [ ] The letter jobs come first in the console log, by score.
- [ ] "+ letters" / "Write a letter" works on the newly checked no-letter jobs.
- [ ] Reopen the panel within the hour: no second fast mode.
- [ ] No Cloudflare challenge during or after fast mode.
- [ ] Open a closed Seek ad you know of: note what it shows (blank, redirect, banner) and
      which result the console logs for it. Decides whether D7's limit needs tuning.
