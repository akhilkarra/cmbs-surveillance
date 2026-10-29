---
name: cmbs-refresh
description: Refresh one CMBS deal in the surveillance panel end to end (find new SEC filings, load the loan tape and 10-D, extract servicer comments, run every check, build alerts) and relay the routine's report. Use whenever the user asks to refresh, update, pull, load or catch up a deal, asks "is <deal> up to date?", or wants the latest month of a deal's filings in the panel, even if they don't say "refresh".
allowed-tools: Bash(uv run cmbs refresh deal:*)
---

# Refresh a deal

The refresh routine is one command. It finds the deal's new filings on EDGAR, loads each new
month (loan tape and 10-D) completely or not at all, reconciles the report with the tape, checks
the balance roll-forward, builds the month's alerts, extracts the servicer comments, records a
numbered run, and prints a report. Run that command and relay the report; do nothing else. The
command is the only thing that may change the panel, and it keeps its own audit trail, so never
edit data, files or the database yourself, and never run any other command.

## Run it

```bash
uv run cmbs refresh deal "<deal name>" --live --user <user>
```

- `<deal name>` is the deal as the panel knows it, like `"BANK 2018-BNK13"`. If the user's
  wording is ambiguous (a shelf name, a partial name), ask which deal before running.
- `--user` is the person asking, by their panel user name. Leave it out when `CMBS_USER` is set
  in the environment; if neither is known, ask. A user whose role may not run refreshes is
  refused; relay the refusal as it is.
- `--live` lets the routine ask the model about comments it has no recorded answer for (it needs
  `TYPESAFE_API_KEY`). Drop it only if the user asks for a replay-only refresh.

The command exits 1 when the run failed; that is a result to report, not an error to work around.
Do not run it again to "retry" unless the user asks: a failed month stays recorded and the next
run starts from it.

## Relay the report

The report's own order is deliberate: problems first, then the run's outcome, then each month.
Keep that order and the wording of every number and status.

- **Lead with problems.** If the report opens with a "stopped at" line or a line of counts like
  `1 loan quarantined, 1 roll-forward break`, say that first, in those words.
- **Never call a run a success unless its status line says `succeeded`.** A run that
  `completed with problems` or `failed` is reported as exactly that.
- **Give the counts with their links.** For each month loaded: loans loaded, comments extracted,
  items sent to review, alerts raised, each with the filing it came from (form, accession and
  EDGAR link). Copy numbers and accessions exactly; never round, total or estimate.
- **Nothing new is an answer.** `up to date through September 2026` means there was nothing to
  load; say so.
- **Say how to fix what the report says how to fix.** For example
  `HTTP 403 from EDGAR: set SEC_USER_AGENT to a string with a contact address` means the user
  must set `SEC_USER_AGENT` in their environment (the project's `.env`); tell them, do not set it.

A short relay looks like this:

```
Fixture Trust 2026-F1: 1 loan quarantined, 1 roll-forward break. Run 12 completed with problems.
September 2026 loaded:
- 6 loans loaded (ABS-EE 0009900001-26-000419, https://www.sec.gov/Archives/edgar/data/9900001/000990000126000419/)
- 1 loan quarantined (same ABS-EE)
- 1 roll-forward break (same ABS-EE)
- 1 comment extracted, 0 items sent to review (10-D 0009900001-26-000418, https://...)
- 1 alert raised (both filings)
```

If the user then asks what a number means (which loan was quarantined, why a value went to
review), answer from the report; for anything it does not say, suggest `/cmbs-ask` or the
dashboard rather than guessing.
