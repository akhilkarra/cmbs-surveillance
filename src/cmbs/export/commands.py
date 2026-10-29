"""`cmbs export`: take the panel away as of a date, or prove it rebuilds from raw filings."""

import datetime as dt
from pathlib import Path
from typing import Annotated

import typer

from cmbs.db import engine
from cmbs.errors import Refused
from cmbs.export.service import export_panel, rebuild

NAME = "export"
app = typer.Typer(help="Export the panel as of a date; rebuild it from raw filings.")

User = Annotated[str, typer.Option(envvar="CMBS_USER", help="Who is acting.")]
Deal = Annotated[str | None, typer.Option(help="One deal; the whole panel when left out.")]


def _day(text: str) -> dt.date:
    try:
        return dt.date.fromisoformat(text)
    except ValueError as error:
        raise typer.BadParameter(f"not a date like 2026-09-20: {text!r}") from error


@app.command("panel")
def panel(
    as_of: Annotated[str, typer.Option("--as-of", help="Known on this date, like 2026-09-20.")],
    user: User,
    deal: Deal = None,
    fmt: Annotated[str, typer.Option("--format", help="csv or parquet.")] = "csv",
    out: Annotated[Path, typer.Option(help="Directory to write the file to.")] = Path("output"),
) -> None:
    """Write the panel as it was known on a date; the file names what produced it."""
    day = _day(as_of)
    try:
        with engine.session() as db:
            made = export_panel(db, user, as_of=day, fmt=fmt, deal=deal)
    except Refused as refused:
        typer.echo(str(refused), err=True)
        raise typer.Exit(1) from refused
    out.mkdir(parents=True, exist_ok=True)
    (out / made.file_name).write_bytes(made.content)
    typer.echo(f"{made.text()}\nwrote {out / made.file_name}")


@app.command("rebuild")
def rebuild_panel(user: User, deal: Deal = None) -> None:
    """Rebuild the panel from the stored raw filings and compare; changes nothing. Exits 1 when
    the rebuilt panel differs."""
    try:
        found = rebuild(user, deal=deal)
    except Refused as refused:
        typer.echo(str(refused), err=True)
        raise typer.Exit(1) from refused
    typer.echo(found.text())
    if not found.identical:
        raise typer.Exit(1)
