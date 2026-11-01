# cmbs-surveillance

Turns public CMBS filings into a verified, point-in-time surveillance panel. Loan tapes come from SEC Form ABS-EE (Regulation AB II Schedule AL), and servicer text comes from Form 10-D distribution reports. Every value shows where it came from, what checked it, and which run loaded it.

## What it does

1. **Finds a deal's filings on EDGAR** and keeps every file byte for byte, with its SHA-256.
2. **Loads each month in one transaction**: the ABS-EE loan tape through raw, bronze and silver (a contract quarantines bad rows with a reason), and the 10-D's special-servicing comments, appraisal reductions and delinquencies. Point in time: a value is visible only from its filing date, and amendments add versions, never overwrite.
3. **Reads servicer comments into typed fields** (transfer reason and date, DSCR and occupancy with their as-of dates, the borrower's request and its status, the workout strategy) with TypeSafe's Jev decision model. Every value cites one sentence of the comment, quoted exactly. Decisions are a truth table in code: confident and supported values go to the panel, unsettled ones to a human review queue, and fields no sentence states are left blank. Model answers are recorded and replayed.
4. **Checks the sources against each other** without changing either: report against tape (delinquency), text against tape (transfer dates, newer financials), and the scheduled-balance roll-forward.
5. **Raises monthly alerts** a PM can act on (transfer to special servicing, worse payment status, paid-through not advancing, DSCR below the threshold in force, workout strategy changes, new comment sentences), with the exposure the book holds behind each one, and a lifecycle from new to closed.
6. **Governs it**: named users and roles, a versioned settings ledger with four-eyes approval, an append-only audit trail, numbered runs, overrides that keep the original, a coverage report of what the panel cannot see, extraction-quality reports, and point-in-time exports that can be rebuilt from the raw filings.

Surfaces: the `cmbs` command, a FastAPI service, a React dashboard (book view and loan view), and two Claude skills, `/cmbs-refresh` and the read-only `/cmbs-ask`.

## Next to the data vendors

Commercial data vendors (Trepp, Intex and the like) already give a desk cleaned loan data, cash-flow models and analysts' watchlists, across every deal and with history. This panel is meant to sit on top of that, not replace it. What it adds is the servicer's own words read into typed fields, each beside the sentence it came from; checks of the text against the tape and of the tape against itself; values exactly as they were known on any past day; and every decision written down and testable. The tape loader is the boundary: it reads EDGAR's ABS-EE today, and a vendor feed would be another adapter behind the same contract.

## How it is verified

- **Requirements first.** Every behaviour is a Gherkin scenario in `features/`, written before the code and approved by the reviewer. CI runs every approved scenario on every push, the dashboard ones in a real browser.
- **Tests** with exact values and a coverage floor, run against a fresh Postgres database per test.
- **Proofs.** The pool invariants (delinquency buckets partition the pool, the balance rolls forward, the weighted-average coupon lies between the lowest and highest rates) and the extraction decision table are proved in Lean 4 and checked against the Python by differential tests (in progress).
- **Mutation testing** with mutmut and Stryker (paused in CI for now; `CLAUDE.md` says how to turn it back on).
- **Real data.** Real BANK 2018-BNK13 and BANK5 2026-5YR23 filings are test fixtures, and the first live Jev answers are kept as regression tests.

Why each choice was made is in `docs/GRILL-LOG.md`; the quirks of real filings are in `docs/MESS-REGISTER.md`; mistakes caught in AI-written work are in `docs/ai-catch-log.md`.

## Layout

The package `cmbs` is organised by job; each feature package holds its `service.py` and, where it has them, `tables.py`, `routes.py` and `commands.py`, found by convention.

| Area | Packages |
|---|---|
| Sources | `edgar` (EDGAR search, submissions, archives, discovery), `filings` (filings and stored files), `absee` (Schedule AL parsing), `distribution` (10-D reports) |
| Panel | `tape` (loan tapes through raw, bronze and silver), `panel` (point-in-time reads), `analysis` (staleness), `deals` (one deal per trust, aliases, CIKs) |
| Text | `model` (provider-neutral model interface, record and replay), `extraction` (comments into cited fields), `review` (queue and overrides), `quality` (golden set and reports) |
| Surveillance | `checks` (flags keeping both sides), `crosscheck` (text versus tape, roll-forward), `alerts`, `book`, `coverage` |
| Operations | `refresh` (refresh and backfill as runs), `runs`, `export` (exports and rebuilds) |
| Governance | `access` (users and roles), `settings` (versioned ledger), `audit` |
| Surfaces | `api`, `dashboard` (API for `web/`), `ask`, `cli.py`; the React app is in `web/`, the Claude skills in `.claude/skills/` |
| Support | `db` (models and Alembic migrations), `testing` (scratch databases and the fixture world) |

Elsewhere: `features/` (requirements and their steps), `tests/`, `tools/` (developer helpers), `docs/`, `research/`.

## Usage

```bash
uv sync
docker compose up -d --wait db
uv run alembic upgrade head
uv run cmbs --help
```

To see the dashboard, build it once (`docker compose run --rm web sh -c "npm ci && npm run build"`), run `uv run cmbs serve`, and open http://127.0.0.1:8000/?user=pat. On an empty database, `uv run python tools/seed_demo.py` adds the fixture users and settings, and `uv run cmbs discover deal "BANK 2018-BNK13" --user alex` then `uv run cmbs refresh deal "BANK 2018-BNK13" --user quinn --live` load a real deal. Or run it all in Compose: after migrating and seeding as above, `docker compose up -d --build --wait api` serves the same page and API at http://localhost:8000/?user=pat. The container serves only the API and the dashboard; the `cmbs` command and the Claude skills run on the host, and the container holds no TypeSafe key.

The web app calls the API through a client generated from the API's schema. After changing a route, regenerate both and commit them: `uv run python tools/export_openapi.py` (writes `web/openapi.json`), then `docker compose run --rm web npm run generate` (writes `web/src/generated/`). A test and CI fail when either is stale.

The `cmbs` command reads `.env` (copy `.env.example`): `DATABASE_URL`, `SEC_USER_AGENT` (a string with a contact address you control; `www.sec.gov` requires one) and `TYPESAFE_API_KEY` (only for live model calls; tests replay recorded answers). The main commands: `cmbs refresh deal "<deal>" --user <name>` loads a deal's new months end to end; `cmbs alerts book` lists the month's alerts on the book; `cmbs review queue` shows what waits for an analyst; `cmbs ask question "<text>" ...` answers from the panel with citations; `cmbs export panel ...` writes the panel as of a date. Each has `--help`.

## Checks

```bash
uvx pre-commit install                            # once: linters on commit, tests on push
uvx pre-commit run --all-files                    # every commit-stage check
uv run pytest --cov                               # tests with the coverage floor
uv run behavex -t 'not @draft' -o output/behavex  # every approved scenario
```

`CLAUDE.md` lists every check, how to run each part of the stack, and the version pins.
