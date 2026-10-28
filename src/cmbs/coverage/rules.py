"""Coverage classes, exposure shares and overdue filings. Pure functions, no database.

A deal's coverage says what the panel can see of it from public filings
(research/11-data-sources.md §1.2 and §1.6):

- full: monthly loan tapes (ABS-EE) and distribution reports (10-D).
- distribution reports only: 10-Ds but no ABS-EE. Asset-level ABS-EE applies only to
  registered deals offered on or after 2016-11-23, so older deals never file one.
- reporting suspended: the issuing entity filed a Form 15 and stopped reporting.
- not publicly reported: no issuing entity on EDGAR (a privately placed deal, known only from
  the depositor's ABS-15G), or one that files no periodic reports.
- full, via vendor: not full from public filings, but a vendor loan-tape source is configured.
- not yet checked: filing discovery has not run for the deal, so nothing is known either way.
"""

import datetime as dt
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from cmbs.domain import Period


class Coverage(StrEnum):
    FULL = "full"
    VENDOR = "full, via vendor"
    DISTRIBUTION_ONLY = "distribution reports only"
    SUSPENDED = "reporting suspended"
    NOT_PUBLIC = "not publicly reported"
    NOT_CHECKED = "not yet checked"


# Report order: what the panel sees best first.
ORDER: tuple[Coverage, ...] = tuple(Coverage)
# Classes that should keep filing every month, so a late filing is worth calling out.
REPORTING = frozenset({Coverage.FULL, Coverage.DISTRIBUTION_ONLY})

ASSET_LEVEL_COMPLIANCE = dt.date(2016, 11, 23)
SUSPENSION_FORMS = frozenset({"15-15D", "15-12G", "15-12B"})
DEPOSITOR_FORM = "ABS-15G"
DISTRIBUTION_REPORT = "10-D"
LOAN_TAPE = "ABS-EE"
# The next monthly filing is expected about a month after the latest one.
EXPECTED_EVERY = dt.timedelta(days=30)


@dataclass(frozen=True)
class FilingFact:
    form: str
    filing_date: dt.date
    # The distribution month a 10-D or ABS-EE reports on, when known.
    period: Period | None = None


@dataclass(frozen=True)
class Classified:
    coverage: Coverage
    detail: str


def _dates(filings: Iterable[FilingFact], forms: Iterable[str]) -> list[dt.date]:
    wanted = set(forms)
    return sorted(f.filing_date for f in filings if f.form in wanted)


def _listed(items: Sequence[str]) -> str:
    """'a', 'a and b', 'a, b and c'."""
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def issued_on(filings: Sequence[FilingFact]) -> dt.date | None:
    """The offering date: the first prospectus (424 forms), else the first filing of any form."""
    prospectus = sorted(f.filing_date for f in filings if f.form.startswith("424"))
    if prospectus:
        return prospectus[0]
    return min((f.filing_date for f in filings), default=None)


def classify(filings: Sequence[FilingFact], *, has_cik: bool) -> Classified:
    """The coverage class of a deal from the public filings known as of the report date.

    Amendments (10-D/A, ABS-EE/A) do not change the class; only original filings count.
    """
    if not has_cik:
        depositor = _dates(filings, [DEPOSITOR_FORM])
        if not depositor:
            return Classified(Coverage.NOT_PUBLIC, "no filing found on EDGAR")
        dates = [day.isoformat() for day in depositor]
        noun = DEPOSITOR_FORM if len(dates) == 1 else f"{DEPOSITOR_FORM} filings"
        return Classified(Coverage.NOT_PUBLIC, f"only the depositor's {noun} of {_listed(dates)}")
    reports = _dates(filings, [DISTRIBUTION_REPORT])
    tapes = _dates(filings, [LOAN_TAPE])
    suspensions = sorted(
        (f for f in filings if f.form in SUSPENSION_FORMS), key=lambda f: f.filing_date
    )
    if suspensions:
        form = f"Form {suspensions[0].form}"
        if not reports:
            return Classified(Coverage.SUSPENDED, f"{form}, no 10-D filed")
        return Classified(Coverage.SUSPENDED, f"{form}, last 10-D {Period.of(reports[-1]).label}")
    if reports and tapes:
        return Classified(Coverage.FULL, "loan tape and distribution reports monthly")
    if reports:
        issued = issued_on(filings)
        if issued is not None and issued < ASSET_LEVEL_COMPLIANCE:
            return Classified(
                Coverage.DISTRIBUTION_ONLY, "issued before November 2016, no loan tape"
            )
        return Classified(Coverage.DISTRIBUTION_ONLY, "no loan tape filed")
    if tapes:
        return Classified(Coverage.FULL, "loan tape filed, no distribution report yet")
    return Classified(Coverage.NOT_PUBLIC, "no 10-D or ABS-EE on EDGAR")


def with_vendor(public: Classified, vendor_sources: Sequence[str]) -> Classified:
    """A configured vendor loan tape makes a deal full unless public filings already do."""
    if not vendor_sources or public.coverage is Coverage.FULL:
        return public
    if public.coverage is Coverage.NOT_CHECKED:
        public_detail = "filing discovery has not run"
    else:
        public_detail = f"{public.coverage.value} on EDGAR"
    return Classified(
        Coverage.VENDOR, f"loan tape from {_listed(list(vendor_sources))}; {public_detail}"
    )


def expected_by(filings: Iterable[FilingFact]) -> dt.date | None:
    """When the next monthly filing is expected: 30 days after the latest month was first filed.

    The latest month is the latest reporting period of a 10-D or ABS-EE, so a late filing for
    an earlier month does not move the date. Without periods, the latest filing date is used.
    """
    periodic = [f for f in filings if f.form in (DISTRIBUTION_REPORT, LOAN_TAPE)]
    if not periodic:
        return None
    dated = [f for f in periodic if f.period is not None]
    if dated:
        latest = max(f.period for f in dated if f.period is not None)
        filed = min(f.filing_date for f in dated if f.period == latest)
    else:
        filed = max(f.filing_date for f in periodic)
    return filed + EXPECTED_EVERY


def overdue_since(coverage: Coverage, filings: Iterable[FilingFact], on: dt.date) -> dt.date | None:
    """The date the next filing was expected by, if it is late on `on`; otherwise None.

    Only deals that report monthly can be late; a suspended or private deal never files again.
    """
    if coverage not in REPORTING:
        return None
    expected = expected_by(filings)
    return expected if expected is not None and on > expected else None


def shares(amounts: Sequence[Decimal]) -> list[int]:
    """Whole percents of the total that add up to exactly 100 (largest remainder method).

    Each share is first rounded down; the points left over go to the largest remainders,
    the earlier amount first on a tie. All zeros when the total is not positive.
    """
    total = sum(amounts, Decimal(0))
    if total <= 0:
        return [0] * len(amounts)
    exact = [amount * 100 / total for amount in amounts]
    whole = [int(value) for value in exact]
    spare = 100 - sum(whole)
    by_remainder = sorted(range(len(amounts)), key=lambda i: (whole[i] - exact[i], i))
    for index in by_remainder[:spare]:
        whole[index] += 1
    return whole


def exposure_by_class(exposures: Mapping[Coverage, Decimal]) -> list[tuple[Coverage, Decimal]]:
    """Exposure per coverage class in report order, leaving out classes with none."""
    return [(c, exposures[c]) for c in ORDER if exposures.get(c, Decimal(0)) > 0]
