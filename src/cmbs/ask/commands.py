"""`cmbs ask`: the facts the panel holds for a loan, each cited, and answers from them.

Read-only. `--json` prints the facts as data for the `/cmbs-ask` skill to compose from.
`loans` lists a deal's loans with their special-servicing state (grill log Q96), `history` one
loan's months as filed (Q98).
"""

import dataclasses
import datetime as dt
import json
from typing import Annotated, Any

import typer

from cmbs.ask.answer import Answer
from cmbs.ask.history import LoanHistory
from cmbs.ask.history import history as loan_history
from cmbs.ask.roster import Roster
from cmbs.ask.roster import loans as deal_loans
from cmbs.ask.service import ask, loan_facts
from cmbs.db import engine
from cmbs.errors import Refused

NAME = "ask"
app = typer.Typer(help="Ask the panel; reads only, every fact cited.", no_args_is_help=True)

User = Annotated[str, typer.Option(envvar="CMBS_USER", help="Who is asking.")]
AsOf = Annotated[str | None, typer.Option("--as-of", help="Known on this date (default today).")]
Json = Annotated[bool, typer.Option("--json", help="Print data for the /cmbs-ask skill.")]


def _day(text: str | None) -> dt.date | None:
    if text is None:
        return None
    try:
        return dt.date.fromisoformat(text)
    except ValueError as error:
        raise typer.BadParameter(f"not a date like 2026-10-06: {text!r}") from error


def as_data(value: Any) -> Any:
    """Facts and answers as JSON-ready data: dates in ISO form."""
    return json.loads(json.dumps(dataclasses.asdict(value), default=str))


def answer_data(answer: Answer) -> dict[str, Any]:
    return {**as_data(answer), "text": answer.text()}


def roster_data(roster: Roster) -> dict[str, Any]:
    """The roster as data, each loan with how it reads."""
    data = as_data(roster)
    for row, loan in zip(data["loans"], roster.loans, strict=True):
        row["reads"] = loan.reading()
    return {**data, "text": roster.text()}


def history_data(found: LoanHistory) -> dict[str, Any]:
    """The history as data, each month with how it reads."""
    data = as_data(found)
    for row, month in zip(data["months"], found.months, strict=True):
        row["reads"] = month.reading()
    return {**data, "text": found.text()}


@app.command("facts")
def facts(
    deal: Annotated[str, typer.Argument(help="Deal name.")],
    loan: Annotated[str, typer.Argument(help="Loan, like 2.")],
    user: User,
    as_of: AsOf = None,
    as_json: Json = False,
) -> None:
    """Every fact the panel holds for a loan, with its filing and sentence."""
    day = _day(as_of)
    try:
        with engine.session() as db:
            found = loan_facts(db, user, deal=deal, loan=loan, as_of=day)
    except Refused as refused:
        typer.echo(str(refused), err=True)
        raise typer.Exit(1) from refused
    typer.echo(json.dumps(as_data(found), indent=1) if as_json else found.text())


@app.command("question")
def question(
    text: Annotated[str, typer.Argument(help="The question, in plain English.")],
    deal: Annotated[str, typer.Option(help="The deal the question is about.")],
    user: User,
    loan: Annotated[str | None, typer.Option(help="The loan, if the question names none.")] = None,
    as_of: AsOf = None,
    as_json: Json = False,
) -> None:
    """Answer a question from the panel's facts; never changes anything."""
    day = _day(as_of)
    try:
        with engine.session() as db:
            answer = ask(db, user, text, deal=deal, loan=loan, as_of=day)
    except Refused as refused:
        typer.echo(str(refused), err=True)
        raise typer.Exit(1) from refused
    typer.echo(json.dumps(answer_data(answer), indent=1) if as_json else answer.text())


@app.command("loans")
def loans(
    deal: Annotated[str, typer.Argument(help="Deal name.")],
    user: User,
    as_of: AsOf = None,
    special_servicing: Annotated[
        bool, typer.Option("--special-servicing", help="Only the loans in special servicing.")
    ] = False,
    as_json: Json = False,
) -> None:
    """A deal's loans with their special-servicing state, each citing its tape."""
    day = _day(as_of)
    try:
        with engine.session() as db:
            found = deal_loans(db, user, deal=deal, as_of=day, special_servicing=special_servicing)
    except Refused as refused:
        typer.echo(str(refused), err=True)
        raise typer.Exit(1) from refused
    typer.echo(json.dumps(roster_data(found), indent=1) if as_json else found.text())


@app.command("history")
def history(
    deal: Annotated[str, typer.Argument(help="Deal name.")],
    loan: Annotated[str, typer.Argument(help="Loan, like 2.")],
    user: User,
    as_of: AsOf = None,
    as_json: Json = False,
) -> None:
    """A loan's months as filed, each citing its own tape; nothing computed."""
    day = _day(as_of)
    try:
        with engine.session() as db:
            found = loan_history(db, user, deal=deal, loan=loan, as_of=day)
    except Refused as refused:
        typer.echo(str(refused), err=True)
        raise typer.Exit(1) from refused
    typer.echo(json.dumps(history_data(found), indent=1) if as_json else found.text())
