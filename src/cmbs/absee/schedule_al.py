"""Parse CMBS ABS-EE Schedule AL asset XML (EX-102) into one record per top-level loan.

- Property-level sub-assets of portfolio loans (asset numbers with a dash, like "4-001") are
  skipped.
- Pari passu companion notes (property name "NA", no property data) are skipped.
- Empty or missing tags mean "not reported" and come back as "" or None.
- Dates in the XML are MM-DD-YYYY.
"""

import datetime as dt
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass
from functools import partial
from pathlib import Path

NAMESPACE = "{http://www.sec.gov/edgar/document/absee/cmbs/assetdata}"


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


def _text(element: ET.Element | None, tag: str) -> str:
    if element is None:
        return ""
    child = element.find(NAMESPACE + tag)
    return child.text.strip() if child is not None and child.text else ""


def iter_loans(path: Path | str) -> Iterator[LoanRecord]:
    root = ET.parse(path).getroot()
    for asset in root.findall(NAMESPACE + "assets"):
        asset_number = _text(asset, "assetNumber")
        if "-" in asset_number:
            continue
        prop = asset.find(NAMESPACE + "property")
        field = partial(_text, prop)
        if field("propertyName") == "NA":
            continue
        yield LoanRecord(
            asset_number=asset_number,
            reporting_period_end=parse_date(_text(asset, "reportingPeriodEndDate")),
            ending_balance=float(_text(asset, "reportPeriodEndActualBalanceAmount") or 0),
            paid_through=_text(asset, "paidThroughDate"),
            payment_status=_text(asset, "paymentStatusLoanCode"),
            special_servicer_transfer=_text(asset, "mostRecentSpecialServicerTransferDate"),
            master_servicer_return=_text(asset, "mostRecentMasterServicerReturnDate"),
            workout_strategy=_text(asset, "workoutStrategyCode"),
            modification_code=_text(asset, "modificationCode"),
            property_type=field("propertyTypeCode"),
            property_name=field("propertyName"),
            financials_start=parse_date(field("mostRecentFinancialsStartDate")),
            financials_end=parse_date(field("mostRecentFinancialsEndDate")),
            noi=field("mostRecentNetOperatingIncomeAmount"),
            dscr_noi=field("mostRecentDebtServiceCoverageNetOperatingIncomePercentage"),
            occupancy=field("mostRecentPhysicalOccupancyPercentage"),
        )
