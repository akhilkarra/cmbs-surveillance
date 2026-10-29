"""`cmbs quality`: load the golden set, build and read quality reports, confirm label candidates."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import typer

from cmbs.access.roles import Action
from cmbs.access.service import authorize
from cmbs.db import engine
from cmbs.errors import Refused
from cmbs.quality import service
from cmbs.quality.files import BadFile, read_golden, read_predictions
from cmbs.quality.render import candidate_text, report_text

NAME = "quality"
app = typer.Typer(
    help="Extraction quality: the golden set, label candidates and reports.", no_args_is_help=True
)

User = Annotated[str, typer.Option(envvar="CMBS_USER", help="Who is acting.")]
File = Annotated[Path, typer.Argument(help="A JSON file.", exists=True, dir_okay=False)]
Candidate = Annotated[int, typer.Argument(help="The candidate's number.")]


@contextmanager
def refusals() -> Iterator[None]:
    """Print a domain refusal as the user-facing message and exit 1."""
    try:
        yield
    except Refused as refused:
        typer.echo(str(refused), err=True)
        raise typer.Exit(1) from refused


def _json(path: Path) -> object:
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as error:
        raise BadFile(f"{path.name} is not JSON: {error.msg} at line {error.lineno}") from None


@app.command("load-golden")
def load_golden(path: File, user: User) -> None:
    """Load a golden-set labels file (every labeller must be an analyst)."""
    with refusals(), engine.session() as db:
        # The right is checked before the file is read, so a refusal is audited either way.
        authorize(db, user, Action.PROPOSE_SETTING)
        comments = read_golden(_json(path))
        new = service.import_golden_set(db, user=user, comments=comments, source_name=path.name)
    typer.echo(f"{len(comments)} golden comments in {path.name}, {new} new")


@app.command("report")
def report(path: File, user: User) -> None:
    """Measure a predictions file against the golden set and store the report."""
    with refusals(), engine.session() as db:
        authorize(db, user, Action.PROPOSE_SETTING)
        predictions = read_predictions(_json(path))
        built = service.build_report(db, user=user, predictions=predictions)
    typer.echo(report_text(built), nl=False)


@app.command("show")
def show(
    user: User,
    number: Annotated[int | None, typer.Argument(help="Default: the latest report.")] = None,
) -> None:
    """A stored quality report."""
    with refusals(), engine.session() as db:
        found = service.report(db, reader=user, report_id=number)
    typer.echo(report_text(found), nl=False)


@app.command("extend")
def extend(user: User) -> None:
    """Propose the panel values a person decided as golden-set candidates."""
    with refusals(), engine.session() as db:
        extended = service.extend_golden_set(db, user=user)
    noun = "decision" if extended.skipped == 1 else "decisions"
    typer.echo(f"{len(extended.proposed)} proposed, {extended.skipped} {noun} skipped")
    for candidate in extended.proposed:
        typer.echo(candidate_text(candidate))


@app.command("candidates")
def list_candidates(user: User) -> None:
    """Candidates waiting for a second analyst."""
    with refusals(), engine.session() as db:
        waiting = service.candidates(db, reader=user)
    if not waiting:
        typer.echo("no candidates waiting")
    for candidate in waiting:
        typer.echo(candidate_text(candidate))


@app.command("confirm")
def confirm(number: Candidate, user: User) -> None:
    """Confirm a candidate's labels as a second analyst; it joins the golden set."""
    with refusals(), engine.session() as db:
        confirmed = service.confirm_candidate(db, user=user, item_id=number)
    typer.echo(candidate_text(confirmed))


@app.command("reject")
def reject(
    number: Candidate,
    user: User,
    reason: Annotated[str, typer.Option(help="Why the label is wrong.")],
) -> None:
    """Reject a candidate as a second analyst; it stays out of the golden set."""
    with refusals(), engine.session() as db:
        rejected = service.reject_candidate(db, user=user, item_id=number, reason=reason)
    typer.echo(f"{candidate_text(rejected)}; rejected: {rejected.reason}")
