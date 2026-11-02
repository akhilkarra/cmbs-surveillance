"""`cmbs brief`: the committee brief on a deal's special-servicing book (grill log Q98).

Read-only. `--json` prints the brief as data, with its text, for the `/cmbs-brief` skill.
"""

import datetime as dt
import json
from typing import Annotated, Any

import typer

from cmbs.ask.commands import as_data
from cmbs.brief.service import Brief, brief
from cmbs.db import engine
from cmbs.errors import Refused

NAME = "brief"
app = typer.Typer(
    help="Brief committee from the panel; reads only, every value cited.", no_args_is_help=True
)


def brief_data(found: Brief) -> dict[str, Any]:
    """The brief as data, with the text it reads as."""
    return {**as_data(found), "text": found.text()}


def _day(text: str | None) -> dt.date | None:
    if text is None:
        return None
    try:
        return dt.date.fromisoformat(text)
    except ValueError as error:
        raise typer.BadParameter(f"not a date like 2026-10-06: {text!r}") from error


@app.command("deal")
def deal(
    name: Annotated[str, typer.Argument(metavar="DEAL", help="Deal name.")],
    user: Annotated[str, typer.Option(envvar="CMBS_USER", help="Who is asking.")],
    as_of: Annotated[
        str | None, typer.Option("--as-of", help="Known on this date (default today).")
    ] = None,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print data for the /cmbs-brief skill.")
    ] = False,
) -> None:
    """The deal's loans in special servicing, a cited section each, then the other loans."""
    day = _day(as_of)
    try:
        with engine.session() as db:
            found = brief(db, user, deal=name, as_of=day)
    except Refused as refused:
        typer.echo(str(refused), err=True)
        raise typer.Exit(1) from refused
    typer.echo(json.dumps(brief_data(found), indent=1) if as_json else found.text())
