# Skills under pressure: a practitioner's session

**Date:** 2026-11-01.

**Setup.** Two agents, with the lead relaying every message between them.
- **The practitioner:** "pat", a PM with a real goal: brief the risk committee tomorrow on BANK 2018-BNK13's specially serviced loans and recommend escalation or not. It saw only the assistant's replies, never the code.
- **The assistant:** a Claude Code session in the repo, limited to `/cmbs-refresh` and `/cmbs-ask` and their rules. It listed every command it ran.

There were four exchanges, ending with the practitioner's verdict.

## Verdict, in the practitioner's words

> "As a cited fact-checker on a loan I already know is in trouble, yes. As my surveillance
> screen, no."

The practitioner trusted every fact it was given: each carried an accession number, tape field or quoted sentence, and tape-versus-text differences were shown side by side. It did not trust coverage: "only loan 11 among 1–15" is not "only loan 11 in the deal".

## What held under pressure

- Every fact was cited, with no estimates. Tape DSCR 0.0348x was shown next to comment DSCR 0.03x.
- **Refusals that were right:**
  - The write ("flag loan 11 for watchlist under my name") was refused as read-only.
  - "Give me the unreviewed sentence" was refused under rule 4.
  - An open-ended loan sweep was refused as unable to prove completeness.
- **The judgement call was fair:** "nothing the panel holds argues against it", followed by the list of missing facts that could change the call.
- **Command discipline:** the assistant ran only `cmbs refresh deal`, `cmbs ask facts` and `cmbs ask question`.

## What failed, ranked

| # | Finding | Kind | Evidence |
|---|---|---|---|
| 1 | **The assistant invented a product feature.** It said twice that "the dashboard's book view shows every loan in the deal and lets you filter by special servicing". The book view lists open alerts only, one row per alert, and has no filters (`web/src/bookGrid.ts`). | Trust: the "only facts" rule covers loans, not the product | Turn 1 and turn 3 replies |
| 2 | **No deal-wide question in the skills.** "Which loans are with the special servicer?" can't be answered: `ask` takes one loan. The API's `GET /deals/{deal}/loans` does list a deal's loans, but neither skill can reach it. | Missing capability in the skill | The `ask question` without `--loan` asked for a loan |
| 3 | **No "in special servicing now" status, and the return date is hidden.** Loans 12, 13 and 15 show an old transfer date plus "resolved". The panel holds their master-servicer return dates (2024-05-24, 2020-07-31, 2021-02-26), but `ask facts` leaves that field out, so the assistant could not say they had returned. | Field left out of `ask`, no derived state | Turn 2; `silver.loan_period` |
| 4 | **Paid-off loans read as data gaps.** Loans 2, 3 and 10 have a zero balance: since July 2026, September 2024 and June 2023 respectively. They left the pool. `/cmbs-ask` said "the panel holds only the property name" and could not say why. | Wrong framing of a real fact | `silver.loan_period` for BNK13 |
| 5 | **No history before August 2026** in the demo panel at the time of the session: no trend, no securitization baseline, no "as of May". Only August–October 2026 were loaded. | Data loaded, not a design limit | The `--as-of 2026-05-31` call exits 1 |
| 6 | **No advances, ARA, ASER, appraisal or watchlist data.** For a delinquent loan these are the numbers that size the loss. | Missing data: the 10-D sections and tape fields aren't loaded | Turn 3 |
| 7 | **Rule 4 blocks even the raw sentence.** With the workout strategy awaiting review, the PM can't see the servicer's own words, even labelled as unreviewed, the night before committee. | Design decision for the reviewer | Turn 2 |
| 8 | **A PM can't refresh.** The refusal "pm may not run refreshes and backfills" doesn't say who can. | The role design is deliberate; the message should name the roles that can | Turn 1 |
| 9 | **Tape codes aren't decoded.** The property type comes back as "OF", not "office". | Small presentation fix | Turn 1 |

## Proposed changes (each needs the reviewer's yes; features go through a draft)

1. **`/cmbs-ask` rule:** name only product features the skill lists (`/cmbs-refresh`, the review queue, alert actions, the loan view). Never describe a screen's abilities. A skill-file change.
2. **New feature, deal-wide questions:** list a deal's loans with their special-servicing state as of a date. The state is derived from the tape: transferred and not returned, returned, or paid off. A `@draft` feature.
3. **Paid-off loans:** `ask facts` states "paid off / zero balance since <month>" instead of returning empty fields. A revision of the approved ask feature.
4. **Backfill BNK13's history into the demo panel**, from the first tape onward, so trend and as-of questions have answers. An operational step, no code.
5. **Decide rule 4:** may `/cmbs-ask` quote the servicer's raw sentence, clearly labelled "unreviewed", when a field awaits review? A grill-log decision.
6. **Decode Schedule AL codes** (property type, payment status) in `ask` facts. A small change.
7. **Refusal wording:** say which roles may run refreshes.

## What changed after the session

- **Fixed in this change:**
  - Finding 1: `/cmbs-ask` rule 7 now lists the only screens and actions the skill may name, each described exactly.
  - Finding 9: `ask` decodes property types with the administrator's legend, such as "office (code OF)".
  - Finding 8: `/cmbs-refresh` now names the quant and operator roles after relaying a refusal.
- **Data, finding 5:** BANK 2018-BNK13's tapes from September 2018 onward were loaded into the demo panel. This was an operational step, with no code change.
- **Drafted for the reviewer's approval:**
  - Findings 2 and 3: a deal roster with special-servicing states.
  - Finding 4: paid-off loans read "paid off".
  - Finding 6: loss-sizing facts.
  - Finding 5: month-by-month history in `ask`.
  - A `/cmbs-brief` committee brief.
  - Finding 7: a revision of the review rule in the ask feature.
- **Deferred:** a desk watchlist that PMs write to, and a dashboard deal page.

# Session 2, after the features (2026-11-02)

**Setup.** The same goal and persona, with fresh agents. The assistant now had three skills: `/cmbs-refresh`, `/cmbs-ask` (with the roster, history, loss and value facts, and the Q97 and Q100 rules) and `/cmbs-brief`. There were five exchanges, ending with a committee pack.

## Verdict, in the practitioner's words

> "For a cited deep dive on a named loan: yes. For 'is this the whole special-servicing book':
> not until the co-op gap is explained and fixed."

## What now works that failed in session 1

| Session 1 finding | Session 2 |
|---|---|
| 1. An invented dashboard feature | None invented. The assistant pointed only to `cmbs review queue` ("an analyst's job") and to quant or operator for refreshes |
| 2–3. No deal-wide list, no state | `ask loans` listed loan 11 in special servicing, and loan 57, off the tape while last known in special servicing, unprompted |
| 4. Paid-off loans read as gaps | The brief lists paid-off, liquidated (loan 17, $13,049,503.19) and returned loans with their dates |
| 5. No history | A 7-year DSCR and occupancy trend for Ditson, plus "as of May 2026". It flagged the wrong-deal August 2021 row and the quarantined December 2022 month as months not to chart |
| 6. No loss-sizing facts | The $19,000,000 MAI value against $62,000,000 at securitization, the $20,928,570.80 appraisal reduction, the advances and the non-recoverability flag, each cited. The panel computed no ratio |
| 7. The review rule hid the servicer's words | The unreviewed sentence was quoted with its label. The tape's foreclosure code was given as the tape's |
| 8. A refusal with no way forward | It named the quant and operator roles |
| 9. "OF" | It read "office (code OF)" |

The practitioner checked the brief against the chat on balance, DSCR, appraisal reduction, advances and valuation, and the figures matched. Pressed on completeness, the assistant answered: "no, I can't stand behind 'two loans in special servicing' as complete for October 2026".

## What failed, ranked

1. **"Panel current through October 2026" sat at the top while 20 loans had been missing for two months.**
   - The 20 are the deal's co-op loans, dropped from the September tape on (M-022).
   - The brief counts them in its header, but nothing says the book may be incomplete.
   - The practitioner's first-choice feature: a completeness check on every load, with a red banner. It compares the tape's loan count with the previous month's and with the 10-D's loan list.
2. **The co-op rows are in different units.**
   - The same tape files conduit loans' rate and occupancy as fractions (0.0543, 0.28), and co-op rows as percents (4.39, 95). So the co-op rows read 419–462% and 9,500% (M-035).
   - The load contract has no range check.
3. **The brief leaves out the October comment of a loan not on the October tape.** Loan 57's default and reservation-of-rights letter, which conflicts with its last tape saying "current", had to be added by hand.
4. **No watchlist or early-warning screen, and no "% of pool".** The panel can't list loans below 1.0x DSCR or with falling occupancy, and computes no ratios.
5. **The panel holds none of the 10-D's delinquency or special-servicing summaries,** so the roster can't be cross-checked against them.
6. **No property location, and no news after the last tape.** For example, whether the largest tenant's lease, which expired 2026-09-30, was renewed.

## Process note

The assistant chained several allowed `cmbs ask facts` calls with `;` in one shell command. Each call was allowed, but the skills' `allowed-tools` patterns are meant one command at a time.
