# What to build next

Ranked by evidence. Each item names the session, review or study that found it. Grill log Q98 froze new features for the 2026-11-03 demo; these come after it, each through a `@draft` feature and the reviewer's approval.

| # | What | Why, and the evidence |
|---|---|---|
| 1 | **A completeness and sanity check on every load, with a red banner** | Before a month counts as loaded, compare the tape's loan count with the previous month's and with the 10-D's loan list, range-check rates, occupancy and DSCR, and detect a tape that is another deal's. The freshness line turns red when anything fails. Evidence: 20 BNK13 co-ops missing for two months under a "current" banner (practitioner session 2, M-022); co-op rows in percent units (M-035); 44 wrong-deal tapes in 37 deals (M-029, M-032). |
| 2 | **A watchlist screen across a deal** | Loans by DSCR below a threshold, falling occupancy, or delinquency, each row cited, with no ratios computed. Evidence: "committee always asks what's next" (sessions 1 and 2). |
| 3 | **The 10-D's delinquency and special-servicing tables in `ask`** | The reconciliation already reads the delinquency detail (`cmbs.distribution.reconcile`). Surfacing it lets the roster be cross-checked against the servicer's own list. Evidence: session 2. |
| 4 | **The current comment on loans the current tape omits** | The brief's last-known section should quote the latest 10-D comment, labelled. Evidence: loan 57's default letter, missing from the brief (session 2). |
| 5 | **Discovery that tolerates filings shared by several trusts** | Skip CORRESP and other forms the panel does not read, or let a filing belong to several deals. Evidence: 18 of 128 study deals failed (M-033). |
| 6 | **A desk watchlist that PMs write to** | Deferred in Q98: a new write path with permissions, audit and UI. Evidence: "flag loan 11 for watchlist under my name" (session 1). |
| 7 | **A dashboard deal page** | The roster and brief in the web view. Deferred in Q98. |
| 8 | **Lead-lag study, version 3** | Pari passu clustering, code 9 read as open, about 30 labels, keyword hedges, capped pairs (`research/30-lead-lag-study.md`). With an investor-portal feed, run the pre-transfer test public data cannot. |
| 9 | **Clef-flash on the study's comments, overnight** | Agreement with Jev at scale, on the same 16,710 comments. |
