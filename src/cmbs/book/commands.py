"""`cmbs book`: load positions files and read exposure."""

import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import typer

from cmbs import clock
from cmbs.access.roles import Action
from cmbs.access.service import authorize
from cmbs.book.service import exposure_for, load_positions, parse_positions
from cmbs.db import engine
from cmbs.domain import format_money
from cmbs.errors import Refused

NAME = "book"
app = typer.Typer(help="The book: positions and exposure.", no_args_is_help=True)

User = Annotated[str, typer.Option(envvar="CMBS_USER", help="Who is acting.")]


@contextmanager
def refusals() -> Iterator[None]:
    """Print a domain refusal as the user-facing message and exit 1."""
    try:
        yield
    except Refused as refused:
        typer.echo(str(refused), err=True)
        raise typer.Exit(1) from refused


@app.command("load")
def load(
    path: Annotated[Path, typer.Argument(help="CSV with deal, class and face amount columns.")],
    as_of: Annotated[
        dt.datetime, typer.Option(formats=["%Y-%m-%d"], help="The date the positions are as of.")
    ],
    user: User,
) -> None:
    """Load a positions file as the book from its as-of date."""
    with refusals(), engine.session() as db:
        # The right is checked before the file is read, so a refusal is audited either way.
        authorize(db, user, Action.LOAD_POSITIONS)
        lines = parse_positions(path.read_text())
        loaded = load_positions(
            db, user=user, as_of=as_of.date(), lines=lines, source_name=path.name
        )
    deals = len({line.deal for line in lines})
    typer.echo(
        f"loaded positions file {loaded.id}: {len(lines)} positions in {deals} deals "
        f"as of {loaded.as_of.isoformat()}"
    )


@app.command("exposure")
def exposure(
    user: User,
    on: Annotated[
        dt.datetime | None, typer.Option(formats=["%Y-%m-%d"], help="Default today.")
    ] = None,
) -> None:
    """Total face held per deal in the book as of the date."""
    day = on.date() if on is not None else clock.today()
    with refusals(), engine.session() as db:
        held = exposure_for(db, user=user, on=day)
    if not held:
        typer.echo(f"the book holds nothing as of {day.isoformat()}")
    for deal, amount in sorted(held.items()):
        typer.echo(f"{deal}: {format_money(amount)}")
