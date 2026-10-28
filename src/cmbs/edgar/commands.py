"""`cmbs discover`: look deals up on EDGAR and register what exists for them.

Set CMBS_EDGAR_REPLAY to a recordings directory to replay instead of calling EDGAR.
"""

import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import typer

from cmbs import clock
from cmbs.book.service import book_as_of
from cmbs.coverage.service import classify_deal
from cmbs.db import engine
from cmbs.deals.service import by_name
from cmbs.edgar.discovery import Discovery, discover
from cmbs.edgar.replay import Live, Recorder, Source, default_source
from cmbs.edgar.service import discover_deal
from cmbs.errors import Refused

NAME = "discover"
app = typer.Typer(help="Find the public filings of a deal on EDGAR.", no_args_is_help=True)

User = Annotated[str, typer.Option(envvar="CMBS_USER", help="Who is acting.")]
On = Annotated[
    dt.datetime | None,
    typer.Option(formats=["%Y-%m-%d"], help="Only filings on or before this date (default today)."),
]


@contextmanager
def refusals() -> Iterator[None]:
    """Print a domain refusal as the user-facing message and exit 1."""
    try:
        yield
    except Refused as refused:
        typer.echo(str(refused), err=True)
        raise typer.Exit(1) from refused


def describe(found: Discovery) -> list[str]:
    """What a discovery found, in a few lines: the match, then periods with filings."""
    if found.match.cik is None:
        lines = [f"{found.deal}: no issuing entity on EDGAR"]
    else:
        lines = [f"{found.deal}: CIK {found.match.cik} ({found.match.edgar_name})"]
    if found.match.note:
        lines.append(f"  note: {found.match.note}")
    lines.append(f"  {len(found.filings)} filings on or before {found.on.isoformat()}")
    for form in ("10-D", "ABS-EE"):
        periods = sorted({f.period for f in found.of_form(form) if f.period is not None})
        if periods:
            lines.append(f"  {form}: {len(periods)} months, {periods[0]} to {periods[-1]}")
    others = sorted({f.form for f in found.filings} - {"10-D", "ABS-EE"})
    if others:
        lines.append(f"  other forms: {', '.join(others)}")
    return lines


def _day(on: dt.datetime | None) -> dt.date:
    return on.date() if on is not None else clock.today()


def _look_up(name: str, day: dt.date, user: str, source: Source) -> Discovery | None:
    """One deal in its own transaction; a refusal is printed and the next deal still runs."""
    try:
        with engine.session() as db:
            return discover_deal(db, source, name, day, user=user)
    except Refused as refused:
        typer.echo(f"{' '.join(name.split()) or repr(name)}: {refused}", err=True)
        return None


@app.command("deal")
def discover_one(
    names: Annotated[list[str], typer.Argument(help="Deal display names, e.g. 'BANK 2018-BNK13'.")],
    user: User,
    on: On = None,
) -> None:
    """Look deals up, register their filings and print their coverage class."""
    day = _day(on)
    source = default_source()
    failed = False
    for name in names:
        found = _look_up(name, day, user, source)
        if found is None:
            failed = True
            continue
        with engine.session() as db:
            deal = by_name(db, found.deal)
            if deal is None:  # pragma: no cover - discover_deal registers the deal
                raise RuntimeError(f"{found.deal} was not registered")
            classified = classify_deal(db, deal, day)
        for line in describe(found):
            typer.echo(line)
        typer.echo(f"  coverage: {classified.coverage.value} ({classified.detail})")
    if failed:
        raise typer.Exit(1)


@app.command("book")
def discover_book(user: User, on: On = None) -> None:
    """Look up every deal in the book as of the date, each in its own transaction."""
    day = _day(on)
    source = default_source()
    with refusals(), engine.session() as db:
        book = book_as_of(db, day)
        if book is None:
            typer.echo(f"no positions file is dated on or before {day.isoformat()}", err=True)
            raise typer.Exit(1)
        names = sorted({held.deal for held in book.positions})
    failed = False
    for name in names:
        found = _look_up(name, day, user, source)
        if found is None:
            failed = True
        else:
            typer.echo(describe(found)[0])
    if failed:
        raise typer.Exit(1)


@app.command("record")
def record_responses(
    names: Annotated[list[str], typer.Argument(help="Deal display names to record.")],
    into: Annotated[Path, typer.Option(help="Recordings directory.")] = Path(
        "tests/fixtures/edgar"
    ),
    on: On = None,
) -> None:
    """Fetch each deal's EDGAR responses live and save them for replay in tests."""
    into.mkdir(parents=True, exist_ok=True)
    recorder = Recorder(into, Live())
    with refusals():
        for name in names:
            found = discover(recorder, name, _day(on))
            typer.echo(f"recorded {found.deal}: {len(found.filings)} filings")
