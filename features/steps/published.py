"""Months of a fixture deal published as a scenario states them, then loaded by a refresh run.

The world is the synthetic EDGAR in tests/fixtures/refresh/ (its README), replayed the way
production reads EDGAR. A scenario may state months that EDGAR does not record, or filing dates
other than the recorded ones (a Friday instead of a Saturday): `Publication` publishes each
stated month on its stated day, building the files of a month the recordings lack exactly as
tests/fixtures/refresh/build.py builds the recorded ones, and applies the scenario's edits to the
month's tape (EX-102) and 10-D report (EX-99.1). Accession numbers keep the fixture scheme, so a
recorded month keeps its recorded accession.

`load` registers the deal and backfills the stated months in one run at the time given; each
month is still public only from its own filing date, which is what the panel reads.

No step is defined here; the modules that use it define the steps.
"""

import copy
import datetime as dt
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import partial
from typing import Any

from cmbs import clock
from cmbs.absee.schedule_al import NAMESPACE, AssetKind, asset_kind
from cmbs.deals.service import get_or_create
from cmbs.domain import Period
from cmbs.edgar.replay import MissingRecording, Replay
from cmbs.refresh import service as refresh
from cmbs.refresh.service import RunReport
from cmbs.refresh.sources import ArchiveReplay, FirstRecorded, Sources
from cmbs.testing.bdd import in_session
from cmbs.testing.world import FIXTURE_CIKS, set_next_run_number
from tests.fixtures.refresh import build

EDGAR = (build.HERE / "edgar", build.HERE.parent / "edgar")
ARCHIVE = build.HERE / "archive"
OPERATOR = "olly"  # runs refreshes in the fixture world
SPECS = {deal.name: deal for deal in build.DEALS}
FORMS = {"10-D": ("10d", "ex991", ".htm"), "ABS-EE": ("absee", "ex102", ".xml")}

type Edit = Callable[[bytes], bytes]


# ---------------------------------------------------------------------------- editing a tape


def _asset(root: ET.Element, loan: str) -> ET.Element:
    found = [
        asset
        for asset in root.findall(NAMESPACE + "assets")
        if (asset.findtext(NAMESPACE + "assetNumber") or "").strip() == loan
    ]
    assert len(found) == 1, f"{len(found)} assets numbered {loan!r}"
    return found[0]


def _written(root: ET.Element) -> bytes:
    ET.register_namespace("", NAMESPACE.strip("{}"))
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def set_tags(loan: str, tags: Mapping[str, str | None]) -> Edit:
    """An edit setting the loan's tags; 'property/<tag>' is a tag of its property, and None
    removes the tag (the tape then does not report it)."""

    def edit(content: bytes) -> bytes:
        root = ET.fromstring(content)
        asset = _asset(root, loan)
        for path, value in tags.items():
            parent = asset
            if path.startswith("property/"):
                path = path.removeprefix("property/")
                prop = asset.find(NAMESPACE + "property")
                assert prop is not None, f"loan {loan} has no property"
                parent = prop
            element = parent.find(NAMESPACE + path)
            if value is None:
                if element is not None:
                    parent.remove(element)
                continue
            if element is None:
                element = ET.SubElement(parent, NAMESPACE + path)
            element.text = value
        return _written(root)

    return edit


def only_loans(loans: set[str]) -> Edit:
    """An edit leaving out every loan not in `loans`; companion notes and portfolio properties
    are not loans and stay."""

    def edit(content: bytes) -> bytes:
        root = ET.fromstring(content)
        for asset in root.findall(NAMESPACE + "assets"):
            number = (asset.findtext(NAMESPACE + "assetNumber") or "").strip()
            name = (asset.findtext(f"{NAMESPACE}property/{NAMESPACE}propertyName") or "").strip()
            if asset_kind(number, name) is AssetKind.LOAN and number not in loans:
                root.remove(asset)
        return _written(root)

    return edit


def tape_date(day: dt.date) -> str:
    return build.tape_date(day)


# ---------------------------------------------------------------------------- publishing


@dataclass
class Month:
    period: Period
    filed: dt.date
    tape: list[Edit] = field(default_factory=list)
    report: list[Edit] = field(default_factory=list)


class Publication:
    """The months of one fixture deal a scenario publishes, with their edits."""

    def __init__(self, deal: str) -> None:
        assert deal in SPECS, f"{deal!r} is not a fixture deal of tests/fixtures/refresh"
        self.deal = deal
        self.spec = SPECS[deal]
        self.months: dict[Period, Month] = {}
        self.loaded = False
        self.recorded_edgar = FirstRecorded(*(Replay(directory) for directory in EDGAR))
        self.recorded_archive = FirstRecorded(ArchiveReplay(ARCHIVE))

    def month(self, period: Period, filed: dt.date | None = None) -> Month:
        """The month, published on `filed` (the fixture's day when not stated before)."""
        found = self.months.get(period)
        if found is None:
            default = build.MonthFilings(self.spec, period.year, period.month).filed
            found = self.months[period] = Month(period, filed or default)
        elif filed is not None:
            found.filed = filed
        return found

    # ------------------------------------------------------------------ the files

    def accession(self, month: Month, form: str) -> str:
        sequence = 400 + month.period.month * 2 + (1 if form == "ABS-EE" else 0)
        return f"{self.spec.cik:010d}-{month.filed.year % 100:02d}-{sequence:06d}"

    def _stem(self, month: Month, kind: str) -> str:
        return f"{self.spec.slug}_{kind}-{month.period.year}{month.period.month:02d}"

    def _recorded(self, url: str, made: Callable[[], str]) -> bytes:
        try:
            return self.recorded_archive.get_bytes(url)
        except MissingRecording:
            return made().encode()

    def files(self) -> dict[str, bytes]:
        """Every archive file of the stated months, by URL, with the edits applied."""
        served: dict[str, bytes] = {}
        for month in self.months.values():
            year, number = month.period.year, month.period.month
            for form, (kind, exhibit, suffix) in FORMS.items():
                accession = self.accession(month, form)
                folder = build.folder_url(self.spec.cik, accession)
                name = self._stem(month, exhibit) + suffix
                names = [self._stem(month, kind) + ".htm", name]
                if form == "ABS-EE":
                    footnotes = self._stem(month, "ex103") + ".xml"
                    names.append(footnotes)
                    served[folder + footnotes] = b"<footnotes/>\n"
                    content = self._recorded(
                        folder + name, partial(build.month_tape, self.spec, year, number)
                    )
                    edits = month.tape
                else:
                    content = self._recorded(
                        folder + name, partial(build.month_report, self.spec, year, number)
                    )
                    edits = month.report
                for edit in edits:
                    content = edit(content)
                served[folder + name] = content
                served[folder + "index.json"] = build.index_json(
                    self.spec.cik, accession, [names[0], *sorted(names[1:])]
                ).encode()
        return served

    def submissions(self, recorded: dict[str, Any]) -> dict[str, Any]:
        """The deal's submissions with the stated months filed on their stated days."""
        data = copy.deepcopy(recorded)
        recent = data["filings"]["recent"]
        stated = {
            self.accession(month, form): (month, form)
            for month in self.months.values()
            for form in FORMS
        }
        # A recorded month is replaced by the stated one: same month, same form.
        replaced = {(m.period.first_day, form) for m, form in stated.values()}
        keep = [
            i
            for i, accession in enumerate(recent["accessionNumber"])
            if accession not in stated and not self._same_month(recent, i, replaced)
        ]
        for column in list(recent):
            recent[column] = [recent[column][i] for i in keep]
        for accession, (month, form) in stated.items():
            kind = FORMS[form][0]
            recent["accessionNumber"].append(accession)
            recent["filingDate"].append(month.filed.isoformat())
            recent["form"].append(form)
            recent["primaryDocument"].append(self._stem(month, kind) + ".htm")
            distribution = dt.date(month.period.year, month.period.month, build.DISTRIBUTION_DAY)
            recent["reportDate"].append(distribution.isoformat())
        return data

    @staticmethod
    def _same_month(recent: dict[str, list], i: int, replaced: set) -> bool:
        report = recent["reportDate"][i]
        if not report:
            return False
        day = dt.date.fromisoformat(report)
        return (dt.date(day.year, day.month, 1), recent["form"][i]) in replaced

    def sources(self) -> Sources:
        return Sources(edgar=_Edgar(self), archive=_Archive(self.files(), self.recorded_archive))

    # ------------------------------------------------------------------ loading

    def load(self, at: dt.datetime, *, run_number: int | None = None) -> RunReport:
        """Backfill every stated month in one run at `at`, numbered `run_number` if given."""
        assert self.months, "no month is stated"
        assert all(month.filed <= at.date() for month in self.months.values()), (
            "a month is loaded before it is filed"
        )
        in_session(lambda db: get_or_create(db, self.deal, cik=FIXTURE_CIKS[self.deal]))
        if run_number is not None:
            in_session(lambda db: set_next_run_number(db, run_number))
        clock.set_now(at)
        periods = sorted(self.months)
        report = refresh.backfill(
            OPERATOR, self.deal, periods[0], periods[-1], sources=self.sources()
        )
        loaded = [
            (m["period"], m["outcome"]) for d in report.run.summary["deals"] for m in d["months"]
        ]
        assert loaded == [(p.label, "loaded") for p in periods], report.text()
        self.loaded = True
        return report


# A scenario's months are loaded the morning after the last is filed, unless a step names the
# run that loaded them, and the scenario reads the panel at noon that day.
LOAD_TIME = dt.time(9, 0, tzinfo=dt.UTC)
READ_TIME = dt.time(12, 0, tzinfo=dt.UTC)


def publication(context: Any, deal: str) -> Publication:
    """The scenario's publication of `deal`, made by the first step that names it."""
    found = getattr(context, "publications", None)
    if found is None:
        found = context.publications = {}
    if deal not in found:
        found[deal] = Publication(deal)
    context.deal = deal
    return found[deal]


def load_run(context: Any, number: int, day: dt.date) -> None:
    """The scenario's months are loaded by run `number` on `day`."""
    context.load_run = (number, dt.datetime.combine(day, LOAD_TIME))


def publish(context: Any) -> None:
    """Load what the scenario's Given steps published, once, and read the panel at noon on the
    day it was loaded."""
    pending = [p for p in (getattr(context, "publications", None) or {}).values() if not p.loaded]
    if not pending:
        return
    run = getattr(context, "load_run", None)
    last_filed = max(month.filed for p in pending for month in p.months.values())
    number, at = run or (None, dt.datetime.combine(last_filed + dt.timedelta(days=1), LOAD_TIME))
    for found in pending:
        found.load(at, run_number=number)
        number = None  # a later deal's run takes the next number
    clock.set_now(dt.datetime.combine(at.date(), READ_TIME))


class _Edgar:
    def __init__(self, publication: Publication) -> None:
        self.publication = publication
        self.submissions_url = f"{build.SUBMISSIONS}/CIK{publication.spec.cik:010d}.json"

    def get_json(self, url: str) -> Any:
        recorded = self.publication.recorded_edgar.get_json(url)
        if url == self.submissions_url:
            return self.publication.submissions(recorded)
        return recorded


class _Archive:
    def __init__(self, served: dict[str, bytes], recorded: FirstRecorded) -> None:
        self.served = served
        self.recorded = recorded

    def get_bytes(self, url: str) -> bytes:
        found = self.served.get(url)
        return found if found is not None else self.recorded.get_bytes(url)
