"""Command-line interface: `cmbs <group> <command>`."""

import datetime as dt
import json
import statistics
import urllib.error
from pathlib import Path
from typing import Annotated

import typer

from cmbs.analysis import staleness
from cmbs.db.registry import all_command_modules
from cmbs.edgar import archives, search, submissions
from cmbs.universe import SAMPLE_DEALS

app = typer.Typer(help="CMBS surveillance over public SEC filings.", no_args_is_help=True)
edgar_app = typer.Typer(help="Search and download from SEC EDGAR.", no_args_is_help=True)
analyze_app = typer.Typer(help="Measurements over parsed loan tapes.", no_args_is_help=True)
app.add_typer(edgar_app, name="edgar")
app.add_typer(analyze_app, name="analyze")
# Feature command groups, found by convention: cmbs.<feature>.commands with `app` and `NAME`.
for _module in all_command_modules():
    app.add_typer(_module.app, name=_module.NAME)


@edgar_app.command("search")
def edgar_search(
    out_json: Annotated[Path, typer.Argument(help="Where to write the results as JSON.")],
) -> None:
    """Full-text search EDGAR for every deal in the sample universe."""
    results = search.search_deals(SAMPLE_DEALS)
    for name, result in results.items():
        typer.echo(f"{name:20s} total={result['total']:5d} forms={result['forms_seen']}")
        names = sorted({n for hit in result["hits"] for n in (hit["names"] or [])})
        for filer in names[:6]:
            typer.echo(f"      {filer}")
    out_json.write_text(json.dumps(results, indent=1))


@edgar_app.command("submissions")
def edgar_submissions(
    out_dir: Annotated[Path, typer.Argument(help="Directory for the raw submissions JSON.")],
    ciks: Annotated[list[str], typer.Argument(help="Filer CIKs, each optionally CIK:label.")],
) -> None:
    """Save each filer's submissions JSON and print its filing history."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for spec in ciks:
        cik_text, _, label = spec.partition(":")
        cik = int(cik_text)
        data = submissions.fetch_submissions(cik)
        (out_dir / f"{cik}.json").write_text(json.dumps(data))
        filings = submissions.recent_filings(data)
        dates = [f.filing_date for f in filings]
        date_range = f"{min(dates)}..{max(dates)}" if dates else "-..-"
        older = len(data["filings"].get("files", []))
        typer.echo(
            f"\n{data['name']} (CIK {cik}) {label}  n={len(filings)} range={date_range}"
            f" older_files={older}"
        )
        typer.echo(f"   forms: {dict(submissions.form_counts(data))}")
        periodic = [f for f in filings if f.form in submissions.PERIODIC_FORMS]
        for filing in periodic[:6]:
            typer.echo(f"    {filing}")


@edgar_app.command("download")
def edgar_download(
    out_dir: Annotated[Path, typer.Argument(help="Directory to save the filing under.")],
    cik: Annotated[int, typer.Argument(help="Filer CIK.")],
    accession: Annotated[str, typer.Argument(help="Accession number, dashes optional.")],
    with_distribution_report: Annotated[
        bool, typer.Option(help="Also fetch the 10-D EX-99.1 distribution report.")
    ] = False,
) -> None:
    """Download a filing's asset XML (EX-102, EX-103) from www.sec.gov/Archives."""
    try:
        saved = archives.download_filing(
            cik, accession, out_dir, include_distribution_report=with_distribution_report
        )
    except urllib.error.HTTPError as error:
        typer.echo(
            f"HTTP {error.code} from www.sec.gov. Set SEC_USER_AGENT to a string with a "
            "contact address you control.",
            err=True,
        )
        raise typer.Exit(1) from error
    for path in saved:
        typer.echo(f"saved {path.name} ({path.stat().st_size:,} bytes)")


@analyze_app.command("staleness")
def analyze_staleness(
    filing_date: Annotated[
        dt.datetime, typer.Argument(formats=["%Y-%m-%d"], help="ABS-EE filing date.")
    ],
    xml_files: Annotated[list[Path], typer.Argument(help="EX-102 asset XML files.")],
) -> None:
    """Measure how stale each tape's most recent property financials are."""
    pct = staleness.nearest_rank_percentile
    pooled: list[int] = []
    for path in xml_files:
        result = staleness.file_staleness(path, filing_date.date())
        days = result.days_vs_period_end
        pooled += days
        typer.echo(
            f"{path}: active={result.active_loans} with_fin={result.loans_with_financials}"
            f" median={pct(days, 0.5)} p75={pct(days, 0.75)} p90={pct(days, 0.9)}"
            f" >183d={result.over_six_months}"
            f" vs_filing_median={pct(result.days_vs_filing, 0.5)}"
        )
    if pooled:
        typer.echo(
            f"POOLED n={len(pooled)} median={statistics.median(pooled)}"
            f" p75={pct(pooled, 0.75)} p90={pct(pooled, 0.9)}"
        )
