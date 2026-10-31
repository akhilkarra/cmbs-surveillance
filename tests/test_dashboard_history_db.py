"""A loan's history read from the database, point in time, and as the loan page sends it.

The world: Fixture Trust 2026-F1's July, August and September 2026 tapes (the fixture tape moved
to each month, `tests.test_alert_service.month_tape`), filed on the 29th, with loan 2's ending
balance, DSCR and payment status set as the loan history chart's feature sets them.
"""

import datetime as dt
import xml.etree.ElementTree as ET
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from cmbs import clock
from cmbs.absee.schedule_al import NAMESPACE
from cmbs.api.app import create_app
from cmbs.dashboard import service
from cmbs.dashboard.service import MonthRef, PaymentStatus, StatusChange
from cmbs.domain import Period
from cmbs.tape.service import AMENDMENT, load_tape
from tests.conftest import needs_db
from tests.test_alert_service import ASSET, CIKS, F1, STATUS, accession, deal_id, month_tape

JULY, AUGUST, SEPTEMBER = Period(2026, 7), Period(2026, 8), Period(2026, 9)
BALANCE = "reportPeriodEndActualBalanceAmount"
DSCR = "mostRecentDebtServiceCoverageNetOperatingIncomePercentage"
READ = dt.datetime(2026, 10, 28, 12, tzinfo=dt.UTC)
# month: (ending balance, DSCR, payment status), as the feature's Background reports them.
REPORTED = {
    JULY: ("30400000.00", "1.12", "0"),
    AUGUST: ("30200000.00", None, "A"),
    SEPTEMBER: ("30000000.00", "0.96", "1"),
}


def tape(period: Period, balance: str, dscr: str | None, status: str) -> bytes:
    """The fixture tape moved to `period`, loan 2 reporting these values (no DSCR: None)."""
    content = month_tape(period, {("2", BALANCE): balance, ("2", STATUS): status})
    root = ET.fromstring(content)
    [loan] = [
        asset
        for asset in root.findall(NAMESPACE + "assets")
        if asset.findtext(NAMESPACE + "assetNumber") == "2"
    ]
    prop = loan.find(NAMESPACE + "property")
    assert prop is not None
    for element in prop.findall(NAMESPACE + DSCR):
        prop.remove(element)
    if dscr is not None:
        ET.SubElement(prop, NAMESPACE + DSCR).text = dscr
    ET.register_namespace("", NAMESPACE.strip("{}"))
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def load(db: Session, period: Period, content: bytes, **kw) -> str:
    number = kw.pop("accession", accession(F1, period, 1))
    load_tape(
        db,
        deal=F1,
        content=content,
        file_name=f"{period.first_day:%Y-%m}.xml",
        accession=number,
        filing_date=kw.pop("filing_date", dt.date(period.year, period.month, 29)),
        cik=CIKS[F1],
        **kw,
    )
    return number


@pytest.fixture
def world(seeded: Session) -> Session:
    for period, values in REPORTED.items():
        load(seeded, period, tape(period, *values))
    seeded.commit()
    clock.set_now(READ)
    return seeded


def history(db: Session, day: dt.date, loan: str = "2") -> service.LoanHistory:
    return service.loan_history(db, deal_id=deal_id(db, F1), loan=loan, day=day)


@needs_db
def test_the_history_is_each_month_as_published_with_the_filing_it_came_from(world):
    found = history(world, dt.date(2026, 10, 28))
    shown = [
        (
            m.month,
            m.day,
            m.on_tape,
            m.ending_balance,
            m.dscr,
            m.payment_status,
            m.source.form,
            m.source.accession,
            m.source.filing_date,
        )
        for m in found.months
    ]
    assert shown == [
        (
            MonthRef("2026-07", "July 2026"),
            dt.date(2026, 7, 1),
            True,
            "30400000.00",
            "1.12",
            PaymentStatus("0", "current"),
            "ABS-EE",
            "0009900001-26-000701",
            dt.date(2026, 7, 29),
        ),
        (
            MonthRef("2026-08", "August 2026"),
            dt.date(2026, 8, 1),
            True,
            "30200000.00",
            None,
            PaymentStatus("A", "in grace period"),
            "ABS-EE",
            "0009900001-26-000801",
            dt.date(2026, 8, 29),
        ),
        (
            MonthRef("2026-09", "September 2026"),
            dt.date(2026, 9, 1),
            True,
            "30000000.00",
            "0.96",
            PaymentStatus("1", "30 days"),
            "ABS-EE",
            "0009900001-26-000901",
            dt.date(2026, 9, 29),
        ),
    ]
    assert found.months[0].source.url.startswith(
        "https://www.sec.gov/Archives/edgar/data/9900001/000990000126000701/"
    )
    assert found.status_changes == (
        StatusChange(MonthRef("2026-08", "August 2026"), dt.date(2026, 8, 1), "A", "grace or late"),
        StatusChange(MonthRef("2026-09", "September 2026"), dt.date(2026, 9, 1), "1", "30 days"),
    )


@needs_db
def test_a_month_filed_later_is_not_in_the_history_of_an_earlier_day(world):
    assert [m.month.key for m in history(world, dt.date(2026, 9, 1)).months] == [
        "2026-07",
        "2026-08",
    ]
    # Published on the 29th: in the history from that day, not the day before.
    assert len(history(world, dt.date(2026, 9, 28)).months) == 2
    assert len(history(world, dt.date(2026, 9, 29)).months) == 3
    assert history(world, dt.date(2026, 7, 28)).months == ()


@needs_db
def test_an_amendment_changes_its_month_only_from_the_day_it_was_filed(world):
    amended = load(
        world,
        AUGUST,
        tape(AUGUST, "30250000.00", "1.01", "A"),
        accession="0009900001-26-000899",
        filing_date=dt.date(2026, 10, 5),
        form=AMENDMENT,
        amends=accession(F1, AUGUST, 1),
    )
    world.commit()
    before = history(world, dt.date(2026, 10, 4)).months[1]
    after = history(world, dt.date(2026, 10, 5)).months[1]
    assert (before.ending_balance, before.dscr, before.source.accession) == (
        "30200000.00",
        None,
        "0009900001-26-000801",
    )
    assert (after.ending_balance, after.dscr, after.source.accession) == (
        "30250000.00",
        "1.01",
        amended,
    )
    assert after.source.filing_date == dt.date(2026, 10, 5)


@needs_db
def test_a_month_whose_tape_leaves_the_loan_out_is_a_gap(seeded):
    load(seeded, JULY, tape(JULY, *REPORTED[JULY]))
    load(seeded, AUGUST, month_tape(AUGUST, {("2", ASSET): None}))
    load(seeded, SEPTEMBER, tape(SEPTEMBER, *REPORTED[SEPTEMBER]))
    seeded.commit()
    months = history(seeded, dt.date(2026, 10, 28)).months
    assert [(m.month.key, m.on_tape, m.ending_balance) for m in months] == [
        ("2026-07", True, "30400000.00"),
        ("2026-08", False, None),
        ("2026-09", True, "30000000.00"),
    ]
    assert months[1].source.accession == "0009900001-26-000801"


@needs_db
def test_a_loan_on_no_tape_has_no_history(world):
    assert history(world, dt.date(2026, 10, 28), loan="99") == service.LoanHistory((), ())


@needs_db
def test_the_loan_page_carries_its_history_as_of_its_day(world):
    page = service.loan_page(world, reader="pat", deal=F1, loan="2", as_of=dt.date(2026, 9, 1))
    assert page.month.key == "2026-08"
    assert [m.month.key for m in page.history.months] == ["2026-07", "2026-08"]
    assert [c.bucket for c in page.history.status_changes] == ["grace or late"]


@needs_db
def test_the_loan_route_sends_the_history_with_nulls_for_values_not_reported(world):
    client = TestClient(create_app())
    response = client.get(
        "/dashboard/loan",
        params={"deal": F1, "loan": "2", "as_of": "2026-10-28"},
        headers={"X-CMBS-User": "pat"},
    )
    assert response.status_code == 200, response.text
    sent = response.json()["history"]
    august = sent["months"][1]
    assert august == {
        "month": {"key": "2026-08", "label": "August 2026"},
        "day": "2026-08-01",
        "on_tape": True,
        "ending_balance": "30200000.00",
        "dscr": None,
        "payment_status": {"code": "A", "name": "in grace period"},
        "source": {
            "form": "ABS-EE",
            "accession": "0009900001-26-000801",
            "filing_date": "2026-08-29",
            "url": august["source"]["url"],
        },
    }
    assert sent["status_changes"][1] == {
        "month": {"key": "2026-09", "label": "September 2026"},
        "day": "2026-09-01",
        "code": "1",
        "bucket": "30 days",
    }
    assert Decimal(sent["months"][2]["dscr"]) == Decimal("0.96")
