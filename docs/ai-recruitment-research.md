# AI recruitment: how screeners read applications, what they're biased towards, and what it means for our letters

**Written:** 2026-10-23, as background for docs/letter-metrics-plan.md (each finding below
links to the metric it feeds, §5).
**Scope:** published research on (a) how AI systems screen job applications, (b) their biases,
and (c) what human recruiters respond to, so the letters can serve both readers. It does not
cover gaming a screener. §6 says what we will never do.

## 1. How to read this document

Every finding has a **strength** rating, and a finding only drives a metric if it is Strong or
Moderate:

| Strength | Meaning |
|---|---|
| **Strong** | Peer-reviewed or large field data, AND confirmed by at least one independent study (different authors and data) |
| **Moderate** | One rigorous study, or several weaker sources that agree |
| **Weak** | Vendor or student work, or unreplicated: recorded for context, drives nothing |
| **Rejected** | Looked plausible but failed replication or was contradicted |

**Read in full (methods and results):** Galdin & Silbert 2025 (§§2–4, the measure and the
descriptive evidence), Vaishampayan et al. 2025 (NAACL), Xu, Li & Jiang 2025 (main text), Castleman
et al. 2026 (main text), Cui, Dias & Ye 2025 (first two-thirds). **The rest come from abstracts,
publisher pages or press summaries**, and their numbers are marked "(abstract)" where it matters.
Caveat: Galdin & Silbert and Cui, Dias & Ye both use Freelancer.com data around the same AI tool
launch, so they are two analyses of one natural experiment, not two independent datasets.

## 2. How AI screening works today

Across vendor documentation and the research systems studied, the pipeline is the same:
**parse the application into fields -> extract the ad's requirements -> match evidence to each
requirement -> score -> rank**, with recruiters reading from the top of the list (Jobscan's
summary of Workday HiredScore's A–F grades and Oracle's 0–5 scale; seekAi shortlisting on SEEK's
Asian sites; the multi-agent screener in arXiv 2504.02870). Two generations coexist:
- **Literal keyword systems** (older ATS filters): an exact or alias match on a skill or
  credential field. The Harvard Business School / Accenture *Hidden Workers* report (2021,
  employer survey) found these "negative" filters rejecting qualified people for missing an exact
  criterion or for a career gap.
- **LLM / embedding systems** (newer): read meaning, can infer skills that aren't written, and
  give a reasoned score.

**What we don't know:** no study found shows how much commercial screeners weigh the *cover
letter* versus the résumé, or what SEEK's Australian employer tools do with a letter. The
practical answer is that a letter has to work for both kinds of machine reader, and for the
human who makes the decision.

## 3. Findings

### A. What readers (human and AI) value

**A1. Content tailored to the specific job is what employers pay for; generic quality isn't a
signal. Strong.**
- Galdin & Silbert 2025 (arXiv 2511.08785; 2.7M applications) measured each application with a
  9-question LLM rubric, each question scored 0/1/2, which they validated against human readers:
  the LLM's scores were no further from a human's than two humans' were from each other.
  - **Custom criteria** (the proposal shows the writer): (1) has read the details of the post;
    (2) has tailored it, avoiding boilerplate; (3) understands the goal of the task;
    (4) understands its complexity; (5) has shown initiative towards the task.
  - **Generic criteria**: (6) directly mentions the needed skills; (7) directly provides evidence
    of relevant experience; (8) clear, mostly grammatical English; (9) professional, non-spammy
    tone.
  - Before LLMs, employers' willingness to pay rose with the **custom** score, but **not with
    the generic score** (not significantly different from zero). The top-scoring applications were
    hired almost 4x as often as the average one. They also set the custom score to 0 when a
    proposal was over 96% copied from another one by the same worker.
- Corroborated by: ResumeGo's field experiment (7,287 applications: 10.7% callback with no
  letter, 12.5% generic, 16.4% tailored); Cui, Dias & Ye 2025 (letter-to-post overlap predicted
  interviews before the AI tool); Kristof-Brown 2000 (*Personnel Psychology*: recruiters'
  perceived person-job fit, judged from knowledge, skills and abilities, independently predicts
  hiring recommendations); SEEK employer research (47% say a tailored letter influences the hire).

**A2. Basic writing quality is table stakes: errors cost, polish doesn't stand out. Strong.**
- Sterkens et al. 2023 (*PLOS ONE*, 445 recruiters): spelling errors cut interview chances by 7.3
  points (a few errors) to 18.5 points (many), mainly because recruiters infer lower ability.
- Wiles, Munyikwa & Horton 2025 (*Management Science*, ~480k jobseekers): non-generative writing
  help (fewer errors, easier to read) gave 8% more hires, with no drop in employer satisfaction.
- Consistent with A1: the generic criteria take effort but don't earn a premium on their own.
  Our letters are LLM-written, so errors are rare. The risk is the opposite one (B1, B4).

**A3. Once everyone can write a tailored letter, the letter stops separating candidates, and
employers turn to verifiable history. Moderate** (two analyses of one platform; see the caveat in
§1).
- After Freelancer.com's AI writing tool: the overlap-to-interview link fell 51% (Cui, Dias & Ye);
  the tailoring premium disappeared, and the score stopped predicting hires at all (Galdin &
  Silbert, Fig. 4). Employers leaned more on reputation and work history.
- **What it means:** what still separates candidates is content an AI can't fake for someone
  else: **specific, checkable facts from the candidate's own history**, applied to this job. A
  fluent, tailored-sounding letter is now the floor.

**A4. Moderate self-promotion helps; too much reads as disingenuous. Moderate.**
- Knouse, Giacalone & Pollard 1988 and Waung et al. 2017 (both *Journal of Business and
  Psychology*; abstracts and press summaries): moderate self-promotion and ingratiation in résumés
  and letters raised perceived job and organisation fit, while heavy self-promotion lowered it.

**A5. Concrete wording makes claims seem truer. Rejected as a reason.**
- Hansen & Wänke 2010 reported it, but a preregistered replication (Henderson et al. 2019,
  *Collabra*) found a much smaller effect, inconsistent with the original. Concrete evidence is
  still rewarded (A1 criterion 7, A3, Kristof-Brown), just not for this reason.

### B. How AI readers are biased (and what's in our control)

**B1. LLMs prefer text written by LLMs, especially by themselves. Strong.**
- Xu, Li & Jiang 2025 (arXiv 2509.00462; 2,245 résumés, 9 evaluator models, length controlled):
  with quality held constant, evaluators chose their own-model summary over a human one 67–82% of
  the time (the large models). Their own revision of a human summary was still preferred over the
  original. In simulated shortlists, the evaluator's own-model candidates were 23–60% more likely
  to be shortlisted. Human annotators sometimes rated the human summary better when the model
  chose its own. Mitigations: a majority vote with other models cut the bias by 60–71%; a "don't
  infer authorship" prompt by 17–62%.
- Corroborated by: Laurito et al. 2025 (*PNAS*: LLMs prefer LLM-written product descriptions,
  papers and film summaries over human-written ones); Panickssery, Bowman & Feng 2024 (*NeurIPS*:
  models recognise their own text, and that recognition tracks how much they favour it).
- **What it means:** we don't know which model an employer's screener runs. A letter tuned to
  please one model (e.g. Gemini, our writer) may gain with that screener and lose with humans and
  other models. **Robust = passes readers from several model families AND humans.**

**B2. LLM screeners read generously and sometimes wrongly; they agree poorly with humans unless
guided with examples. Strong.**
- Vaishampayan et al. 2025 (NAACL Findings; 736 real résumés, 4 human raters vs GPT-4):
  - human–GPT-4 agreement was low: Fleiss' kappa 0.02–0.23 per criterion, correlation 0.38–0.57;
  - GPT-4 was **more lenient on skills** and stricter on certifications and education. It
    **assumed skills that were never written** (31 cases: years in a role taken as proof of MS
    Office), **hallucinated requirements** (35 cases), and **missed content under an unexpected
    heading**;
  - chain of thought plus a worked example improved agreement the most.
- Castleman et al. 2026 (arXiv 2602.18550; 186 real Greenhouse postings, 9 models): frontier
  models picked the better-qualified résumé ≥ 90% of the time (Claude Sonnet 4: 0.96), but **small
  gaps (one qualification) were hard**, soft-skill qualifications were judged less reliably than
  credentials (0.81 vs 0.85), and models sometimes gave **untrue reasons** ("better formatting"
  when the formatting was the same).
- **What it means:** (a) **state each requirement's evidence explicitly, in the employer's own
  words**, rather than relying on a screener to infer it: a literal ATS needs it, and an LLM
  screener's inferences are unreliable either way; (b) our own judge must be calibrated with your
  examples and must quote its evidence.

**B3. Keyword stuffing fools some screeners and hurts with others. Mixed: don't do it.**
- Webster 2025 (arXiv 2507.11548, abstract): several "unbiased-looking" models failed to separate
  relevant from irrelevant experience, and keyword-stuffed résumés scored far higher with some
  models. A student study (Illinois Gies, Weak) found stuffing lowered scores.
- Real-world manipulation exists: about 1% of 200,000 real résumés had hidden injections, over 90%
  of them hidden keywords or skills (Duke / hireEZ, USENIX Security 2026, press summary). Vendors
  now look for it.
- **What it means:** the effect depends on the model and is getting patched. Stuffing also reads
  badly to humans (A1 criterion 2, A4). Keywords come only with real evidence, in the ad's words,
  and no skill is named more than twice.

**B4. LLM judges favour longer text; human recruiters don't. Strong (for LLM judges).**
- Saito et al. 2023 (arXiv 2310.10076; LLM judges prefer longer answers even where humans prefer
  shorter ones); Dubois et al. 2024 (length-controlled AlpacaEval: removing the length effect
  raised agreement with human rankings); a 2026 study (arXiv 2604.23178, summary) found most judges
  favoured padded text, with Claude slightly against padding.
- On Freelancer.com, median proposal length rose from 79 to 104 words after ChatGPT, and AI-tool
  proposals were the longest (Galdin & Silbert, Table 1). Recruiters skim (Ladders 2018: 7.4 s on
  a first résumé screen).
- **What it means:** keep letters short (250–340 words), and test that our judge doesn't reward
  padding.

**B5. LLMs favour whatever comes first when comparing two candidates. Strong, but not ours to
control.**
- Rozado 2026 (*PeerJ CS*, 22 models: the first-listed candidate picked about 63.5% of the time,
  author's summary); MIT's "First Come, First Hired?" (2025); Zheng et al. 2023 (MT-Bench: position
  bias in LLM judges).
- **What it means:** an applicant can't control it. It matters for **our** pairwise judge: run
  both orders.

**B6. Demographic bias is real, but its direction changes by model and study. Strong that it
exists; not something a letter should try to steer.**
- White-associated names favoured and female-associated names disfavoured by embedding retrievers
  (Wilson & Caliskan 2024, AIES); female candidates favoured and Black men penalised by five LLMs
  across 361k résumés (An et al. 2025, *PNAS Nexus*); female-named candidates favoured by all 22
  LLMs (Rozado 2026); White candidates favoured by GPT-3.5 (Armstrong et al. 2024); disability-related
  credentials ranked lower by GPT-4 (Glazko et al. 2024, FAccT); worse jobs assigned to speakers of
  African American English (Hofmann et al. 2024, *Nature*).
- Australian context: Sheard 2025 (*Journal of Law and Society*, interviews with Australian HR
  professionals) reports Australian employers using screeners trained mostly on overseas data (one
  vendor: 6% of its training data from Australia or New Zealand), with higher risks for people
  without American accents or with a disability.
- **What it means:** because the direction varies by model, nothing about identity can be
  "optimised", and trying would be wrong. Two things are in scope: the letter mentions personal
  attributes only when the candidate chooses to (eligibility items are already `not_for_letter`),
  and it is written in standard professional Australian English.

**B7. Education prestige and career gaps are weighted. Moderate (gaps), Weak (prestige).**
- Gaps: *Hidden Workers* (employer survey) and the Gies student study. Prestige: an LLM persona
  study (Ranjan & Gupta 2024) and a peer-review audit (Howell et al. 2025), neither of which scores
  résumés.
- **What it means:** little for the letter beyond naming the degree and institution plainly, as
  the writer already does for graduates.

## 4. One letter, two readers

| | Human recruiter | AI screener | Our call |
|---|---|---|---|
| Evidence for each requirement, stated explicitly | wants it (A1, Kristof-Brown) | needs it (B2) | **Both agree: the core metric** |
| Employer-specific, non-boilerplate content | pays for it (A1) | can't separate it reliably (B2, B3) | Human wins: keep it |
| Verifiable specifics from the candidate's history | what still differentiates (A3) | neutral to positive | Keep it |
| AI house style | penalised when obvious (TopResume 2025) | favoured, especially its own (B1) | **Human wins**: we can't know the screener's model |
| Length | short, skimmed (B4) | longer favoured (B4) | **Human wins**: 250–340 |
| Keyword density | stuffing reads as spam (A4) | sometimes rewarded (B3) | **Human wins**: each keyword with evidence, at most twice |

Where the two readers disagree, the human wins: a human makes the decision, the AI preferences
change by model, and they are being patched.

## 5. What changes in the metrics (docs/letter-metrics-plan.md)

| Finding | Metric change | Where |
|---|---|---|
| A1 (custom vs generic, validated) | **D7 "Understands the job"**: does the letter show it grasped the goal and the hard part of the role, beyond restating duties? A **custom vs generic split** of the recruiter checklist, reported separately, with custom weighted 2x in the checklist score as in Galdin & Silbert | Layer 2, scorecard |
| A1 (copy-paste correction) | **C7 nearest-letter similarity**: normalised edit distance from each letter's body to the closest other letter in the run; flagged under 10% (near-template). Complements C4 | Layer 3 |
| A3 | D5 also asks whether at least one specific is **checkable** (a named project, tool, organisation, or a number from the profile) | Layer 2 |
| A4 | D6 asks whether self-promotion is kept moderate, with no stacked superlatives. (A code count of intensifiers, C8, was cut: a word list can't read context.) | Layer 2 |
| B1 | Judge from a different family to the writer (already). Layer 4 screener = **2+ model families**, reporting both scores and their agreement (Xu et al.'s ensemble mitigation) | §6, Layer 4 |
| B2 | M1/M2 "addressed" requires an **explicit** mention a literal reader would find; a requirement covered only by implication counts as not addressed. Judge quotes its evidence (already) and is calibrated with your examples (already, now backed by evidence) | Layer 1, §6 |
| B3 | **G1 no-stuffing guard**: no comma-run of 5+ skill terms and no skill named more than twice; C3 ad-echo stays diagnostic | Layer 0/3 |
| B4 | **Judge length probe**: during calibration, the judge also grades padded copies of 3 letters (+60 words of filler); it passes only if it doesn't score them higher | §6 |
| B5 | Pairwise in both orders (already) | §5–6 |
| B6 | No identity-based metric. D6's "standard Australian English" stays | — |

## 6. Lines we don't cross

Hidden or white text, prompt injections, keyword lists added for machines, claiming credentials
or skills the profile doesn't hold, and changing anything about the candidate's identity. These
are deceptive, increasingly detected (B3), and against the project's honesty rules
(`check_claims`, M4).

## Sources

**Read in full (main text):**
- Galdin & Silbert, "Making Talk Cheap: Generative AI and Labor Market Signaling", arXiv
  2511.08785 (2025)
- Vaishampayan et al., "Human and LLM-Based Resume Matching: An Observational Study", Findings of
  NAACL 2025
- Xu, Li & Jiang, "AI Self-preferencing in Algorithmic Hiring", arXiv 2509.00462
- Castleman, Shen, Metevier, Springer & Korolova, "Measuring Validity in LLM-based Resume
  Screening", arXiv 2602.18550
- Cui, Dias & Ye, "Signaling in the Age of AI: Evidence from Cover Letters", arXiv 2509.25054

**Abstract, publisher page or summary:**
- Laurito et al., "AI–AI bias", PNAS 122(31), 2025
- Panickssery, Bowman & Feng, "LLM Evaluators Recognize and Favor Their Own Generations",
  NeurIPS 2024
- Wilson & Caliskan, "Gender, Race, and Intersectional Bias in Resume Screening via Language
  Model Retrieval", AIES 2024
- An, Huang, Lin & Tai, "Measuring gender and racial biases in LLMs", PNAS Nexus 4(3), 2025
- Armstrong et al., "The Silicon Ceiling", arXiv 2405.04412
- Rozado, "Gender and positional biases in LLM-based hiring decisions", PeerJ CS 2026
- Glazko et al., "Identifying and Improving Disability Bias in GPT-Based Resume Screening", FAccT
  2024
- Hofmann et al., "AI generates covertly racist decisions about people based on their dialect",
  Nature 2024
- Sheard, algorithm-facilitated discrimination in Australian hiring, Journal of Law and Society,
  2025
- Webster, "Fairness Is Not Enough", arXiv 2507.11548
- Duke / hireEZ, "Measuring Real-World Prompt Injection Attacks in LLM-based Resume Screening",
  USENIX Security 2026
- Anzenberg et al. (Eightfold), "Evaluating the Promise and Pitfalls of LLMs in Hiring Decisions",
  arXiv 2507.02087 (vendor-authored)
- Wiles, Munyikwa & Horton, "Algorithmic Writing Assistance on Jobseekers' Resumes Increases
  Hires", Management Science 2025
- Sterkens et al., "Costly mistakes", PLOS ONE 2023
- Kristof-Brown, "Perceived applicant fit", Personnel Psychology 53, 2000
- Knouse, Giacalone & Pollard 1988 and Waung et al. 2017, Journal of Business and Psychology
- Hansen & Wänke 2010 (PSPB) and Henderson et al. 2019 (Collabra, replication)
- Saito et al., "Verbosity Bias in Preference Labeling by LLMs", arXiv 2310.10076
- Dubois et al. 2024, length-controlled AlpacaEval
- Zheng et al. 2023, MT-Bench
- Harvard Business School & Accenture, "Hidden Workers: Untapped Talent", 2021
- ResumeGo cover-letter study
- SEEK employer research and product pages
- Ladders eye-tracking study 2018
- TopResume hiring-manager survey 2025
