"""`cmbs refresh`: refresh the book or one deal, or backfill a deal's months, as a run.

`cmbs refresh deal` is the refresh routine: it also extracts each new month's special-servicing
comments and prints the routine's report (`cmbs.refresh.report`), problems first. Extraction
replays recorded model answers unless `--live` is given, which asks the provider for the model in
force (Jev needs TYPESAFE_API_KEY) and records every call.

EDGAR is called live unless CMBS_EDGAR_REPLAY and CMBS_ARCHIVE_REPLAY name recordings
(`cmbs.refresh.sources`). A run that fails prints its summary and exits 1.
"""

from typing import Annotated

import typer

from cmbs.domain import Period
from cmbs.errors import Refused
from cmbs.extraction.service import model_client
from cmbs.refresh import service
from cmbs.refresh.report import routine_report
from cmbs.refresh.service import RunReport
from cmbs.refresh.sources import ModelFor, default_sources

NAME = "refresh"
app = typer.Typer(help="Refresh deals from EDGAR as numbered runs.", no_args_is_help=True)

User = Annotated[str, typer.Option(envvar="CMBS_USER", help="Who is running it.")]


def _month(text: str) -> Period:
    try:
        return Period.parse(text)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error


def models(*, live: bool) -> ModelFor:
    """The model client a routine extracts with: replay only, or live with the provider."""
    return lambda db, user: model_client(db, user, live=live)


def _show(action, render=RunReport.text) -> None:
    try:
        report: RunReport = action()
    except Refused as refused:
        typer.echo(str(refused), err=True)
        raise typer.Exit(1) from refused
    typer.echo(render(report))
    if report.run.status == "failed":
        raise typer.Exit(1)


@app.command("book")
def book(user: User) -> None:
    """Refresh every deal in today's book."""
    _show(lambda: service.refresh_book(user))


@app.command("deal")
def deal(
    name: Annotated[str, typer.Argument(help='Deal name, like "BANK 2018-BNK13".')],
    user: User,
    live: Annotated[
        bool, typer.Option(help="Ask the model provider when no recording answers.")
    ] = False,
) -> None:
    """Bring one deal up to date, extract its new comments and report what changed."""
    sources = default_sources(models(live=live))
    _show(
        lambda: service.refresh_deal(user, name, sources=sources),
        lambda report: routine_report(report.run),
    )


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
