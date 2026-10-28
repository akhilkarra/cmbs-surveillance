"""The coverage report over stored positions, filings and discoveries (replayed from EDGAR)."""

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from cmbs import clock
from cmbs.access.service import PermissionRefused
from cmbs.audit.tables import AuditEntry
from cmbs.book.service import PositionLine, load_positions
from cmbs.coverage.rules import Coverage
from cmbs.coverage.service import (
    InvalidVendorSource,
    build_report,
    classify_deal,
    configure_vendor_source,
    render,
    report,
    vendor_sources_of,
)
from cmbs.coverage.tables import VendorSource
from cmbs.db import engine
from cmbs.deals.service import by_name
from cmbs.edgar.replay import Replay
from cmbs.edgar.service import discover_deal
from tests.conftest import needs_db

pytestmark = needs_db

RECORDINGS = Path(__file__).parent / "fixtures" / "edgar"
ON = dt.date(2026, 10, 27)
BOOK = [
    PositionLine("BANK 2018-BNK13", "D", Decimal("12000000.00")),
    PositionLine("WFRBS 2014-C25", "E", Decimal("6000000.00")),
    PositionLine("COMM 2014-CR18", "D", Decimal("2000000.00")),
    PositionLine("HYT 2024-RGCY", "E", Decimal("30000000.00")),
]


def load_and_discover(db, on: dt.date = ON) -> None:
    load_positions(db, user="olly", as_of=dt.date(2026, 9, 30), lines=BOOK)
    db.commit()
    edgar = Replay(RECORDINGS)
    for line in BOOK:
        discover_deal(edgar, line.deal, on, user="quinn")


def test_each_deal_is_classed_from_its_filings(seeded):
    load_and_discover(seeded)
    result = build_report(seeded, ON)
    assert [(r.deal, r.classes, r.exposure, r.coverage, r.detail) for r in result.deals] == [
        (
            "HYT 2024-RGCY",
            ("E",),
            Decimal("30000000.00"),
            Coverage.NOT_PUBLIC,
            "only the depositor's ABS-15G of 2024-08-06",
        ),
        (
            "BANK 2018-BNK13",
            ("D",),
            Decimal("12000000.00"),
            Coverage.FULL,
            "loan tape and distribution reports monthly",
        ),
        (
            "WFRBS 2014-C25",
            ("E",),
            Decimal("6000000.00"),
            Coverage.DISTRIBUTION_ONLY,
            "issued before November 2016, no loan tape",
        ),
        (
            "COMM 2014-CR18",
            ("D",),
            Decimal("2000000.00"),
            Coverage.SUSPENDED,
            "Form 15-15D, last 10-D September 2024",
        ),
    ]
    assert all(r.overdue_expected_by is None for r in result.deals)
    assert (result.on, result.positions_as_of, result.total) == (
        ON,
        dt.date(2026, 9, 30),
        Decimal("50000000.00"),
    )


def test_exposure_is_summed_by_class_with_its_share_of_the_book(seeded):
    load_and_discover(seeded)
    result = build_report(seeded, ON)
    assert [(c.coverage, c.exposure, c.share) for c in result.by_class] == [
        (Coverage.FULL, Decimal("12000000.00"), 24),
        (Coverage.DISTRIBUTION_ONLY, Decimal("6000000.00"), 12),
        (Coverage.SUSPENDED, Decimal("2000000.00"), 4),
        (Coverage.NOT_PUBLIC, Decimal("30000000.00"), 60),
    ]
    assert "60% of the book's exposure cannot be surveilled from public filings." in render(result)


def test_a_late_filing_is_called_out_with_its_expected_date(seeded):
    load_and_discover(seeded, dt.date(2026, 10, 26))
    result = build_report(seeded, dt.date(2026, 11, 5))
    assert result.deal("BANK 2018-BNK13").overdue_expected_by == dt.date(2026, 10, 29)
    assert result.deal("WFRBS 2014-C25").overdue_expected_by == dt.date(2026, 10, 30)
    assert result.deal("COMM 2014-CR18").overdue_expected_by is None
    assert result.deal("HYT 2024-RGCY").overdue_expected_by is None
    # Discovery ran as of 2026-10-26, so filings since then may be missing; the report says so.
    assert result.deal("BANK 2018-BNK13").stale(dt.date(2026, 11, 5))
    assert "BANK 2018-BNK13: last looked up as of 2026-10-26" in render(result).splitlines()


def test_filings_after_the_report_date_are_not_seen(seeded):
    load_and_discover(seeded)
    deal = by_name(seeded, "COMM 2014-CR18")
    assert deal is not None
    assert classify_deal(seeded, deal, dt.date(2024, 10, 8)).coverage is Coverage.DISTRIBUTION_ONLY
    assert classify_deal(seeded, deal, dt.date(2024, 10, 9)).coverage is Coverage.SUSPENDED


def test_a_deal_never_looked_up_is_not_yet_checked(seeded):
    load_positions(seeded, user="olly", as_of=dt.date(2026, 9, 30), lines=BOOK)
    seeded.commit()
    discover_deal(Replay(RECORDINGS), "BANK 2018-BNK13", ON, user="quinn")
    result = build_report(seeded, ON)
    assert result.deal("HYT 2024-RGCY").coverage is Coverage.NOT_CHECKED
    assert result.deal("HYT 2024-RGCY").detail == "filing discovery has not run for this deal"
    assert result.share_of(Coverage.NOT_CHECKED) == 76
    # Filings are dated, so a discovery run as of a later date knows the earlier state.
    earlier = build_report(seeded, dt.date(2026, 10, 26)).deal("BANK 2018-BNK13")
    assert (earlier.coverage, earlier.checked_as_of, earlier.stale(dt.date(2026, 10, 26))) == (
        Coverage.FULL,
        ON,
        False,
    )


def test_without_a_book_the_report_is_empty(seeded):
    result = build_report(seeded, ON)
    assert (result.positions_as_of, result.deals, result.by_class, result.total) == (
        None,
        (),
        (),
        Decimal("0.00"),
    )


def test_a_vendor_source_makes_a_private_deal_full_via_vendor(seeded):
    clock.set_now(dt.datetime(2026, 10, 27, 23, 59, tzinfo=dt.UTC))
    load_and_discover(seeded)
    configure_vendor_source(seeded, user="olly", deal="HYT 2024-RGCY", source="  tape   feed ")
    result = build_report(seeded, ON)
    hyt = result.deal("HYT 2024-RGCY")
    assert (hyt.coverage, hyt.detail, hyt.vendor_sources) == (
        Coverage.VENDOR,
        "loan tape from tape feed; not publicly reported on EDGAR",
        ("tape feed",),
    )
    assert result.share_of(Coverage.NOT_PUBLIC) == 0
    assert result.share_of(Coverage.VENDOR) == 60
    assert "HYT 2024-RGCY: tape feed" in render(result)
    # Configured late on the 27th (UTC): not part of the report on the 26th.
    assert build_report(seeded, dt.date(2026, 10, 26)).deal("HYT 2024-RGCY").vendor_sources == ()


def test_configuring_a_vendor_source_twice_stores_and_audits_it_once(seeded):
    first = configure_vendor_source(seeded, user="quinn", deal="HYT 2024-RGCY", source="feed")
    again = configure_vendor_source(seeded, user="olly", deal="HYT 2024-RGCY", source="feed")
    assert first.id == again.id and again.configured_by == "quinn"
    deal = by_name(seeded, "HYT 2024-RGCY")
    assert deal is not None
    assert vendor_sources_of(seeded, deal.id, dt.date(2100, 1, 1)) == ["feed"]
    entries = seeded.scalars(select(AuditEntry)).all()
    assert [(e.user_name, e.role, e.action, e.subject_key, e.after) for e in entries] == [
        ("quinn", "quant", "configure vendor source", "HYT 2024-RGCY", "feed")
    ]


def test_vendor_sources_are_listed_by_name(seeded):
    configure_vendor_source(seeded, user="olly", deal="HYT 2024-RGCY", source="b feed")
    configure_vendor_source(seeded, user="olly", deal="HYT 2024-RGCY", source="a feed")
    deal = by_name(seeded, "HYT 2024-RGCY")
    assert deal is not None
    assert vendor_sources_of(seeded, deal.id, dt.date(2100, 1, 1)) == ["a feed", "b feed"]


def test_a_vendor_source_needs_a_name_that_fits(seeded):
    with pytest.raises(InvalidVendorSource, match="^name the vendor source$"):
        configure_vendor_source(seeded, user="olly", deal="HYT 2024-RGCY", source="   ")
    with pytest.raises(
        InvalidVendorSource, match="^a vendor source name is at most 128 characters, not 129$"
    ):
        configure_vendor_source(seeded, user="olly", deal="HYT 2024-RGCY", source="x" * 129)
    configure_vendor_source(seeded, user="olly", deal="HYT 2024-RGCY", source="x" * 128)
    assert len(seeded.scalars(select(VendorSource)).all()) == 1


def test_only_users_who_run_refreshes_configure_vendor_sources(seeded):
    with pytest.raises(PermissionRefused, match="^pm may not run refreshes and backfills$"):
        configure_vendor_source(seeded, user="pat", deal="HYT 2024-RGCY", source="feed")


def test_the_report_needs_the_right_to_read_the_panel(seeded):
    assert report(seeded, user="pat", on=ON).deals == ()
    with pytest.raises(PermissionRefused, match="^operator may not read the panel$"):
        report(seeded, user="olly", on=ON)


def test_the_report_reads_a_discovery_stored_by_an_earlier_run(seeded):
    load_and_discover(seeded, dt.date(2026, 10, 27))
    seeded.commit()
    # A later report, in a new session, with no discovery run before it.
    with engine.session() as db:
        result = build_report(db, dt.date(2026, 11, 5))
    assert [(r.deal, r.coverage) for r in result.deals] == [
        ("HYT 2024-RGCY", Coverage.NOT_PUBLIC),
        ("BANK 2018-BNK13", Coverage.FULL),
        ("WFRBS 2014-C25", Coverage.DISTRIBUTION_ONLY),
        ("COMM 2014-CR18", Coverage.SUSPENDED),
    ]
    assert all(r.checked_as_of == dt.date(2026, 10, 27) for r in result.deals)
    assert "WFRBS 2014-C25: last looked up as of 2026-10-27" in render(result)
