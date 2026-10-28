"""Steps for loan tape ingestion, point-in-time reads and financials staleness.

Fixture-deal steps start from tests/fixtures/schedule_al_minimal.xml and may change it before it
is loaded ("loan 5 reports an ending balance of ..."); the tape is loaded at the first step that
needs it. Real-data steps load the real EX-102 files in tests/fixtures/tape/ (gzip-compressed,
byte for byte as filed) and take filing dates and accession numbers from the recorded
submissions JSON there.
"""

import datetime as dt
import gzip
import hashlib
import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path

import parse
from behave import given, register_type, then, when  # pyright: ignore[reportAttributeAccessIssue]
from lxml import etree  # pyright: ignore[reportAttributeAccessIssue]
from sqlalchemy import text

from cmbs.absee.schedule_al import NAMESPACE, AssetKind, parse_assets
from cmbs.domain import Period, money
from cmbs.edgar.submissions import recent_filings
from cmbs.panel import service as panel
from cmbs.panel.contract import model_input, rejections
from cmbs.tape.service import AMENDMENT, ORIGINAL, load_tape, quarantined_loans, stored_assets
from cmbs.tape.tables import LoanPeriod
from cmbs.testing.bdd import changed_tables, data_snapshot, in_session

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures"
FIXTURE_TAPE = FIXTURES / "schedule_al_minimal.xml"
FIXTURE_DEAL = "Fixture Trust 2026-F1"
# The fixture deal is synthetic: a CIK and accession numbers no real filer has.
FIXTURE_CIK = 9_900_001
FIXTURE_ACCESSIONS = iter(f"0009900001-26-{number:06d}" for number in range(101, 200))
FIXTURE_PERIOD = Period(2026, 9)
FIXTURE_PERIOD_END = dt.date(2026, 9, 11)
URI = NAMESPACE.strip("{}")


@dataclass(frozen=True)
class RealTape:
    file: str
    cik: int
    accession: str
    period: Period
    closed: Period


REAL_TAPES = {
    "BANK 2018-BNK13": [
        RealTape(
            "bank-2018-bnk13_2026-08_ex102.xml.gz",
            1744707,
            "0001888524-26-015516",
            Period(2026, 8),
            Period(2018, 9),
        ),
        RealTape(
            "bank-2018-bnk13_2026-09_ex102.xml.gz",
            1744707,
            "0001888524-26-017705",
            Period(2026, 9),
            Period(2018, 9),
        ),
    ],
    "BANK5 2026-5YR23": [
        RealTape(
            "bank5-2026-5yr23_2026-09_ex102.xml.gz",
            2140978,
            "0001888524-26-018152",
            Period(2026, 9),
            Period(2026, 6),
        ),
    ],
}


@parse.with_pattern(r"\d{4}-\d{2}-\d{2}")
def _day(string: str) -> dt.date:
    return dt.date.fromisoformat(string)


@parse.with_pattern(r"[A-Z][a-z]+ \d{4}")
def _month(string: str) -> Period:
    return Period.parse(string)


@parse.with_pattern(r'[^"]+')
def _name(string: str) -> str:
    return string


@parse.with_pattern(r"-?\$-?[\d,]+\.\d{2}")
def _money(string: str) -> Decimal:
    return money(string)


@parse.with_pattern(r"\d+\.\d+")
def _ratio(string: str) -> Decimal:
    return Decimal(string)


register_type(TapeDay=_day, TapeMonth=_month, TapeName=_name, TapeMoney=_money, TapeRatio=_ratio)


@dataclass(frozen=True)
class Tape:
    """A tape waiting to be loaded, or already loaded."""

    deal: str
    cik: int
    content: bytes
    file_name: str
    accession: str
    filing_date: dt.date
    form: str = ORIGINAL
    amends: str | None = None


# ---------------------------------------------------------------------------- tape helpers


def _tree(content: bytes) -> ET.Element:
    return ET.fromstring(content)


def _bytes(root: ET.Element) -> bytes:
    ET.register_namespace("", URI)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _asset(root: ET.Element, loan: str) -> ET.Element:
    for asset in root.findall(NAMESPACE + "assets"):
        number = asset.find(NAMESPACE + "assetNumber")
        if number is not None and (number.text or "").strip() == loan:
            return asset
    raise AssertionError(f"the tape has no asset {loan!r}")


def _container(asset: ET.Element, on_property: bool) -> ET.Element:
    if not on_property:
        return asset
    prop = asset.find(NAMESPACE + "property")
    if prop is None:
        prop = ET.SubElement(asset, NAMESPACE + "property")
    return prop


def with_tag(content: bytes, loan: str, tag: str, value: str, *, on_property: bool) -> bytes:
    root = _tree(content)
    parent = _container(_asset(root, loan), on_property)
    element = parent.find(NAMESPACE + tag)
    if element is None:
        element = ET.SubElement(parent, NAMESPACE + tag)
    element.text = value
    return _bytes(root)


def without_tags(content: bytes, loan: str, prefix: str) -> bytes:
    """Remove every property tag of `loan` whose name starts with `prefix`."""
    root = _tree(content)
    prop = _container(_asset(root, loan), on_property=True)
    for element in list(prop):
        if element.tag.removeprefix(NAMESPACE).startswith(prefix):
            prop.remove(element)
    return _bytes(root)


def fixture_tape(filing_date: dt.date) -> Tape:
    return Tape(
        deal=FIXTURE_DEAL,
        cik=FIXTURE_CIK,
        content=FIXTURE_TAPE.read_bytes(),
        file_name="schedule_al_minimal.xml",
        accession=next(FIXTURE_ACCESSIONS),
        filing_date=filing_date,
    )


def filing_dates(cik: int) -> dict[str, dt.date]:
    recorded = json.loads((FIXTURES / "tape" / f"submissions_CIK{cik:010d}.json").read_text())
    return {
        filing.accession: dt.date.fromisoformat(filing.filing_date)
        for filing in recent_filings(recorded)
    }


def real_tapes(deal: str) -> list[Tape]:
    return [
        Tape(
            deal=deal,
            cik=real.cik,
            content=gzip.decompress((FIXTURES / "tape" / real.file).read_bytes()),
            file_name=real.file,
            accession=real.accession,
            filing_date=filing_dates(real.cik)[real.accession],
        )
        for real in REAL_TAPES[deal]
    ]


def real_tape(deal: str, period: Period) -> Tape:
    found = [
        tape
        for tape, real in zip(real_tapes(deal), REAL_TAPES[deal], strict=True)
        if real.period == period
    ]
    assert found, f"no recorded {period} tape of {deal}"
    return found[0]


def ingest(tape: Tape):
    return in_session(
        lambda db: load_tape(
            db,
            deal=tape.deal,
            content=tape.content,
            file_name=tape.file_name,
            accession=tape.accession,
            filing_date=tape.filing_date,
            form=tape.form,
            amends=tape.amends,
            cik=tape.cik,
        )
    )


def ensure_loaded(context) -> None:
    """Load the scenario's pending tape, if it has not been loaded yet."""
    if not getattr(context, "tape_loaded", False):
        context.summary = ingest(context.tape)
        context.tape_loaded = True


def prepare(context, tape: Tape) -> None:
    context.tape = tape
    context.deal = tape.deal
    context.tape_loaded = False


def change_tape(context, content: bytes) -> None:
    assert not getattr(context, "tape_loaded", False), "the tape was loaded before this change"
    context.tape = replace(context.tape, content=content)


def rows_for(context, period: Period) -> list[LoanPeriod]:
    def read(db):
        deal = panel.deal_named(db, context.deal)
        return panel.as_of(db, dt.date.max, deal_id=deal.id, period=period)

    return in_session(read)


def loan_row(context, loan: str, period: Period = FIXTURE_PERIOD) -> LoanPeriod:
    rows = [row for row in rows_for(context, period) if row.loan == loan]
    assert len(rows) == 1, f"loan {loan} has {len(rows)} rows for {period}"
    return rows[0]


def _plain(amount: Decimal) -> str:
    return f"{amount:.2f}"


# ---------------------------------------------------------------------------- ingestion


@given('the {period:TapeMonth} ABS-EE for "{deal:TapeName}", filed on {filed:TapeDay}')
def step_fixture_abs_ee(context, period, deal, filed):
    assert (deal, period) == (FIXTURE_DEAL, FIXTURE_PERIOD), "only the fixture tape is on file"
    prepare(context, fixture_tape(filed))


@given(
    "the tape lists {assets:d} assets: {loans:d} loans, the pari passu companion note "
    '"{note:TapeName}" and the portfolio property "{prop:TapeName}"'
)
def step_tape_lists(context, assets, loans, note, prop):
    parsed = parse_assets(context.tape.content)
    kinds = {asset.asset_number: asset.kind for asset in parsed}
    assert len(parsed) == assets, f"the tape lists {len(parsed)} assets"
    assert sum(kind is AssetKind.LOAN for kind in kinds.values()) == loans, kinds
    assert kinds[note] is AssetKind.COMPANION_NOTE, kinds
    assert kinds[prop] is AssetKind.PORTFOLIO_PROPERTY, kinds


@given("the tape has already been ingested")
def step_already_ingested(context):
    ensure_loaded(context)


@when("the tape is ingested")
def step_ingest(context):
    context.tape_loaded = False
    ensure_loaded(context)


@when("the tape is ingested again")
def step_ingest_again(context):
    context.summary = ingest(context.tape)


@then("{period:TapeMonth} has {count:d} loan rows")
def step_period_has_rows(context, period, count):
    rows = rows_for(context, period)
    assert len(rows) == count, f"{period} has {len(rows)} loan rows: {[r.loan for r in rows]}"


@then("{period:TapeMonth} still has {count:d} loan rows")
def step_period_still_has_rows(context, period, count):
    assert context.summary.already_loaded, "the second load did not find the tape loaded"
    step_period_has_rows(context, period, count)


@then(
    'loan "{loan:TapeName}" has an ending balance of {amount:TapeMoney} '
    "and is paid through {day:TapeDay}"
)
def step_balance_and_paid_through(context, loan, amount, day):
    row = loan_row(context, loan)
    assert row.ending_balance == amount, f"ending balance {row.ending_balance}"
    assert row.paid_through_date == day, f"paid through {row.paid_through_date}"


@then("every loan row records the filing's accession number and the file's SHA-256 hash")
def step_rows_trace_to_file(context):
    digest = hashlib.sha256(context.tape.content).hexdigest()
    rows = rows_for(context, FIXTURE_PERIOD)
    assert rows, "no loan rows"
    for row in rows:
        assert row.accession == context.tape.accession, (row.loan, row.accession)
        assert row.file_sha256 == digest, (row.loan, row.file_sha256)


def _as_reported(content: bytes) -> list[tuple[dict[str, str], list[dict[str, str]]]]:
    """Every asset's leaf tags read with lxml, a second parser, to check the stored copy."""
    root = etree.fromstring(content)
    found = []
    for asset in root.iterfind("a:assets", {"a": URI}):
        tags = {etree.QName(e).localname: e.text or "" for e in asset if len(e) == 0}
        props = [
            {etree.QName(e).localname: e.text or "" for e in prop}
            for prop in asset.iterfind("a:property", {"a": URI})
        ]
        found.append((tags, props))
    return found


@then("all {count:d} assets are stored exactly as reported")
def step_assets_stored(context, count):
    stored = in_session(lambda db: stored_assets(db, context.tape.accession))
    assert len(stored) == count, f"{len(stored)} assets stored"
    expected = _as_reported(context.tape.content)
    assert [(asset.tags, asset.properties) for asset in stored] == expected

    def raw_bytes(db):
        return db.execute(
            text("SELECT content FROM raw.filing_file WHERE accession = :a"),
            {"a": context.tape.accession},
        ).scalar_one()

    assert bytes(in_session(raw_bytes)) == context.tape.content


@then('neither "{first:TapeName}" nor "{second:TapeName}" appears as a loan row')
def step_not_loan_rows(context, first, second):
    loans = {row.loan for row in rows_for(context, FIXTURE_PERIOD)}
    assert not {first, second} & loans, loans


@then("no stored value has changed")
def step_nothing_changed(context):
    assert context.snapshot is not None, "no When step ran before this check"
    changed = changed_tables(context.snapshot, data_snapshot())
    assert not changed, f"stored data changed in {changed}"


@given('loan "{loan:TapeName}" reports an ending balance of {amount:TapeMoney}')
def step_reports_balance(context, loan, amount):
    content = with_tag(
        context.tape.content,
        loan,
        "reportPeriodEndActualBalanceAmount",
        _plain(amount),
        on_property=False,
    )
    change_tape(context, content)


@then('loan "{loan:TapeName}" is quarantined with the reason "{reason}"')
def step_quarantined(context, loan, reason):
    stored = in_session(lambda db: quarantined_loans(db, context.tape.accession))
    reasons = {q.loan: q.reason for q in stored}
    assert reasons.get(loan) == reason, reasons
    assert loan not in {row.loan for row in rows_for(context, FIXTURE_PERIOD)}


@then("the ingestion summary reports {count:d} quarantined {noun}")
def step_summary_quarantined(context, count, noun):
    assert noun in ("loan", "loans")
    assert len(context.summary.quarantined) == count, context.summary
    assert f"{count} {noun} quarantined" in context.summary.text(), context.summary.text()


@given('loan "{loan:TapeName}" has no most-recent financials tags')
def step_no_financials_tags(context, loan):
    change_tape(context, without_tags(context.tape.content, loan, "mostRecent"))


@then('loan "{loan:TapeName}" has no most-recent NOI, DSCR or financials dates')
def step_no_financials_values(context, loan):
    row = loan_row(context, loan)
    values = (row.noi, row.dscr, row.financials_start, row.financials_end)
    assert values == (None, None, None, None), values


@then("none of those fields is stored as 0")
def step_not_stored_as_zero(context):
    def nulls(db):
        return db.execute(
            text(
                "SELECT loan, noi IS NULL, dscr IS NULL, occupancy IS NULL, "
                "financials_start IS NULL, financials_end IS NULL "
                "FROM silver.loan_period WHERE accession = :a AND loan = '5'"
            ),
            {"a": context.tape.accession},
        ).one()

    assert all(in_session(nulls)[1:]), "a missing value was stored as something other than NULL"


# ---------------------------------------------------------------------------- point in time


def _load_real(context, deal: str, period: Period, filed: dt.date) -> Tape:
    tapes = real_tapes(deal)
    for tape in tapes:
        ingest(tape)
    tape = real_tape(deal, period)
    assert tape.filing_date == filed, f"EDGAR shows {period} filed on {tape.filing_date}"
    context.deal = deal
    context.real_tape = tape
    return tape


@given(
    'the {period:TapeMonth} ABS-EE for "{deal:TapeName}" has reporting period end '
    "{end:TapeDay} and was filed on {filed:TapeDay}"
)
def step_real_tape_with_end(context, period, deal, end, filed):
    tape = _load_real(context, deal, period, filed)
    ends = {a.tag("reportingPeriodEndDate") for a in parse_assets(tape.content)}
    assert ends == {end.strftime("%m-%d-%Y")}, ends


@given('the {period:TapeMonth} ABS-EE for "{deal:TapeName}" was filed on {filed:TapeDay}')
def step_real_tape(context, period, deal, filed):
    _load_real(context, deal, period, filed)


@when("the panel is queried as of {day:TapeDay}")
def step_query_as_of(context, day):
    def read(db):
        return panel.as_of(db, day, deal_id=panel.deal_named(db, context.deal).id)

    context.panel_rows = in_session(read)


@then("no {period:TapeMonth} loan rows are returned")
def step_no_rows_for_period(context, period):
    rows = [row for row in context.panel_rows if row.period == period.first_day]
    assert not rows, f"{len(rows)} {period} rows returned"


@then("the latest period returned is {period:TapeMonth}")
def step_latest_period(context, period):
    assert context.panel_rows, "nothing was returned"
    assert panel.periods(context.panel_rows)[-1] == period


@then("the {period:TapeMonth} loan rows are returned")
def step_rows_for_period(context, period):
    returned = sorted(row.loan for row in context.panel_rows if row.period == period.first_day)
    tape = real_tape(context.deal, period)
    on_tape = sorted(a.asset_number for a in parse_assets(tape.content) if a.kind is AssetKind.LOAN)
    assert returned == on_tape, f"returned {len(returned)} of {len(on_tape)} loans"
    assert {row.accession for row in context.panel_rows if row.period == period.first_day} == {
        tape.accession
    }


@given(
    'loan "{loan:TapeName}" of "{deal:TapeName}" showed DSCR {dscr:TapeRatio}x in the '
    "{period:TapeMonth} ABS-EE filed on {filed:TapeDay}"
)
def step_original_dscr(context, loan, deal, dscr, period, filed):
    assert (deal, period) == (FIXTURE_DEAL, FIXTURE_PERIOD), "only the fixture tape is on file"
    tape = fixture_tape(filed)
    tape = replace(
        tape,
        content=with_tag(
            tape.content,
            loan,
            "mostRecentDebtServiceCoverageNetOperatingIncomePercentage",
            str(dscr),
            on_property=True,
        ),
    )
    prepare(context, tape)
    context.dscr_loan = loan
    ensure_loaded(context)


@given("an ABS-EE/A filed on {filed:TapeDay} corrects that DSCR to {dscr:TapeRatio}x")
def step_amended_dscr(context, filed, dscr):
    original = context.tape
    amendment = replace(
        original,
        content=with_tag(
            original.content,
            context.dscr_loan,
            "mostRecentDebtServiceCoverageNetOperatingIncomePercentage",
            str(dscr),
            on_property=True,
        ),
        accession=next(FIXTURE_ACCESSIONS),
        filing_date=filed,
        form=AMENDMENT,
        amends=original.accession,
    )
    context.amendment_summary = ingest(amendment)


@then('loan "{loan:TapeName}" shows DSCR {dscr:TapeRatio}x for {period:TapeMonth}')
def step_shows_dscr(context, loan, dscr, period):
    rows = [r for r in context.panel_rows if r.loan == loan and r.period == period.first_day]
    assert len(rows) == 1, f"{len(rows)} rows for loan {loan} in {period}"
    assert rows[0].dscr == dscr, f"DSCR {rows[0].dscr}"


@then("the DSCR of {dscr:TapeRatio}x is still stored with its filing date of {filed:TapeDay}")
def step_original_kept(context, dscr, filed):
    def read(db):
        deal = panel.deal_named(db, context.deal)
        return panel.versions(db, deal_id=deal.id, loan=context.dscr_loan, period=FIXTURE_PERIOD)

    stored = [(version.dscr, version.filing_date) for version in in_session(read)]
    assert (dscr, filed) in stored, stored
    assert len(stored) == 2, stored


@given('a model input row for loan "{loan:TapeName}" is built as of {day:TapeDay}')
def step_model_row(context, loan, day):
    context.model_loan = loan
    context.model_as_of = day


@given("the row uses the DSCR from the ABS-EE filed on {filed:TapeDay}")
def step_model_uses(context, filed):
    prepare(context, fixture_tape(filed))
    ensure_loaded(context)

    def read(db):
        deal = panel.deal_named(db, context.deal)
        stored = panel.versions(db, deal_id=deal.id, loan=context.model_loan, period=FIXTURE_PERIOD)
        return next(version for version in stored if version.filing_date == filed)

    context.model_row = model_input(in_session(read), "dscr", context.model_as_of)


@when("the row is checked against the as-of contract")
def step_check_as_of(context):
    context.rejections = rejections([context.model_row])


@then('the row is rejected with the reason "{reason}"')
def step_rejected(context, reason):
    assert [r.reason for r in context.rejections] == [reason], context.rejections


# ---------------------------------------------------------------------------- staleness


@given(
    'the {period:TapeMonth} ABS-EE for "{deal:TapeName}", with reporting period end '
    "{end:TapeDay}, filed on {filed:TapeDay}"
)
def step_fixture_with_end(context, period, deal, end, filed):
    assert end == FIXTURE_PERIOD_END, f"the fixture tape's period ends {FIXTURE_PERIOD_END}"
    step_fixture_abs_ee(context, period, deal, filed)


@given('loan "{loan:TapeName}"\'s most recent financials end on {day:TapeDay}')
def step_financials_end(context, loan, day):
    content = with_tag(
        context.tape.content,
        loan,
        "mostRecentFinancialsEndDate",
        day.strftime("%m-%d-%Y"),
        on_property=True,
    )
    change_tape(context, content)


@given('loan "{loan:TapeName}" has no most-recent financials')
def step_no_financials(context, loan):
    step_no_financials_tags(context, loan)


@given(
    'the {period:TapeMonth} ABS-EE for "{deal:TapeName}", a deal that closed in {closed:TapeMonth}'
)
def step_new_issue(context, period, deal, closed):
    real = next(r for r in REAL_TAPES[deal] if r.period == period)
    assert real.closed == closed, f"{deal} closed in {real.closed}"
    prepare(context, real_tape(deal, period))


@when("staleness is measured")
def step_measure(context):
    ensure_loaded(context)
    day = context.tape.filing_date
    context.staleness = in_session(lambda db: panel.staleness(db, context.deal, day)[1])


def _staleness(context, loan: str):
    return context.staleness.loan(loan)


@then(
    'loan "{loan:TapeName}"\'s financials are {at_end:d} days old at period end '
    "and {at_filing:d} days old at filing"
)
def step_days_old(context, loan, at_end, at_filing):
    measured = _staleness(context, loan)
    assert (measured.days_vs_period_end, measured.days_vs_filing) == (at_end, at_filing), measured


@then(
    'defeased loan "{defeased:TapeName}", co-op loan "{coop:TapeName}" and paid-off loan '
    '"{paid_off:TapeName}" are excluded'
)
def step_excluded(context, defeased, coop, paid_off):
    statuses = {loan.loan: loan.status for loan in context.staleness.loans}
    assert statuses[defeased] == "excluded: defeased", statuses
    assert statuses[coop] == "excluded: co-op", statuses
    assert statuses[paid_off] == "excluded: paid off", statuses


@then("{count:d} loans are counted as active")
def step_active(context, count):
    assert context.staleness.summary.active_loans == count, context.staleness.summary


@then('loan "{loan:TapeName}" is listed as "{status}"')
def step_listed_as(context, loan, status):
    assert _staleness(context, loan).status == status, _staleness(context, loan)


@then('loan "{loan:TapeName}" is not part of the staleness percentiles')
def step_not_in_percentiles(context, loan):
    measured = _staleness(context, loan)
    assert measured.days_vs_period_end is None and measured.days_vs_filing is None, measured
    in_percentiles = [
        row.loan for row in context.staleness.loans if row.days_vs_period_end is not None
    ]
    assert loan not in in_percentiles
    assert context.staleness.summary.loans_with_financials == len(in_percentiles)


@then(
    "the deal summary shows {count:d} loans with financials, a median of {median:d} days "
    "and a 90th percentile of {p90:d} days"
)
def step_summary(context, count, median, p90):
    summary = context.staleness.summary
    assert (summary.loans_with_financials, summary.median_days, summary.p90_days) == (
        count,
        median,
        p90,
    ), summary


@then("{count:d} loan's financials are more than six months old")
def step_over_six_months(context, count):
    assert context.staleness.summary.over_six_months == count, context.staleness.summary


@then("the deal summary shows {count:d} loans with updated financials")
def step_updated_financials(context, count):
    assert context.staleness.summary.loans_with_financials == count, context.staleness.summary


@then('the summary says "{note}"')
def step_summary_says(context, note):
    summary = context.staleness.summary
    assert summary.note == note, summary
    assert note in summary.text()
