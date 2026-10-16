# Plan: Easier Centrelink evidence for Quick Apply jobs (branch `centrelink-ease-apply`)

**Status (2026-10-14): Steps 0-3 BUILT, Step 4 automated part done; the live check by the user
is still to do (see "Live check" at the end).** What was built, and where it differs from the
design below, is in the Decision log at the end.

## Goal

Three fixes. Two are in this plan; the third is being built in another session.

1. **Easy screenshot save** for Quick Apply jobs: one action, saved inside the app, nothing
   dropped in Downloads.
2. **Auto-register applications** for Quick Apply jobs: when the user submits a Quick Apply on
   Seek, the app records it as applied (sets `applied_at`) without the user pressing Mark Applied.
3. *(other session)* Auto-update ads that are no longer advertised. See "Coordination" below.

Both of ours depend on one thing we don't have yet: **telling a Quick Apply job from an
external-application job.** That is Step 1 and everything else hangs off it.

## What exists today (read from the code, 2026-10-13)

| Thing | Where | State |
|---|---|---|
| Screenshot capture | `captureAndDownloadScreenshot` (`extension/content_script.js:309`), relayed through `background.js` `CAPTURE_SCREENSHOT` (`captureVisibleTab`) | Takes the visible tab, **downloads a PNG to Downloads AND uploads it** |
| Screenshot upload | `POST /jobs/{id}/screenshot` (`app/api/evidence.py:109`), stored on `matches.screenshot_path` / `screenshot_taken_at`, 30-day TTL (`app/retention.py`) | Works; one screenshot per match, latest wins |
| Where the screenshot is triggered | The cover-letter overlay on `/job/{id}` only: a 📸 button, and automatically after the overlay's Mark Applied | **Only appears if the job has a cover letter.** The sidebar and Overview "Mark Applied" (`markApplied`, `sidebar_shared.js:154`) take no screenshot at all |
| What the screenshot shows | The **detail page** the user is on | Weak evidence: it shows the ad, not that an application was sent |
| Mark applied | `PATCH /jobs/{id}/status` (`app/api/job_actions.py:69`) stamps `applied_at` once; after that the status can't go back | Always a manual click |
| Apply flow watcher | `startApplyWatcher` / `checkApplyStep` (`content_script.js`), keyed on `/job/{id}/apply/...` via `extractApplyJobId` | Captures the "Answer employer questions" step only. It **already knows the user is inside a Quick Apply flow** and reads the progress bar (Choose documents → Answer employer questions → Update SEEK Profile → Review and submit) |
| Quick Apply vs external | nothing | **No field, no function.** The only mention is the parked fast-follow in CLAUDE.md and `docs/extension-revamp-plan.md` §5.2 |

Two observations that shape the design:

- The best evidence is the **"application submitted" confirmation page**, not the ad page. The
  watcher already runs on the apply flow, so it is the natural place to detect the submit.
- An external application happens on the employer's site, where the extension has no access
  (no host permission, and we must not add one). So external jobs stay **manual Mark Applied**.
  That matches your scope: screenshots and auto-register are Quick Apply only.

## What we don't know yet (do not guess these)

Same rule as the Phase 9 work: the real DOM has to be inspected, not invented. These are
unverified:

1. How a Quick Apply job looks different from an external one **on the detail page** (the
   Apply button's text, `data-automation` value, link target).
2. Whether search cards carry any marker.
3. **What the page looks like after Submit**: URL (does it change?), heading text, any
   `data-automation` hooks. Also whether it can be reached again by refresh or back.
4. Whether Seek shows an "Applied" marker on jobs you've already applied to (useful for a
   later backfill, not for this plan).

So Step 0 is evidence collection, like `docs/quick-apply-samples.md` was for the questions.

## Findings so far (samples from the user, 2026-10-14)

**Quick Apply detail-page button (sample 1, job 95141040):**

```html
<a href="/job/95141040/apply?sol=…" data-automation="job-detail-apply"
   aria-label="Apply for <title> at <employer>" target="_blank">
  … <span>Quick apply</span> …
</a>
```

- Stable hook: `a[data-automation="job-detail-apply"]`. The generated class names are ignored.
- Quick Apply signal: visible text is "Quick apply" (and the `href` is an internal
  `/job/{id}/apply` path, which `extractApplyJobId` already parses).
- **`target="_blank"`: the apply flow opens in a NEW tab.** So the apply watcher, the
  confirmation detection and the screenshot all run in that new tab. `captureVisibleTab` works
  there because the user is looking at it, but nothing may assume the ad tab is still active.
**External-application detail-page button (sample 2, job 95231667):** identical `<a>`: same
`data-automation="job-detail-apply"`, same `target="_blank"`, same `aria-label` pattern, and
**the same internal `href` shape `/job/{id}/apply?sol=…`**. Only two things differ:

| | Quick Apply | External |
|---|---|---|
| Visible text | `Quick apply` | `Apply` |
| Icon | none | an `aria-hidden` "opens elsewhere" SVG after the text |

Consequences:
- **The `href` and `aria-label` cannot tell them apart.** The discriminator is the button
  text: exactly "Quick apply" (case-insensitive, trimmed) = `quick`; exactly "Apply" =
  `external`; anything else = unknown (NULL). The external-link SVG is a second, weaker signal
  (no class names, so it is a plain `svg` inside the button); use it only to corroborate.
- **An external job's button also lands on `/job/{id}/apply`** (presumably then sends the user
  to the employer's site; not yet confirmed). So **the `/apply` URL is NOT proof of Quick
  Apply.** The existing watcher is safe (it only acts when `parseQuestionnaire()` finds
  questions), but any new logic must not treat the URL alone as proof. Proof of Quick Apply is
  what only that flow has: the progress bar (`nav[aria-label="Progress bar"]`) and its steps,
  or the questions, or the confirmation page.
- Confirmed by the user (2026-10-14): clicking the external button takes you to the
  employer's own website. It never reaches `/apply/success`, so auto-register can't fire for
  it. Whether a Seek page flashes up first doesn't matter: the apply-flow code only acts on
  the progress bar, the questions or the success page.

**Confirmation page after Submit (sample 3, job 95158567, Robert Half):**

| Step | URL |
|---|---|
| Review and submit | `/job/{id}/apply/review?sol=…` |
| After Submit | `/job/{id}/apply/success?sol=…` (**the path changes**) |

- Tab title: `Application sent | SEEK`.
- `<h1 id="applicationSent">`: a real, hand-written `id` (not a generated class), so it is a
  stable hook. Its **text varies and contains the user's first name** ("Nice work, Andre" in
  the screenshot, "Keep it up, Andre" in the pasted HTML), so never match on, store or log it.
- Next to it, a `<span>`: "Your application has been sent to <employer>". Could backfill
  `company` if empty; not needed for detection.
- Below: a "You might also like…" list of other jobs' links. Never read or followed (no second
  hop). `main()` already returns early on `/apply` paths, so they are not ingested.
- The screenshot shows the user's first name and avatar initial. It stays local (screenshots
  folder + evidence zip), which is acceptable for Centrelink evidence; no masking planned.

**Confirmation rule:** the path matches `^/job/(\d+)/apply/success` AND `#applicationSent` is
in the DOM. Both are required: the path alone could be a stale URL mid-render, and the id alone
is not tied to a job. The job id comes from the path. A refresh or back-navigation to the same
page re-runs it harmlessly (`applied_at` is stamped once; the screenshot is replaced).

## Design

### A. Apply type on the job

New column `job_listings.apply_type`: `'quick'`, `'external'` or `NULL` (unknown). It is a
property of the ad, so it lives on the job, not the match. Rules:

- Set from the detail page at capture time (detail page is the only place the button is
  reliably there). Sent through `/ingest` like `classification` already is.
- Ingest backfills `NULL` but never overwrites a known value with `NULL`.
- **Positive proof wins:** if the watcher sees the real Quick Apply flow for a job (the
  progress bar with its steps, the questions, or the confirmation page), that job is
  `'quick'`, whatever the button detection said. This also covers the button text changing
  later: jobs you actually apply to still get labelled. **The `/apply` URL alone is NOT
  proof**: an external job's Apply button also goes through `/job/{id}/apply` (sample 2).
- **Detection rule (from samples 1 and 2):** read `a[data-automation="job-detail-apply"]`,
  trim its text; "Quick apply" = `quick`, "Apply" = `external`, anything else = NULL.
- Detection is a pure function over a DOM root (`detectApplyType(root)`) so it can be tested
  against saved HTML fixtures, with the selector kept in `selectors.js` and mirrored in
  `app/scraper/selectors.py` (the sync test requires it).
- `NULL` is treated as "not Quick Apply" by every feature. A missed detection means the old
  manual path, never a wrong auto-register.

### B. Auto-register on submit

Extend the existing apply watcher. When it sees the confirmation page for job N:

1. Resolve the internal job id (`resolveOrCreateJob`, already there).
2. Mark `apply_type = 'quick'`.
3. `PATCH /jobs/{id}/status {status: 'applied', via: 'auto'}`. This is already idempotent
   (`applied_at` is stamped once), so a repeat visit or a re-render can't double count.
4. Screenshot the confirmation page and upload it (Section C).
5. Tell the side panel (the existing SSE/`chrome.runtime` route) so Overview/Applied refresh.

New column `matches.applied_via` (`'auto'` or `'manual'`, `NULL` for old rows). Reason: this
is Centrelink evidence, and "the app saw Seek's confirmation page and has a screenshot of it"
is different, and stronger, than "the user clicked a button". The Applied tab can show it,
and the CSV can carry it.

**Trigger = the confirmation page, never the Submit click.** Clicking Submit can fail
(validation, network), and the extension never clicks or reads Seek's buttons. Detecting the
resulting page means we only register what Seek actually accepted. The exact signal comes from
Step 0.

False-positive guard (settled from sample 3): `/job/{id}/apply/success` in the path AND
`h1#applicationSent` present; the watcher waits for the heading to render, then registers and
captures. No need to have seen earlier steps in the same tab (that would miss a flow opened
before the extension loaded). External jobs never reach this page.

### C. Simple screenshot, in-app only

- **Automatic** at the confirmation page (Section B), because that is the strongest evidence
  and needs no click.
- **One manual button as a fallback**, shown only when `apply_type = 'quick'`, on the apply
  flow (small floating control, same Shadow DOM approach as the questions panel). It does the
  same capture-and-upload, nothing else. Not tied to having a cover letter any more.
- **Drop the local download.** `captureAndDownloadScreenshot` currently saves a copy to
  Downloads as well. You asked for "inside the application", so upload only. The existing
  Export evidence button (zip of CSV + screenshots) remains the way to get files out.
- Upload failure is surfaced (button says "Failed, retry") instead of silently warning in the
  console, because a missed evidence upload is exactly the thing you'd otherwise find out
  about a month later.
- Remove the screenshot trigger from the cover-letter overlay's Mark Applied, and the 📸 button
  there. The overlay on the detail page keeps Copy / Save edits / Mark Applied.
- Backend `POST /jobs/{id}/screenshot` is unchanged except it should 409/ignore nothing new.
  It already handles overwrite and downscaling.
- The screenshot may contain personal details from Seek's page (name, email in the header or
  summary). It stays on the local machine and in the evidence zip. Check the confirmation page
  during Step 0 and note whether anything needs cropping or masking
  (`[data-adora-mask]` marks what Seek itself treats as personal).

### D. External-application jobs

Unchanged and explicit: no screenshot control, no auto-register, Mark Applied stays manual.
Optionally the card can say "Applies on employer site" so the difference is visible, once
`apply_type` is reliable.

## Steps

Each step is one commit, in this order. After Step 0, stop for your check.

**Step 0: collect samples (you drive, no code at all). DONE 2026-10-14:** samples 1-3 in
"Findings so far"; rows 3 (search cards) and 6 (re-opened applied job) were skipped as not
needed. Step 1 copies the scrubbed samples into `docs/quick-apply-samples.md`.
Nothing is built until this is done. It is copy-paste from your own browser, the same way
the question samples were collected. Open each page yourself, right-click the element,
Inspect, right-click the highlighted node, Copy > Copy outerHTML, and paste it to me.

| # | What to open | What to copy |
|---|---|---|
| 1 | A **Quick Apply** ad's detail page (`/job/{id}`) | The outerHTML of the Apply button and the area around it. Also the page URL |
| 2 | An **external-application** ad's detail page | The same area. If you click the button, tell me where it goes (new tab? what domain?) but don't paste the employer's page |
| 3 | A search results page that has both kinds | One card of each kind, if the cards look different at all (a badge, a label). If they look identical, just say so |
| 4 | The **last step of a real Quick Apply** ("Review and submit") | The Submit button area and the URL |
| 5 | The page you land on **right after Submit** | The URL (and whether it changed from step 4), the page heading, and the outerHTML of the main content. Take this from a job you were applying to anyway |
| 6 | A job you **already applied to**, reopened | Whether the Apply button changes (e.g. "Applied") and its outerHTML. Used later, not in this plan |

Don't paste your name, email, phone or CV filename; I'll scrub what comes through and
record it in `docs/quick-apply-samples.md` (new section). If a Quick Apply you were
going to do anyway isn't coming up, 1-3 are enough to start Step 1 and 2, and 4-5 can
follow when you next apply. This is only you reading pages you opened yourself. No request
to Seek from the app.

**Step 1: backend.**
Migration (`apply_type`, `applied_via`), models, `docs/database-schema.md`, `/ingest`
accepts and backfills `apply_type`, `StatusUpdate.via`, GET /jobs and /jobs/{id} expose
`apply_type` and `applied_via`. Python tests: migration, ingest backfill rules, status
idempotency with `via`, API schema/route-table test.

**Step 2: detection.**
`detectApplyType` in the extension, selector in `selectors.js` + `app/scraper/selectors.py`,
saved fixtures (Quick and external, from Step 0) and a test over them; send `apply_type`
from `parseDetailPage`; the apply watcher marks `'quick'` on sight. Node-side test of the
function against the fixtures.

**Step 3: auto-register + screenshot.**
Confirmation-page detection in the apply watcher, the status call, the automatic capture
and upload, the manual fallback button, removal of the download and of the overlay's
screenshot trigger, SSE/refresh of the panel. Show `applied_via` and the screenshot note in
the Applied tab.

**Step 4: tests, docs, live check.**
Extend `tests/e2e/quick_apply_e2e.py` with a scrubbed confirmation-page fixture (scratch DB,
stubbed `chrome.tabs.create`, per the e2e rule: tabs the extension opens can reach real
Seek). Update CLAUDE.md (this becomes the CURRENT TASK, §5.2 fast-follow closed) and the
database schema doc. Then a live pass: one real Quick Apply on the TEST backend, then REAL.

## Coordination with the "expired ads" session (UPDATE 2026-10-14)

That work has landed on this same branch (CLAUDE.md, "Availability checks + freshness gate"),
so there is no merge to do; the points below now describe code already in the tree. It
re-visits stored `/job/{id}` pages from the side panel in background tabs. Things to respect:
those check tabs are in the scan-tab set (mirrored in `chrome.storage.session`), so the content
script skips them: apply-type detection must not run there either, and nothing here may open
a Seek page. Applied jobs are already never re-checked or flagged, which is the right rule for
auto-registered jobs too.

*Original note, kept for reference:* both pieces touch `extension/content_script.js` and
`app/api/job_actions.py`.

- The expired work lives in `main()`'s detail-page branch (the `markExpired` block around
  lines 958-973) and `PATCH /jobs/{id}/expired`. Ours lives in the apply watcher section
  and `update_status`, plus a field added to `parseDetailPage`. Different regions, but the
  same files, so expect a small textual conflict at merge.
- To keep that small, new extension logic goes in a **new content-script file**
  (`extension/apply_flow.js`, added to the manifest's `js` list) rather than growing
  `content_script.js`. Only `parseDetailPage` and the watcher hook-up touch the shared file.
- Interaction to get right: an ad you just applied to can later show differently or close.
  Auto-register must never depend on the ad still being live, and the expired logic must never
  un-apply anything (`applied_at` already can't be undone, and applied matches are exempt from
  retention). Worth one test once both branches are merged.
- Land whichever finishes first on `main`, then rebase the other.

## Policy check (Seek access)

Everything here is the content script reading the page the user themselves opened, and
`captureVisibleTab` of that same tab. No backend request to Seek, no link-following, no extra
host permission, nothing clicked or filled on Seek's form. The 1-hop rule is untouched.

## Risks

| Risk | Mitigation |
|---|---|
| The confirmation page is still unseen, and Seek can reword the button | Button rule is from two real samples; unknown = manual path; the Quick Apply flow itself (progress bar / questions / confirmation) is positive proof of `'quick'`, the bare `/apply` URL is not (external jobs use it too); selectors kept in one place with a sync test |
| An external job's pass through `/job/{id}/apply` is mistaken for a Quick Apply | Apply-flow code acts only on the progress bar, questions or confirmation page, never on the URL alone; e2e fixture of the external redirect page once seen |
| Confirmation page is a client-side state with no URL change | Watcher already uses a MutationObserver; Step 0 tells us which signal exists |
| Double counting or a false register | `applied_at` is stamped once; trigger tied to the job id in the URL; never registered from a click |
| Screenshot captures the wrong tab or a half-rendered page | `captureVisibleTab` needs the tab active and visible; wait for the confirmation heading to be present, and show "Failed, retry" if the capture is empty |
| Upload fails when the backend is down | Button falls back to a visible retry; auto-register still happens first (the date is the important part), and the screenshot can be retried from the fallback button |
| Merge conflict with the expired-ads branch | Separate file for new logic; rebase after the first lands |

## Decisions (confirmed by the user, 2026-10-14)

1. Auto-register fires on the **confirmation page**, not on the Submit click.
2. The screenshot is taken **automatically** at that point, with a manual button as a fallback.
3. The **local Downloads copy is dropped**; the evidence zip is the export route.
4. Unknown `apply_type` behaves like external (manual only).
5. A Seek-side "Applied" marker on old ads is **not** used to backfill in this plan. It would
   record dates we didn't witness, which is a different standard of evidence. Raise it later
   if you want it.
6. **The extension gets the `<all_urls>` host permission** (user, 2026-10-14). Found while
   building: Chrome refuses `captureVisibleTab` without `<all_urls>` or `activeTab`, and a Seek
   host permission is not enough (`activeTab` needs a toolbar click or shortcut, so the capture
   couldn't be automatic). This also means the old 📸 button and the capture after Mark Applied
   almost certainly never saved anything. Alternatives offered: a keyboard shortcut or a
   toolbar-popup button (no new permission, one or two key presses). The grant is used for the
   screenshot only; `tests/test_extension_permissions.py` pins that content scripts still run on
   Seek only, the backend relay still reaches only localhost, `captureVisibleTab` is called in
   one place and only for the asking tab while it is on screen, and only the two side-panel Seek
   flows inject scripts.

## Decision log (built 2026-10-14)

- **Backend:** migration `c4e8a2f6b913` (`job_listings.apply_type`, `matches.applied_via`).
  `POST /ingest` takes `apply_type` (latest non-NULL reading wins) and lets a stub's placeholder
  title be replaced by the first capture with a description. New `POST /jobs/{id}/quick-apply-sent`
  (`app/api/job_actions.py`): sets `apply_type='quick'`, stamps `applied_at` once with
  `applied_via='auto'`, lifts an expired flag, broadcasts SSE `job_applied`. `PATCH /status`
  now records `'manual'`; both share `_stamp_applied`. Fields shown by `GET /jobs`,
  `GET /jobs/{id}`, `GET /obligation`; both CSVs have a `Recorded` column.
- **Changed from the design: the endpoint creates the match if there is none.** `PATCH /status`
  404s without a match, and a Quick Apply to a job the app never scored must still count. The
  new match is unscored (score NULL); the idle loop only scores jobs with no match, and letters
  need a score, so it costs no AI call. It shows on the dashboard with no score badge.
- **Extension:** `selectors.js` has `DETAIL_APPLY_BUTTON`, `APPLY_SENT_HEADING`,
  `APPLY_BUTTON_TEXT`, `detectApplyType()`, `extractApplySentJobId()` (mirrored selector in
  `app/scraper/selectors.py`). `parseDetailPage` and the scan's `pageScrapeDetail` send
  `apply_type`. New content script `apply_flow.js` (loaded before `content_script.js`, called
  from its apply watcher): on `/job/{id}/apply/success` + `h1#applicationSent` it records the
  application, waits for the tab to be on screen, screenshots it BEFORE drawing its panel, uploads
  it, then shows "Application recorded" with Retake (the panel hides itself for a retake).
  Backend down: 3 automatic tries 10 s apart, then a Retry button. A never-captured job gets a
  stub titled `Seek job <id>` with the employer from "has been sent to …". The cover-letter
  overlay lost its 📸 button and its capture-on-Mark-Applied; the Downloads copy is gone.
  `background.js` only captures for the asking tab while it is the active one. Applied tab:
  "recorded from Seek" and a View link for the saved screenshot.
- **Test-env only:** `APP_SCREENSHOTS_DIR` moves the TEST screenshots folder (the e2e uses it);
  the real environment ignores it.
- **Tests:** `tests/test_quick_apply_sent.py` (23, including Node runs of the detection against
  the samples), `tests/test_extension_permissions.py` (5), `tests/e2e/apply_sent_e2e.py` (33
  checks: detection, manual vs auto, screenshot file and panel, Retake, reload, never-captured
  job, review -> sent without a page load, review page and bare `/apply` record nothing, name
  never stored, no page events, no download, no Seek request beyond fixtures). The saved
  screenshots were looked at: no panel in them. Full suite 1628 passed (new tests included); e2e
  quick_apply 124/124, dashboard 92/92, availability 48/48, sidebar_load 57/57 (it now expects
  `job_applied`).

## Live check, part 1 (PASSED 2026-10-14)

The user ran it on REAL: a real Quick Apply was recorded and its screenshot saved
(`app/screenshots_real/54-1-20261014T025203Z.png`, the old naming, from before part 2).

---

## Part 2: Centrelink-ready PDFs (built 2026-10-14)

**The need (user, 2026-10-14).** Centrelink takes ONE file per upload ("PDF, DOCX, RTF, JPG, PNG or
DOC format and under 3 MB in size") and then asks how many applications it covers. Uploading one
screenshot per job is slow; one PDF holding several screenshots covers several jobs per upload.

**User decisions (2026-10-14):**

1. Quick Apply jobs only. External and hand-marked applications have no screenshot and are left
   out of the PDFs entirely.
2. Each PDF page gets a caption (date applied, job title, employer, Seek link). The whole system
   works with **no AI calls**: Pillow draws the captions and writes the PDF.
3. Collate both saves the PDF in the app and downloads it.
4. Undo is offered.
5. Bundle PDFs are kept forever. Screenshots are a rolling ~5 months (`screenshot_ttl_days` 30 → 150).

**Changed from the user's sketch (agreed in chat):** the user proposed two screenshot folders (all
evidence by month, plus a "current" folder of unreported ones, with files moved out after collating).
Built instead: each screenshot is stored **once**, in `evidence/<YYYY-MM>/`, and the database records
which bundle each application went into. "Unreported" = applied, with a screenshot, and no
`evidence_bundle_items` row. That way two copies can't drift apart (a hand deletion, or a Retake that
updates one copy). The PDF name uses `-` instead of `/`, because Windows file names can't hold `/`.
Corrected maths: 30 × 104 KB ≈ 3.19 MB is just OVER 3 MB, so the packer measures each page.

**Layout** (inside `app/screenshots_real/`, or `app/screenshots/` on TEST):

```
evidence/2026-10/1312_14-10-2026_Acme-Pty-Ltd.png   every screenshot, once; (time)_(date)_(company)
centrelink_ready/1312_14-10-2026_5_jobs.pdf         the upload files; (time)_(date)_(number of jobs)
```

Names hold only letters, digits, `_` and `-` (company names are cleaned; no company → `job-<id>`;
a clash gets `_<job id>` / `_2`). Older screenshots keep their old top-level path and still work.

**Rules:**

- An **unreported screenshot never expires**: it is the only copy of evidence still to report. The
  150-day clock only removes files that are already in a bundle. The emptied month folder goes too.
- PDFs: one page per screenshot, scaled to 1200 px wide, JPEG quality 80, oldest application first.
  A real Seek "application sent" page is ~90 KB a page, so 20 = 1.8 MB in one PDF. Pages are packed
  by their measured size up to 2.8 MB; anything that still comes out over 3,000,000 bytes is split in
  half. Several PDFs from one click are named with their own counts.
- Undo deletes the PDF and the bundle row; the screenshots were never moved, so they are simply
  unreported again.
- Deleting an application (Applied tab) removes its bundle item but keeps the PDF.

**Built:**

- Migration `d8f3b6a1c527`: `evidence_bundles` (filename, job_count, size_bytes, created_at) and
  `evidence_bundle_items` (bundle_id, match_id UNIQUE). A separate table, not a column on `matches`,
  so the migration never rebuilds `matches` on SQLite.
- `app/screenshots.py`: `evidence_relpath`, `safe_name_part`, `screenshot_relpath` /
  `screenshot_file` (old and new paths, refuses `..`), month-folder cleanup, expiry skips unreported.
- `app/evidence_pdf.py` (new, pure: no DB, no LLM): `render_page` (caption band + screenshot),
  `build_bundles` (size-aware packing).
- `app/api/evidence.py`: the upload writes to `evidence/<month>/` and broadcasts SSE `evidence_changed`;
  `GET /evidence/status`, `POST /evidence/bundles`, `GET /evidence/bundles/{id}/download`,
  `DELETE /evidence/bundles/{id}` (undo). `GET /jobs` and `GET /obligation` rows gain
  `screenshot_reported`; `screenshot_expires_at` is null while unreported.
- Side panel: a **Centrelink evidence** card on the Overview under the progress card (count not yet
  reported, "Collate N screenshots into a PDF", the result with what to enter on Centrelink's form,
  the last 5 PDFs with Download and a two-click Undo that survives live reloads). The Applied tab's
  note reads "Screenshot saved · not yet reported" or "Reported · screenshot kept until …".
- Tests: `tests/test_evidence_bundles.py` (23; every LLM entry point made to raise), retention tests
  updated for the new rule, `tests/e2e/apply_sent_e2e.py` now 51 checks (+ month-folder name, the
  Overview card, collate → one 3-page PDF downloaded AND saved, Undo, no tab opened, no model call
  during collate/undo). Full suite 1655 passed; e2e sidebar_load 58/58, dashboard 92/92.

## Live check (for the user, not done yet)

1. Reload the unpacked extension in `chrome://extensions`, and restart `run_api.py real` so the
   migration `d8f3b6a1c527` runs.
2. Overview: the Centrelink evidence card should show **1 Quick Apply application not yet reported**
   (the screenshot from part 1's live check).
3. Press **Collate**: a PDF named like `1312_14-10-2026_1_job.pdf` lands in Downloads and in
   `app/screenshots_real/centrelink_ready/`. Open it: caption above the screenshot.
4. Try **Undo** (two clicks), then collate again. Upload the PDF to Centrelink when you report,
   entering the number of jobs from the file name.
