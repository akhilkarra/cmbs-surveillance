# Lead-lag study v2: results

Run once, as fixed in PLAN-v2.md. Seed 20261102; 2000 deal-cluster bootstrap resamples.

## Data

- Deals loaded: 109 (vintages {'2017': 36, '2018': 31, '2019': 42}); tape 2017-03-01 to 2026-10-01, comments 2019-01-01 to 2026-10-01.
- Loan-months 455,314; comments 16,710; Jev extractions 16,710.
- Deals that failed to load: 19 (18 shared filing already registered (ConflictingFiling), 1 EDGAR search error (KeyError 'hits')).

## Cleaning

- Wrong-deal tapes (modal-name rule): 44 deal-months dropped (1,987 loan-months, 37 deals); 2 are a deal's first month. Versus v1: 2 newly dropped, 2 dropped by v1 only.
- Placeholder comments dropped: 1465 of 16,710.
- Comment loan IDs: 14,516 joined directly, 728 joined by stripping a note letter, 1 still unjoined (excluded).

## Outcomes (episodes with at least one comment in the window)

- Episodes: 973; with a comment: 868.
- cured_returned 449, loss 101, open 278, other_exit 40.
- Loss route: realized-loss rise only 55, liquidation exit only 21, both 25.
- Cured/returned on code 8 or 9 with no return date: 32. Open: 113 with >= 24 months follow-up, 165 with < 24; 25 of the open were cut by a later transfer.

## Analysis A: lead-lag (months of publication; lead = tape month minus text month)

### Text by keywords

| Family | Both | Text first | Same | Tape first | Median lead (IQR) | Text-first share [95% CI] | Text only | Tape only |
|---|---|---|---|---|---|---|---|---|
| liquidation | 291 | 110 | 86 | 95 | +0 (-1, +2) | 38% [32%, 43%] | 131 | 38 |
| return | 215 | 101 | 72 | 42 | +0 (+0, +2) | 47% [38%, 55%] | 14 | 272 |
| workout_decided | 199 | 85 | 46 | 68 | +0 (-2, +3) | 43% [32%, 55%] | 73 | 161 |
| borrower_request | 164 | 91 | 46 | 27 | +1 (+0, +4) | 55% [47%, 63%] | 179 | 101 |

### Text by Jev auto-accepted workout strategy / borrower request

| Family | Both | Text first | Same | Tape first | Median lead (IQR) | Text-first share [95% CI] | Text only | Tape only |
|---|---|---|---|---|---|---|---|---|
| liquidation | 236 | 72 | 63 | 101 | +0 (-3, +1) | 31% [25%, 37%] | 70 | 93 |
| return | 106 | 60 | 28 | 18 | +1 (+0, +4) | 57% [46%, 66%] | 9 | 381 |
| workout_decided | 155 | 52 | 36 | 67 | +0 (-4, +1) | 34% [20%, 47%] | 22 | 205 |
| borrower_request | 170 | 99 | 44 | 27 | +1 (+0, +4) | 58% [50%, 66%] | 258 | 95 |

### Keyword precision (liquidation)

50 of 422 first liquidation hits (12%, Wilson [9%, 15%]) sit in a sentence with a hedge: 'to avoid' 1, 'moratorium' 12, 'if necessary' 12, 'potential' 22, 'may' 4.

## Analysis B: prediction of loss at landmarks

Six primary comparisons: at 3 landmarks, tape + keywords and tape + Jev, each against tape alone. None of the six has a 95% CI wholly above zero. One, tape + Jev at landmark 0, is wholly below zero. A base-rate forecast has a Brier score of 0.143, 0.160 and 0.182 at landmarks 0, 3 and 6. The tape model at landmark 0 (0.146) does no better than that.

### Primary (open episodes dropped)

| Landmark | n (losses, deals) | Model | AUC [95% CI] | dAUC vs tape [95% CI] | Brier |
|---|---|---|---|---|---|
| 0 | 532 (92, 106) | tape | 0.575 [0.510, 0.645] |  | 0.146 |
| 0 | 532 (92, 106) | tape_keywords | 0.560 [0.494, 0.629] | -0.015 [-0.058, +0.026] | 0.144 |
| 0 | 532 (92, 106) | tape_jev | 0.511 [0.446, 0.586] | -0.064 [-0.098, -0.029] | 0.147 |
| 3 | 464 (93, 103) | tape | 0.716 [0.654, 0.774] |  | 0.142 |
| 3 | 464 (93, 103) | tape_keywords | 0.741 [0.666, 0.805] | +0.025 [-0.019, +0.069] | 0.137 |
| 3 | 464 (93, 103) | tape_jev | 0.709 [0.640, 0.774] | -0.007 [-0.038, +0.022] | 0.138 |
| 6 | 381 (91, 99) | tape | 0.670 [0.601, 0.739] |  | 0.169 |
| 6 | 381 (91, 99) | tape_keywords | 0.684 [0.621, 0.743] | +0.014 [-0.024, +0.056] | 0.165 |
| 6 | 381 (91, 99) | tape_jev | 0.649 [0.583, 0.715] | -0.021 [-0.058, +0.022] | 0.175 |

Sample exclusions: landmark 0: no_comment_by_landmark 75, open_dropped_ge_24 97, open_dropped_lt_24 143, resolved_by_landmark 21; landmark 3: no_comment_by_landmark 18, open_dropped_ge_24 105, open_dropped_lt_24 112, resolved_by_landmark 122, window_ended_before_landmark 47; landmark 6: no_comment_by_landmark 9, open_dropped_ge_24 109, open_dropped_lt_24 91, resolved_by_landmark 208, window_ended_before_landmark 70.

### Sensitivity (open >= 24 months = not loss)

| Landmark | n (losses, deals) | Model | AUC [95% CI] | dAUC vs tape [95% CI] | Brier |
|---|---|---|---|---|---|
| 0 | 629 (92, 108) | tape | 0.617 [0.548, 0.684] |  | 0.124 |
| 0 | 629 (92, 108) | tape_keywords | 0.562 [0.485, 0.642] | -0.056 [-0.100, -0.013] | 0.126 |
| 0 | 629 (92, 108) | tape_jev | 0.533 [0.451, 0.614] | -0.085 [-0.124, -0.049] | 0.127 |
| 3 | 569 (93, 105) | tape | 0.700 [0.634, 0.763] |  | 0.126 |
| 3 | 569 (93, 105) | tape_keywords | 0.726 [0.652, 0.791] | +0.026 [-0.013, +0.070] | 0.124 |
| 3 | 569 (93, 105) | tape_jev | 0.688 [0.617, 0.753] | -0.011 [-0.039, +0.017] | 0.127 |
| 6 | 490 (91, 103) | tape | 0.662 [0.589, 0.726] |  | 0.147 |
| 6 | 490 (91, 103) | tape_keywords | 0.682 [0.613, 0.742] | +0.021 [-0.019, +0.063] | 0.144 |
| 6 | 490 (91, 103) | tape_jev | 0.640 [0.573, 0.706] | -0.021 [-0.070, +0.026] | 0.148 |

Sample exclusions: landmark 0: no_comment_by_landmark 75, open_dropped_lt_24 143, open_ge_24_as_not_loss 97, resolved_by_landmark 21; landmark 3: no_comment_by_landmark 18, open_dropped_lt_24 112, open_ge_24_as_not_loss 105, resolved_by_landmark 122, window_ended_before_landmark 47; landmark 6: no_comment_by_landmark 9, open_dropped_lt_24 91, open_ge_24_as_not_loss 109, resolved_by_landmark 208, window_ended_before_landmark 70.

## Analysis C: landmark 3, tape not yet coded liquidation

n = 389 (excluded 75 whose tape already coded liquidation).

| Split | Text names liquidation: loss / n (rate, Wilson 95%) | Text does not |
|---|---|---|
| Keywords (plan) | 20 / 54 (37%, [25%, 50%]) | 34 / 335 (10%, [7%, 14%]) |
| Jev (added, not in plan) | 10 / 26 (38%, [22%, 57%]) | 44 / 363 (12%, [9%, 16%]) |

## Sanity checks

- Window bug, exact v1 pipeline and v1 label: 107 losses with the bug; 91 with contiguous windows (same episodes); 91 counting episodes with a comment in the fixed window.
- Window bug, v2 cleaning: v1 label 97 (bug) -> 91 (fixed); v2 label 98 (bug) -> 101 (fixed).
- Hand check of 10 episodes (3 loss, 3 cured/returned, 2 other exit, 2 open, seeded draw; traces in `handcheck_v2.txt`). This was run before Analyses A-C, on the labels alone. All 10 labels agree with their tape rows:
  - UBS 2018-C11 loan 37: **loss**. Note sale (code 5); then a $2.57M realized loss, 41% of $6.25M, and a zero balance in Dec 2022.
  - BBCMS 2017-C1 loan 52: **loss**. Foreclosure, then DPO, then bankruptcy codes; a $269k loss (7.5%) with a zero balance in Feb 2023.
  - JPMCC 2017-JP5 loan 15: **loss**. REO (code 7) from Jun 2021; a $12.98M loss (42%) in Feb 2023.
  - MSC 2019-H7 loan 14: **cured/returned**. Code 8 and return date 2021-06-02.
  - MSC 2019-L2 loan 31: **cured/returned**. Code 8 and return date 2025-09-08.
  - Benchmark 2018-B4 loan 22: **cured/returned**. Code 9 in Mar 2021; return date 2021-04-15.
  - CD 2017-CD3 loan 19: **other exit**. DPO code, then code 11 (full payoff); the loan leaves the tape in Oct 2026 with no loss.
  - MSC 2018-H3 loan 20: **other exit**. Matured balloon; zero balance in Nov 2025, no loss.
  - BANK 2019-BNK20 loan 53: **open**. Foreclosure, then REO in Jul 2026, still on the tape.
  - COMM 2017-COR2 loan 23: **open**. Transferred Jul 2026.

  The check found one labelling bug, fixed before Analyses A-C ran. In the UBS case, a code 8 on zero-balance carry rows after the payoff counted as a "return". Return evidence now counts only up to the exit row. That moved 3 episodes from cured/returned to other exit.
- Window-bug check: confirmed. In the exact v1 pipeline with v1's label, making windows contiguous takes losses from **107 to 91**, as the review said.

## Deviations and readings of the plan

Each item below was fixed in code before any v2 result was seen, except item 9. That one was a labelling bug found by the hand check before Analyses A-C were first run. `analyze_v2.py` was then run once. One more deviation: the adversarial review's PM sentence is not in the study folder. So the sentence at the end is written fresh, in the review's terms, from these numbers.


1. Placeholder pattern: a comment is dropped only when its whole text (after removing the report's '(1) Resolution Strategy Code' legend, a leading date or loan number, and trailing punctuation) is a 'too lengthy' / 'not available' / 'no comment provided' notice. 'Special Servicer comments were not provided, as this loan returned to Master Servicing' is kept, because it states a fact (the return); cross-references ('refer to commentary on loan N') are kept, as the plan names only unavailable-comment notices.
2. Wrong-deal months: only the tape rows are dropped; the 10-D comments for those months are kept, because M-029 concerns the ABS-EE tape, not the distribution report. Loans present = loans with a property name that month.
3. A-note join: a single trailing letter is stripped, and only when the stripped ID is on the deal's tape and the original ID is not (judged over the deal's whole tape). Comments of several notes of one loan in one month are joined, identical texts once.
4. Exit from the tape: after liquidation the tape keeps most loans with a zero balance and the loss carried forward, so 'exits the tape' is read as the first month with an ending balance of zero, or, for a loan that disappears while the deal keeps reporting, the month after its last row.
5. Liquidation exit: the loan's latest non-blank workout code at or before its exit row is 5, 6, 7 or 10. (The count under the alternative reading, any such code in the window before the exit, is reported, not used.)
6. Loss by realized loss: a month's realized loss minus the last reported value before it (zero if none) is at least 2% of the balance at transfer. Balance at transfer = ending balance in the episode's first month, else the latest positive earlier ending balance, else that month's beginning balance.
7. Cured or returned: read as a return date on or after the transfer date, or workout code 8 or 9, anywhere in the window, with no loss; 'followed by the return' is read as description. The episodes labelled by code 8 or 9 alone are counted.
8. Loss takes precedence over a return in the same window (the plan's 'with no loss').
9. Found by the hand check and fixed before Analyses A-C were first run: return evidence (a return date or code 8/9) counts only up to the exit row, because the tape keeps paid-off loans as zero-balance carry rows that can show code 8 after the payoff.
10. Resolution date for landmark eligibility = the earliest of the loss month, the cure/return month and the exit month, so a loan cured before the landmark is out of that landmark's set even if it later takes a loss.
11. Open episodes: the plan's '24 months' is read as follow-up from the episode's first month to its window end. Primary: every open episode is dropped (none has a label); the counts with >= 24 and < 24 months are reported. Sensitivity: open episodes with >= 24 months are 'not loss'; those with < 24 stay dropped.
12. Workout decided, tape side: a modification code counts only when it is new in the window (blank/0 or different from the last code reported before the episode), so a code carried from an earlier modification is not read as this episode's decision.
13. Workout keywords: a sentence with modif/extension/extend counts as decided only if it has no request word (request, propos, seek, sought, asked, asking). Borrower request: a sentence with a request word and a workout term. The same borrower-request family is the 'workout-request' feature in Analysis B.
14. Jev families: liquidation = foreclosure, property in REO, deed in lieu, note sale, discounted payoff; return = pending return to master servicer, resolved; workout decided = modification, extension; borrower request = any auto-accepted borrower_request value. When several notes of one loan have answers in one month, each family is set if any note's answer is in it.
15. Analysis B features: a missing indicator is added for months since financials end (needed to impute it); payment-status rank uses v1's map with blank as current; property-type dummies cover every code on the tape (blank is the base); log length is of all comments up to the landmark; the tape row used is the landmark month's, else the latest before it in the window. Features are standardised inside each fit; inner CV scores by AUC.
16. Analysis C: 'tape has not coded liquidation' = no workout code 2, 5, 6, 7 or 10 in any window month up to the landmark; 'text names liquidation' = the liquidation keyword family in any comment up to the landmark. The Jev split is added as a labelled extra.

## Changes from v1, in brief

- **Return leads were mostly mechanical.** v1 had text first in 141 of 164 pairs (86%, median +2 months). Counting tape code 9 (pending return) and fixing the windows brings this to 47% [38%, 55%], with a median of 0.
- **Liquidation lead-lag barely moved.** Text first 38%, tape first 33%, median 0.
- **Prediction is now tested at landmarks with C tuned.** v1 found no gain at the first comment. Now none of the six comparisons gains.

## PM sentence

> After a loan reaches special servicing, the Computershare 10-D comment does not reliably beat the tape. In 291 episodes where both show a liquidation path, the comment names it first in 38% (95% CI 32% to 43%), the same month in 30%, and later in 33%. The median lead is 0 months, and 12% of first liquidation mentions are hedged.
>
> Borrower requests are the one place the text tends to come first. The text names a request a median 1 month before the tape codes a modification or extension: text first in 91 of 164 pairs (55%, 47% to 63%), tape first in 27 (16%). The tape has no field for requests, so its first workout code stands in for one.
>
> Adding comment keywords or Jev's answers to the tape features does not measurably improve loss prediction at 0, 3 or 6 months after transfer. ΔAUC runs from -0.064 to +0.025, and no 95% CI is wholly above zero. Jev's features make the landmark-0 model worse: -0.064 [-0.098, -0.029].
>
> At 3 months, among loans whose tape has not yet coded liquidation, those whose comments name it went on to a loss 37% of the time (20 of 54, 25% to 50%), against 10% (34 of 335, 7% to 14%). That gap is descriptive: it is not adjusted for the tape, and it does not survive into a better model.
>
> Scope: the post-transfer window only, Computershare's comments only, 2017-2019 conduit vintages (109 deals), and a period dominated by COVID-era transfers. There are 101 losses among 868 episodes with comments, so the intervals are wide.
