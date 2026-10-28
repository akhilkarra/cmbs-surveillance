"""Filing history per filer from data.sec.gov submissions JSON.

The JSON holds the latest filings (up to 1,000) in `filings.recent`, as columns, and names older
pages in `filings.files`. Each older page holds the same columns at its top level.
"""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from cmbs.edgar import client

REQUEST_INTERVAL_S = 0.2
PERIODIC_FORMS = ("ABS-EE", "10-D", "10-D/A")
SUBMISSIONS_URL = "https://data.sec.gov/submissions/"


@dataclass(frozen=True)
class Filing:
    filing_date: str
    form: str
    accession: str
    report_date: str
    primary_document: str


def submissions_url(cik: int) -> str:
    return f"{SUBMISSIONS_URL}CIK{cik:010d}.json"


def page_url(name: str) -> str:
    """The URL of an older page named in `filings.files`."""
    return SUBMISSIONS_URL + name


def fetch_submissions(cik: int) -> dict[str, Any]:
    data = client.get_json(submissions_url(cik))
    client.pause(REQUEST_INTERVAL_S)
    return data


def filing_rows(block: Mapping[str, Any]) -> list[Filing]:
    """One column block (`filings.recent` or an older page) as rows, in the order given.

    `reportDate` and `primaryDocument` are blank for some forms (a prospectus has no report
    date); a column missing altogether reads as blank too.
    """
    count = len(block["accessionNumber"])
    report_dates = block.get("reportDate") or [""] * count
    documents = block.get("primaryDocument") or [""] * count
    return [
        Filing(
            filing_date=block["filingDate"][i],
            form=block["form"][i],
            accession=block["accessionNumber"][i],
            report_date=report_dates[i],
            primary_document=documents[i],
        )
        for i in range(count)
    ]


def recent_filings(submissions: Mapping[str, Any]) -> list[Filing]:
    """The `filings.recent` block as rows, newest first (up to 1,000 filings)."""
    return filing_rows(submissions["filings"]["recent"])


def older_pages(submissions: Mapping[str, Any]) -> list[str]:
    """The names of the older pages listed in `filings.files`."""
    return [page["name"] for page in submissions["filings"].get("files", [])]


def form_counts(submissions: dict[str, Any]) -> Counter[str]:
    return Counter(submissions["filings"]["recent"]["form"])
