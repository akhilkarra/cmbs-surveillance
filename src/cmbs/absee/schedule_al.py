"""Parse CMBS ABS-EE Schedule AL asset XML (EX-102).

Two readings of the same file:

- `parse_assets` returns every asset exactly as reported: its leaf tags with their text
  untouched, and the tags of each `<property>` element. This is what the bronze layer stores.
- `iter_loans` returns one simplified record per top-level loan, for quick measurements over
  files on disk (`cmbs analyze staleness`).

What counts as a loan (`asset_kind`):

- Property-level sub-assets of portfolio loans (asset numbers with a dash, like "4-001") are
  properties, not loans.
- Pari passu companion notes (property name "NA", no property data) are not loans of this deal.
- Empty or missing tags mean "not reported" and come back as "" or None.
- Dates in the XML are MM-DD-YYYY.
"""

import datetime as dt
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from cmbs.errors import Refused

NAMESPACE = "{http://www.sec.gov/edgar/document/absee/cmbs/assetdata}"
ROOT_TAG = NAMESPACE + "assetData"
ASSET_TAG = NAMESPACE + "assets"
PROPERTY_TAG = NAMESPACE + "property"
COMPANION_PROPERTY_NAME = "NA"


class MalformedTape(Refused):
    """The file is not Schedule AL asset XML this parser can read faithfully."""


class AssetKind(StrEnum):
    LOAN = "loan"
    COMPANION_NOTE = "companion note"
    PORTFOLIO_PROPERTY = "portfolio property"


def asset_kind(asset_number: str, property_name: str) -> AssetKind:
    """'2-001' is a portfolio property, a loan with property name 'NA' a companion note."""
    if "-" in asset_number:
        return AssetKind.PORTFOLIO_PROPERTY
    if property_name == COMPANION_PROPERTY_NAME:
        return AssetKind.COMPANION_NOTE
    return AssetKind.LOAN


@dataclass(frozen=True)
class Asset:
    """One `<assets>` element. Tag names are without the namespace; text is as reported."""

    position: int
    tags: dict[str, str]
    properties: list[dict[str, str]]

    def tag(self, name: str) -> str:
        """A loan-level tag's text without surrounding whitespace; "" when not reported."""
        return self.tags.get(name, "").strip()

    def property_tag(self, name: str) -> str:
        """The first property's tag, stripped; "" when there is no property or no such tag."""
        return self.properties[0].get(name, "").strip() if self.properties else ""

    @property
    def asset_number(self) -> str:
        return self.tag("assetNumber")

    @property
    def kind(self) -> AssetKind:
        return asset_kind(self.asset_number, self.property_tag("propertyName"))


def _local(tag: str) -> str:
    return tag.removeprefix(NAMESPACE)


def _leaves(children: Iterable[ET.Element], where: str) -> dict[str, str]:
    leaves: dict[str, str] = {}
    for child in children:
        name = _local(child.tag)
        if len(child):
            raise MalformedTape(f"{where} has an unexpected nested element {name!r}")
        if name in leaves:
            raise MalformedTape(f"{where} reports {name!r} twice")
        leaves[name] = child.text or ""
    return leaves


def _asset(element: ET.Element, position: int) -> Asset:
    where = f"asset {position}"
    properties = [_leaves(prop, where) for prop in element.findall(PROPERTY_TAG)]
    loan_level = (child for child in element if child.tag != PROPERTY_TAG)
    return Asset(position=position, tags=_leaves(loan_level, where), properties=properties)


def parse_assets(content: bytes) -> list[Asset]:
    """Every asset in the file, in file order, numbered from 1."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError as error:
        raise MalformedTape(f"the file is not XML: {error}") from error
    if root.tag != ROOT_TAG:
        raise MalformedTape(f"the file is not Schedule AL asset data: root is {_local(root.tag)!r}")
    return [
        _asset(element, position)
        for position, element in enumerate(root.findall(ASSET_TAG), start=1)
    ]


@dataclass(frozen=True)
class LoanRecord:
    asset_number: str
    reporting_period_end: dt.date | None
    ending_balance: float
    paid_through: str
    payment_status: str
    special_servicer_transfer: str
    master_servicer_return: str
    workout_strategy: str
    modification_code: str
    property_type: str
    property_name: str
    financials_start: dt.date | None
    financials_end: dt.date | None
    noi: str
    dscr_noi: str
    occupancy: str


def parse_date(text: str) -> dt.date | None:
    return dt.datetime.strptime(text, "%m-%d-%Y").date() if text else None


def iter_loans(path: Path | str) -> Iterator[LoanRecord]:
    for asset in parse_assets(Path(path).read_bytes()):
        if asset.kind is not AssetKind.LOAN:
            continue
        field = asset.property_tag
        yield LoanRecord(
            asset_number=asset.asset_number,
            reporting_period_end=parse_date(asset.tag("reportingPeriodEndDate")),
            ending_balance=float(asset.tag("reportPeriodEndActualBalanceAmount") or 0),
            paid_through=asset.tag("paidThroughDate"),
            payment_status=asset.tag("paymentStatusLoanCode"),
            special_servicer_transfer=asset.tag("mostRecentSpecialServicerTransferDate"),
            master_servicer_return=asset.tag("mostRecentMasterServicerReturnDate"),
            workout_strategy=asset.tag("workoutStrategyCode"),
            modification_code=asset.tag("modificationCode"),
            property_type=field("propertyTypeCode"),
            property_name=field("propertyName"),
            financials_start=parse_date(field("mostRecentFinancialsStartDate")),
            financials_end=parse_date(field("mostRecentFinancialsEndDate")),
            noi=field("mostRecentNetOperatingIncomeAmount"),
            dscr_noi=field("mostRecentDebtServiceCoverageNetOperatingIncomePercentage"),
            occupancy=field("mostRecentPhysicalOccupancyPercentage"),
        )
