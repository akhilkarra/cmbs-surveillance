"""`cmbs extract`: read comments into typed fields, show them, and decide review items.

Extraction replays recorded answers unless `--live` is given; a live run asks the provider for
the model in the settings in force (Jev needs TYPESAFE_API_KEY) and records every call.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import typer

from cmbs.db import engine
from cmbs.domain import Period
from cmbs.errors import Refused
from cmbs.extraction.service import (
    comment_view,
    decide_review,
    export_recording_files,
    extract_comments,
    import_recording_files,
    render_summary,
    render_view,
    review_item,
)
from cmbs.model.client import ModelClient
from cmbs.model.providers import provider_for
from cmbs.model.recordings import DbRecordings
from cmbs.settings.service import in_force

NAME = "extract"
app = typer.Typer(help="Read special-servicing comments into typed fields.", no_args_is_help=True)

User = Annotated[str, typer.Option(envvar="CMBS_USER", help="Who is acting.")]
Deal = Annotated[str, typer.Argument(help="Deal name, as in the deal registry.")]
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


@app.command("comments")
def extract(
    deal: Deal,
    period: Month,
    user: User,
    loan: Annotated[str | None, typer.Option(help="Only this loan.")] = None,
    live: Annotated[
        bool, typer.Option(help="Ask the model provider when no recording answers.")
    ] = False,
) -> None:
    """Extract a month's special-servicing comments into the panel."""
    month = _period(period)
    with refusals(), engine.session() as db:
        provider = provider_for(in_force(db).values.model) if live else None
        client = ModelClient(DbRecordings(db), provider=provider, replay_only=not live)
        summary = extract_comments(db, user=user, deal=deal, period=month, loan=loan, client=client)
    typer.echo(render_summary(summary), nl=False)


@app.command("show")
def show(deal: Deal, loan: Annotated[str, typer.Argument()], period: Month, user: User) -> None:
    """Show a loan's extracted fields with their evidence."""
    month = _period(period)
    with refusals(), engine.session() as db:
        view = comment_view(db, user, deal=deal, loan=loan, period=month)
    if view is None:
        typer.echo(f"loan {loan} has no extracted comment for {month}", err=True)
        raise typer.Exit(1)
    typer.echo(render_view(view), nl=False)


@app.command("review")
def review(item: Annotated[int, typer.Argument(help="Review item number.")], user: User) -> None:
    """Show a review item: the comment, the question and the model's probability."""
    with refusals(), engine.session() as db:
        found = review_item(db, user, item)
    typer.echo(
        f"item {found.id}: loan {found.loan} of {found.deal}, {found.period}, {found.label} "
        f"({found.status}: {found.reason})\n"
        f"question: {found.question}\nprobability: {found.probability}\n"
    )
    for number, text in enumerate(found.sentences, start=1):
        typer.echo(f"  [{number}] {text}")


@app.command("decide")
def decide(
    item: Annotated[int, typer.Argument(help="Review item number.")],
    value: Annotated[str, typer.Argument(help="The field's value.")],
    sentence: Annotated[int, typer.Option(help="The sentence that supports it.")],
    user: User,
) -> None:
    """Decide a review item: the value and the sentence that supports it."""
    with refusals(), engine.session() as db:
        decided = decide_review(db, user, item, value=value, sentence=sentence)
    typer.echo(f"{decided.label}: {decided.display} (human-reviewed by {decided.decided_by})")


@app.command("import-recordings")
def import_files(
    directory: Annotated[Path, typer.Argument(exists=True, file_okay=False)], user: User
) -> None:
    """Load recorded model calls from a folder of JSON files."""
    with refusals(), engine.session() as db:
        count = import_recording_files(db, user, directory)
    typer.echo(f"imported {count} recordings from {directory}")


@app.command("export-recordings")
def export_files(directory: Annotated[Path, typer.Argument(file_okay=False)], user: User) -> None:
    """Write every recorded model call to a folder, one JSON file each."""
    with refusals(), engine.session() as db:
        written = export_recording_files(db, user, directory)
    typer.echo(f"wrote {len(written)} recordings to {directory}")
