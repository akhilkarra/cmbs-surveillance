"""`cmbs alerts`: build a deal's month, list alerts, and acknowledge, assign or close them."""

import datetime as dt
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Annotated

import typer

from cmbs.alerts import service
from cmbs.alerts.service import AlertView
from cmbs.db import engine
from cmbs.domain import Period, format_money
from cmbs.errors import Refused

NAME = "alerts"
app = typer.Typer(help="Monthly surveillance alerts and their lifecycle.", no_args_is_help=True)

User = Annotated[str, typer.Option(envvar="CMBS_USER", help="Who is acting.")]
Month = Annotated[str, typer.Option(help="The month, as 'September 2026'.")]
Deal = Annotated[str, typer.Option(help="Deal name, as in the deal registry.")]
AlertId = Annotated[int, typer.Argument(help="The alert's number.")]


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


def alert_line(view: AlertView) -> str:
    """'#12 high  Fixture Trust 2026-F1 loan 7: transferred to special servicing on 2026-09-02
    [new] exposure $25,000,000.00 (E, F)'."""
    where = "; ".join(f"{loan.deal} loan {loan.loan}" for loan in view.loans)
    held = f" ({', '.join(view.classes)})" if view.classes else ""
    return (
        f"#{view.id} {view.severity.value:<6} {where}: {view.message} [{view.state_text}] "
        f"exposure {format_money(view.exposure)}{held}"
    )


def _echo(views: Sequence[AlertView], empty: str | None = None) -> None:
    if not views and empty:
        typer.echo(empty)
    for view in views:
        typer.echo(alert_line(view))
        for sentence in view.added:
            typer.echo(f"    + {sentence}")


@app.command("build")
def build(
    deal: Deal,
    period: Month,
    user: User,
    as_of: Annotated[
        dt.datetime | None,
        typer.Option(formats=["%Y-%m-%d"], help="Read both months as of this day."),
    ] = None,
) -> None:
    """Compare the deal's month with the month before and store what changed."""
    month = _period(period)
    with refusals(), engine.session() as db:
        summary = service.build_for(
            db, user=user, deal=deal, period=month, as_of=as_of.date() if as_of else None
        )
    typer.echo(summary.text())
    _echo(summary.alerts)


@app.command("list")
def list_alerts(deal: Deal, period: Month, user: User) -> None:
    """A deal's alerts for a month, most severe first."""
    month = _period(period)
    with refusals(), engine.session() as db:
        listed = service.month_alerts(db, reader=user, deal=deal, period=month)
    _echo(listed.alerts, listed.note)


@app.command("book")
def book(period: Month, user: User) -> None:
    """The month's open alerts on deals the book holds, by severity, exposure, then balance."""
    month = _period(period)
    with refusals(), engine.session() as db:
        views = service.book_view(db, reader=user, period=month)
    _echo(views, f"no open alerts on the book for {month.label}")


@app.command("work-list")
def work_list(user: User) -> None:
    """The open alerts assigned to you."""
    with refusals(), engine.session() as db:
        views = service.work_list(db, user=user)
    _echo(views, f"no alerts are assigned to {user}")


@app.command("acknowledge")
def acknowledge(alert: AlertId, user: User) -> None:
    with refusals(), engine.session() as db:
        view = service.acknowledge(db, user=user, alert_id=alert)
    typer.echo(f"alert {view.id}: {view.state_text}")


@app.command("assign")
def assign(
    alert: AlertId,
    to: Annotated[str, typer.Option(help="Who takes the alert.")],
    user: User,
    note: Annotated[str | None, typer.Option(help="What to look at.")] = None,
) -> None:
    with refusals(), engine.session() as db:
        view = service.assign(db, user=user, alert_id=alert, to=to, note=note)
    typer.echo(f"alert {view.id}: {view.state_text}" + (f": {view.note}" if view.note else ""))


@app.command("close")
def close(
    alert: AlertId,
    note: Annotated[str, typer.Option(help="Why the alert is closed.")],
    user: User,
) -> None:
    with refusals(), engine.session() as db:
        view = service.close(db, user=user, alert_id=alert, note=note)
    typer.echo(f"alert {view.id}: {view.state_text}: {view.note}")
