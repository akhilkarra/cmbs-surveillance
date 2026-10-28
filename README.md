# cmbs-surveillance

Turns public CMBS filings into a verified, point-in-time surveillance panel. Loan tapes come from SEC Form ABS-EE, and servicer text comes from Form 10-D distribution reports.

## Layout

| Path | What it holds |
|---|---|
| `src/cmbs/edgar/` | SEC EDGAR access: HTTP client, full-text search, filer submissions, filing downloads |
| `src/cmbs/absee/` | Parsing of ABS-EE Schedule AL asset XML into loan records |
| `src/cmbs/analysis/` | Measurements over the parsed tapes, such as financials staleness |
| `src/cmbs/universe.py` | The sample deal universe |
| `src/cmbs/cli.py` | The `cmbs` command |
| `tests/` | Unit tests and fixtures |
| `tools/` | Developer helpers that are not part of the package |
| `research/` | Research notes behind the design |
| `docs/` | Design decisions, data-mess register, AI catch log |

## Usage

```bash
uv sync
uv run cmbs --help
uv run cmbs analyze staleness 2026-09-29 tests/fixtures/schedule_al_minimal.xml
```

Requests to `www.sec.gov` need `SEC_USER_AGENT` set to a string with a contact address you control. See `.env.example`.

## Checks

```bash
uvx pre-commit install           # once: linters on commit, tests with coverage on push
uvx pre-commit run --all-files   # every commit-stage check
uv run pytest --cov              # tests with the coverage floor
```

`CLAUDE.md` lists every check, how to run each part of the stack, and the version pins.
