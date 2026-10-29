"""`cmbs audit`: read the audit log. Nothing here changes it; nothing can.

`loan` is a loan's change history, for anyone who may read the panel. `log` reads the whole log,
by user, loan, run or date, for risk and operators.
"""

import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

import typer

from cmbs.audit.service import loan_history, read_log, render_entries
from cmbs.db import engine
from cmbs.errors import Refused

NAME = "audit"
app = typer.Typer(help="Read the append-only audit log.", no_args_is_help=True)

User = Annotated[str, typer.Option(envvar="CMBS_USER", help="Who is reading.")]


@contextmanager
def refusals() -> Iterator[None]:
    """Print a domain refusal as the user-facing message and exit 1."""
    try:
        yield
    except Refused as refused:
        typer.echo(str(refused), err=True)
        raise typer.Exit(1) from refused


@app.command("loan")
def history(
    deal: Annotated[str, typer.Argument(help="Deal name, as in the deal registry.")],
    loan: Annotated[str, typer.Argument(help="Loan number.")],
    user: User,
) -> None:
    """A loan's change history, newest first."""
    with refusals(), engine.session() as db:
        entries = loan_history(db, user, deal=deal, loan=loan)
        typer.echo(render_entries(entries), nl=False)


@app.command("log")
def log(
    user: User,
    by: Annotated[str | None, typer.Option(help="Only this user's entries.")] = None,
    start: Annotated[
        dt.datetime | None,
        typer.Option(formats=["%Y-%m-%d"], help="From this day (UTC), inclusive."),
    ] = None,
    end: Annotated[
        dt.datetime | None,
        typer.Option(formats=["%Y-%m-%d"], help="To this day (UTC), inclusive."),
    ] = None,
    deal: Annotated[str | None, typer.Option(help="Only this deal's loans.")] = None,
    loan: Annotated[str | None, typer.Option(help="Only this loan (with --deal).")] = None,
    run: Annotated[int | None, typer.Option(help="Only this run's entries.")] = None,
) -> None:
    """The whole log, newest first, narrowed as asked. Risk and operators only."""
    with refusals(), engine.session() as db:
        entries = read_log(
            db,
            user,
            user=by,
            start=start.date() if start else None,
            end=end.date() if end else None,
            deal=deal,
            loan=loan,
            run=run,
        )
        typer.echo(render_entries(entries), nl=False)
