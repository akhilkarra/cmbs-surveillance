"""The facts the panel holds for a loan, and answers composed from them. Read-only.

`loan_facts` gathers, as known on a day (today by default):
- the latest tape row of the loan (`cmbs.panel.service.as_of`): balance, payment status, paid-
  through date, special-servicer transfer and master-servicer return dates, workout strategy
  and modification codes, realized loss, property, and its most recent financials, each cited
  by the ABS-EE accession and the field;
- from the same tape asset as stored in bronze (`bronze.loan_tape_asset.tags`, the asset the
  row was typed from, so the same filing): the advances outstanding, the non-recoverability
  indicator and the liquidation date and code (`ASSET_TAGS`). They are read at ask time rather
  than typed into silver: the silver contract quarantines a loan whose typed value cannot be
  read, and these are facts to cite, not values the panel computes with, so an unreadable one
  is shown as reported instead of dropping the loan;
- the latest 10-D appraisal reduction of the loan filed by that day
  (`cmbs.distribution.service.appraisal_reductions`), cited by its report table and date;
- the latest extracted comment of the loan (`cmbs.extraction.service.comment_view`, which shows
  review decisions and overrides made by that day): each field with a value, cited by the 10-D
  accession, the sentence number and the sentence; a field awaiting review is listed without a
  value. A field the comment does not state is not a fact.

Nothing here writes: asking needs `Action.ASK` and reads the panel, and a refused asker is
audited by `authorize` like any refusal. `ask` composes an answer (`cmbs.ask.answer.compose`),
refusing any request to change data.
"""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from cmbs import clock
from cmbs.access.roles import Action
from cmbs.access.service import authorize
from cmbs.alerts.codes import PAYMENT_STATUS, PROPERTY_TYPE, WORKOUT_STRATEGY, Code, lookup, name_of
from cmbs.ask.answer import (
    COMMENT_FORM,
    NEEDS_REVIEW,
    REPORT_FORM,
    TAPE_FORM,
    Answer,
    Citation,
    Fact,
    Kind,
    compose,
    is_write_request,
    loan_in,
)
from cmbs.deals.service import by_name
from cmbs.deals.tables import Deal
from cmbs.distribution.service import appraisal_reductions, canonical_loan
from cmbs.domain import Period, format_money
from cmbs.edgar.archives import filing_base_url
from cmbs.errors import Refused
from cmbs.extraction.service import CommentView, comment_view
from cmbs.extraction.tables import Extraction
from cmbs.filings.tables import Filing
from cmbs.panel import service as panel
from cmbs.tape.convert import Unreadable, read_date, read_money
from cmbs.tape.tables import LoanPeriod, TapeAsset

# Extracted fields by topic: what a question about the topic is answered from.
COMMENT_TOPICS = {
    "transfer_reason": "special servicing transfer",
    "transfer_date": "special servicing transfer",
    "dscr": "DSCR",
    "dscr_as_of": "DSCR",
    "occupancy": "occupancy",
    "occupancy_as_of": "occupancy",
    "borrower_request": "borrower request",
    "request_status": "borrower request",
    "workout_strategy": "workout strategy",
}


class UnknownDeal(Refused):
    pass


class NoLoan(Refused):
    """The question names no loan, and none was given."""


class UnknownLoan(Refused):
    """The panel holds nothing for the loan by the day asked about."""


@dataclass(frozen=True)
class TapeField:
    """A loan-tape column as a fact: its label, topic, Schedule AL tag and how it reads."""

    column: str
    label: str
    topic: str
    tag: str


TAPE_FIELDS = (
    TapeField("ending_balance", "ending balance", "balance", "reportPeriodEndActualBalanceAmount"),
    TapeField("payment_status", "payment status", "payment status", "paymentStatusLoanCode"),
    TapeField("paid_through_date", "paid-through date", "payment status", "paidThroughDate"),
    TapeField(
        "special_servicer_transfer_date",
        "special-servicer transfer date on the tape",
        "special servicing transfer",
        "mostRecentSpecialServicerTransferDate",
    ),
    TapeField(
        "master_servicer_return_date",
        "master-servicer return date on the tape",
        "special servicing transfer",
        "mostRecentMasterServicerReturnDate",
    ),
    TapeField(
        "workout_strategy",
        "workout strategy on the tape",
        "workout strategy",
        "workoutStrategyCode",
    ),
    TapeField("modification_code", "modification code", "modification", "modificationCode"),
    TapeField("realized_loss", "realized loss", "realized loss", "realizedLossToTrustAmount"),
    TapeField("property_name", "property name", "property", "propertyName"),
    TapeField("property_type", "property type", "property", "propertyTypeCode"),
    TapeField(
        "financials_end", "most recent financials end", "financials", "mostRecentFinancialsEndDate"
    ),
    TapeField("noi", "most recent NOI", "financials", "mostRecentNetOperatingIncomeAmount"),
    TapeField(
        "dscr",
        "most recent DSCR on the tape",
        "DSCR",
        "mostRecentDebtServiceCoverageNetOperatingIncomePercentage",
    ),
    TapeField(
        "occupancy",
        "most recent occupancy on the tape",
        "occupancy",
        "mostRecentPhysicalOccupancyPercentage",
    ),
)


MONEY_COLUMNS = frozenset({"ending_balance", "noi", "realized_loss"})
CODED: dict[str, dict[str, Code]] = {
    "payment_status": PAYMENT_STATUS,
    "workout_strategy": WORKOUT_STRATEGY,
    "property_type": PROPERTY_TYPE,
}


def tape_value(column: str, value: object) -> str:
    """A stored tape value as it reads: money in dollars, codes by name, ratios with 'x'."""
    if column in MONEY_COLUMNS and isinstance(value, Decimal):
        return format_money(value)
    if column in CODED:
        code = str(value)
        found = lookup(CODED[column], code)
        # A code not on the list reads "code XX" once, not "code XX (code XX)".
        return f"{found.name} (code {code})" if found is not None else name_of(CODED[column], code)
    if column == "dscr" and isinstance(value, Decimal):
        return f"{value}x"
    if column == "occupancy" and isinstance(value, Decimal):
        return f"{(value * 100).normalize():f}%"
    return str(value)


def tape_facts(row: LoanPeriod, cik: int) -> list[Fact]:
    """The reported values of a loan's tape row; a value not reported is not a fact."""
    period = Period.of(row.period).label
    facts = []
    for spec in TAPE_FIELDS:
        value = getattr(row, spec.column)
        if value is None:
            continue
        citation = Citation(
            form=TAPE_FORM,
            accession=row.accession,
            filing_date=row.filing_date,
            url=filing_base_url(cik, row.accession),
            tape_field=spec.tag,
        )
        facts.append(
            Fact(
                name=f"tape.{spec.column}",
                label=spec.label,
                topic=spec.topic,
                period=period,
                value=tape_value(spec.column, value),
                status="reported",
                citation=citation,
            )
        )
    return facts


class TagKind(StrEnum):
    MONEY = "money"
    INDICATOR = "indicator"
    DATE = "date"
    CODE = "code"


@dataclass(frozen=True)
class AssetTag:
    """A loan-level Schedule AL tag read from the stored asset: its label, topic and kind."""

    tag: str
    label: str
    topic: str
    kind: TagKind


ASSET_TAGS = (
    AssetTag(
        "totalPrincipalInterestAdvancedOutstandingAmount",
        "P&I advances outstanding",
        "advances",
        TagKind.MONEY,
    ),
    AssetTag(
        "totalTaxesInsuranceAdvancesOutstandingAmount",
        "T&I advances outstanding",
        "advances",
        TagKind.MONEY,
    ),
    AssetTag(
        "otherExpensesAdvancedOutstandingAmount",
        "other expenses advanced",
        "advances",
        TagKind.MONEY,
    ),
    AssetTag(
        "nonRecoverabilityIndicator",
        "advances deemed non-recoverable",
        "advances",
        TagKind.INDICATOR,
    ),
    AssetTag("liquidationPrepaymentDate", "liquidation date", "liquidation", TagKind.DATE),
    AssetTag("liquidationPrepaymentCode", "liquidation code", "liquidation", TagKind.CODE),
)
# XML Schema booleans, as Schedule AL indicators are written.
_YES = frozenset({"true", "1"})
_NO = frozenset({"false", "0"})


def tag_value(kind: TagKind, text: str, label: str) -> str:
    """A reported tag as it reads: money in dollars, an indicator as yes or no, a date in ISO
    form, a code as 'code 3'. A value that cannot be read is shown as reported, never guessed."""
    try:
        if kind is TagKind.MONEY:
            amount = read_money(text, label)
            return format_money(amount) if amount is not None else text
        if kind is TagKind.DATE:
            day = read_date(text, label)
            return day.isoformat() if day is not None else text
    except Unreadable:
        return f"{text!r} as reported, not readable as a {kind.value}"
    if kind is TagKind.INDICATOR:
        lowered = text.lower()
        if lowered in _YES:
            return "yes"
        if lowered in _NO:
            return "no"
        return f"{text!r} as reported, not readable as yes or no"
    return f"code {text}"


def asset_facts(asset: TapeAsset | None, row: LoanPeriod, cik: int) -> list[Fact]:
    """The ASSET_TAGS the loan's stored asset reports; a tag not reported is not a fact."""
    if asset is None:
        return []
    period = Period.of(row.period).label
    facts = []
    for spec in ASSET_TAGS:
        text = str(asset.tags.get(spec.tag) or "").strip()
        if not text:
            continue
        facts.append(
            Fact(
                name=f"tape.{spec.tag}",
                label=spec.label,
                topic=spec.topic,
                period=period,
                value=tag_value(spec.kind, text, spec.label),
                status="reported",
                citation=Citation(
                    form=TAPE_FORM,
                    accession=row.accession,
                    filing_date=row.filing_date,
                    url=filing_base_url(cik, row.accession),
                    tape_field=spec.tag,
                ),
            )
        )
    return facts


def stored_asset(db: Session, row: LoanPeriod) -> TapeAsset | None:
    """The bronze asset the loan row was typed from."""
    return db.get(TapeAsset, (row.accession, row.file_name, row.asset_position))


def appraisal_facts(db: Session, deal: Deal, loan: str, day: dt.date) -> list[Fact]:
    """The appraisal reductions the latest 10-D filed by `day` lists for the loan, each cited
    by its report table and dated as the report dates it. The same amount and date in two
    tables of one report is one fact."""
    found = appraisal_reductions(db, deal=deal.name, loan=loan, as_of=day)
    if not found:
        return []
    latest = max(item.period for item in found)
    facts: list[Fact] = []
    seen: set[tuple[str, Decimal, dt.date | None]] = set()
    for item in found:
        key = (item.accession, item.amount, item.reduction_date)
        if item.period != latest or key in seen:
            continue
        seen.add(key)
        facts.append(
            Fact(
                name="report.appraisal_reduction",
                label="appraisal reduction amount",
                topic="appraisal reduction",
                period=latest.label,
                value=format_money(item.amount),
                status="reported",
                citation=Citation(
                    form=REPORT_FORM,
                    accession=item.accession,
                    filing_date=item.filing_date,
                    url=filing_base_url(deal.cik or 0, item.accession),
                    report_table=item.source_table,
                    dated=item.reduction_date,
                ),
            )
        )
    return facts


def comment_facts(view: CommentView, cik: int) -> list[Fact]:
    """The comment's fields with a value (cited by sentence) or awaiting review (no value). A
    field the comment does not state, or that a reviewer found it cannot settle, is no fact."""
    facts = []
    for shown in view.fields.values():
        waiting = shown.status == NEEDS_REVIEW
        if shown.field not in COMMENT_TOPICS or not (waiting or shown.display is not None):
            continue
        # A field awaiting review cites the sentence the model pointed to, if any: the filer's
        # words, quoted as unreviewed (Q97), never its value.
        sentence, quote = (
            (shown.model_sentence, shown.model_quote)
            if waiting
            else (shown.sentence, shown.evidence)
        )
        citation = (
            None
            if waiting and quote is None
            else Citation(
                form=COMMENT_FORM,
                accession=view.accession,
                filing_date=view.filing_date,
                url=filing_base_url(cik, view.accession),
                sentence=sentence,
                quote=quote,
            )
        )
        facts.append(
            Fact(
                name=shown.field,
                label=shown.label,
                topic=COMMENT_TOPICS[shown.field],
                period=view.period.label,
                value=None if waiting else shown.display,
                status=shown.status,
                citation=citation,
                set_by=shown.overridden_by or shown.decided_by,
            )
        )
    return facts


@dataclass(frozen=True)
class LoanFacts:
    deal: str
    loan: str
    as_of: dt.date
    facts: list[Fact]

    def text(self) -> str:
        head = f"loan {self.loan} of {self.deal}, as known on {self.as_of.isoformat()}"
        if not self.facts:
            return f"{head}: the panel holds no facts"
        return "\n".join([head + ":", *(f"- {fact.text()}" for fact in self.facts)])


def _deal(db: Session, name: str) -> Deal:
    deal = by_name(db, name)
    if deal is None:
        raise UnknownDeal(f"no deal named {name!r}")
    return deal


def _latest_comment(
    db: Session, reader: str, deal: Deal, loan: str, day: dt.date
) -> CommentView | None:
    """The latest month's extracted comment known on `day`."""
    periods = db.scalars(
        select(Extraction.period)
        .join(Filing, Filing.accession == Extraction.accession)
        .where(
            Extraction.deal_id == deal.id,
            Extraction.loan_id == canonical_loan(loan),
            Filing.filing_date <= day,
        )
        .distinct()
        .order_by(Extraction.period.desc())
    ).all()
    for period in periods:
        view = comment_view(
            db, reader, deal=deal.name, loan=loan, period=Period.of(period), as_of=day
        )
        if view is not None:
            return view
    return None


def loan_facts(
    db: Session, reader: str, *, deal: str, loan: str, as_of: dt.date | None = None
) -> LoanFacts:
    """Every fact the panel holds for the loan as known on `as_of` (today by default)."""
    authorize(db, reader, Action.ASK)
    day = as_of or clock.today()
    deal_row = _deal(db, deal)
    cik = deal_row.cik or 0
    rows = panel.as_of(db, day, deal_id=deal_row.id, loan=loan)
    view = _latest_comment(db, reader, deal_row, loan, day)
    if not rows and view is None:
        raise UnknownLoan(f"the panel holds nothing for loan {loan} of {deal_row.name} by {day}")
    facts: list[Fact] = []
    if rows:
        latest = rows[-1]
        facts = tape_facts(latest, cik) + asset_facts(stored_asset(db, latest), latest, cik)
    facts += appraisal_facts(db, deal_row, loan, day)
    if view is not None:
        facts = comment_facts(view, cik) + facts
    return LoanFacts(deal=deal_row.name, loan=loan, as_of=day, facts=facts)


def ask(
    db: Session,
    reader: str,
    question: str,
    *,
    deal: str,
    loan: str | None = None,
    as_of: dt.date | None = None,
) -> Answer:
    """Answer a question about a loan from the panel's facts; the loan the question names
    comes first, then the one given."""
    authorize(db, reader, Action.ASK)
    which = loan_in(question) or loan
    if is_write_request(question):
        deal_row = _deal(db, deal)
        return Answer(question, deal_row.name, which or "", Kind.READ_ONLY)
    if which is None:
        raise NoLoan("name the loan the question is about, like 'loan 2'")
    facts = loan_facts(db, reader, deal=deal, loan=which, as_of=as_of)
    return compose(question, deal=facts.deal, loan=which, facts=facts.facts)
