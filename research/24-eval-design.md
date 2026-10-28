# 24 — Evaluating a multi-stage decision pipeline without a big golden set

Written 2026-10-24. Tags: **[V]** = checked today against the linked primary source. **[I]** = my inference or background knowledge, not re-checked today. No TypeSafe API calls were made. Builds on `21-trust-pipeline-llm.md`, which has the Jev facts and gap map, and `docs/GRILL-LOG.md`.

## Summary

1. **Yes, there is a better way, but it moves labeling rather than removing it.** "Judgments from Jev, decisions in code" splits the system into a **finite deterministic decision function** (exhaustively tested, provable in Lean) and **atomic judgments**, the only part needing empirical accuracy numbers. Atomic labels are at-a-glance yes/no, so they are much cheaper and more diagnostic than holistic labels [I].
2. **Behavioral tests replace much of the golden set for finding bugs, not for measuring accuracy.** CheckList MFT/INV/DIR tests, metamorphic relations (option permutation, distractors, reordering), minimal pairs and cross-question logic need expectations, not labels. They run as Gherkin under behavex against a record/replay cache. **Limit:** a consistently wrong model passes them all.
3. **Labels are still needed for per-question accuracy and calibration, and for one small holistic check that the decomposition means what an analyst means.** PPI, stratified/active sampling and conformal abstention stretch the budget: **demo ≈ 40 comments × 7 questions (≈60 min), reported with wide CIs; summer ≈ 300 atomic-labeled comments (20% double-labeled) plus 50 holistic.**
4. **The ABS-EE tape labels lagging facts for free** (transfer, modification, maturity default, workout code). Disagreement is a flagged row, never a correction. For the thesis field, disagreement with the tape *is the signal*, so the tape can only label it later, as an outcome.
5. **Every decision leaves a trace**: each atomic probability, its Y/N/U band, the rule fired, the evidence line, tape facts as of filing. Store it in `decision_trace`; show it in a "why" panel with the single answer that would flip the outcome.
6. **Division of labor:** Lean proves the truth table is total, monotone and abstains correctly; behavex checks behavioral relations and every code path; labels measure per-question accuracy and fit conformal sets; the tape cross-checks lagging facts now and early-warning outcomes 6–12 months later.

---

## 1. Decomposition as an evaluation strategy

**What changes.** Asked of one LLM, "Does this comment signal deterioration before the tape?" is a black box evaluable only end to end, with many labels per stratum. Decomposed, it becomes `decision = T(b(q₁(x)), …, b(q_k(x)), tape_facts)`: each `q_i` is a Jev Noul or Choice, `b` bands a probability into {Y, N, U} at 0.7/0.3 (TypeSafe's suggested defaults [V]), and `T` is pure code over a finite domain (3^k × tape states):

| Component | Nature | How it is assured |
|---|---|---|
| `T` (truth table) | Finite, deterministic | Exhaustive enumeration in pytest/behavex; Lean theorems for totality, unambiguity and monotonicity |
| `b` (banding) | Deterministic, parameterized | Unit tests; thresholds fit on labeled data or replaced by conformal sets (§3) |
| `q_i` (atomic judgments) | Stochastic, learned | Behavioral tests (§2) find bugs; labels (§3) and tape cross-checks (§4) measure accuracy |
| Decomposition validity: does `T∘q` mean what the analyst means? | Conceptual | A small holistic labeled set, plus outcome validation against the future tape (§4). **Lean cannot touch this** |

**What Lean adds, honestly.** For a finite table, "total and unambiguous" is already guaranteed if `T` is a single Lean `match` over an inductive `Band` type: the exhaustiveness checker rejects a missing case, and a function returns one value [I]. An exhaustive Python loop over 3^7 = 2,187 combinations gives the same assurance for the Python copy. Lean earns its place on **properties a reviewer cares about**:
- **Monotonicity:** flipping any adverse answer from N to Y never lowers severity.
- **Abstention soundness:** if no adverse question is Y and any is U, the result is `REVIEW`, never `NONE`.
- **Mitigation bound:** the improvement question can lower severity by at most one level.

These can be proved with `decide` over all `Band`⁷ inputs. Avoid `native_decide`, because the grill log rejects its axiom. Keep the Python `T` honest with a Lean-exported table and a differential test, the same pattern as Q4 in the grill log [I]. Kernel `decide` on 2,187 cases should be fine but is untested [I].

**What the TypeSafe docs say that applies** (fetched today from [llms.txt](https://docs.typesafe.ai/llms.txt)) [V]:

| Page | What applies here |
|---|---|
| [How to build](https://docs.typesafe.ai/concepts/how-to-build-with-system-one.md) | Decomposition is "probably the most important concept"; test thresholds "by plotting confidence against accuracy on your data" |
| [Fan-out](https://docs.typesafe.ai/patterns/fan-out.md) | Ask all questions, conditional ones included, in one request; code ignores the irrelevant. One call per comment yields the whole trace |
| [Composite scoring](https://docs.typesafe.ai/patterns/composite-scoring.md), [intent](https://docs.typesafe.ai/patterns/intent-routing.md) / [confidence routing](https://docs.typesafe.ai/patterns/confidence-routing.md) | Weights and per-action thresholds live in code, set by stakes: accept, review, escalate |
| [Citation check](https://docs.typesafe.ai/cookbooks/citation_check.md) | String match catches `fabricated` quotes, then a Choice gives `verified`/`unsupported`/`contradicted`; below 0.8 "a human confirms the verdict". Our grounding layer |
| [SDE cascade](https://docs.typesafe.ai/cookbooks/sde_cascade.md) | Per-field Nouls where `true` = "something is wrong", `max` gate at 0.7; the holistic "escalate?" head is not used for gating |
| [Pre-parsed values](https://docs.typesafe.ai/cookbooks/pre_parsed_value_extraction_cookbook.md) | Regex over-finds, Jev picks, so it "cannot invent a value or transpose a digit": DSCR, occupancy, dates |
| [Line-by-line search](https://docs.typesafe.ai/cookbooks/semantic_find.md) | Choice over line IDs plus an "answer exists" Noul: our evidence line, since Jev returns no spans |
| [Self-consistency](https://docs.typesafe.ai/cookbooks/consistency_choice_cookbook.md) | Plurality labels repeat 90.8%; with `uncertain` below 0.60, agreement 99.2% at 74.2% automatic |
| [Jaggedness, jev-1.13](https://docs.typesafe.ai/model-jaggedness/jev-1.13.md) | Literal reading, no math/counting, dates as text, indirection, distractors, adversarial text, contradictory criteria, **option-order bias** ("reorder the options to double check"). Each becomes a §2 test |

The cookbooks ship a `JsonCache` "so re-running replays the published numbers instead of calling the API": TypeSafe's own practice is record/replay.

**Honest limits of decomposition** [I]:
- **Errors in the `q_i` are correlated.** They share a state and a model, so you cannot multiply per-question accuracies to get decision accuracy. Estimate decision accuracy directly on a small holistic set.
- **The decomposition itself can be wrong**, for example by missing a deterioration channel such as an insurance or casualty event. That shows up as low recall on holistic labels, not in any unit test.

## 2. Behavioral testing without labels

| Technique | Source | CMBS instance | Needs labels? |
|---|---|---|---|
| **Minimum functionality test (MFT)**: simple templated cases with known answers | CheckList, [Ribeiro et al., ACL 2020](https://arxiv.org/abs/2005.04118) [V] | "Occupancy fell from 91% to 62%" ⇒ `occupancy_or_tenant_loss` = Y | Template-level only; one author writes 20 at once |
| **Invariance (INV)** | CheckList; metamorphic testing, Xie et al. 2011 (JSS); Chen et al. 2018 (ACM CSUR) [I] | Permuting options, reordering sentences, appending a neutral boilerplate sentence, or swapping the property name leaves the answer unchanged | No |
| **Directional expectation (DIR)** | CheckList | Appending "The borrower has stated it will no longer fund operating shortfalls" must not *lower* P(`borrower_support_withdrawn`) | No |
| **Minimal pairs / contrast sets** | [Gardner et al. 2020](https://arxiv.org/abs/2004.02709) [V]; Kaushik et al. ICLR 2020 [I] | "has requested a forbearance" vs "has not requested a forbearance" ⇒ answer flips | Pair-level expectation |
| **Option-order bias** | [Zheng et al. 2023](https://arxiv.org/abs/2309.03882) [V]; the Jev jaggedness page [V] | Cyclic rotations of the `workout_strategy` options keep the argmax | No |
| **Cross-question consistency** | [Elazar et al. 2021](https://arxiv.org/abs/2102.01017) [V] | If `returned_to_master` = Y, a stated workout must be N/A; `currently_with_special_servicer` and `returned_to_master` should not both be Y | No |

**What these tests prove, and what they don't.** CheckList found many failures in commercial models with high held-out accuracy [V, abstract]. Behavioral tests are a **bug finder and regression net**: necessary conditions, not sufficient, and a pass rate is not an accuracy estimate. Their value here is that each targets a *documented* Jev weakness, and one reviewed perturbation template covers hundreds of real comments [I].

### Example scenarios (behave/behavex Gherkin)

```gherkin
@layer:model @mft @replay
Feature: Atomic judgments on special-servicing comments

  Scenario: Negation minimal pair flips borrower relief request
    Given the comment "The borrower has requested a 12-month forbearance and maturity extension."
    And its minimal pair "The borrower has not requested a forbearance or maturity extension."
    When Jev answers "borrower_relief_request" for both
    Then the first answer band is "Y"
    And the second answer band is "N"

  @inv @option_order
  Scenario Outline: Workout strategy is invariant to option order
    Given the fixture comment "SS-2026-09-BANK18-loan57"
    When Jev answers "workout_strategy" with options in <order> order
    Then the top option equals the top option under canonical order
    And no option probability differs from canonical by more than 0.10
    Examples:
      | order     |
      | canonical |
      | reversed  |
      | rotate_3  |

  @dir @distractor
  Scenario: Neutral boilerplate does not move deterioration judgments
    Given the fixture comment "SS-2026-06-BANK13-Ditson"
    When I append "The Special Servicer will continue to monitor the loan."
    Then every Noul band is unchanged
    And no Noul probability moves by more than 0.10

  @layer:logic @consistency @xcheck
  Scenario: A comment that says the loan returned to the master servicer cannot carry an active workout
    Given recorded Jev answers for every fixture comment
    When "returned_to_master" is "Y"
    Then the decision "workout_status" is "NOT_APPLICABLE"
    And if the tape shows a transfer date after the return, an xcheck flag "ss_status_conflict" exists
```

The tolerances (0.10) are bounds, never exact probabilities. TypeSafe lists "tests that assert an exact probability" as a yellow flag [V].

### Making runs deterministic

- **Cache key:** `request_hash = sha256(canonical_json({model: "jev-1.14.0", sdk_version, state, questions}))`. The questions are stored **with option order**, so that a permutation test is its own cassette. Store the raw response in a DuckDB `llm_calls` table, mirrored to `tests/cassettes/*.json` for CI [I].
- **Modes, set through env var `JEV_MODE`.** An env var is used instead of `-D` because behavex does not pass through every behave argument (per the grill log):

  | Mode | When it runs | Behavior |
  |---|---|---|
  | `replay` | Default; every commit | **A cache miss is an error, not a live call.** Fully deterministic, no network, no key |
  | `record` | Run by a human when questions, state builders, fixtures or the pinned model change; also before the demo | Live calls write new cassettes, and the diff gets reviewed. Needs your approval and key |
  | `live` | Nightly or weekly, tagged `@live`; optional | Re-runs the behavioral suite fresh. Because re-runs vary by about 0.01 ([cookbook](https://docs.typesafe.ai/cookbooks/consistency_choice_cookbook.md)) [V], assertions use bands and tolerances |

  **Ban `@AUTORETRY` on `@layer:model`**, because it hides nondeterminism. behavex 4.6.8 (released 2026-10-21) supports `@AUTORETRY`, `@MUTE` and tag-expression filtering [V, [PyPI](https://pypi.org/project/behavex/), [README](https://github.com/hrcorval/behavex)].
- **Code-only scenarios (`@layer:logic`) never touch Jev.** They feed synthetic band vectors into `T`. That covers all 2,187 combinations in one parametrized step, and those tests are deterministic by construction.
- **Caveat:** a replayed model test only proves the *recorded* behavior still flows through the code. It re-tests the model only when you re-record.

## 3. Label-efficient evaluation for what still needs labels

**What to label.** Label each **atomic question** Y, N or "can't tell", with its evidence line, plus one **holistic** label ("does this comment signal deterioration?") to test decomposition validity. A label app shows one comment and seven buttons; budget about 90 seconds per comment [I].

**Stratified and active sampling.** Stratify on servicer, Jev band (Y/N/U) and tape flag; oversample the rare Y and U strata so recall is measurable, then **reweight by stratum size** (Horvitz–Thompson) when reporting population numbers [I]. Active testing ([Kossen et al. 2021](https://arxiv.org/abs/2103.05331)) and active statistical inference ([Zrnic & Candès 2024](https://arxiv.org/abs/2403.03208)) [V] spend labels where the model is uncertain while staying unbiased through the same weighting.

**Prediction-powered inference** ([Angelopoulos et al. 2023](https://arxiv.org/abs/2301.09633); [PPI++](https://arxiv.org/abs/2311.01453); [stratified PPI](https://arxiv.org/abs/2406.04291)) [V]:
- **Prevalence** ("what share of 2026 SS comments report occupancy loss?"): Jev on all N comments, humans on n. `θ̂ = mean_N f(x) − mean_n (f(x) − y)`, with a CI from the rectifier's variance. Valid whatever Jev's accuracy, narrower the more accurate it is; PPI++'s λ makes it never worse than labels alone. Package `ppi-python` 0.2.3 [V].
- **Jev's own accuracy** needs a *second* predictor of correctness: agreement with a Claude backend, or with the tape where §4 applies. That is the AutoEval setting ([Boyeau et al. 2024](https://arxiv.org/abs/2403.07008)) [V].
- **Limit:** gains are large only when the proxy tracks the truth closely; at n ≈ 40 expect modest gains [I].

**Conformal prediction** ([Angelopoulos & Bates](https://arxiv.org/abs/2107.07511); [conformal risk control](https://arxiv.org/abs/2208.02814)) [V]. On a held-out calibration split, score `s = 1 − p(true label)`, take the `⌈(n+1)(1−α)⌉/n` quantile `q̂`, and keep every label with `p ≥ 1 − q̂`. For a Noul the set is {Y}, {N} or {Y, N}: **{Y, N} is the "unsettled" band, with data-fit edges replacing 0.3/0.7.** For a Choice, set size > 1 routes to review; use Mondrian (per-class) conformal for rare classes [I]. **Limits:** the guarantee is marginal and assumes exchangeability (a new servicer's style breaks it); small-n coverage is itself noisy; the calibration split cannot double as the evaluation split.

**Report** a per-question confusion matrix with **Wilson 95% intervals** on precision and recall, a reliability plot, and the "can't tell" count as a ceiling on achievable accuracy [I].

### Recommended label budgets

| | Demo (about 1.5 hours total) | Full build |
|---|---|---|
| Atomic-labeled comments | **40** (≈60 min), stratified: about 12 Jev-Y, 12 Jev-U, 16 Jev-N, across at least 3 servicers | **300**, stratified, plus about 50/month active top-ups |
| Holistic labels | The same 40 (adds about 10 s each) | 50 fresh, held out |
| Agreement | Re-label 10 blind after a break (intra-rater, ≈10 min) | 20% double-labeled by a second person; Cohen's κ |
| Remaining time | ≈20 min adjudicating tape disagreements (§4) | — |
| What you can claim | At 90% observed accuracy, the Wilson 95% CI is about **[77%, 96%]** per question. **Show the interval, call it a pilot.** Conformal with α = 0.2 on 20 calibration items is a demo of the mechanism, not a guarantee | ±3.5 pp at 90%. Enough for per-servicer conformal sets and a credible PPI prevalence panel |

**Why 40.** It fits the time, and it is the smallest n where the interval excludes "coin flip" for a decent question [I]. **Why 300.** It matches the 300-item golden set in `21`, so this is not new spending, but here it is labeled at the atomic level. Rare positives still need oversampling: at 5% prevalence, a random 300 gives only about 15 positives [I].

## 4. Weak supervision from the tape

Snorkel-style labeling functions ([Ratner et al. 2017](https://arxiv.org/abs/1711.10160)) [V] built from ABS-EE fields (names from `11-data-sources.md` §1.4) [V for the names]:

| Text judgment | Tape labeling function, as of the filing date | Type |
|---|---|---|
| `transferred_to_ss` | `mostRecentSpecialServicerTransferDate` set, and no later `mostRecentMasterServicerReturnDate` | Lagging fact: a usable label |
| `modification_reported` | `modifiedIndicator`, `reportPeriodModificationIndicator`, `modificationCode` (10 = forbearance) | Lagging fact |
| `maturity_default` | `paymentStatusLoanCode` = 5 | Lagging fact |
| `workout_strategy` (Choice) | `workoutStrategyCode` (2, 7, 8, 11, 13, 98) via a code mapping | Lagging fact; the mapping is many-to-one |
| `occupancy_loss` / `income_decline` | `mostRecentPhysicalOccupancyPercentage`, `mostRecentNetOperatingIncomeAmount` vs. the prior period | **Weak:** these fields go stale (Ditson's occupancy stayed frozen at 0.28 for 14 months) |
| **`early_warning` (the thesis)** | **None at filing time.** Delinquency or transfer 6–12 months *later* is the outcome label | Outcome validation |

**Disagreement policy.** Write an `xcheck_flags` row (both values, as-of dates, reason code); never overwrite. Disagreements are the **highest-value items to label**, so they go first in the demo's adjudication minutes. On lagging facts, the agreement rate (with CI) is a free accuracy *proxy*, biased by tape errors and lags, and doubles as the AutoEval/PPI proxy in §3 [I]. **For deterioration fields, "text adverse, tape clean" is the product, not an error**: validate it by outcome (does `EARLY_WARNING` raise the 12-month hazard of delinquency or transfer?), with point-in-time joins on `available_at` (`21` §2 #9). **Caveat:** SS comments are written at or after transfer (`11` §2), so the earliest-warning text is more likely in watchlist comments, which are public only for Freddie K-deals [I].

## 5. Explainability: the trace

**`decision_trace` (DuckDB, one row per decision × question):** `accession, loan_id, comment_hash, available_at` · `decision_name, decision_version` (git SHA of questions module + truth table) · `request_hash, model_id` (joins `llm_calls`) · `question_id, probs_json, band, thresholds_json, conformal_set` · `evidence_line_id, evidence_text` (semantic-find Choice, exact-substring checked) · `rule_id, outcome` · `tape_facts_json` (as of `available_at`), `xcheck_flag_ids`. It is a pure function of cached responses, so it is reproducible byte for byte [I].

**The "why" panel** (per loan-month):
1. Outcome badge and the fired rule in plain English ("R1: borrower support withdrawn ⇒ STRONG").
2. One bar per atomic question: probability, 0.3/0.7 lines, Y/N/U chip, evidence line highlighted in the comment.
3. **"What would change this"**: the minimal band flips that change the outcome, cheap because `T` is total and finite.
4. Tape facts as of the filing date, with xcheck flags.
5. Links to the Gherkin scenarios and Lean theorem covering the fired rule, and the question's measured P/R with CIs. This turns "trust me" into "here is the evidence for each step" [I].

## 6. Recommended design for this project

### Decision A: `early_deterioration_signal` (one Jev request per comment, speculative fan-out)

| ID | Type | Question, about `comment.text` | Weakness avoided |
|---|---|---|---|
| q1 `occupancy_or_tenant_loss` | Noul | Does the comment say the property has lost tenants or occupancy? | Numbers are pre-parsed by regex; any % comparison happens in code |
| q2 `income_decline` | Noul | Does the comment say property income, NOI or cash flow has fallen? | — |
| q3 `borrower_support_withdrawn` | Noul | Does the comment say the borrower will not or cannot fund shortfalls or debt service? | Positive phrasing, no double negative |
| q4 `borrower_relief_request` | Noul | Does the comment say the borrower asked for a modification, forbearance or extension? | — |
| q5 `upcoming_major_tenant_loss` | Noul | Does the comment name a large tenant that is leaving or whose lease is about to end? | Dates are compared in code |
| q6 `refinance_difficulty` | Noul | Does the comment say the borrower is unable to refinance or pay off at maturity? | — |
| q7 `improvement_or_cure` | Noul | Does the comment say performance has improved or the default has been cured? | Counter-evidence, asked separately |
| q8 `evidence_line` | Choice over line IDs | Which line best supports the most adverse statement? With a companion Noul. | The semantic-find pattern |

**Truth table** (Y means p ≥ 0.7, N means p ≤ 0.3, U otherwise):
- `ops = q1 ∨ q2`
- `borrower = q3 ∨ q4`
- `forward = q5 ∨ q6`

| Rule | Condition, evaluated in order | Signal |
|---|---|---|
| R1 | q3 = Y, or at least 2 of {ops, borrower, forward} have a Y | STRONG |
| R2 | Exactly 1 group has a Y | MODERATE |
| R3 | No Y anywhere, and any of q1–q6 is U | REVIEW |
| R4 | All of q1–q6 are N | NONE |
| F | Under R1/R2, any remaining U among q1–q6 adds a `partial_unsettled` flag (shown, not escalated) | Flag |
| M | q7 = Y lowers STRONG→MODERATE and MODERATE→NONE. q7 = U adds a `mitigation_unsettled` flag | Adjustment |

Then a second step in code, using the tape as of `available_at`. `tape_adverse` means status ∈ {1, 2, 3, 5}, or currently in SS, or a modification flag:

| Signal | Tape | Outcome |
|---|---|---|
| STRONG or MODERATE | Not adverse | **EARLY_WARNING** |
| STRONG or MODERATE | Adverse | CONFIRMS_TAPE |
| NONE | Adverse | TEXT_SILENT (flagged) |
| NONE | Not adverse | QUIET |
| REVIEW | Any | REVIEW_QUEUE |

**Lean theorems:**
1. `T` is monotone in q1–q6.
2. Any U with no Y gives REVIEW.
3. M lowers the signal by at most 1 level.
4. EARLY_WARNING implies `¬tape_adverse`.

### Decision B: `workout_status_consistency` (short)

Nouls `currently_with_special_servicer` and `returned_to_master`, plus a Choice `workout_strategy` over {foreclosure, modification, note_sale, discounted_payoff, reo_sale, return_to_master} with a companion Noul `workout_strategy_stated`. Code maps the Choice to `workoutStrategyCode` classes and emits `NOT_APPLICABLE`, `CONSISTENT` or an `xcheck_flag`. Because the tape labels it, B is mainly Jev's **calibration probe** on this corpus.

### Who checks what

| Artifact | Lean (proved) | behavex `@layer:logic` (exhaustive, deterministic) | behavex `@layer:model` (replay; live on re-record) | Labels (measured) | Tape (cross-checked) |
|---|---|---|---|---|---|
| Truth tables A and B | Totality, monotonicity, abstention, mitigation bound | All 3^k combinations; Lean-to-Python differential | — | — | — |
| Banding / conformal | A non-empty set always contains the argmax [I] | Boundary cases at 0.3/0.7 | — | Thresholds fit on the calibration split | — |
| q1–q7, workout Choice | — | — | MFT, INV (order, distractor, reorder), DIR, minimal pairs, cross-question rules | Per-question P/R with Wilson CIs, reliability, PPI prevalence | q4 vs mod flags; SS and workout vs the tape |
| Evidence line | — | Exact-substring check | Citation-check verdict | Spot check within the 40 | — |
| Decision A end to end | — | Golden traces replayed | — | 40 → 50 holistic labels (decomposition validity) | **Outcome:** 6–12-month delinquency or transfer hazard |

**Net effect** [I]: without decomposition, an end-to-end golden set needs hundreds of holistic labels *per stratum* and still can't say *why* a case failed. With it, the labeled surface shrinks to seven at-a-glance yes/no questions plus a 50-item validity check; everything else is proved, enumerated or behavior-tested, and the thesis claim is checked by the tape's future.

**What remains unprotected:** consistent-but-wrong judgments outside the labeled sample; servicer drift, which breaks exchangeability; and a decomposition that omits a deterioration channel (e.g. casualty or litigation). Monthly spot-labels and the outcome backtest are the only guards.
