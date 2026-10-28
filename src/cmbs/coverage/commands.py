"""`cmbs coverage`: the coverage report, and vendor sources that widen it."""

import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

import typer

from cmbs import clock
from cmbs.coverage.service import configure_vendor_source, render, report
from cmbs.db import engine
from cmbs.errors import Refused

NAME = "coverage"
app = typer.Typer(help="What the panel can and cannot see of the book.", no_args_is_help=True)

User = Annotated[str, typer.Option(envvar="CMBS_USER", help="Who is acting.")]


@contextmanager
def refusals() -> Iterator[None]:
    """Print a domain refusal as the user-facing message and exit 1."""
    try:
        yield
    except Refused as refused:
        typer.echo(str(refused), err=True)
        raise typer.Exit(1) from refused


@app.command("report")
def coverage_report(
    user: User,
    on: Annotated[
        dt.datetime | None, typer.Option(formats=["%Y-%m-%d"], help="Default today.")
    ] = None,
) -> None:
    """Print the coverage of every deal in the book from what is stored.

    Run `cmbs discover book` first to refresh what EDGAR shows.
    """
    day = on.date() if on is not None else clock.today()
    with refusals(), engine.session() as db:
        result = report(db, user=user, on=day)
    typer.echo(render(result), nl=False)


@app.command("vendor")
def add_vendor_source(
    deal: Annotated[str, typer.Argument(help="Deal display name.")],
    source: Annotated[str, typer.Argument(help="Name of the vendor loan-tape source.")],
    user: User,
) -> None:
    """Record that a vendor loan-tape source covers a deal."""
    with refusals(), engine.session() as db:
        configure_vendor_source(db, user=user, deal=deal, source=source)
    typer.echo(f"{' '.join(deal.split())}: vendor source {' '.join(source.split())}")
