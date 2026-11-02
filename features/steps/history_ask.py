"""Steps for features/surfaces/loan_history_ask.feature: `cmbs ask history`, a loan's months as
filed (grill log Q98), driven through `cmbs.ask.history` in process.

The Background is the loan history chart's (features/steps/history_chart.py): each month
published on its stated day with the loan reporting the table's values, loaded by one run, and
read on 2026-10-28 at noon.
"""

import datetime as dt

from behave import then, when  # pyright: ignore[reportAttributeAccessIssue]
from sqlalchemy import select

from cmbs.ask.history import history
from cmbs.deals.service import by_name
from cmbs.domain import Period
from cmbs.filings.tables import Filing
from cmbs.testing.bdd import in_session
from features.steps.history_chart import words


def _ask(context, user, loan, deal, as_of: dt.date | None = None):
    context.deal = deal
    context.history = in_session(lambda db: history(db, user, deal=deal, loan=loan, as_of=as_of))


@when('"{user}" asks for the history of loan "{loan}" of "{deal:DashName}"')
def step_asks_history(context, user, loan, deal):
    _ask(context, user, loan, deal)


@when('"{user}" asks for the history of loan "{loan}" of "{deal:DashName}" as of {day:DashDay}')
def step_asks_history_as_of(context, user, loan, deal, day):
    _ask(context, user, loan, deal, day)


def _month(context, period: Period):
    found = [month for month in context.history.months if month.period == period.label]
    assert len(found) == 1, context.history.text()
    assert f"- {found[0].text()}" in context.history.text(), context.history.text()
    return found[0]


@then("the history lists {names} {year:d} in that order")
def step_history_lists(context, names, year):
    expected = [Period.parse(f"{name} {year}").label for name in words(names)]
    listed = [month.period for month in context.history.months]
    assert listed == expected, context.history.text()


@then('{period:DashMonth} reads "{text}"')
def step_month_reads(context, period, text):
    """The month's values read so: all of them, or the ones named, in their order."""
    reading = _month(context, period).reading()
    parts = reading.split("; ")
    expected = text.split("; ")
    assert all(part in parts for part in expected), reading
    assert [part for part in parts if part in expected] == expected, reading


@then("each month cites its own ABS-EE accession")
def step_months_cite(context):
    def read(db):
        deal = by_name(db, context.deal)
        assert deal is not None
        return {
            Period.of(period).label: (accession, filed)
            for accession, period, filed in db.execute(
                select(Filing.accession, Filing.period, Filing.filing_date).where(
                    Filing.deal_id == deal.id, Filing.form == "ABS-EE"
                )
            )
        }

    filings = in_session(read)
    assert context.history.months, "no month is listed"
    accessions = [month.citation.accession for month in context.history.months]
    assert len(set(accessions)) == len(accessions), accessions
    for month in context.history.months:
        cited = month.citation
        assert cited.form == "ABS-EE", cited
        assert (cited.accession, cited.filing_date) == filings[month.period], (month, filings)
        assert cited.accession in _month(context, Period.parse(month.period)).text()


@then("the history ends at {period:DashMonth}")
def step_history_ends(context, period):
    listed = [month.period for month in context.history.months]
    assert listed and listed[-1] == period.label, context.history.text()
