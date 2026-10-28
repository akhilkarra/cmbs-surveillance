"""`cmbs reports`: parse 10-D distribution reports into the panel."""

import datetime as dt
from pathlib import Path
from typing import Annotated

import typer

from cmbs.db import engine
from cmbs.distribution.service import load_report
from cmbs.domain import Period
from cmbs.errors import Refused

NAME = "reports"
app = typer.Typer(help="Parse 10-D distribution reports (EX-99.1).", no_args_is_help=True)


@app.callback()
def reports() -> None:
    """Keep `parse` a subcommand even while it is the only one."""


def _period(text: str | None) -> Period | None:
    if text is None:
        return None
    try:
        return Period.parse(text)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error


@app.command("parse")
def parse(
    report: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="EX-99.1 file.")],
    deal: Annotated[str, typer.Option(help="Deal name, as in the deal registry.")],
    accession: Annotated[str, typer.Option(help="The 10-D's accession number.")],
    cik: Annotated[int, typer.Option(help="The filer's CIK.")],
    filing_date: Annotated[
        dt.datetime, typer.Option(formats=["%Y-%m-%d"], help="The 10-D's filing date.")
    ],
    user: Annotated[str, typer.Option(envvar="CMBS_USER", help="Who is loading the report.")],
    period: Annotated[
        str | None,
        typer.Option(help="Distribution month, like 'September 2026'; checked against the file."),
    ] = None,
) -> None:
    """Parse a distribution report and store its comments, appraisal reductions and
    delinquency detail per loan."""
    covered = _period(period)
    try:
        with engine.session() as db:
            loaded = load_report(
                db,
                user=user,
                deal=deal,
                accession=accession,
                cik=cik,
                filing_date=filing_date.date(),
                file_name=report.name,
                content=report.read_bytes(),
                period=covered,
            )
    except Refused as refusal:
        typer.echo(str(refusal), err=True)
        raise typer.Exit(1) from refusal
    typer.echo(f"{loaded.deal} {loaded.period} ({loaded.accession})")
    typer.echo(loaded.summary)
