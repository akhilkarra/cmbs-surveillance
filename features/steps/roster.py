"""Steps for features/surfaces/deal_roster.feature: `cmbs ask loans`, the deal's loans and their
special-servicing state (grill log Q96), driven through `cmbs.ask.roster` in process.

The Background publishes the deal's September 2026 tape with exactly the loans its table lists,
each reporting the table's property, balance, dates and workout code (a blank cell is a tag the
tape does not report), on the synthetic EDGAR (features/steps/published.py). The months are
loaded when the scenario first asks, by one run the morning after the last filing (or by the run
a step names), and asked about at noon that day.
"""

import datetime as dt

from behave import given, then, when  # pyright: ignore[reportAttributeAccessIssue]
from sqlalchemy import select

from cmbs.ask.roster import ROSTER_FIELDS, loans
from cmbs.deals.service import by_name
from cmbs.domain import format_money, money
from cmbs.filings.tables import Filing
from cmbs.testing.bdd import in_session
from features.steps.published import (
    Edit,
    load_run,
    only_loans,
    publication,
    publish,
    set_tags,
    tape_date,
)

TRANSFER_TAG = "mostRecentSpecialServicerTransferDate"
RETURN_TAG = "mostRecentMasterServicerReturnDate"


def _date(text: str) -> str | None:
    text = text.strip()
    return tape_date(dt.date.fromisoformat(text)) if text else None


def roster_edits(table) -> list[Edit]:
    """The month's tape with only the table's loans, each reporting the row's values."""
    edits = [only_loans({row["loan"].strip() for row in table})]
    for row in table:
        tags = {
            "property/propertyName": row["property"].strip(),
            "reportPeriodEndActualBalanceAmount": str(money(row["ending balance"])),
            TRANSFER_TAG: _date(row["transfer date"]),
            RETURN_TAG: _date(row["return date"]),
            "workoutStrategyCode": row["workout strategy"].strip() or None,
        }
        edits.append(set_tags(row["loan"].strip(), tags))
    return edits


@given('"{deal:DashName}" reported these loans in {period:DashMonth}, filed {filed:DashDay}:')
def step_reported_loans(context, deal, period, filed):
    month = publication(context, deal).month(period, filed)
    context.roster_table = list(context.table)
    context.roster_period = period
    month.tape.extend(roster_edits(context.table))


@given('loan "{loan}" of "{deal:DashName}" last had a balance above zero in {period:DashMonth}')
def step_last_above_zero(context, loan, deal, period):
    """The months from `period` up to the Background's report the same loans and values, but
    the loan's balance is above zero in `period` and zero after it."""
    found = publication(context, deal)
    latest = context.roster_period
    rows = {row["loan"].strip(): row for row in context.roster_table}
    assert money(rows[loan]["ending balance"]) == 0, f"loan {loan} reports a balance"
    month = period
    while month < latest:
        edits = roster_edits(context.roster_table)
        if month == period:
            edits.append(set_tags(loan, {"reportPeriodEndActualBalanceAmount": "8000000.00"}))
        found.month(month).tape.extend(edits)
        month = month.shift(1)


@given('the latest run that loaded "{deal:DashName}" was run {number:d} on {day:DashDay}')
def step_latest_run(context, deal, number, day):
    publication(context, deal)
    load_run(context, number, day)


@given(
    '"{deal:DashName}" reported loan "{loan}" transferred to special servicing on '
    "{day:DashDay} in {period:DashMonth}, filed {filed:DashDay}"
)
def step_reported_transfer(context, deal, loan, day, period, filed):
    month = publication(context, deal).month(period, filed)
    month.tape.extend(roster_edits(context.roster_table))
    month.tape.append(set_tags(loan, {TRANSFER_TAG: tape_date(day)}))


def _ask(context, user, deal, as_of=None, special_servicing=False):
    publish(context)
    context.deal = deal
    context.roster = in_session(
        lambda db: loans(db, user, deal=deal, as_of=as_of, special_servicing=special_servicing)
    )


@when('"{user}" asks for the loans of "{deal:DashName}"')
def step_asks_loans(context, user, deal):
    _ask(context, user, deal)


@when('"{user}" asks for the loans of "{deal:DashName}" as of {day:DashDay}')
def step_asks_loans_as_of(context, user, deal, day):
    _ask(context, user, deal, as_of=day)


@when('"{user}" asks for the loans of "{deal:DashName}" in special servicing')
def step_asks_special_servicing(context, user, deal):
    _ask(context, user, deal, special_servicing=True)


def _row(context, loan: str):
    found = [row for row in context.roster.loans if row.loan == loan]
    assert len(found) == 1, f"loan {loan} is listed {len(found)} times:\n{context.roster.text()}"
    assert f"- {found[0].text()}" in context.roster.text(), context.roster.text()
    return found[0]


@then("the roster lists, in this order:")
def step_roster_lists(context):
    listed = [(row.loan, row.state.value, row.since or "") for row in context.roster.loans]
    expected = [
        (row["loan"].strip(), row["state"].strip(), row["since"].strip()) for row in context.table
    ]
    assert listed == expected, context.roster.text()


@then(
    "each row names its property and ending balance and cites the {period:DashMonth} ABS-EE "
    "accession and the tape fields it read"
)
def step_rows_cite(context, period):
    def read(db):
        deal = by_name(db, context.deal)
        assert deal is not None
        return db.scalars(
            select(Filing.accession).where(
                Filing.deal_id == deal.id,
                Filing.form == "ABS-EE",
                Filing.period == period.first_day,
            )
        ).one()

    accession = in_session(read)
    table = {row["loan"].strip(): row for row in context.roster_table}
    assert sorted(row.loan for row in context.roster.loans) == sorted(table)
    for row in context.roster.loans:
        given_row = table[row.loan]
        assert row.property_name == given_row["property"].strip(), row
        assert row.ending_balance == format_money(money(given_row["ending balance"])), row
        assert (row.citation.form, row.citation.accession) == ("ABS-EE", accession), row
        assert row.tape_fields == ROSTER_FIELDS, row
        text = _row(context, row.loan).text()
        for part in (row.property_name, row.ending_balance, accession, *ROSTER_FIELDS):
            assert part in text, (part, text)


@then('loan "{loan}" reads "{text}"')
def step_loan_reads(context, loan, text):
    assert _row(context, loan).reading() == text, _row(context, loan).reading()


@then('the roster opens with "{text}"')
def step_roster_opens(context, text):
    assert context.roster.text().startswith(text), context.roster.text()


@then("the roster lists loans {listed} only")
def step_roster_lists_only(context, listed):
    expected = [part.strip().strip('"') for part in listed.replace(" and ", ", ").split(",")]
    assert [row.loan for row in context.roster.loans] == expected, context.roster.text()
