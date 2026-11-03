# Demo: one real deal, from public filings to a committee brief

The story in one line: an AI-built tool turns public SEC filings into a cited committee brief. The demo shows where to trust it and where not to.

The deal is BANK 2018-BNK13 (CIK 1744707), loaded from September 2018 to October 2026. The hero is loan 11, the Ditson Building: a $37.5M Manhattan office loan.

## The six-minute demo

**1. The hook (30s).**
- Open on loan 11 and ask: "Before it transferred in June 2026, what did the public filings already tell you?"

**2. Find the trouble (45s).**

```bash
uv run cmbs ask loans "BANK 2018-BNK13" --special-servicing --user pat
```

- **Shows:** the freshness line, loan 11 in special servicing, and loan 57, a co-op no longer on the tape and last known in special servicing.
- **Say:** every row cites its tape, and no loan is silently dropped.

**3. The trend: the tape saw it years early (75s).**

```bash
uv run cmbs ask history "BANK 2018-BNK13" 11 --user pat
```

- **Scroll to 2021–2023.** DSCR falls 1.21x, 0.74x, 0.39x, 0.05x while the loan stays current, three years before the transfer.
- **Point at August 2021:** the property reads 305 East 72nd Street. That is another deal's tape, filed under this deal's CIK; there are 44 such months across 109 deals (MESS-REGISTER M-029, M-032).
- **Optional:** the same history as a chart in the dashboard's loan view (step 4 of the longer walkthrough below).

**4. The committee brief (90s). This is the centrepiece.**

```bash
uv run cmbs brief deal "BANK 2018-BNK13" --user pat
```

Walk loan 11's section:
- 90+ days delinquent, paid through 2026-03-01;
- a $19,000,000 MAI valuation against $62,000,000 at securitization;
- a $20,928,570.80 appraisal reduction;
- $1,115,458.57 of P&I advances, not deemed non-recoverable;
- the servicer's sentence on the workout, labelled "unreviewed: not a panel fact";
- "Not in the panel: ASER, LTV, …".

**Say:** every number cites a filing field or a sentence. The tool computes nothing, and the escalation call is the PM's, not the model's.

**5. The honest finding (60s).**
- Open `research/30-lead-lag-study.md`.
- The test: across 109 deals and 868 special-servicing episodes, do servicer comments flag trouble before the tape?
- The answer: they don't reliably.
- The process: the first version was wrong, an adversarial review caught it, and the analysis plan was written before the rerun.
- **Say:** the value is coverage, speed and control, not alpha. With investor-portal access, I would run the pre-transfer test that public data can't support.

**6. How it was built (45s).**
- Open `AIDLC.html`. An AI wrote the code, automatic checks gate every merge, and a person signs off every requirement.
- Two practitioner role-plays stress-tested the skills (`docs/SKILLS-TEST.md`).
- The second found the next gap: a "current" banner over 20 missing co-op loans. That is item 1 on `docs/BUILD-NEXT.md`.

## If they push

| Question | Answer from |
|---|---|
| "Is the AI accurate?" | The known gaps in `research/30-lead-lag-study.md`: accuracy is barely measured on real comments, and every answer is recorded and replayed |
| "What would you build next?" | `docs/BUILD-NEXT.md`: a completeness and sanity check on every load, then a watchlist screen |
| "Why not just read the 10-D?" | 16,710 comments across 109 deals become cited fields and a one-page brief |
| "Show me it refusing something" | `/cmbs-ask` with "mark loan 11 resolved": asking is read-only |
| "Why is loan 57 off the tape?" | All 20 missing loans are co-ops, dropped from the tape from September 2026 (M-022), and their rows are in percent units (M-035). It is a known gap, item 1 on the build-next list |

## Pre-flight, 30 minutes before

```bash
git switch main && git pull --ff-only
docker compose up -d --wait db && docker compose up -d --build --wait api
uv run cmbs brief deal "BANK 2018-BNK13" --user pat > output/demo-brief.txt
```

- Run the commands of steps 2–4 once: this warms the cache and checks the output.
- Keep `output/demo-brief.txt` open as the fallback if anything breaks live.
- Use a large terminal font. Have tabs ready for `AIDLC.html`, the study page and the dashboard at http://127.0.0.1:8000/?user=pat.
- **Don't run `/cmbs-refresh` live.** It reaches EDGAR, and the PM role is refused anyway.

## Longer walkthrough: setup, refresh, review and the dashboard

**Setup**, once:

```bash
docker compose up -d --wait db
uv run alembic upgrade head
uv run python tools/seed_demo.py                     # users pat, alex, quinn, rae, olly, morgan; settings v7
docker compose run --rm web sh -c "npm ci && npm run build"   # the dashboard bundle
```

`.env` needs `SEC_USER_AGENT` (a string with a contact address) and, for live extraction, `TYPESAFE_API_KEY`.

**1. Find the deal on EDGAR.**

```bash
uv run cmbs discover deal "BANK 2018-BNK13" --user quinn
```

- It shows the CIK, the monthly ABS-EE tapes and 10-D reports from 2018, and coverage "full".
- The deal registry knows each trust once, whatever it is called.

**2. Refresh, and backfill the history.** These need a quant or operator.

```bash
uv run cmbs refresh deal "BANK 2018-BNK13" --user quinn --live
uv run cmbs refresh backfill "BANK 2018-BNK13" --from "September 2018" --to "October 2026" --user quinn
```

- A month loads completely or not at all, and each run is numbered and audited.
- The report opens with problems, then gives each count with a link to its filing.

**3. The review queue.**

```bash
uv run cmbs review queue --user alex
uv run cmbs extract show "BANK 2018-BNK13" 11 "October 2026" --user pat
```

- Only genuinely unsettled fields wait for a person, such as the workout strategy of loans 11 and 57.
- Everything else is auto-accepted with its quoted sentence, or "not stated".
- Jev answers typed questions; the decision is a truth table in code (grill log Q16, Q87).

**4. The dashboard.**

```bash
printf 'deal,class,face amount\nBANK 2018-BNK13,D,25000000.00\nBANK 2018-BNK13,E,10000000.00\n' > output/positions.csv
uv run cmbs book load output/positions.csv --as-of 2026-09-30 --user olly
uv run cmbs serve
```

- Open http://127.0.0.1:8000/?user=pat.
- The book view lists open alerts by severity, then by exposure, then by balance.
- The loan view shows each tape value with its filing and what verified it, each extracted value beside its sentence, and the loan's history chart.

## How it was built

- **Requirements first,** as Gherkin: 156 approved scenarios run in CI.
- **Parallel agents** in worktrees, with an independent review of every pull request.
- **Decisions** in `docs/GRILL-LOG.md` (Q1–Q100).
- **Real-filing quirks** in `docs/MESS-REGISTER.md`.
- **AI mistakes caught** in `docs/ai-catch-log.md`.
- **Pool invariants proved** in Lean 4.
- **The lifecycle,** for outside readers, in `AIDLC.html`.
