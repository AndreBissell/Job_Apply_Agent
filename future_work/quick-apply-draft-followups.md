# Quick Apply drafts: follow-ups from the first eval

**Put off:** 2026-10-05, after `screening-v1` (`evals/results/screening-v1.md`; the
answers are in the gitignored `evals/runs/screening-v1/drafts.md`).
**Waiting on:** the user's go-ahead to spend (~$0.19 for a re-run) and, for the wording
items, the go-ahead to tune wording again (2026-10-04 decision: not now).

## 1. Re-measure the "note for the candidate" fix (~$0.19)

In screening-v1, 4 of the 8 answered drafts (D1, D4, D5, D7) explained things to the
candidate inside the text meant for the employer ("Because my profile lists SQL as a
skill without detailing..., I have not stated a duration."). Fixed the same day in two
ways: a code check (`drafts.code_issues`, "talks to you, not the employer") so such a
draft is flagged and never marked as checked, and one prompt line (the answer is pasted
as written; explanations go in the note). The code check was re-scored on the stored
answers for free (it flags exactly those 4). The prompt line was NOT measured.

Do: `python scripts/screening_eval.py drafts --run screening-v2 --fresh --confirm-spend`,
then `report --run screening-v2`; compare the "note for the candidate" count with v1's 4.

## 2. The 9b view and a 9c draft disagree on compound skills

For "React Native with Expo" (or any skill in parts), 9b's "What your profile has" needs
ONE experience backing every part, so with no Expo it says "Nothing in your profile backs
this". The 9c draft counts each part the profile has (React Native: N months, evidence)
and only refuses to claim the missing ones. Both are honest, but side by side the panel
reads as a contradiction when the draft cites evidence the view says doesn't exist.
Option: a per-part line in the 9b "have" block ("React Native: 1 year 8 months (work);
Expo: not in your profile"), reusing `drafts.part_facts`. Seen in the e2e screenshot.

## 3. Wording (wait for the go-ahead)

- Drafts pad with the thesis description when the question's skill is only listed (D1:
  SQL answered with the travel-planner thesis, which the profile doesn't tie to SQL).
- D2's "I have 4 months of experience with React.js, where I developed with..." names no
  role ("where" has nothing to point to).

## 4. Cost

$0.0137 per draft on average; P4 (Terraform, a blank "answer this yourself") cost $0.038,
almost all thinking tokens. If that repeats, a lower thinking level for this task, or
answering a question whose whole skill is NOT IN PROFILE in code with no call at all
("Your profile has no trace of X: answer this one yourself"), would save the call. The
second is also more honest by construction.
