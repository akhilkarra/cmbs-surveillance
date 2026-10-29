"""How a loan's properties combine into its row: one property as is, several by the rules."""

import datetime as dt
from decimal import Decimal

import pytest

from cmbs.tape.properties import (
    DIFFERENT_PERIODS,
    MISSING_DEBT_SERVICE,
    MISSING_NCF,
    MISSING_NOI,
    NO_DEBT_SERVICE,
    LoanProperties,
    PropertyValues,
    combine,
    common_defeasance,
    common_type,
    portfolio_financials,
    total,
)

JUNE = dt.date(2026, 6, 30)
DEC_2024 = dt.date(2024, 12, 31)
H1 = {"financials_start": dt.date(2026, 1, 1), "financials_end": JUNE}


def prop(**values) -> PropertyValues:
    return PropertyValues(**values)


def test_no_properties():
    assert combine([]) == LoanProperties(property_count=0)


def test_one_property_is_read_as_reported():
    single = prop(
        property_type="OF",
        property_name="Harbor Office Tower",
        defeased_status="N",
        noi=Decimal("2100000.00"),
        ncf=Decimal("2000000.00"),
        debt_service=Decimal("1448275.86"),
        dscr=Decimal("1.45"),
        occupancy=Decimal("0.88"),
        **H1,
    )
    assert combine([single]) == LoanProperties(
        property_count=1,
        property_type="OF",
        property_name="Harbor Office Tower",
        defeased_status="N",
        financials_start=dt.date(2026, 1, 1),
        financials_end=JUNE,
        financials_end_newest=JUNE,
        noi=Decimal("2100000.00"),
        ncf=Decimal("2000000.00"),
        dscr=Decimal("1.45"),
        occupancy=Decimal("0.88"),
        note=None,
    )


def test_two_properties_with_one_period_sum_noi_and_recompute_dscr():
    combined = combine(
        [
            prop(
                property_type="IN",
                noi=Decimal("1000000.00"),
                ncf=Decimal("900000.00"),
                debt_service=Decimal("800000.00"),
                dscr=Decimal("9.99"),
                occupancy=Decimal("1"),
                **H1,
            ),
            prop(
                property_type="IN",
                noi=Decimal("2000000.00"),
                ncf=Decimal("1800000.00"),
                debt_service=Decimal("1600000.00"),
                dscr=Decimal("0.01"),
                occupancy=Decimal("0.5"),
                **H1,
            ),
        ]
    )
    assert combined == LoanProperties(
        property_count=2,
        property_type="IN",
        property_name="Portfolio (2 properties)",
        defeased_status=None,
        financials_start=dt.date(2026, 1, 1),
        financials_end=JUNE,
        financials_end_newest=JUNE,
        noi=Decimal("3000000.00"),
        ncf=Decimal("2700000.00"),
        dscr=Decimal("1.2500"),  # 3,000,000 / 2,400,000, never one property's 9.99 or 0.01
        occupancy=None,
        note=None,
    )


def test_two_properties_with_different_periods_give_the_oldest_end_and_no_noi():
    """The reviewer's case: NOI 1M to June 2026 and 2M to December 2024. Adding a half year
    to a full year from eighteen months earlier is not the loan's NOI, so it is not reported;
    the end date for staleness is the older one."""
    combined = combine(
        [
            prop(noi=Decimal("1000000.00"), **H1),
            prop(
                noi=Decimal("2000000.00"),
                financials_start=dt.date(2024, 1, 1),
                financials_end=DEC_2024,
            ),
        ]
    )
    assert (combined.financials_start, combined.financials_end) == (dt.date(2024, 1, 1), DEC_2024)
    assert combined.financials_end_newest == JUNE
    assert (combined.noi, combined.ncf, combined.dscr) == (None, None, None)
    assert combined.note == DIFFERENT_PERIODS


def test_a_property_without_financials_leaves_the_loan_without_noi():
    combined = combine([prop(noi=Decimal("1.00"), **H1), prop(), prop()])
    assert combined.financials_end == JUNE
    assert combined.noi is None
    assert combined.note == (
        "2 of 3 properties report no most recent financials, "
        "so the loan's NOI, NCF and DSCR are not reported"
    )


def test_no_property_with_financials_is_a_new_issue_not_a_problem():
    combined = combine([prop(property_type="LO"), prop(property_type="LO")])
    assert (combined.financials_end, combined.financials_end_newest, combined.note) == (
        None,
        None,
        None,
    )


@pytest.mark.parametrize(
    ("second", "noi", "ncf", "dscr", "note"),
    [
        (
            {"ncf": Decimal("1.00"), "debt_service": Decimal("1.00")},
            None,
            Decimal("2.00"),
            None,
            MISSING_NOI,
        ),
        (
            {"noi": Decimal("3.00"), "debt_service": Decimal("1.00")},
            Decimal("4.00"),
            None,
            Decimal("2.0000"),
            MISSING_NCF,
        ),
        (
            {"noi": Decimal("3.00"), "ncf": Decimal("1.00")},
            Decimal("4.00"),
            Decimal("2.00"),
            None,
            MISSING_DEBT_SERVICE,
        ),
        (
            {"noi": Decimal("3.00"), "ncf": Decimal("1.00"), "debt_service": Decimal("-1.00")},
            Decimal("4.00"),
            Decimal("2.00"),
            None,
            NO_DEBT_SERVICE,
        ),
    ],
)
def test_each_missing_piece_is_named(second, noi, ncf, dscr, note):
    first = prop(noi=Decimal("1.00"), ncf=Decimal("1.00"), debt_service=Decimal("1.00"), **H1)
    financials = portfolio_financials([first, prop(**second, **H1)])
    assert (financials.noi, financials.ncf, financials.dscr) == (noi, ncf, dscr)
    assert financials.notes == (note,)


def test_both_noi_and_ncf_missing_give_both_notes():
    financials = portfolio_financials([prop(**H1), prop(**H1)])
    assert financials.notes == (MISSING_NOI, MISSING_NCF)


def test_dscr_is_rounded_half_up_to_four_places():
    financials = portfolio_financials(
        [
            prop(noi=Decimal("1.00"), ncf=Decimal("1"), debt_service=Decimal("3.00"), **H1),
            prop(noi=Decimal("1.00"), ncf=Decimal("1"), debt_service=Decimal("3.00"), **H1),
        ]
    )
    assert financials.dscr == Decimal("0.3333")
    assert portfolio_financials(
        [prop(noi=Decimal("0.00005"), ncf=Decimal(1), debt_service=Decimal(1), **H1)] * 2
    ).dscr == Decimal("0.0001")


@pytest.mark.parametrize(
    ("types", "expected"),
    [([None, None], None), (["OF", "OF"], "OF"), (["OF", None], "OF"), (["OF", "RT"], "MU")],
)
def test_common_type(types, expected):
    assert common_type(types) == expected


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (["F", "F"], "F"),
        (["N", "N"], "N"),
        (["F", "N"], "P"),
        (["P", "N"], "P"),
        (["N", "X"], None),
        ([None, None], None),
    ],
)
def test_common_defeasance(statuses, expected):
    assert common_defeasance(statuses) == expected


def test_total_needs_every_value():
    assert total([Decimal("1.00"), Decimal("2.00")]) == Decimal("3.00")
    assert total([Decimal("1.00"), None]) is None
    assert total([]) == Decimal(0)


def test_portfolio_name_and_mixed_type():
    combined = combine([prop(property_type="OF"), prop(property_type="RT"), prop()])
    assert (combined.property_name, combined.property_type) == ("Portfolio (3 properties)", "MU")
