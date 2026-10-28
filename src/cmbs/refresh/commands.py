"""`cmbs refresh`: refresh the book or one deal, or backfill a deal's months, as a run.

EDGAR is called live unless CMBS_EDGAR_REPLAY and CMBS_ARCHIVE_REPLAY name recordings
(`cmbs.refresh.sources`). A run that fails prints its summary and exits 1.
"""

from typing import Annotated

import typer

from cmbs.domain import Period
from cmbs.errors import Refused
from cmbs.refresh import service
from cmbs.refresh.service import RunReport

NAME = "refresh"
app = typer.Typer(help="Refresh deals from EDGAR as numbered runs.", no_args_is_help=True)

User = Annotated[str, typer.Option(envvar="CMBS_USER", help="Who is running it.")]


def _month(text: str) -> Period:
    try:
        return Period.parse(text)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error


def _show(action) -> None:
    try:
        report: RunReport = action()
    except Refused as refused:
        typer.echo(str(refused), err=True)
        raise typer.Exit(1) from refused
    typer.echo(report.text())
    if report.run.status == "failed":
        raise typer.Exit(1)


@app.command("book")
def book(user: User) -> None:
    """Refresh every deal in today's book."""
    _show(lambda: service.refresh_book(user))


@app.command("deal")
def deal(
    name: Annotated[str, typer.Argument(help='Deal name, like "BANK 2018-BNK13".')], user: User
) -> None:
    """Bring one deal up to date."""
    _show(lambda: service.refresh_deal(user, name))


@app.command("backfill")
def backfill(
    name: Annotated[str, typer.Argument(help="Deal name.")],
    start: Annotated[str, typer.Option("--from", help="First month, like 'August 2025'.")],
    end: Annotated[str, typer.Option("--to", help="Last month, like 'September 2026'.")],
    user: User,
) -> None:
    """Load every month of a deal from one month to another, oldest first."""
    first, last = _month(start), _month(end)
    _show(lambda: service.backfill(user, name, first, last))
