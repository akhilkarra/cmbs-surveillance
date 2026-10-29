"""Write the synthetic EDGAR the refresh tests replay: two fixture deals' monthly filings.

Run from the repository root after changing the fixture tape or report, then commit the result:

    uv run python tests/fixtures/refresh/build.py

Every month is derived from the two fixture files the other features use, so the refresh loads
the same world: tests/fixtures/schedule_al_minimal.xml (the September 2026 loan tape) and
tests/fixtures/distribution/fixture-trust-2026-f1_10d_2026-09.htm (its 10-D report). See the
README next to this file for what changes from month to month.
"""

import calendar
import datetime as dt
import json
import re
import shutil
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURES = HERE.parent
TAPE = FIXTURES / "schedule_al_minimal.xml"
REPORT = FIXTURES / "distribution" / "fixture-trust-2026-f1_10d_2026-09.htm"
RECORDED_ON = "2026-10-28"
ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
EFTS = "https://efts.sec.gov/LATEST/search-index"
SUBMISSIONS = "https://data.sec.gov/submissions"
# The determination date (the tape's reporting period end), the distribution date and how long
# after the distribution the 10-D and ABS-EE are filed, the same for every month.
DETERMINATION_DAY = 11
DISTRIBUTION_DAY = 17
FILED_AFTER = dt.timedelta(days=12)
# Financials are at least this old when a tape reports them.
FINANCIALS_LAG = dt.timedelta(days=70)


@dataclass(frozen=True)
class FixtureDeal:
    name: str
    cik: int
    first: tuple[int, int]
    last: tuple[int, int]
    # Property names replaced in the tape, so the two deals cannot be confused.
    renamed: dict[str, str]

    @property
    def slug(self) -> str:
        return re.sub(r"[^a-z0-9]+", "-", self.name.lower()).strip("-")


DEALS = (
    FixtureDeal("Fixture Trust 2026-F1", 9_900_001, (2025, 8), (2026, 10), {}),
    FixtureDeal(
        "Fixture Trust 2026-F2",
        9_900_002,
        (2026, 8),
        (2026, 10),
        {
            "Harbor Office Tower": "Bayview Office Center",
            "Lakeside Hotel Portfolio": "Summit Hotel Portfolio",
            "Lakeside Hotel A": "Summit Hotel A",
            "Elm Street Cooperative": "Oak Lane Cooperative",
            "Maple Industrial Park": "Cedar Logistics Park",
            "Paid Off Plaza": "Settled Square",
            "Riverside Apartments": "Hillcrest Apartments",
        },
    ),
)


def months(first: tuple[int, int], last: tuple[int, int]) -> list[tuple[int, int]]:
    found = []
    year, month = first
    while (year, month) <= last:
        found.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return found


def shift_months(day: dt.date, count: int) -> dt.date:
    index = day.year * 12 + day.month - 1 + count
    year, month = divmod(index, 12)
    return dt.date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def quarter_end_on_or_before(day: dt.date) -> dt.date:
    month = (day.month - 1) // 3 * 3 + 3
    end = dt.date(day.year, month, calendar.monthrange(day.year, month)[1])
    if end <= day:
        return end
    return quarter_end_on_or_before(dt.date(day.year, month - 2, 1) - dt.timedelta(days=1))


def tape_date(day: dt.date) -> str:
    return day.strftime("%m-%d-%Y")


def read_tape_date(text: str) -> dt.date:
    return dt.datetime.strptime(text, "%m-%d-%Y").date()


def _financials(block: str, determination: dt.date) -> str:
    """Financials no newer than FINANCIALS_LAG before the determination date, same span."""
    end_match = re.search(r"<mostRecentFinancialsEndDate>([\d-]+)<", block)
    if end_match is None:
        return block
    end = read_tape_date(end_match.group(1))
    latest = quarter_end_on_or_before(determination - FINANCIALS_LAG)
    if end <= latest:
        return block
    start_match = re.search(r"<mostRecentFinancialsStartDate>([\d-]+)<", block)
    block = block.replace(end_match.group(0), f"<mostRecentFinancialsEndDate>{tape_date(latest)}<")
    if start_match is not None:
        # The same number of months: six months to June 30 become six months to September 30.
        start = read_tape_date(start_match.group(1))
        span = (end.year - start.year) * 12 + end.month - start.month + 1
        moved = shift_months(latest + dt.timedelta(days=1), -span)
        block = block.replace(
            start_match.group(0), f"<mostRecentFinancialsStartDate>{tape_date(moved)}<"
        )
    return block


def month_tape(deal: FixtureDeal, year: int, month: int) -> str:
    """The fixture tape as of one month: dates moved, loan 4 reported 30 days delinquent."""
    determination = dt.date(year, month, DETERMINATION_DAY)
    head, *blocks = TAPE.read_text().split("<assets>")
    written = []
    for block in blocks:
        loan = re.search(r"<assetNumber>([^<]+)<", block)
        number = loan.group(1) if loan else ""
        block = block.replace("09-11-2026", tape_date(determination))
        block = block.replace(
            "<paidThroughDate>09-01-2026<",
            f"<paidThroughDate>{tape_date(dt.date(year, month, 1))}<",
        )
        if number == "4":
            block = re.sub(
                r"(<reportPeriodEndActualBalanceAmount>[^<]*</reportPeriodEndActualBalanceAmount>)",
                r"\1\n    <paymentStatusLoanCode>1</paymentStatusLoanCode>",
                block,
            )
        block = _financials(block, determination)
        for old, new in deal.renamed.items():
            block = block.replace(f"<propertyName>{old}<", f"<propertyName>{new}<")
        written.append(block)
    return "<assets>".join([head, *written])


def report_date(day: dt.date) -> str:
    return day.strftime("%m/%d/%Y")


def month_report(deal: FixtureDeal, year: int, month: int) -> str:
    """The fixture 10-D as of one month: dates moved; the appraisal reduction from August 2026."""
    determination = dt.date(year, month, DETERMINATION_DAY)
    distribution = dt.date(year, month, DISTRIBUTION_DAY)
    text = REPORT.read_text()
    text = text.replace("Fixture Trust 2026-F1", deal.name)
    text = text.replace("September 2026", f"{calendar.month_name[month]} {year}")
    text = text.replace(
        "<td>Distribution Date:</td><td>09/17/26</td>",
        f"<td>Distribution Date:</td><td>{distribution.strftime('%m/%d/%y')}</td>",
    )
    text = text.replace(
        "<td>Determination Date:</td><td>09/11/26</td>",
        f"<td>Determination Date:</td><td>{determination.strftime('%m/%d/%y')}</td>",
    )
    # Paid through four months back for loan 2 (90+ days), two months back for loan 4.
    text = text.replace(
        "<td>2</td><td>05/11/2026</td>",
        f"<td>2</td><td>{report_date(shift_months(determination, -4))}</td>",
    )
    text = text.replace(
        "<td>4</td><td>07/11/2026</td>",
        f"<td>4</td><td>{report_date(shift_months(determination, -2))}</td>",
    )
    if (year, month) < (2026, 8):
        text = text.replace("<td>08/11/2026</td><td>6,500,000.00</td>", "<td></td><td>0.00</td>")
    return "\n".join(line.rstrip() for line in text.splitlines()) + "\n"


@dataclass(frozen=True)
class MonthFilings:
    deal: FixtureDeal
    year: int
    month: int

    @property
    def filed(self) -> dt.date:
        return dt.date(self.year, self.month, DISTRIBUTION_DAY) + FILED_AFTER

    @property
    def distribution(self) -> dt.date:
        return dt.date(self.year, self.month, DISTRIBUTION_DAY)

    def accession(self, form: str) -> str:
        sequence = 400 + self.month * 2 + (1 if form == "ABS-EE" else 0)
        return f"{self.deal.cik:010d}-{self.filed.year % 100:02d}-{sequence:06d}"

    def stem(self, kind: str) -> str:
        return f"{self.deal.slug}_{kind}-{self.year}{self.month:02d}"


def folder_url(cik: int, accession: str) -> str:
    return f"{ARCHIVES}/{cik}/{accession.replace('-', '')}/"


def index_json(cik: int, accession: str, names: list[str]) -> str:
    folder = f"/Archives/edgar/data/{cik}/{accession.replace('-', '')}"
    directory = {
        "directory": {
            "item": [{"name": name, "type": "text.gif", "size": ""} for name in names],
            "name": folder,
            "parent-dir": f"/Archives/edgar/data/{cik}",
        }
    }
    return json.dumps(directory, indent=1) + "\n"


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")


def build() -> None:
    edgar, archive = HERE / "edgar", HERE / "archive"
    for directory in (edgar, archive):
        if directory.exists():
            shutil.rmtree(directory)
    edgar_responses: dict[str, str] = {}
    archive_responses: dict[str, str] = {}
    for deal in DEALS:
        typeahead = f"{EFTS}?{urllib.parse.urlencode({'keysTyped': deal.name})}"
        typeahead_file = f"search/keysTyped_{deal.name.replace(' ', '_')}.json"
        write_json(
            edgar / typeahead_file,
            {
                "hits": {
                    "hits": [{"_id": str(deal.cik), "_source": {"entity": deal.name}}],
                    "total": {"relation": "eq", "value": 1},
                }
            },
        )
        edgar_responses[typeahead] = typeahead_file
        recent: dict[str, list[str]] = {
            "accessionNumber": [],
            "filingDate": [],
            "form": [],
            "primaryDocument": [],
            "reportDate": [],
        }
        for year, month in reversed(months(deal.first, deal.last)):
            filings = MonthFilings(deal, year, month)
            for form, kind, exhibits in (
                ("10-D", "10d", {filings.stem("ex991") + ".htm": month_report(deal, year, month)}),
                (
                    "ABS-EE",
                    "absee",
                    {
                        filings.stem("ex102") + ".xml": month_tape(deal, year, month),
                        # The footnotes exhibit is listed but never read.
                        filings.stem("ex103") + ".xml": "<footnotes/>\n",
                    },
                ),
            ):
                accession = filings.accession(form)
                primary = filings.stem(kind) + ".htm"
                recent["accessionNumber"].append(accession)
                recent["filingDate"].append(filings.filed.isoformat())
                recent["form"].append(form)
                recent["primaryDocument"].append(primary)
                recent["reportDate"].append(filings.distribution.isoformat())
                url = folder_url(deal.cik, accession)
                (archive / accession).mkdir(parents=True)
                index_file = f"{accession}/index.json"
                (archive / index_file).write_text(
                    index_json(deal.cik, accession, [primary, *sorted(exhibits)])
                )
                archive_responses[url + "index.json"] = index_file
                for name, content in exhibits.items():
                    (archive / accession / name).write_text(content)
                    archive_responses[url + name] = f"{accession}/{name}"
        submissions_url = f"{SUBMISSIONS}/CIK{deal.cik:010d}.json"
        submissions_file = f"submissions/CIK{deal.cik:010d}.json"
        write_json(
            edgar / submissions_file,
            {"cik": str(deal.cik), "filings": {"files": [], "recent": recent}, "name": deal.name},
        )
        edgar_responses[submissions_url] = submissions_file
    for directory, responses in ((edgar, edgar_responses), (archive, archive_responses)):
        manifest = {"recorded_on": RECORDED_ON, "responses": dict(sorted(responses.items()))}
        (directory / "recordings.json").write_text(json.dumps(manifest, indent=1) + "\n")


if __name__ == "__main__":
    build()
