"""`cmbs runs`: the run history, one run, and where a loaded value came from."""

import datetime as dt
from typing import Annotated

import typer

from cmbs.db import engine
from cmbs.domain import Period
from cmbs.errors import Refused
from cmbs.runs import service

NAME = "runs"
app = typer.Typer(help="Run history: what each refresh and backfill did.", no_args_is_help=True)

User = Annotated[str, typer.Option(envvar="CMBS_USER", help="Who is reading.")]
Day = Annotated[dt.datetime | None, typer.Option(formats=["%Y-%m-%d"])]


def _refused(refused: Refused) -> typer.Exit:
    typer.echo(str(refused), err=True)
    return typer.Exit(1)


@app.command("list")
def list_runs(user: User, start: Day = None, end: Day = None) -> None:
    """Runs started from one day to another (UTC, inclusive), newest first."""
    try:
        with engine.session() as db:
            views = service.history(
                db,
                user,
                start=start.date() if start else None,
                end=end.date() if end else None,
            )
    except Refused as refused:
        raise _refused(refused) from refused
    if not views:
        typer.echo("no runs")
    for view in views:
        typer.echo(f"{view.headline()} ({view.kind}, {view.status})")


@app.command("show")
def show(number: Annotated[int, typer.Argument(help="The run's number.")], user: User) -> None:
    """One run: what it did, deal by deal, and where it stopped if it failed."""
    try:
        with engine.session() as db:
            view = service.read_run(db, user, number)
    except Refused as refused:
        raise _refused(refused) from refused
    typer.echo(view.text())


@app.command("trace")
def trace(
    deal: Annotated[str, typer.Argument(help="Deal name.")],
    loan: Annotated[str, typer.Argument(help='Prospectus loan id, like "7".')],
    period: Annotated[str, typer.Argument(help="Month, like 'September 2026'.")],
    user: User,
    as_of: Day = None,
) -> None:
    """Where a loan's balance for a month came from, and which run loaded it."""
    try:
        month = Period.parse(period)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    try:
        with engine.session() as db:
            found = service.trace(
                db,
                user,
                deal=deal,
                loan=loan,
                period=month,
                as_of=as_of.date() if as_of else None,
            )
    except Refused as refused:
        raise _refused(refused) from refused
    typer.echo(found.text())
