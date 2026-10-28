# Feature files

The requirements for this project, written as Gherkin scenarios before any code (design log round 5). Each feature's code is built to make its accepted scenarios pass.

## Workflow

1. Drafts are written in `features/proposed/`.
2. The reviewer accepts a draft by running `uv run python tools/accept_feature.py <file>`. It moves the file to `features/accepted/` and updates `features/accepted/MANIFEST.sha256`.
3. The acceptance commit carries an `Approved-by:` trailer.
4. Accepted files are never edited by the agent. A change is proposed as a new draft in `features/proposed/` and goes through the same review. CI fails if an accepted file no longer matches the manifest.

## Layout

| Folder | Features |
|---|---|
| `data/` | Filing discovery, loan tape ingestion, point in time, pool invariants, distribution reports |
| `extraction/` | Comment extraction, abstention and review, model replay |
| `surveillance/` | Financials staleness, text-vs-tape checks, monthly alerts |
| `surfaces/` | Refresh routine, ask, dashboard |

## Tags

| Tag | Meaning |
|---|---|
| `@layer:data` | Deterministic loading and checking of filings |
| `@layer:logic` | Deterministic decision rules, no model calls |
| `@layer:model` | Uses model answers, always replayed from recorded calls in tests |
| `@layer:ui` | Checked through the dashboard |
| `@surface:cli`, `@surface:api`, `@surface:ui`, `@surface:skill` | Where the scenario is run. Scenarios tagged with both CLI and API run against both |
| `@theorem:<name>` | The Lean theorem that proves the rule for all inputs |
| `@contract:<name>` | The data contract that enforces the rule at load time |
| `@real-data` | Values come from actual SEC filings, verified in `research/11-data-sources.md` |

## Test data

Scenarios without `@real-data` use the synthetic deal "Fixture Trust 2026-F1". Its September 2026 loan tape is `tests/fixtures/schedule_al_minimal.xml`: reporting period end 2026-09-11, filed 2026-09-29.

| Loan | What it is in the fixture |
|---|---|
| 1 | Office, $50,000,000.00, current, financials through 2026-06-30, DSCR 1.45x |
| 1A | Pari passu companion note of loan 1, not a loan of its own |
| 2 | Hotel portfolio, $30,000,000.00, 90+ days delinquent, in special servicing since 2025-03-06, financials through 2024-12-31 |
| 2-001 | A property of loan 2's portfolio, not a loan of its own |
| 3 | Defeased |
| 4 | Residential co-op, which reports no operating statements |
| 5 | Industrial, $12,000,000.00, no updated financials |
| 6 | Paid off, balance $0.00 |
| 7 | Multifamily, $9,000,000.00, financials through 2026-03-31 |

A scenario may state other values in its own Given steps. Those override the fixture for that scenario only.
