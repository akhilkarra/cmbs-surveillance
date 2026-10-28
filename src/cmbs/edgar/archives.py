"""Download filing exhibits from www.sec.gov/Archives.

Covers the ABS-EE asset data (EX-102 XML), its footnotes (EX-103 XML), and optionally the
10-D distribution report (EX-99.1 HTML).
"""

from pathlib import Path

from cmbs.edgar import client

REQUEST_INTERVAL_S = 0.15


def filing_base_url(cik: int, accession: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/"


def list_filing_files(cik: int, accession: str) -> list[str]:
    index = client.get_json(filing_base_url(cik, accession) + "index.json")
    return [item["name"] for item in index["directory"]["item"]]


def is_wanted(name: str, *, include_distribution_report: bool) -> bool:
    if name.endswith(".xml"):
        return True
    return include_distribution_report and "99" in name and name.endswith(".htm")


def download_filing(
    cik: int, accession: str, dest_dir: Path, *, include_distribution_report: bool = False
) -> list[Path]:
    """Save the wanted exhibits of one filing under dest_dir/<accession>/."""
    base = filing_base_url(cik, accession)
    target = dest_dir / accession
    target.mkdir(parents=True, exist_ok=True)
    saved = []
    for name in list_filing_files(cik, accession):
        if not is_wanted(name, include_distribution_report=include_distribution_report):
            continue
        client.pause(REQUEST_INTERVAL_S)
        path = target / name
        path.write_bytes(client.get_bytes(base + name))
        saved.append(path)
    return saved
