# Demo: one real deal, end to end

About ten minutes, on a laptop, with live public data. Everything below was run on 2026-10-29 against BANK 2018-BNK13 (CIK 1744707).

## Before

```bash
docker compose up -d --wait db
uv run alembic upgrade head
uv run python tools/seed_demo.py                     # users pat, alex, quinn, rae, olly, morgan; settings v7
docker compose run --rm web sh -c "npm ci && npm run build"   # the dashboard bundle, once
```

`.env` needs `SEC_USER_AGENT` (a string with a contact address) and, for live extraction, `TYPESAFE_API_KEY`.

## 1. Find the deal on EDGAR

```bash
uv run cmbs discover deal "BANK 2018-BNK13" --user alex
```

Shows the CIK, 231 filings, monthly ABS-EE tapes and 10-D reports from 2018 to October 2026, coverage "full". Point: the deal registry knows each trust once, whatever it is called (aliases, one CIK per deal).

## 2. Refresh the newest month, end to end

```bash
uv run cmbs refresh deal "BANK 2018-BNK13" --user quinn --live
```

About five seconds: run 1 loads October 2026's tape (42 loans) and 10-D, reads both special-servicing comments with Jev, and runs every check. The report opens with problems (none here), gives each count with a link to its filing, and says "roll-forward passed", "reconciliation passed", "text versus tape agreed". Point: a month loads completely or not at all, and the run is numbered and audited.

## 3. History and alerts

```bash
uv run cmbs refresh backfill "BANK 2018-BNK13" --from "August 2026" --to "September 2026" --user quinn
uv run cmbs alerts list --deal "BANK 2018-BNK13" --period "September 2026" --user pat
```

The story is loan 11, the Ditson Building (a 1907 Class B office building in Murray Hill): paid-through stuck at 2026-03-01, workout strategy changed from "to be determined" to foreclosure, and a new sentence in the servicer's comment. Point: each alert is a pure rule over two published months, with the settings version that produced it.

Honest data mess to show: the September tape silently omits the deal's residential co-op loans (MESS-REGISTER M-022); the panel says so instead of guessing.

## 4. The review queue

```bash
uv run cmbs review queue --user alex
uv run cmbs extract show "BANK 2018-BNK13" 11 "October 2026" --user pat
```

Only genuinely unsettled fields wait for a person (the workout strategy of loans 11 and 57). Everything else is either auto-accepted with its quoted sentence or "not stated". Point: Jev answers typed questions with calibrated probabilities; the decision is a truth table in code (grill log Q16, Q87), and the first live run changed one row of it (Q87).

## 5. The dashboard

```bash
printf 'deal,class,face amount\nBANK 2018-BNK13,D,25000000.00\nBANK 2018-BNK13,E,10000000.00\n' > /tmp/positions.csv
uv run cmbs book load /tmp/positions.csv --as-of 2026-09-30 --user olly
uv run cmbs serve
```

Open http://127.0.0.1:8000/?user=pat. The book view lists open alerts by severity, then exposure ($35,000,000.00 here), then balance. Click loan 11: every tape value shows its filing on EDGAR, what verified it ("contract passed", "scheduled balance roll-forward passed") and the run that loaded it; every extracted value sits beside the sentence it came from.

## 6. Ask, read-only

```bash
uv run cmbs ask question "Why is loan 11 in special servicing and what is its DSCR?" --deal "BANK 2018-BNK13" --loan 11 --user pat
uv run cmbs ask question "What is the sponsor's equity in loan 11?" --deal "BANK 2018-BNK13" --loan 11 --user pat
```

The first lists each fact with its 10-D accession and the quoted sentence (DSCR 0.03x as of 2026-03-31 from the comment, beside the tape's own 0.0348x). The second says "not in the data" and estimates nothing. In Claude Code, `/cmbs-ask` composes a plain-English answer from the same facts under the same rules, and cannot change data.

## How it was built

Requirements first as Gherkin (121 approved scenarios run in CI), parallel agents in worktrees with an independent review of every pull request, decisions in `docs/GRILL-LOG.md`, real-filing quirks in `docs/MESS-REGISTER.md`, AI mistakes caught in `docs/ai-catch-log.md`, and proofs of the pool invariants in Lean 4.
