"""`cmbs crosscheck`: run the checks on a deal's month, read the roll-forward and the flags."""

import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

import typer

from cmbs import clock
from cmbs.access.roles import Action
from cmbs.access.service import authorize
from cmbs.crosscheck import report, service
from cmbs.db import engine
from cmbs.domain import Period
from cmbs.errors import Refused

NAME = "crosscheck"
app = typer.Typer(
    help="Text-versus-tape checks and the balance roll-forward.", no_args_is_help=True
)

User = Annotated[str, typer.Option(envvar="CMBS_USER", help="Who is acting.")]
Deal = Annotated[str, typer.Option(help="Deal name, as in the deal registry.")]
Month = Annotated[str, typer.Option(help="The month, as 'September 2026'.")]
AsOf = Annotated[
    dt.datetime | None,
    typer.Option(formats=["%Y-%m-%d"], help="Read as known on this day (default today)."),
]


@contextmanager
def refusals() -> Iterator[None]:
    """Print a domain refusal as the user-facing message and exit 1."""
    try:
        yield
    except Refused as refused:
        typer.echo(str(refused), err=True)
        raise typer.Exit(1) from refused


def _period(text: str) -> Period:
    try:
        return Period.parse(text)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error


@app.command("run")
def run(
    deal: Deal,
    period: Month,
    user: User,
    only: Annotated[
        str, typer.Option(help="Which checks: all, text-vs-tape or rollforward.")
    ] = "all",
) -> None:
    """Run the checks on the month as known today and raise flags for what disagrees."""
    if only not in service.CHECKS:
        choices = ", ".join(service.CHECKS)
        raise typer.BadParameter(f"choose one of: {choices}", param_hint="--only")
    month = _period(period)
    with refusals(), engine.session() as db:
        summary = service.CHECKS[only](db, user=user, deal=deal, period=month)
    typer.echo(report.summary_text(summary), nl=False)


@app.command("rollforward")
def rollforward(deal: Deal, period: Month, user: User, as_of: AsOf = None) -> None:
    """Each loan's balance roll-forward for the month; reads only, raises nothing."""
    month = _period(period)
    day = as_of.date() if as_of is not None else clock.today()
    with refusals(), engine.session() as db:
        authorize(db, user, Action.READ_PANEL)
        results = service.rollforward(
            db, deal_id=service.deal_id(db, deal), period=month, as_of=day
        )
    heading = f"{deal}, {month.label}, as of {day.isoformat()}"
    if not results:
        typer.echo(f"{heading}: no loan tape filed")
        return
    typer.echo(f"{heading}: {report.rollforward_counts(results)}")
    for result in results:
        typer.echo(report.rollforward_line(result))


@app.command("flags")
def flags(
    deal: Deal,
    user: User,
    period: Annotated[str | None, typer.Option(help="Only this month.")] = None,
    loan: Annotated[str | None, typer.Option(help="Only this loan.")] = None,
) -> None:
    """A deal's check flags and notes, oldest first, each with both sides."""
    month = _period(period) if period is not None else None
    with refusals(), engine.session() as db:
        views = service.flags(db, reader=user, deal=deal, period=month, loan=loan)
    if not views:
        typer.echo("no flags")
    for view in views:
        typer.echo(report.flag_line(view))
        for side, values in view.sides.items():
            typer.echo(f"    {side}: {values}")
