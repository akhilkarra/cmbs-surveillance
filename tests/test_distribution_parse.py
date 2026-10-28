"""Administrator formats and whole-report parsing (cmbs.distribution.layouts and .parse).

The BANK 2018-BNK13 and CGCMT 2016-C1 files used here are reconstructions, not the filed
documents (see the comment at the top of each); the fixture deal's report is synthetic.
"""

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from cmbs.distribution import layouts
from cmbs.distribution.grid import title_key
from cmbs.distribution.layouts import Administrator, detect
from cmbs.distribution.parse import (
    NO_COMMENT_COLUMN,
    AppraisalReduction,
    Comment,
    Delinquency,
    ParsedReport,
    UnrecognisedFormat,
    distribution_date,
    parse_report,
)

FIXTURES = Path(__file__).parent / "fixtures" / "distribution"
SS2 = "Specially Serviced Loan Detail - Part 2"
ML2 = "Mortgage Loan Detail (Part 2)"
DLD = "Delinquency Loan Detail"
LOAN_2_COMMENT = (
    "The loan transferred to Special Servicing effective 3/6/2025 due to imminent monetary "
    "default. As of 12/31/2024, DSCR NOI was 0.71x and occupancy was 61.4%. The borrower has "
    "requested a 12-month maturity extension. The special servicer is evaluating the request."
)


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def page(*tables: str, marker: str = "www.ctslink.com") -> bytes:
    head = f"<table><tr><td>Distribution Date: 09/17/26</td><td>{marker}</td></tr></table>"
    return f"<html><body>{head}{''.join(tables)}</body></html>".encode()


@pytest.mark.parametrize(
    ("text", "administrator"),
    [
        ("Visit www.CTSLink.com for reports", Administrator.COMPUTERSHARE),
        ("https://sf.citidirect.com", Administrator.CITI),
        ("ctslink.com and citidirect.com", None),
        ("Computershare Trust Company, N.A.", Administrator.COMPUTERSHARE),
        ("Citibank, N.A., as Certificate Administrator", Administrator.CITI),
        ("Computershare as trustee, Citibank as administrator", None),
        ("www.ctslink.com; Citibank, N.A. as custodian", Administrator.COMPUTERSHARE),
        ("A report from somebody else", None),
    ],
)
def test_detect_the_administrator(text, administrator):
    assert detect(text) == administrator


def test_every_table_read_is_a_known_section_title():
    specs = [
        spec
        for tables in (layouts.COMMENT_TABLES, layouts.APPRAISAL_TABLES, layouts.DELINQUENCY_TABLES)
        for specs in tables.values()
        for spec in specs
    ]
    assert {spec.key for spec in specs} <= layouts.TITLE_KEYS
    assert set(layouts.COMMENT_TABLES) == set(Administrator)
    assert set(layouts.APPRAISAL_TABLES) == set(Administrator)
    assert set(layouts.DELINQUENCY_TABLES) == set(Administrator)
    assert len(layouts.TITLE_KEYS) == len(layouts.SECTION_TITLES)
    assert title_key("Historical Appraisal Reduction Detail") in layouts.TITLE_KEYS


@pytest.mark.parametrize(
    ("label", "matches"),
    [
        ("pros id", True),
        ("pros. id", True),
        ("prospectus loan id", True),
        ("prospectus id", True),
        ("pros #", True),
        ("prospectus number", True),
        ("odcr", True),
        ("omcr", True),
        ("offering document cross-reference", True),
        ("loan id", False),
        ("loan number", False),
        ("property", False),
    ],
)
def test_the_prospectus_id_column(label, matches):
    assert layouts.PROSPECTUS_ID.matches(label) is matches


@pytest.mark.parametrize(
    ("label", "field"),
    [
        ("appraisal reduction amount", "amount"),
        ("ara", "amount"),
        ("current ara amount", "amount"),
        ("appraisal reduction date", "reduction_date"),
        ("ara date", "reduction_date"),
        ("historical appraisal reduction amount", None),
        ("cumulative asers", None),
        ("appraisal value", None),
    ],
)
def test_appraisal_columns(label, field):
    found = [column.name for column in layouts.APPRAISAL_COLUMNS[1:] if column.matches(label)]
    assert found == ([] if field is None else [field])


@pytest.mark.parametrize(
    ("label", "field"),
    [
        ("loan status", "status"),
        ("status of mortgage loan", "status"),
        ("resolution strategy status", None),
        ("paid through date", "paid_through"),
        ("paid thru date", "paid_through"),
        ("current p&i advances", "current_advances"),
        ("p&i advances current", "current_advances"),
        ("outstanding p&i advances", "outstanding_advances"),
        ("p&i advances outstanding", "outstanding_advances"),
        ("outstanding servicing advances", None),
        ("actual principal balance", "actual_balance"),
        ("actual balance", "actual_balance"),
        ("scheduled balance", None),
    ],
)
def test_delinquency_columns(label, field):
    found = [column.name for column in layouts.DELINQUENCY_COLUMNS[1:] if column.matches(label)]
    assert found == ([] if field is None else [field])


def test_the_fixture_deals_report():
    parsed = parse_report(fixture("fixture-trust-2026-f1_10d_2026-09.htm"))
    assert parsed == ParsedReport(
        administrator=Administrator.COMPUTERSHARE,
        distribution_date=dt.date(2026, 9, 17),
        comments=(Comment("2", LOAN_2_COMMENT, SS2),),
        appraisal_reductions=(
            AppraisalReduction("2", Decimal("6500000.00"), ML2, dt.date(2026, 8, 11)),
        ),
        delinquencies=(
            Delinquency(
                "2",
                DLD,
                status="3",
                paid_through=dt.date(2026, 5, 11),
                current_advances=Decimal("125000.00"),
                outstanding_advances=Decimal("500000.00"),
                actual_balance=Decimal("30000000.00"),
            ),
            Delinquency(
                "4",
                DLD,
                status="1",
                paid_through=dt.date(2026, 7, 11),
                current_advances=Decimal("18500.00"),
                outstanding_advances=Decimal("37000.00"),
                actual_balance=Decimal("4200000.00"),
            ),
        ),
        notes=(),
    )
    assert parsed.summary == (
        "computershare format\n"
        "1 special-servicing comments\n"
        "1 appraisal reductions\n"
        "2 loans in delinquency detail"
    )


def test_the_reconstructed_computershare_report():
    parsed = parse_report(fixture("bank-2018-bnk13_ex991_2026-09.reconstructed.htm"))
    assert parsed.administrator is Administrator.COMPUTERSHARE
    assert parsed.distribution_date == dt.date(2026, 9, 17)
    assert [(comment.loan, comment.source_table) for comment in parsed.comments] == [
        ("11", SS2),
        ("57", SS2),
    ]
    assert parsed.comments[0].text == (
        "The loan transferred to Special Servicing effective 6/4/2026 due to monetary default. "
        "As of 3/31/2026, DSCR NOI was 0.03x and occupancy was 28.29%."
    )
    assert parsed.notes == ("no appraisal reduction table", "no Delinquency Loan Detail table")


def test_the_reconstructed_citi_report_says_it_has_no_comment_column():
    parsed = parse_report(fixture("cgcmt-2016-c1_ex99_1_2026-09.reconstructed.htm"))
    assert parsed.administrator is Administrator.CITI
    assert parsed.distribution_date == dt.date(2026, 9, 14)
    assert parsed.comments == ()
    assert parsed.has_comment_column is False
    assert parsed.summary == (
        f"citi format\n{NO_COMMENT_COLUMN}\n0 appraisal reductions\n0 loans in delinquency detail"
    )
    assert NO_COMMENT_COLUMN == "this administrator's format has no comment column"


def test_an_unknown_format_is_refused():
    with pytest.raises(UnrecognisedFormat) as refused:
        parse_report(b"<html><body><p>Some trustee's statement</p></body></html>")
    assert str(refused.value) == "the report's certificate administrator is not recognised"


def test_a_citi_report_reads_appraisal_reductions_and_delinquencies():
    parsed = parse_report(
        page(
            "<table><tr><td>Appraisal Reduction Detail</td></tr>"
            "<tr><th>Loan Number</th><th>ODCR</th><th>Appraisal Reduction Date</th>"
            "<th>Appraisal Reduction Amount</th></tr>"
            "<tr><td>300123</td><td>7</td><td>8/1/26</td><td>1,000,000.00</td></tr></table>"
            "<table><tr><td>Delinquency Loan Detail</td></tr>"
            "<tr><th>ODCR</th><th>Status of Mortgage Loan (1)</th></tr>"
            "<tr><td>7</td><td>3</td></tr></table>",
            marker="sf.citidirect.com",
        )
    )
    assert parsed.appraisal_reductions == (
        AppraisalReduction(
            "7", Decimal("1000000.00"), "Appraisal Reduction Detail", dt.date(2026, 8, 1)
        ),
    )
    assert parsed.delinquencies == (Delinquency("7", DLD, status="3"),)
    assert parsed.notes == ()


def test_unreadable_cells_are_noted_and_not_guessed():
    parsed = parse_report(
        page(
            "<table><tr><td>Mortgage Loan Detail (Part 2)</td></tr>"
            "<tr><td>Pros ID</td><td>Appraisal Reduction Date</td>"
            "<td>Appraisal Reduction Amount</td></tr>"
            "<tr><td>1</td><td>soon</td><td>2,000.00</td></tr>"
            "<tr><td>2</td><td></td><td>lots</td></tr>"
            "<tr><td>3</td><td></td><td>0.00</td></tr>"
            "<tr><td>4</td><td></td><td>-</td></tr></table>"
            "<table><tr><td>Delinquency Loan Detail</td></tr>"
            "<tr><td>Pros ID</td><td>Loan Status</td><td>Paid Through Date</td>"
            "<td>Actual Balance</td></tr>"
            "<tr><td>5</td><td></td><td>later</td><td>much</td></tr></table>"
            "<table><tr><td>Specially Serviced Loan Detail - Part 2</td></tr>"
            "<tr><td>Pros ID</td><td>Comments</td></tr>"
            "<tr><td>6</td><td>-</td></tr></table>"
        )
    )
    assert parsed.appraisal_reductions == (AppraisalReduction("1", Decimal("2000.00"), ML2, None),)
    assert parsed.delinquencies == (Delinquency("5", DLD),)
    assert parsed.comments == ()
    assert parsed.notes == (
        f"{ML2}, loan 1: not a date: 'soon'",
        f"{ML2}, loan 2: not an amount of money: 'lots'",
        f"{DLD}, loan 5: not a date: 'later'",
        f"{DLD}, loan 5: not an amount of money: 'much'",
    )
    assert parsed.summary.splitlines()[1:4] == [
        "0 special-servicing comments",
        "1 appraisal reductions",
        "1 loans in delinquency detail",
    ]


def test_a_dedicated_appraisal_table_and_the_loan_detail_are_both_kept():
    parsed = parse_report(
        page(
            "<table><tr><td>Appraisal Reduction Detail</td></tr>"
            "<tr><td>Pros ID</td><td>ARA</td></tr><tr><td>2</td><td>5.00</td></tr></table>"
            "<table><tr><td>Mortgage Loan Detail (Part 2)</td></tr>"
            "<tr><td>Pros ID</td><td>ARA</td></tr><tr><td>2</td><td>6.00</td></tr></table>"
        )
    )
    assert parsed.appraisal_reductions == (
        AppraisalReduction("2", Decimal("5.00"), "Appraisal Reduction Detail"),
        AppraisalReduction("2", Decimal("6.00"), ML2),
    )
    assert "no appraisal reduction table" not in parsed.notes


@pytest.mark.parametrize(
    ("text", "day"),
    [
        ("Distribution Date: 09/17/26 Record Date: 08/31/26", dt.date(2026, 9, 17)),
        ("DISTRIBUTION DATE 9/17/2026", dt.date(2026, 9, 17)),
        ("Record Date: 08/31/26", None),
        ("Distribution Date: 13/45/26", None),
        ("STATEMENT TO NOTEHOLDERS September 14, 2026 TRANSACTION PARTIES", dt.date(2026, 9, 14)),
        ("Statement to Certificateholders March 2, 2026", dt.date(2026, 3, 2)),
        ("STATEMENT TO NOTEHOLDERS Smarch 14, 2026", None),
        ("Distribution Date: 13/45/26 STATEMENT TO NOTEHOLDERS September 14, 2026", None),
    ],
)
def test_distribution_date(text, day):
    assert distribution_date(text) == day


def test_citi_delinquency_columns_under_spanning_header_cells():
    # Delinquency Loan Detail, CGCMT 2016-C1, September 2026 (loan 11's row). "Payment Status"
    # and "Workout Strategy" share one header cell and are told apart by "(2)" and "(3)";
    # "(Net of ASER) Outstanding" spans the current and total P&I advance columns.
    parsed = parse_report(
        page(
            "<table><tr><td>DELINQUENCY LOAN DETAIL</td></tr></table>"
            "<table><tr><td></td></tr>"
            "<tr><td></td><td></td><td>Actual</td><td>Paid</td><td>Current P&amp;I</td>"
            "<td>Total P&amp;I</td><td>Cumulative</td>"
            "<td colspan='4'>Other Expense Payment Workout Most Recent</td></tr>"
            "<tr><td></td><td></td><td>Principal</td><td>Through</td><td>Advances</td>"
            "<td>Advances</td><td>Accrued Unpaid</td><td>Advances</td>"
            "<td colspan='3'>Status Strategy Special Serv</td></tr>"
            "<tr><td>Loan ID</td><td>OMCR</td><td>Balance</td><td>Date</td>"
            "<td colspan='2'>(Net of ASER) Outstanding</td><td>Interest Advances</td>"
            "<td>Outstanding</td><td>(2)</td><td>(3)</td><td>Transfer Date</td></tr>"
            "<tr><td></td></tr>"
            "<tr><td>304101577</td><td>11</td><td>17,150,000.00</td><td>07/06/2026</td>"
            "<td>70,148.26</td><td>140,148.84</td><td>4,348.21</td><td>7,295.00</td>"
            "<td>5</td><td>13</td><td>05/11/2026</td></tr></table>",
            marker="sf.citidirect.com",
        )
    )
    assert parsed.delinquencies == (
        Delinquency(
            "11",
            DLD,
            status="5",
            paid_through=dt.date(2026, 7, 6),
            current_advances=Decimal("70148.26"),
            outstanding_advances=Decimal("140148.84"),
            actual_balance=Decimal("17150000.00"),
        ),
    )
    assert parsed.notes == ("no appraisal reduction table",)


def test_computershare_delinquency_status_with_a_superscript_footnote():
    # Delinquency Loan Detail, BANK 2018-BNK13, September 2026 (loan 22's row).
    parsed = parse_report(
        page(
            "<table><tr><td>Delinquency Loan Detail</td></tr>"
            "<tr><td></td><td></td><td>Paid</td><td colspan='2'>Mortgage</td></tr>"
            "<tr><td></td><td></td><td>Through</td><td>Months</td><td>Loan</td>"
            "<td>Current P&amp;I</td><td>Outstanding P&amp;I</td><td>Actual Principal</td></tr>"
            "<tr><td>Pros ID</td><td>Loan ID</td><td>Date</td><td>Delinquent</td>"
            "<td>Status&sup1;</td><td>Advances</td><td>Advances</td><td>Balance</td></tr>"
            "<tr><td>22</td><td>300801793</td><td>08/01/26</td><td>0</td><td>B</td>"
            "<td>70,490.67</td><td>70,490.67</td><td>11,032,552.34</td></tr></table>"
        )
    )
    assert parsed.delinquencies == (
        Delinquency(
            "22",
            DLD,
            status="B",
            paid_through=dt.date(2026, 8, 1),
            current_advances=Decimal("70490.67"),
            outstanding_advances=Decimal("70490.67"),
            actual_balance=Decimal("11032552.34"),
        ),
    )
