"""Steps for features/surveillance/book.feature that load positions or link whole loans.

Steps that build or read alerts, and the book the features README describes, are in
alerts.py, which also registers the step types used here (AlertDay, AlertName, AlertMoney).
These steps tell them a scenario loaded its own positions (`context.positions_loaded`) and
which loans a quant linked (`context.linked`).
"""

from behave import given, then, when  # pyright: ignore[reportAttributeAccessIssue]

from cmbs.book.service import (
    NoteRef,
    PositionLine,
    book_as_of,
    exposure_by_deal,
    link_whole_loan,
    load_positions,
)
from cmbs.domain import money
from cmbs.testing.bdd import in_session

LOADER = "quinn"


@given("a positions file dated {day:AlertDay}:")
def step_positions_file(context, day):
    context.positions_date = day
    context.positions = [
        PositionLine(row["deal"], row["class"], money(row["face amount"])) for row in context.table
    ]


@when('"{user:AlertName}" loads the positions')
def step_loads_positions(context, user):
    in_session(
        lambda db: load_positions(
            db, user=user, as_of=context.positions_date, lines=context.positions
        )
    )
    context.positions_loaded = True


@then("the book holds {count:d} positions in {deals:d} deals as of {day:AlertDay}")
def step_book_holds_count(context, count, deals, day):
    book = in_session(lambda db: book_as_of(db, day))
    assert book is not None and book.as_of == day, book
    assert (len(book.positions), len({held.deal_id for held in book.positions})) == (count, deals)


@then('exposure to "{deal:AlertName}" is {amount:AlertMoney}')
def step_exposure_to(context, deal, amount):
    held = in_session(lambda db: exposure_by_deal(db, context.positions_date))
    assert held.get(deal) == amount, held


@given('a positions file dated {day:AlertDay} with {amount:AlertMoney} of "{deal:AlertName}"')
def step_positions_with(context, day, amount, deal):
    line = PositionLine(deal, "E", amount)
    in_session(lambda db: load_positions(db, user=LOADER, as_of=day, lines=[line]))
    context.positions_loaded = True


@given(
    '"{user:AlertName}" has linked loan "{first:AlertName}" of "{first_deal:AlertName}" and '
    'loan "{second:AlertName}" of "{second_deal:AlertName}" as the same whole loan'
)
def step_linked(context, user, first, first_deal, second, second_deal):
    context.linked = [(first_deal, first), (second_deal, second)]
    notes = [NoteRef(deal, loan) for deal, loan in context.linked]
    in_session(lambda db: link_whole_loan(db, user=user, notes=notes))
