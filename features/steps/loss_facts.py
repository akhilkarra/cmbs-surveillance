"""Steps for features/surfaces/loss_facts.feature: the facts that size a loss in `cmbs ask facts`
(grill log Q98), driven through `cmbs.ask.service.loan_facts` in process.

The Given steps state what the deal's September 2026 tape and 10-D report for each loan; the
month is published with those changes on the synthetic EDGAR (features/steps/published.py) and
loaded when the scenario first asks, the morning after it is filed, and asked about at noon.
"""

import datetime as dt
import re

from behave import given, then, when  # pyright: ignore[reportAttributeAccessIssue]
from sqlalchemy import select

from cmbs.ask.service import loan_facts
from cmbs.deals.service import by_name
from cmbs.distribution.layouts import APPRAISAL_TABLES
from cmbs.domain import Period, format_money, money
from cmbs.filings.tables import Filing
from cmbs.testing.bdd import in_session
from features.steps.published import Edit, publication, publish, set_tags, tape_date

APPRAISAL_TABLE_NAMES = {spec.name for specs in APPRAISAL_TABLES.values() for spec in specs}


def _tag_value(text: str) -> str:
    """A table cell as the tape writes it: an amount without separators, anything else as is."""
    try:
        return str(money(text))
    except ValueError:
        return text.strip()


@given(
    'loan "{loan}" of "{deal:DashName}" was reported in {period:DashMonth}, '
    "filed {filed:DashDay}, with:"
)
def step_reported_with(context, loan, deal, period, filed):
    tags = {row["tape field"].strip(): _tag_value(row["value"]) for row in context.table}
    context.loan_tags = tags
    publication(context, deal).month(period, filed).tape.append(set_tags(loan, tags))


def appraisal_row(loan: str, amount: str, day: dt.date) -> Edit:
    """The 10-D with the loan's row of the appraisal reduction table giving `amount` on `day`."""
    row = re.compile(
        rf"(<tr><td>0*{re.escape(loan)}</td><td>\d+</td><td>[\d,.-]*</td>)"
        r"<td>[^<]*</td><td>[\d,.-]+</td>"
    )
    cells = rf"\1<td>{day.strftime('%m/%d/%Y')}</td><td>{amount}</td>"

    def edit(content: bytes) -> bytes:
        changed, count = row.subn(cells, content.decode())
        assert count == 1, f"the 10-D has {count} appraisal rows for loan {loan}"
        return changed.encode()

    return edit


@given(
    'the {period:DashMonth} 10-D of "{deal:DashName}" reports for loan "{loan}" an appraisal '
    "reduction of {amount} dated {day:DashDay}"
)
def step_report_appraisal(context, period, deal, loan, amount, day):
    figure = f"{money(amount):,.2f}"
    publication(context, deal).month(period).report.append(appraisal_row(loan, figure, day))


@given(
    'loan "{loan}" of "{deal:DashName}" was reported in {period:DashMonth} with special-servicer '
    "transfer date {transfer:DashDay} and master-servicer return date {returned:DashDay}"
)
def step_reported_return(context, loan, deal, period, transfer, returned):
    tags = {
        "mostRecentSpecialServicerTransferDate": tape_date(transfer),
        "mostRecentMasterServicerReturnDate": tape_date(returned),
    }
    publication(context, deal).month(period).tape.append(set_tags(loan, tags))


@given(
    'loan "{loan}" of "{deal:DashName}" was reported in {period:DashMonth} with a realized loss '
    "of {amount}, liquidation date {day:DashDay} and liquidation code {code}"
)
def step_reported_liquidation(context, loan, deal, period, amount, day, code):
    tags = {
        "realizedLossToTrustAmount": str(money(amount)),
        "liquidationPrepaymentDate": tape_date(day),
        "liquidationPrepaymentCode": code,
    }
    publication(context, deal).month(period).tape.append(set_tags(loan, tags))


@when('"{user}" asks for the facts of loan "{loan}" of "{deal:DashName}"')
def step_asks_facts(context, user, loan, deal):
    publish(context)
    context.deal, context.loan = deal, loan
    context.facts = in_session(lambda db: loan_facts(db, user, deal=deal, loan=loan))


def _accession(context, form: str, period: Period) -> str:
    def read(db):
        deal = by_name(db, context.deal)
        assert deal is not None
        return db.scalars(
            select(Filing.accession).where(
                Filing.deal_id == deal.id, Filing.form == form, Filing.period == period.first_day
            )
        ).one()

    return in_session(read)


def _fact(context, label: str):
    found = [fact for fact in context.facts.facts if fact.label == label]
    assert len(found) == 1, f"{len(found)} facts labelled {label!r}:\n{context.facts.text()}"
    assert f"- {found[0].text()}" in context.facts.text(), context.facts.text()
    return found[0]


def _tape_fact(context, label: str, value: str, tag: str) -> None:
    fact = _fact(context, label)
    assert fact.value == value, fact
    cited = fact.citation
    assert cited is not None and (cited.form, cited.tape_field) == ("ABS-EE", tag), fact
    period = Period.parse(fact.period)
    assert cited.accession == _accession(context, "ABS-EE", period), fact


@then("the facts include:")
def step_facts_include(context):
    for row in context.table:
        label, value, cited_to = (row[name].strip() for name in ("fact", "value", "cited to"))
        form, _, rest = cited_to.partition(", ")
        if form == "ABS-EE":
            _tape_fact(context, label, value, rest)
            continue
        assert form == "10-D", cited_to
        fact = _fact(context, label)
        assert fact.value == value, fact
        cited = fact.citation
        assert cited is not None and cited.form == "10-D", fact
        assert cited.accession == _accession(context, "10-D", Period.parse(fact.period)), fact
        what, _, dated = rest.partition(", dated ")
        assert what == "appraisal reduction table", cited_to
        assert cited.report_table in APPRAISAL_TABLE_NAMES, cited
        assert cited.dated == dt.date.fromisoformat(dated), cited


@then("the facts include the master-servicer return date {day:DashDay}, cited to {tag}")
def step_facts_return_date(context, day, tag):
    _tape_fact(context, "master-servicer return date on the tape", day.isoformat(), tag)


@then("the facts include a realized loss of {amount}, cited to {tag}")
def step_facts_realized_loss(context, amount, tag):
    _tape_fact(context, "realized loss", format_money(money(amount)), tag)


@then(
    "the facts include the liquidation date {day:DashDay} and liquidation code {code}, cited to "
    "the tape"
)
def step_facts_liquidation(context, day, code):
    # The tape's liquidation/prepayment date and code: a liquidation's, in this scenario.
    _tape_fact(context, "liquidation/prepayment date", day.isoformat(), "liquidationPrepaymentDate")
    _tape_fact(context, "liquidation/prepayment code", f"code {code}", "liquidationPrepaymentCode")
