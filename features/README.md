# Feature files

The requirements for this project, written as Gherkin scenarios before any code (design log round 5). Each feature's code is built to make its accepted scenarios pass.

## Workflow

Every feature file carries exactly one status tag on its Feature line, and its scenarios inherit it.

| Tag | Meaning |
|---|---|
| `@draft` | Written by the agent, awaiting review. Never run as a test |
| `@approved` | Accepted by the reviewer. Run as a test, and protected from the agent |

1. The agent writes a feature as `@draft`.
2. The reviewer approves it with `uv run python tools/approve_feature.py <file>`. The tool turns `@draft` into `@approved`, updates `features/APPROVED.sha256` and stages both.
3. The approval commit carries an `Approved-by:` trailer.
4. The agent never edits an approved feature. A change is proposed as a `@draft` file named `<name>.revision.feature` next to the original. Approving the revision replaces the original and removes the revision file.

Approval is per feature, not per scenario. When only part of a feature is ready, it is split into two files.

**Enforcement.** A Claude Code hook stops the agent from editing approved files, adding the `@approved` tag or touching the manifest. CI checks that every file has one status tag, that approved files match the manifest, and that any commit changing an approved file carries an `Approved-by:` trailer.

**Runs.** `behave.ini` sets `default_tags = @approved`, so only approved scenarios run. Drafts never execute and never count as failures.

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
