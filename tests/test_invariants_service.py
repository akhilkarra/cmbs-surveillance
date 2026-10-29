"""A deal's pool as the panel showed it on a day, through the service, the CLI and the API.

"Invariant Trust" gets a September 2026 tape filed 2026-09-29 and an October tape filed
2026-10-29, loaded once into the module's database (loading again changes nothing).
"""

import datetime as dt
from decimal import Decimal
from fractions import Fraction

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from cmbs import clock
from cmbs.access.service import UnknownUser
from cmbs.api.app import create_app
from cmbs.cli import app as cli
from cmbs.domain import Period
from cmbs.invariants import service
from cmbs.invariants.partition import Bucket, Tally
from cmbs.invariants.wac import Wac
from cmbs.panel import service as panel
from cmbs.panel.service import NothingPublished, UnknownDeal
from cmbs.tape.service import load_tape
from tests.conftest import needs_db
from tests.fixtures.tape.database import app_database_fixture, module_database_fixture  # noqa: F401
from tests.fixtures.tape.tapes import asset, tape

pytestmark = needs_db
DEAL = "Invariant Trust"
CIK = 9_900_300
D = Decimal
SEPTEMBER = tape(
    asset("1", paymentStatusLoanCode="0", reportPeriodEndActualBalanceAmount="50000000.00",
          reportPeriodInterestRatePercentage="0.045"),
    asset("2", paymentStatusLoanCode="3", reportPeriodEndActualBalanceAmount="30000000.00",
          reportPeriodInterestRatePercentage="0.0525"),
    # A performing matured balloon reporting no rate.
    asset("3", paymentStatusLoanCode="04", reportPeriodEndActualBalanceAmount="8000000.00"),
    # Paid off, as BANK 2018-BNK13 reports one: no status, no balance, a rate of 0.00.
    asset("4", reportPeriodInterestRatePercentage="0.00"),
    asset("5", paymentStatusLoanCode="b", reportPeriodEndActualBalanceAmount="12000000.00",
          reportPeriodInterestRatePercentage=".06000000"),
    asset("6", paymentStatusLoanCode="7", reportPeriodEndActualBalanceAmount="0.00",
          reportPeriodInterestRatePercentage="0.05"),
    # Rates the loader cannot use: they are noted, and the loans still load.
    asset("7", paymentStatusLoanCode="0", reportPeriodEndActualBalanceAmount="1000000.00",
          reportPeriodInterestRatePercentage="n/a"),
    asset("8", paymentStatusLoanCode="0", reportPeriodEndActualBalanceAmount="2000000.00",
          reportPeriodInterestRatePercentage="4.5"),
)  # fmt: skip
OCTOBER = tape(
    asset("1", reportingPeriodEndDate="10-13-2026", paymentStatusLoanCode="1",
          reportPeriodEndActualBalanceAmount="49900000.00",
          reportPeriodInterestRatePercentage="0.045"),
)  # fmt: skip


@pytest.fixture
def loaded(app_database):
    for month, content, filed in (
        ("09", SEPTEMBER, dt.date(2026, 9, 29)),
        ("10", OCTOBER, dt.date(2026, 10, 29)),
    ):
        load_tape(
            app_database,
            deal=DEAL,
            content=content,
            file_name=f"invariant-trust_2026-{month}.xml",
            accession=f"0009900300-26-0000{month}",
            filing_date=filed,
            cik=CIK,
        )
    app_database.commit()
    clock.set_now(dt.datetime(2026, 10, 1, 15, tzinfo=dt.UTC))
    return app_database


def test_the_september_pool_as_of_october_first(loaded):
    found = service.pool(loaded, reader="pat", deal=DEAL)
    assert (found.deal, found.period, found.as_of, found.accession) == (
        DEAL,
        Period(2026, 9),
        dt.date(2026, 10, 1),
        "0009900300-26-000009",
    )
    assert found.partition.buckets == {
        Bucket.CURRENT: Tally(("1", "7", "8"), D("53000000.00")),
        Bucket.GRACE_OR_LATE: Tally(("5",), D("12000000.00")),
        Bucket.DAYS_30: Tally(),
        Bucket.DAYS_60: Tally(),
        Bucket.DAYS_90_PLUS: Tally(("2",), D("30000000.00")),
        Bucket.PERFORMING_MATURED: Tally(("3",), D("8000000.00")),
        Bucket.NON_PERFORMING_MATURED: Tally(),
        Bucket.UNRECOGNIZED: Tally(("6",), D("0.00")),
        Bucket.NOT_REPORTED: Tally(),
        Bucket.NOT_OUTSTANDING: Tally(("4",), D("0.00"), ("4",)),
    }
    assert found.partition.pool == Tally(
        ("1", "2", "3", "4", "5", "6", "7", "8"), D("103000000.00"), ("4",)
    )
    assert found.wac == Wac(
        Fraction(909, 18400),
        lowest=D("0.045"),
        highest=D("0.06000000"),
        averaged=("1", "2", "5"),
        balance=D("92000000.00"),
        rate_not_reported=("3",),
        rate_not_readable=("7",),
        rate_out_of_range=("8",),
        balance_not_reported=("4",),
    )
    rows = {row.loan: row for row in panel.as_of(loaded, dt.date(2026, 10, 1), loan=None)}
    assert (rows["7"].interest_rate, rows["7"].interest_rate_note) == (
        None,
        "interest rate is not a number: 'n/a'",
    )
    assert (rows["8"].interest_rate, rows["8"].interest_rate_note) == (
        D("4.5"),
        "interest rate '4.5' is outside 0 to 0.25",
    )


def test_the_pool_is_read_point_in_time(loaded):
    later = service.pool(loaded, reader="pat", deal=DEAL, as_of=dt.date(2026, 10, 29))
    assert later.period == Period(2026, 10)
    assert later.partition.buckets[Bucket.DAYS_30] == Tally(("1",), D("49900000.00"))
    september = service.pool(
        loaded, reader="pat", deal=DEAL, period=Period(2026, 9), as_of=dt.date(2026, 10, 29)
    )
    assert september.accession == "0009900300-26-000009"
    with pytest.raises(NothingPublished, match="no loan tape of Invariant Trust had been filed"):
        service.pool(loaded, reader="pat", deal=DEAL, as_of=dt.date(2026, 9, 28))
    with pytest.raises(NothingPublished, match="no October 2026 loan tape"):
        service.pool(loaded, reader="pat", deal=DEAL, period=Period(2026, 10))


def test_an_unknown_deal_or_reader_is_refused(loaded):
    with pytest.raises(UnknownDeal, match="no deal named 'Nobody Trust'"):
        service.pool(loaded, reader="pat", deal="Nobody Trust")
    with pytest.raises(UnknownUser):
        service.pool(loaded, reader="nobody", deal=DEAL)


runner = CliRunner()
EXPECTED_TEXT = (
    "Invariant Trust, September 2026, as of 2026-10-01 (tape 0009900300-26-000009)\n"
    "delinquency buckets:\n"
    "  current                                  3 loans, $53,000,000.00\n"
    "  grace or late                            1 loan, $12,000,000.00\n"
    "  30 days                                  0 loans, $0.00\n"
    "  60 days                                  0 loans, $0.00\n"
    "  90 days or more                          1 loan, $30,000,000.00\n"
    "  performing matured                       1 loan, $8,000,000.00\n"
    "  non-performing matured                   0 loans, $0.00\n"
    "  status not on the code list              1 loan, $0.00\n"
    "  status not reported                      0 loans, $0.00\n"
    "  not outstanding (no status, no balance)  1 loan, $0.00 (balance not reported: loan 4)\n"
    "  pool                                     8 loans, $103,000,000.00 "
    "(balance not reported: loan 4)\n"
    "weighted-average coupon: 4.940217% (rounded), between 4.50% and 6.00%, over 3 loans and "
    "$92,000,000.00; rate not reported: loan 3; rate not readable: loan 7; "
    "rate out of range: loan 8; balance not reported: loan 4\n"
)


def test_the_cli_prints_the_pool(loaded):
    result = runner.invoke(cli, ["invariants", "pool", "--deal", DEAL, "--user", "pat"])
    assert result.exit_code == 0, result.output
    assert result.output == EXPECTED_TEXT
    dated = runner.invoke(
        cli,
        ["invariants", "pool", "--deal", DEAL, "--user", "pat", "--period", "September 2026",
         "--as-of", "2026-10-01"],
    )  # fmt: skip
    assert dated.output == EXPECTED_TEXT


def test_the_cli_refuses_with_the_message(loaded):
    refused = runner.invoke(cli, ["invariants", "pool", "--deal", "Nobody Trust", "--user", "pat"])
    assert refused.exit_code == 1
    assert "no deal named 'Nobody Trust'" in refused.output
    bad = runner.invoke(
        cli, ["invariants", "pool", "--deal", DEAL, "--user", "pat", "--period", "Smarch 2026"]
    )
    assert bad.exit_code == 2
    assert "not a period like 'September 2026'" in bad.output


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_the_api_returns_the_pool(loaded, client):
    response = client.get("/invariants/pool", params={"deal": DEAL}, headers={"X-CMBS-User": "pat"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert {key: body[key] for key in ("deal", "period", "as_of", "accession", "adds_up")} == {
        "deal": DEAL,
        "period": "September 2026",
        "as_of": "2026-10-01",
        "accession": "0009900300-26-000009",
        "adds_up": True,
    }
    assert body["buckets"][5] == {
        "bucket": "performing matured",
        "loans": 1,
        "loan_numbers": ["3"],
        "balance": "8000000.00",
        "balance_not_reported": [],
    }
    assert body["pool"] == {
        "loans": 8,
        "loan_numbers": ["1", "2", "3", "4", "5", "6", "7", "8"],
        "balance": "103000000.00",
        "balance_not_reported": ["4"],
    }
    assert body["wac"] == {
        "available": True,
        "reason": None,
        "rate": "909/18400",
        "percent": "4.940217% (rounded)",
        "lowest": "0.045",
        "highest": "0.06000000",
        "averaged": ["1", "2", "5"],
        "balance": "92000000.00",
        "rate_not_reported": ["3"],
        "rate_not_readable": ["7"],
        "rate_out_of_range": ["8"],
        "balance_not_reported": ["4"],
        "zero_rate": [],
    }


def test_the_api_refuses_without_a_user_or_a_readable_month(loaded, client):
    assert client.get("/invariants/pool", params={"deal": DEAL}).status_code == 401
    bad = client.get(
        "/invariants/pool",
        params={"deal": DEAL, "period": "Smarch 2026"},
        headers={"X-CMBS-User": "pat"},
    )
    assert bad.status_code == 422
    early = client.get(
        "/invariants/pool",
        params={"deal": DEAL, "as_of": "2026-09-01"},
        headers={"X-CMBS-User": "pat"},
    )
    assert early.status_code == 422
    assert early.json() == {
        "detail": "no loan tape of Invariant Trust had been filed by 2026-09-01"
    }
