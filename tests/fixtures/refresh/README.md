# Refresh fixtures: synthetic EDGAR for the two fixture deals

**Everything here is synthetic.** No file was downloaded from SEC. `build.py` writes it all
from two files the other features already use, and the refresh tests and scenarios replay it
exactly as discovery's tests replay `tests/fixtures/edgar`:

- `tests/fixtures/schedule_al_minimal.xml`, the fixture deal's September 2026 loan tape;
- `tests/fixtures/distribution/fixture-trust-2026-f1_10d_2026-09.htm`, its September 2026 10-D.

Regenerate after changing either file, and commit the result:

```bash
uv run python tests/fixtures/refresh/build.py
```

## What is here

| Folder | Holds | Read by |
|---|---|---|
| `edgar/` | Entity typeahead answers and submissions JSON for both deals, with `recordings.json` | `cmbs.edgar.replay.Replay` |
| `archive/` | Each filing's `index.json` and exhibits, by accession, with `recordings.json` | `cmbs.refresh.sources.ArchiveReplay` |

| Deal | CIK | Months | Filings per month |
|---|---|---|---|
| Fixture Trust 2026-F1 | 9900001 (`FIXTURE_CIK`) | August 2025 to October 2026 | 10-D (EX-99.1) and ABS-EE (EX-102, EX-103) |
| Fixture Trust 2026-F2 | 9900002 | August 2026 to October 2026 | the same |

Both CIKs are far above any EDGAR has assigned. Accession numbers are
`<CIK>-<filing year>-<000400 + 2 × month>` for the 10-D and one more for the ABS-EE.

## How the months differ

Every month is the September 2026 fixture moved to that month:

- the determination date (the tape's reporting period end) is the 11th, the distribution date
  the 17th, and both filings are filed 12 days after the distribution date (the 29th; March 1
  for February);
- loan 1 is paid through the first of the month;
- financials are no newer than 70 days before the determination date, keeping their length;
- in the 10-D, loan 2 is paid through four months back and loan 4 two months back;
- loan 2's $6,500,000.00 appraisal reduction appears from August 2026;
- **loan 4 reports payment status 1 (30 to 59 days delinquent) on every tape**, as the 10-D
  lists it. The original fixture tape reports no status for loan 4; without this, every month
  would raise the same reconciliation flag.

So the September 2026 10-D of Fixture Trust 2026-F1 is the fixture report byte for byte, and its
tape differs from the fixture tape only by loan 4's payment status.

Fixture Trust 2026-F2 is the same pool with its property names changed (Harbor Office Tower is
Bayview Office Center, and so on), so the two deals cannot be confused in a test.
