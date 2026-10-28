"""`cmbs tape`: load an ABS-EE loan tape and read the panel point in time."""

import datetime as dt
from pathlib import Path
from typing import Annotated

import typer

from cmbs.access.roles import Action
from cmbs.access.service import authorize
from cmbs.db import engine
from cmbs.errors import Refused
from cmbs.panel import service as panel
from cmbs.tape.service import ORIGINAL, load_tape

NAME = "tape"
app = typer.Typer(help="Load ABS-EE loan tapes and read the panel.", no_args_is_help=True)

DateArgument = Annotated[dt.datetime, typer.Argument(formats=["%Y-%m-%d"])]
UserOption = Annotated[str, typer.Option(help="Who is acting (a registered user).")]


@app.command("load")
def load(
    file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="EX-102 XML file.")],
    deal: Annotated[str, typer.Argument(help='Deal name, like "BANK 2018-BNK13".')],
    accession: Annotated[str, typer.Argument(help="Accession number of the filing.")],
    filing_date: DateArgument,
    user: UserOption,
    form: Annotated[str, typer.Option(help="ABS-EE, or ABS-EE/A for an amendment.")] = ORIGINAL,
    amends: Annotated[str | None, typer.Option(help="Accession the amendment corrects.")] = None,
    cik: Annotated[int | None, typer.Option(help="The deal's CIK, if not yet known.")] = None,
) -> None:
    """Load one EX-102 file of a filing. Loading it again changes nothing."""
    try:
        with engine.session() as db:
            actor = authorize(db, user, Action.RUN_REFRESH)
            summary = load_tape(
                db,
                deal=deal,
                content=file.read_bytes(),
                file_name=file.name,
                accession=accession,
                filing_date=filing_date.date(),
                form=form,
                amends=amends,
                cik=cik,
                actor=actor,
            )
    except Refused as refusal:
        typer.echo(f"refused: {refusal}", err=True)
        raise typer.Exit(1) from refusal
    typer.echo(summary.text())
    for loan in summary.quarantined:
        typer.echo(f"  quarantined loan {loan.loan}: {loan.reason}")


@app.command("staleness")
def staleness(
    deal: Annotated[str, typer.Argument(help="Deal name.")],
    as_of: DateArgument,
    user: UserOption,
) -> None:
    """How old each loan's most recent financials were on a day, and the deal summary."""
    try:
        with engine.session() as db:
            authorize(db, user, Action.READ_PANEL)
            tape, report = panel.staleness(db, deal, as_of.date())
    except Refused as refusal:
        typer.echo(f"refused: {refusal}", err=True)
        raise typer.Exit(1) from refusal
    typer.echo(f"{deal} {tape.period.label} as of {tape.as_of}")
    for loan in report.loans:
        typer.echo(f"  loan {loan.loan}: {loan.status}")
    typer.echo(report.summary.text())
