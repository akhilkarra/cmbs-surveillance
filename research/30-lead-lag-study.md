# Do servicer comments show problems before the loan tape?

**Bottom line.** Once a loan is in special servicing, the 10-D comment does not reliably name the liquidation path before the tape codes it. Borrower requests are the one place the text tends to come first. Adding the text to the tape has not been shown to improve loss prediction, and a small gain is not ruled out either.

## Why it matters

The surveillance panel reads servicer comments with a model because they might carry news the tape lacks. If they don't, the tape is the faster signal and the comments are context. This study tests that on public data, at scale.

## What was tested

| | |
|---|---|
| Deals | 109 public conduits, 2017–2019 vintages, all Computershare-administered (128 were attempted; 19 failed to load, see below) |
| Filings | Monthly ABS-EE tapes and 10-Ds, January 2019 to October 2026: 455,314 loan-months and 16,710 comments |
| Episodes | 973 stays in special servicing, 868 of them with a comment. Outcomes: 101 losses, 449 cured or returned, 40 other exits, 278 still open |
| Text, two ways | Transparent keyword families; and Jev's typed answers (`jev-1.14.0`, 11,551 calls, about $1.15) |

Public comments begin only after a loan transfers (MESS-REGISTER M-010). So the question is limited to what the comment adds *once a loan is in special servicing*. Whether text precedes the transfer itself cannot be tested on public data.

## Results

Lead-lag is measured in months of publication: the ABS-EE and 10-D for a month are filed together in 9,954 of 9,986 deal-months.

| Event | Comment first | Same month | Tape first | Median lead | Reading |
|---|---|---|---|---|---|
| Liquidation path (keywords) | 38% [32–43%] | 30% | 33% | 0 months | No reliable lead |
| Liquidation path (Jev's strategy) | 31% [25–37%] | 27% | 43% | 0 | If anything, the tape is earlier |
| Return to master servicer | 47% [38–55%] | 33% | 20% | 0 | v1's 2-month lead was mostly the tape's own "pending return" code |
| Borrower request, against the tape's first workout code | 55% [47–63%] | 28% | 16% | +1 month | The one real lead; but about half of requests never become a workout code |

Predicting a loss, scored on deals the model hadn't seen, with regularization tuned inside the cross-validation and intervals bootstrapped over deals:

| Months after transfer | Tape only, AUC | Change with keywords | Change with Jev |
|---|---|---|---|
| 0 | 0.575 | −0.015 [−0.058, +0.026] | −0.064 [−0.098, −0.029] |
| 3 | 0.716 | +0.025 [−0.019, +0.069] | −0.007 [−0.038, +0.022] |
| 6 | 0.670 | +0.014 [−0.024, +0.056] | −0.021 [−0.058, +0.022] |

- **At transfer, the tape model has no skill either** (Brier score 0.146 against 0.143 for the base rate). So the −0.064 for Jev is noise from adding sparse features, not evidence against the text.
- **The 3-month gain is fragile, not absent.** It moves between about 0 and +0.04 under equally defensible definitions, mostly over whether tape code 9 ("pending return") counts as resolved.
- **A descriptive gap that does not survive the model.** At 3 months, among loans the tape has not yet coded for liquidation, those whose comment names it went on to a loss 37% of the time (20 of 54). Those whose comment did not, 10% (34 of 335).

## How the answer was reached

The process mattered as much as the answer.

1. **Version 1 was fast and wrong in places.** It had an episode-window bug that mislabelled 16 of 107 losses. It reported a 2-month "return lead" that was the tape's own code. Its keyword "workout lead" was mostly borrower requests.
2. **An adversarial reviewer** found these errors, and also an apparent +3–5 AUC point gain at 3 months, after trying about eight specifications.
3. **A plan written before rerunning** (`lead_lag/PLAN.md`) fixed the definitions and named six primary comparisons. An independent analyst ran it once (`lead_lag/analyze.py`).
4. **The gain shrank to +0.025 with an interval spanning zero.** A second review confirmed the code and the direction, and found what is still open (below).

Writing the plan first is why this page reports "not shown", not the exploratory "+5 points".

## Limits, and what version 3 would fix

- **Scope:**
  - post-transfer only;
  - Computershare's printed comments only;
  - 2017–2019 vintages;
  - a period dominated by COVID-era transfers.
- **Pari passu loans are counted once per deal.** 166 episodes in 44 clusters share comments across deals, so about 101 losses are about 94 distinct ones, and the intervals are too narrow. Version 3 would group by linked-deal cluster.
- **Tape code 9 ("pending return") counts as resolved.** It should count as open until the return happens.
- **About 30 labels are wrong:**
  - liquidation exits with no reported loss;
  - pending returns labelled cured;
  - an REO loan labelled cured after a stray code 8.
- **Keyword hedges are undercounted.** The pattern count found 12% hedged or contingent, but hand reads put it near 27%.
- **Lead-lag pairs are uncapped.** Some pairs span 21 months; a 12-month cap is the fix.
- **Jev answers sent to review are dropped,** which delays Jev's first mention.

## Found along the way

- **Tapes filed under the wrong deal are common.** 44 deal-months across 37 deals carry another deal's loans under the right CIK and file name (M-029, M-032). The panel accepts them today, so an ingestion check is on the build-next list.
- **A joint SEC letter blocks discovery.** A single CORRESP letter filed for several trusts stops discovery of every trust after the first (M-033). That is why 18 deals didn't load.
- **On the hero loan, the tape led by years.** BANK 2018-BNK13's Ditson Building slid from DSCR 1.21x to 0.05x over 2021–2023 while paying current. It transferred in June 2026.

## Reproduce

```bash
DATABASE_URL=postgresql+psycopg://cmbs:cmbs@localhost:5432/cmbs_study uv run --with scikit-learn --with scipy --with 'psycopg[binary]' python research/lead_lag/analyze.py
```

The study database is built by:
1. `research/lead_lag/universe.py`, an EDGAR full-text search for comment-bearing 10-D filers;
2. `research/lead_lag/backfill.py`, which runs the panel's own refresh routine, deal by deal;
3. `research/lead_lag/extract.py`, which reads comments with Jev, capped at 25,000 calls.

The results are in `lead_lag/results.json` and `lead_lag/results.md`.
