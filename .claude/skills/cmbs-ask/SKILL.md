---
name: cmbs-ask
description: Answer questions about a CMBS loan from the surveillance panel only, citing the SEC filing (and comment sentence) behind every fact, and never changing data. Use whenever the user asks about a loan in the panel, such as why it is in special servicing, what the borrower wants, its workout strategy, DSCR, occupancy, balance, delinquency or financials, or asks "what do we know about loan N of <deal>", even if they don't mention the panel.
allowed-tools: Bash(uv run cmbs ask facts:*), Bash(uv run cmbs ask question:*)
---

# Ask the panel

The panel holds, for each loan and month, the values the loan tape (ABS-EE) reports and the
fields extracted from the special servicer's comment in the 10-D, each backed by one numbered
sentence. Answers come only from those facts, so a reader can check every statement against its
filing in seconds. Asking is read-only.

## Get the facts

```bash
uv run cmbs ask facts "<deal name>" <loan> --json --user <user>
```

- Name the deal as the panel knows it and the loan by its prospectus id (`2`, `2-001`). If the
  question does not say which deal or loan, ask.
- `--user` is the person asking (leave it out when `CMBS_USER` is set). Asking needs a role that
  may ask questions; relay a refusal as it is.
- Add `--as-of YYYY-MM-DD` when the user asks what was known on a date: the facts are then only
  what had been filed, extracted and reviewed by that day.

Each fact has a `label`, a `value`, a `status`, the `period` it reports on, who set it (`set_by`,
for a human-reviewed or overridden value) and a `citation`: the `form` and `accession`, then
either the comment's `sentence` number and its text (`quote`) or the tape field (`tape_field`),
and the filing's EDGAR `url`.

`uv run cmbs ask question "<question>" --deal "<deal name>" [--loan <loan>] --json --user <user>`
gives the deterministic answer the panel's own rules compose. Run it when the question is a
plain lookup; your answer must never contradict it.

## Compose the answer

Follow these rules; they are what makes an answer trustworthy:

1. **Only facts, each cited.** Every statement comes from one fact and carries its citation:
   `(10-D 0009900001-26-000418, sentence 1)` for a comment fact, `(ABS-EE <accession>,
   <tape_field>)` for a tape fact. Quote the sentence when it adds detail the value lacks (the
   value says "maturity extension", the sentence says "a 12-month maturity extension").
2. **Never estimate.** No figure that is not a fact's value: no sums, ratios, projections or
   "roughly". If the user wants a calculation, give the facts it would use and say the panel
   does not compute it.
3. **Not in the data.** When no fact answers the question, or part of it (sponsor equity,
   appraisal values the panel does not hold), say that the panel does not contain that
   information, in those words, and do not fill the gap from general knowledge.
4. **Awaiting review is not a fact.** A fact with status `needs review` has no value: say the
   field is awaiting human review, and state no value for that topic from any source, not even
   the tape's, until a reviewer settles it.
5. **Say who set a value.** An `overridden` or `human-reviewed` value names the analyst
   (`set_by`): "forbearance, set by alex (overridden), citing sentence 3".
6. **Asking is read-only.** If the user asks to change anything (mark, close, resolve, override,
   decide, refresh), answer that asking is read-only, and point to where the change is made:
   the review queue or an override for a value, the alert's own actions, `/cmbs-refresh` for new
   filings. Never run any command but the two above.

Example, for "Why is loan 2 in special servicing and what does the borrower want?":

```
Loan 2 of Fixture Trust 2026-F1 transferred to special servicing on 2025-03-06 due to imminent
monetary default (10-D 0009900001-26-000418, sentence 1). The borrower has requested a 12-month
maturity extension, which is pending (sentences 3 and 4: "The borrower has requested a 12-month
maturity extension." "The special servicer is evaluating the request.").
```
