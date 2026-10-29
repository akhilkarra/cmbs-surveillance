"""`cmbs invariants`: a deal's pool as known on a day, its delinquency buckets and its WAC."""

import datetime as dt
from typing import Annotated

import typer

from cmbs.db import engine
from cmbs.domain import Period
from cmbs.errors import Refused
from cmbs.invariants import report, service

NAME = "invariants"
app = typer.Typer(
    help="Pool identities: delinquency buckets and the weighted-average coupon.",
    no_args_is_help=True,
)

User = Annotated[str, typer.Option(envvar="CMBS_USER", help="Who is acting.")]
Deal = Annotated[str, typer.Option(help="Deal name, as in the deal registry.")]
Month = Annotated[
    str | None, typer.Option(help="The month, as 'September 2026' (default the latest).")
]
AsOf = Annotated[
    dt.datetime | None,
    typer.Option(formats=["%Y-%m-%d"], help="Read as known on this day (default today)."),
]


@app.command("pool")
def pool(deal: Deal, user: User, period: Month = None, as_of: AsOf = None) -> None:
    """The month's delinquency buckets and WAC; reads only."""
    try:
        month = Period.parse(period) if period is not None else None
    except ValueError as error:
        raise typer.BadParameter(str(error), param_hint="--period") from error
    try:
        with engine.session() as db:
            found = service.pool(
                db,
                reader=user,
                deal=deal,
                period=month,
                as_of=as_of.date() if as_of is not None else None,
            )
    except Refused as refused:
        typer.echo(str(refused), err=True)
        raise typer.Exit(1) from refused
    typer.echo(report.pool_text(found), nl=False)
