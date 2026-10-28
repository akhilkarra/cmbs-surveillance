"""Filing history per filer from data.sec.gov submissions JSON."""

from collections import Counter
from dataclasses import dataclass
from typing import Any

from cmbs.edgar import client

REQUEST_INTERVAL_S = 0.2
PERIODIC_FORMS = ("ABS-EE", "10-D", "10-D/A")


@dataclass(frozen=True)
class Filing:
    filing_date: str
    form: str
    accession: str
    report_date: str
    primary_document: str


def submissions_url(cik: int) -> str:
    return f"https://data.sec.gov/submissions/CIK{cik:010d}.json"


def fetch_submissions(cik: int) -> dict[str, Any]:
    data = client.get_json(submissions_url(cik))
    client.pause(REQUEST_INTERVAL_S)
    return data


def recent_filings(submissions: dict[str, Any]) -> list[Filing]:
    """The `filings.recent` block as rows, newest first (up to 1,000 filings)."""
    recent = submissions["filings"]["recent"]
    return [
        Filing(
            filing_date=recent["filingDate"][i],
            form=recent["form"][i],
            accession=recent["accessionNumber"][i],
            report_date=recent["reportDate"][i],
            primary_document=recent["primaryDocument"][i],
        )
        for i in range(len(recent["filingDate"]))
    ]


def form_counts(submissions: dict[str, Any]) -> Counter[str]:
    return Counter(submissions["filings"]["recent"]["form"])
