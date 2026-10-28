"""The coverage report: for every deal in the book, what the panel can and cannot see.

The report reads only what is stored: the book as of the date, the filings registered for each
deal and filed by then, and the vendor sources configured by then. Refreshing discovery is a
separate step (`cmbs.edgar.service`).

Filings are dated public facts, so a discovery run as of a later date knows exactly what was
public on the report date. A deal never looked up is "not yet checked"; a deal last looked up
as of an earlier date than the report may have missed filings since, and the report says so.
"""

import datetime as dt
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from cmbs import clock
from cmbs.access.roles import Action
from cmbs.access.service import authorize
from cmbs.audit.service import record
from cmbs.book.service import book_as_of
from cmbs.coverage.rules import (
    Classified,
    Coverage,
    FilingFact,
    classify,
    exposure_by_class,
    overdue_since,
    shares,
    with_vendor,
)
from cmbs.coverage.tables import VendorSource
from cmbs.deals.service import get_or_create
from cmbs.deals.tables import Deal
from cmbs.domain import Period, money
from cmbs.edgar.service import filings_of, latest_discovery
from cmbs.errors import Refused

VENDOR_NAME_LIMIT = 128
NOT_CHECKED = Classified(Coverage.NOT_CHECKED, "filing discovery has not run for this deal")


class InvalidVendorSource(Refused):
    """A vendor source needs a name that fits the registry."""


@dataclass(frozen=True)
class DealCoverage:
    deal: str
    classes: tuple[str, ...]
    exposure: Decimal
    coverage: Coverage
    detail: str
    overdue_expected_by: dt.date | None
    vendor_sources: tuple[str, ...]
    # The date the latest filing discovery ran as of; None if it never ran.
    checked_as_of: dt.date | None = None

    def stale(self, on: dt.date) -> bool:
        """True when discovery last ran as of a date before `on`, so filings may be missing."""
        return self.checked_as_of is not None and self.checked_as_of < on


@dataclass(frozen=True)
class ClassExposure:
    coverage: Coverage
    exposure: Decimal
    share: int


@dataclass(frozen=True)
class CoverageReport:
    on: dt.date
    positions_as_of: dt.date | None
    deals: tuple[DealCoverage, ...]
    by_class: tuple[ClassExposure, ...]
    total: Decimal

    def share_of(self, coverage: Coverage) -> int:
        return next((row.share for row in self.by_class if row.coverage is coverage), 0)

    def deal(self, name: str) -> DealCoverage:
        return next(row for row in self.deals if row.deal == name)


def _end_of(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day + dt.timedelta(days=1), dt.time(), tzinfo=dt.UTC)


def vendor_sources_of(db: Session, deal_id: int, on: dt.date) -> list[str]:
    """Vendor sources configured for the deal by the end of `on` (UTC), by name."""
    return list(
        db.scalars(
            select(VendorSource.name)
            .where(VendorSource.deal_id == deal_id, VendorSource.configured_at < _end_of(on))
            .order_by(VendorSource.name)
        )
    )


def _facts(db: Session, deal_id: int, on: dt.date) -> list[FilingFact]:
    return [
        FilingFact(f.form, f.filing_date, Period.of(f.period) if f.period is not None else None)
        for f in filings_of(db, deal_id, on)
    ]


def _public(deal: Deal, checked_as_of: dt.date | None, facts: Sequence[FilingFact]) -> Classified:
    if checked_as_of is None:
        return NOT_CHECKED
    return classify(facts, has_cik=deal.cik is not None)


def _checked_as_of(db: Session, deal_id: int) -> dt.date | None:
    latest = latest_discovery(db, deal_id)
    return latest.as_of if latest is not None else None


def classify_deal(db: Session, deal: Deal, on: dt.date) -> Classified:
    """The deal's coverage from public filings as of `on`, before vendor sources."""
    return _public(deal, _checked_as_of(db, deal.id), _facts(db, deal.id, on))


def build_report(db: Session, on: dt.date) -> CoverageReport:
    """The coverage of every deal in the book as of `on`, largest exposure first.

    Reads only; nothing is stored.
    """
    book = book_as_of(db, on)
    if book is None:
        return CoverageReport(on, None, (), (), Decimal("0.00"))
    exposure = book.exposure()
    deal_ids = {held.deal: held.deal_id for held in book.positions}
    total = money(sum(exposure.values(), Decimal(0)))
    rows = []
    per_class: dict[Coverage, Decimal] = defaultdict(Decimal)
    for name in sorted(exposure, key=lambda deal: (-exposure[deal], deal)):
        deal = db.get_one(Deal, deal_ids[name])
        facts = _facts(db, deal.id, on)
        vendors = vendor_sources_of(db, deal.id, on)
        checked_as_of = _checked_as_of(db, deal.id)
        classified = with_vendor(_public(deal, checked_as_of, facts), vendors)
        rows.append(
            DealCoverage(
                deal=name,
                classes=tuple(book.classes_of(name)),
                exposure=money(exposure[name]),
                coverage=classified.coverage,
                detail=classified.detail,
                overdue_expected_by=overdue_since(classified.coverage, facts, on),
                vendor_sources=tuple(vendors),
                checked_as_of=checked_as_of,
            )
        )
        per_class[classified.coverage] += exposure[name]
    classes = exposure_by_class(per_class)
    by_class = tuple(
        ClassExposure(coverage, money(amount), share)
        for (coverage, amount), share in zip(
            classes, shares([amount for _, amount in classes]), strict=True
        )
    )
    return CoverageReport(on, book.as_of, tuple(rows), by_class, total)


def report(db: Session, *, user: str, on: dt.date) -> CoverageReport:
    """The coverage report for a user who may read the panel."""
    authorize(db, user, Action.READ_PANEL)
    return build_report(db, on)


def _amount(value: Decimal) -> str:
    return f"{value:,.2f}"


def _table(rows: Sequence[Sequence[str]]) -> list[str]:
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    return [
        "  ".join(cell.ljust(w) for cell, w in zip(row, widths, strict=True)).rstrip()
        for row in rows
    ]


def render(result: CoverageReport) -> str:
    """The report as plain text, for the CLI and for reading in scenarios."""
    title = f"Coverage report as of {result.on.isoformat()}"
    if result.positions_as_of is None:
        return f"{title}\nNo positions file is dated on or before {result.on.isoformat()}.\n"
    if not result.deals:
        return (
            f"{title}\nThe positions file of {result.positions_as_of.isoformat()} "
            "holds no positions.\n"
        )
    lines = [f"{title}, positions as of {result.positions_as_of.isoformat()}", ""]
    lines += _table(
        [("Deal", "Class", "Exposure", "Coverage", "Detail")]
        + [
            (
                row.deal,
                ", ".join(row.classes),
                _amount(row.exposure),
                row.coverage.value,
                row.detail,
            )
            for row in result.deals
        ]
    )
    lines += ["", "Exposure by coverage class"]
    lines += _table(
        [("Coverage", "Exposure", "Share of book")]
        + [(c.coverage.value, _amount(c.exposure), f"{c.share}%") for c in result.by_class]
        + [("total", _amount(result.total), "100%")]
    )
    lines += [
        "",
        f"{result.share_of(Coverage.NOT_PUBLIC)}% of the book's exposure cannot be surveilled "
        "from public filings.",
    ]
    if result.share_of(Coverage.VENDOR):
        lines.append(
            f"{result.share_of(Coverage.VENDOR)}% of the book's exposure is surveilled only "
            "through vendor sources."
        )
    if result.share_of(Coverage.NOT_CHECKED):
        lines.append(
            f"{result.share_of(Coverage.NOT_CHECKED)}% of the book's exposure has not been "
            "checked against EDGAR yet."
        )
    overdue = [row for row in result.deals if row.overdue_expected_by is not None]
    if overdue:
        lines += ["", "Overdue filings"]
        lines += [
            f"{row.deal}: latest filing overdue, expected by about "
            f"{row.overdue_expected_by.isoformat()}"
            for row in overdue
            if row.overdue_expected_by is not None
        ]
    stale = [row for row in result.deals if row.stale(result.on)]
    if stale:
        lines += ["", "Filing discovery older than the report date"]
        lines += [
            f"{row.deal}: last looked up as of {row.checked_as_of.isoformat()}"
            for row in stale
            if row.checked_as_of is not None
        ]
    vendors = [row for row in result.deals if row.vendor_sources]
    if vendors:
        lines += ["", "Vendor sources"]
        lines += [f"{row.deal}: {', '.join(row.vendor_sources)}" for row in vendors]
    return "\n".join(lines) + "\n"


def configure_vendor_source(db: Session, *, user: str, deal: str, source: str) -> VendorSource:
    """Record a vendor loan-tape source for a deal. Idempotent; audited when first added."""
    actor = authorize(db, user, Action.RUN_REFRESH)
    name = " ".join(source.split())
    if not name:
        raise InvalidVendorSource("name the vendor source")
    if len(name) > VENDOR_NAME_LIMIT:
        raise InvalidVendorSource(
            f"a vendor source name is at most {VENDOR_NAME_LIMIT} characters, not {len(name)}"
        )
    stored_deal = get_or_create(db, deal)
    added = db.execute(
        insert(VendorSource)
        .values(deal_id=stored_deal.id, name=name, configured_by=user, configured_at=clock.now())
        .on_conflict_do_nothing(index_elements=[VendorSource.deal_id, VendorSource.name])
        .returning(VendorSource.id)
    ).scalar_one_or_none()
    if added is not None:
        record(
            db,
            user=actor.user,
            role=actor.role.value,
            action="configure vendor source",
            subject="deal",
            subject_key=stored_deal.name,
            after=name,
        )
    return db.scalars(
        select(VendorSource).where(
            VendorSource.deal_id == stored_deal.id, VendorSource.name == name
        )
    ).one()
