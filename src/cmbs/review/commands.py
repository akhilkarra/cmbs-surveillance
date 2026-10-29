"""`cmbs review`: the review queue, declines, overrides and label candidates.

Deciding an item is `cmbs extract decide`.
"""

import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

import typer

from cmbs.db import engine
from cmbs.domain import Period
from cmbs.errors import Refused
from cmbs.review.service import (
    decline,
    label_candidates,
    override,
    override_history,
    queue,
    queue_summary,
    render_queue,
    render_queue_summary,
)

NAME = "review"
app = typer.Typer(help="Work the review queue and override extracted values.", no_args_is_help=True)

User = Annotated[str, typer.Option(envvar="CMBS_USER", help="Who is acting.")]
On = Annotated[
    dt.datetime | None,
    typer.Option(formats=["%Y-%m-%d"], help="Measure on this day (default today)."),
]
Deal = Annotated[str, typer.Argument(help="Deal name, as in the deal registry.")]
Loan = Annotated[str, typer.Argument(help="Loan number.")]
Month = Annotated[str, typer.Argument(help="Distribution month, like 'September 2026'.")]


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


def _day(on: dt.datetime | None) -> dt.date | None:
    return None if on is None else on.date()


@app.command("queue")
def show_queue(user: User, on: On = None) -> None:
    """Open items, the largest exposure first, then the oldest; overdue ones are marked."""
    with refusals(), engine.session() as db:
        entries = queue(db, user, on=_day(on))
    typer.echo(render_queue(entries), nl=False)


@app.command("summary")
def show_summary(user: User, on: On = None) -> None:
    """How many items are open and overdue, the oldest, and open items per field."""
    with refusals(), engine.session() as db:
        summary = queue_summary(db, user, on=_day(on))
    typer.echo(render_queue_summary(summary), nl=False)


@app.command("decline")
def decline_item(
    item: Annotated[int, typer.Argument(help="Review item number.")],
    reason: Annotated[str, typer.Argument(help="Why the comment cannot settle it.")],
    user: User,
) -> None:
    """Close an item the comment cannot settle; the field is marked "not determinable"."""
    with refusals(), engine.session() as db:
        declined = decline(db, user, item, reason=reason)
    typer.echo(f"item {item}: {declined.label} {declined.marked}")


@app.command("override")
def override_value(
    deal: Deal,
    loan: Loan,
    period: Month,
    field: Annotated[str, typer.Argument(help="Field name or label, like 'borrower request'.")],
    value: Annotated[str, typer.Argument(help="The value the panel should show.")],
    user: User,
    sentence: Annotated[
        int | None, typer.Option(help="The sentence of the comment that supports it.")
    ] = None,
    reason: Annotated[str | None, typer.Option(help="Why the value is wrong.")] = None,
) -> None:
    """Replace an extracted value from now on; the model's answer is kept."""
    month = _period(period)
    with refusals(), engine.session() as db:
        made = override(
            db,
            user,
            deal=deal,
            loan=loan,
            period=month,
            field=field,
            value=value,
            sentence=sentence,
            reason=reason,
        )
    typer.echo(
        f"{made.label}: {made.display} ({made.marked}, sentence {made.sentence}; "
        f"the model said {made.model_value or 'nothing'})"
    )


@app.command("overrides")
def show_overrides(deal: Deal, loan: Loan, period: Month, user: User) -> None:
    """Every override of a loan's extracted comment for a month, oldest first."""
    month = _period(period)
    with refusals(), engine.session() as db:
        found = override_history(db, user, deal=deal, loan=loan, period=month)
    if not found:
        typer.echo(f"no overrides of loan {loan} for {month}")
    for made in found:
        before = made.before_display or "-"
        typer.echo(
            f"{made.at.isoformat()} {made.by}: {made.label} {before} -> {made.display} "
            f"(sentence {made.sentence}): {made.reason}"
        )


@app.command("candidates")
def show_candidates(user: User) -> None:
    """Reviewed and overridden values, proposed as golden-set labels."""
    with refusals(), engine.session() as db:
        found = label_candidates(db, user)
    if not found:
        typer.echo("no label candidates")
    for item in found:
        cited = f", sentence {item.sentence}" if item.sentence is not None else ""
        typer.echo(
            f"loan {item.loan} of {item.deal}, {item.period} ({item.accession}): {item.label} "
            f"{item.value}{cited} ({item.source} by {item.by})"
        )
