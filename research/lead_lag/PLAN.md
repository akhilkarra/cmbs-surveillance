# Lead-lag study, version 2: analysis plan (written before any v2 result)

**Fixed on 2026-11-02, before the v2 analysis was run.** Every model listed here is run exactly once and reported, whatever it shows. Anything run beyond this list is labelled exploratory.

## Question

Once a CMBS loan is in special servicing, does the servicer's 10-D comment tell you something the loan tape does not yet show?

Public comments start only after the transfer (MESS-REGISTER M-010). So this study cannot test whether text precedes the transfer itself, and it does not claim to.

## Data

The study database `cmbs_study`:
- **Deals:** the 109 deals that loaded. All are 2017–2019 vintage conduits, all administered by Computershare, with ABS-EE and 10-D filings from January 2019 to October 2026.
- **Extraction:** Jev `jev-1.14.0`, prompt version 3, settings version 9.

The 19 deals that failed to load are reported with their reasons: a shared CORRESP filing, and one search error.

## Cleaning, each item reported as a count

- **Wrong-deal tapes (M-029).** Drop any deal-month where more than half of the loans present carry a property name different from the name each loan carries most often across the deal's whole history. Report the months dropped.
- **Placeholder comments.** Drop comments that say comments are not available, matched by a stated pattern.
- **A-note IDs.** Join comment loan IDs such as "5A" to their tape loan by stripping a trailing note letter, when that letter's loan is not itself on the tape. Report the comments still unjoined.

## Episodes

- An episode is one loan's stay in special servicing, starting at a transfer date's first month.
- It ends the month before the next transfer date's first month, or at the deal's last loaded month.
- Comments are limited to the episode's window.

## Outcomes, fixed at the episode's end and observed as of October 2026

- **loss:** within the window, either:
  - the realized loss rises by at least 2% of the loan's balance at transfer; the first increase counts, not a carried-forward amount; or
  - the loan exits the tape after the tape coded REO (7), a discounted payoff (6), a note sale (5) or a deed in lieu (10).
- **cured or returned:** a master-servicer return date on or after the transfer, or workout code 8 or 9, followed by the return, with no loss.
- **other exit:** a payoff with no loss.
- **open:** none of the above by the deal's last month.

## Analysis A: lead-lag, measured in months of publication

Each family is measured two ways: text by keywords, and text by Jev's auto-accepted workout strategy.

| Family | Text says | Tape codes |
|---|---|---|
| Liquidation path | Foreclosure, receiver, REO, deed in lieu, note sale or DPO (stated keyword patterns) | Workout code 2, 5, 6, 7 or 10 |
| Return | Returned, or pending return | Workout code 8 or 9, or a return date on or after the transfer |
| Workout decided | Modification or extension chosen. Keywords count only when not a request; Jev must say "modification" or "extension" | Workout code 1 or 4, or a modification code |
| Borrower request (reported separately, because the tape has no field for it) | A request | Time to the first workout code 1 or 4 |

For each family, report:
- the episodes where both occur, with text first, same month and tape first;
- the median lead with its interquartile range;
- text only, and tape only;
- a confidence interval on the share of text-first pairs, bootstrapped by deal.

Report the keyword precision too: the share of first liquidation hits that are negated or contingent, using the patterns "to avoid", "moratorium", "if necessary", "potential" and "may".

## Analysis B: prediction at landmarks

- **Landmarks:** 0, 3 and 6 months after the transfer's first month.
- **Sample:** episodes still unresolved at the landmark, with a comment by then.
- **Target:** loss, as defined above, by the episode's end, against cured, returned or other exit.
  - Open episodes: those that pass 24 months unresolved are dropped from that landmark's set, and the count is reported.
  - A sensitivity run treats them as "not loss".
- **Features:**
  - **Tape, as of the landmark month:** DSCR and whether it is missing; occupancy and whether it is missing; months since the financials end; payment-status rank; log balance; months since transfer; whether the workout code is in the liquidation set; whether it is in the workout set; whether the loan is modified; property type dummies.
  - **Text by keywords**, from comments up to the landmark: the liquidation, workout-request, bankruptcy, COVID, cash-trap, vacancy and litigation families, plus log length.
  - **Text by Jev**, its latest auto-accepted answer up to the landmark: the workout-strategy family, whether a borrower request is stated, and the transfer reason.
- **Models:**
  - L2 logistic regression, with C chosen inside each training fold by an inner 5-fold grouped CV over {0.01, 0.1, 1}.
  - The outer CV is 5-fold grouped by deal.
  - Three models: tape; tape + keywords; tape + Jev.
- **Reporting:**
  - AUC for each model, and the AUC change against tape, with a 95% CI from 2,000 deal-cluster bootstrap resamples of the out-of-fold scores.
  - Also the Brier score.
- **Count:** 3 landmarks × 2 text models = 6 primary comparisons, stated as such. No other model is primary.

## Analysis C: descriptive

At the 3-month landmark, among loans whose tape has not coded liquidation, the loss rate when the text names liquidation against when it does not. Give counts and a Wilson interval for each.

## What will be said

The result is stated in the PM sentence of the adversarial review, updated with these numbers. The scope limits go with it: the post-transfer window only; Computershare's comments only; the 2017–2019 vintages; the period dominated by COVID-era transfers.
