# Grading standard: rule questions for the next version

**What it is:** a list of changes to `evals/grading-standard-v2.md` that came out of the owner's review of the
judge's check-half run (`pts-check-1`, 2026-11-04/05; docs/eval-set-plan.md Decision log). In each case the frozen
standard either gave the wrong answer or didn't say enough, and the owner's label went against the written rule.

**Why it was put off:** the standard is frozen for this judge version (`a1b0d9de`). Changing it during stage 8 would
mean recalibrating on letters the rules were already tuned on, so the result would prove nothing. The dev letters'
labels already reflect these readings.

**What it depends on:** fresh letters (the test split, or a new run). Any change here makes a new judge version, so
it needs a calibration run, the probes, and a check on letters neither side has seen.

## The questions

1. **Generic, §7.6: say what "none" means.** The working test is the employer swap test (D3b): every body link
   names work that only this employer has. Links that copy the ad's duty lines, however close to the job, are low.
   - The evidence:
     - none: `seek-94890841` (the links name this employer's own systems) and `seek-95110025` (two of three links
       name its product);
     - low: `seek-94932262`, `seek-95084704` and `seek-95085104` (duty lines copied).
   - The current text talks about direct links to the job, which a copied duty line also passes.
   - Owner: "it specifically targets the company"; may need more refinement after fresh letters.

2. **Example rungs, §4.2, for non-software jobs.** For a client-facing or corporate role, professional work
   (requirements, feedback and presenting at the internship) outranks casual retail work, even though the retail
   job is "customer service" in name. The rungs follow the job's own field, but the standard gives no non-software
   example.
   - Related, and a judge behaviour more than a rule: run the swap test even when the stronger example never appears
     in the letter. The judge missed it on `seek-95205206` and `seek-95191929`, where both weak-example lists were
     empty.
   - A worked example of an absent stronger example might fix it.

3. **Filler, §7.8: a sentence that answers an instruction in the ad is not filler.** Example: `seek-95084704`'s
   line naming the stream the ad asked applicants to choose.
   - The boundary: an instruction meant for another part of the application (`seek-95260505`'s pathway line answers
     an instruction for the video) is likely still filler in the letter.
   - Owner: "it's wanted".

## Not a standard change, but found in the same review

- `v2-agreement` checks untrue per letter, so a judge-only untrue flag on a letter the owner also flagged never shows
  up for review (`seek-95205206` had two judge flags against one owner flag). Match flags by quote and list the
  extras. Small code change in `scripts/grading_panel.py`.
- The profile's thesis entry should say that a large part of the work was finding creative ways to handle the
  constraints (true, per the owner). It waits until the profile is unfrozen; until then, a letter that says it is a
  stretch.
