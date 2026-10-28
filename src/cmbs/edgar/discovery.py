"""Filing discovery: from a deal's display name to the public filings that exist for it.

1. Resolve the name to the issuing entity's CIK through the EDGAR entity typeahead. The name is
   tried as given, then with known renamings of series codes: the market calls a deal
   "COMM 2014-CR18" while EDGAR names the trust "COMM 2014-CCRE18 Mortgage Trust". A renamed
   match says so in its note.
2. With a CIK, list the entity's filings from its submissions JSON, following the older pages.
3. Without one (a privately placed deal), search the full text of ABS-15G filings for the name.
   The depositor files an ABS-15G for each offering. Hits are kept only if they are that form,
   because ticker searches also match unrelated companies (research/11-data-sources.md §1.1).

Only filings on or before the discovery date count. Nothing here touches the database;
`cmbs.edgar.service` registers what was found.
"""

import datetime as dt
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from cmbs.domain import Period
from cmbs.edgar import search, submissions
from cmbs.edgar.replay import Source
from cmbs.errors import Refused

# Forms that report on one distribution month; their report date is the distribution date.
PERIODIC_FORMS = frozenset({"10-D", "10-D/A", "ABS-EE", "ABS-EE/A"})
DEPOSITOR_FORMS = ("ABS-15G",)
# Form 10-D is due within 15 days after the distribution date (General Instruction A.2).
FILING_DEADLINE = dt.timedelta(days=15)
# A report date further before the filing date than this is a typo, not a late filing.
REPORT_DATE_WINDOW = dt.timedelta(days=45)
# Series codes that a deal's market name abbreviates differently from its EDGAR name, by shelf.
SERIES_ALIASES: Mapping[str, Mapping[str, str]] = {"COMM": {"CR": "CCRE"}}

_SERIES = re.compile(r"^(?P<year>\d{4})-(?P<code>[A-Z]+)(?P<number>\d+)$")


class AmbiguousDeal(Refused):
    """More than one EDGAR entity carries every word of the deal's name."""


class BlankDealName(Refused):
    pass


@dataclass(frozen=True)
class Entity:
    cik: int
    name: str


@dataclass(frozen=True)
class Match:
    """What the name resolved to. `cik` is None when the deal has no issuing entity on EDGAR."""

    cik: int | None
    edgar_name: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class FoundFiling:
    accession: str
    cik: int
    form: str
    filing_date: dt.date
    report_date: dt.date | None = None
    primary_document: str | None = None

    @property
    def period(self) -> Period | None:
        """The distribution month a 10-D or ABS-EE reports on; None for other forms.

        EDGAR's report date is the distribution date when the filer agent sets it properly.
        Older filings carry the filing date instead (a 10-D filed 2015-03-02 for the February
        distribution reads 2015-03-02), and a few carry a year typo (filed 2024-06-28, report
        date 2023-06-17). A report date is trusted only if it falls before the filing date and
        within REPORT_DATE_WINDOW of it. Otherwise the month is inferred from the filing date:
        a 10-D is due within 15 days after the distribution date, so the distribution fell in
        the month of the date 15 days before filing.
        """
        if self.form not in PERIODIC_FORMS:
            return None
        report = self.report_date
        if report is not None and dt.timedelta(0) < self.filing_date - report <= REPORT_DATE_WINDOW:
            return Period.of(report)
        return Period.of(self.filing_date - FILING_DEADLINE)


@dataclass(frozen=True)
class Discovery:
    deal: str
    on: dt.date
    match: Match
    filings: tuple[FoundFiling, ...]

    def of_form(self, form: str) -> list[FoundFiling]:
        return [filing for filing in self.filings if filing.form == form]


def name_variants(name: str) -> list[str]:
    """The name as given (whitespace normalized), then each known renaming of its series code."""
    words = name.split()
    variants = [" ".join(words)]
    aliases = SERIES_ALIASES.get(words[0].upper(), {}) if words else {}
    for index, word in enumerate(words[1:], start=1):
        series = _SERIES.match(word.upper())
        if series is None or series["code"] not in aliases:
            continue
        renamed = f"{series['year']}-{aliases[series['code']]}{series['number']}"
        variants.append(" ".join([*words[:index], renamed, *words[index + 1 :]]))
    return variants


def names_match(query: str, entity_name: str) -> bool:
    """True when every word of `query` is a whole word of the EDGAR entity name.

    "WFRBS 2014-C25" matches "WFRBS Commercial Mortgage Trust 2014-C25"; "BANK 2018-BNK1" does
    not match "BANK 2018-BNK13".
    """
    wanted = query.upper().split()
    have = set(entity_name.upper().split())
    return bool(wanted) and all(word in have for word in wanted)


def typeahead_entities(response: Mapping[str, Any]) -> list[Entity]:
    return [
        Entity(cik=int(hit["_id"]), name=hit["_source"]["entity"])
        for hit in response["hits"]["hits"]
    ]


def resolve(source: Source, name: str) -> Match:
    """The issuing entity for a deal name, or a Match without a CIK when none is found."""
    variants = name_variants(name)
    for variant in variants:
        response = source.get_json(search.typeahead_url(variant))
        found: dict[int, Entity] = {}
        for entity in typeahead_entities(response):
            if names_match(variant, entity.name):
                found.setdefault(entity.cik, entity)
        if len(found) > 1:
            listed = ", ".join(f"{e.name} (CIK {e.cik})" for e in found.values())
            raise AmbiguousDeal(f'several EDGAR entities match "{variant}": {listed}')
        if found:
            (entity,) = found.values()
            note = None
            if variant != variants[0]:
                note = f'EDGAR names the trust "{variant}" (entity "{entity.name}")'
            return Match(cik=entity.cik, edgar_name=entity.name, note=note)
    return Match(cik=None)


def _date(text: str | None) -> dt.date | None:
    return dt.date.fromisoformat(text) if text else None


def submission_filings(cik: int, block: Mapping[str, Any]) -> list[FoundFiling]:
    found = []
    for row in submissions.filing_rows(block):
        filing_date = _date(row.filing_date)
        if filing_date is None:  # pragma: no cover - EDGAR dates every filing
            raise ValueError(f"filing {row.accession} has no filing date")
        found.append(
            FoundFiling(
                accession=row.accession,
                cik=cik,
                form=row.form,
                filing_date=filing_date,
                report_date=_date(row.report_date),
                primary_document=row.primary_document or None,
            )
        )
    return found


def entity_filings(source: Source, cik: int) -> list[FoundFiling]:
    """Every filing of the entity: the recent block, then each older page."""
    data = source.get_json(submissions.submissions_url(cik))
    found = submission_filings(cik, data["filings"]["recent"])
    for page in submissions.older_pages(data):
        found += submission_filings(cik, source.get_json(submissions.page_url(page)))
    return found


def full_text_filings(response: Mapping[str, Any], forms: Iterable[str]) -> list[FoundFiling]:
    """Hits of the wanted forms, one per accession (a filing's exhibits are separate hits)."""
    wanted = set(forms)
    found: dict[str, FoundFiling] = {}
    for hit in response["hits"]["hits"]:
        hit_source = hit["_source"]
        form = hit_source.get("form")
        if form not in wanted:
            continue
        accession = hit_source.get("adsh") or hit["_id"].split(":", 1)[0]
        if accession in found:
            continue
        filing_date = _date(hit_source.get("file_date"))
        ciks = hit_source.get("ciks") or []
        if filing_date is None or not ciks:
            continue
        found[accession] = FoundFiling(
            accession=accession,
            cik=int(ciks[0]),
            form=form,
            filing_date=filing_date,
            report_date=_date(hit_source.get("period_ending")),
        )
    return list(found.values())


def depositor_filings(source: Source, name: str) -> list[FoundFiling]:
    """ABS-15G filings whose text names the deal, following the search's pages."""
    forms = ",".join(DEPOSITOR_FORMS)
    found: dict[str, FoundFiling] = {}
    for variant in name_variants(name):
        start = 0
        while True:
            response = source.get_json(search.full_text_url(f'"{variant}"', forms, start))
            hits = response["hits"]["hits"]
            for filing in full_text_filings(response, DEPOSITOR_FORMS):
                found.setdefault(filing.accession, filing)
            start += len(hits)
            if not hits or start >= response["hits"]["total"]["value"]:
                break
    return list(found.values())


def discover(source: Source, name: str, on: dt.date) -> Discovery:
    """The deal's issuing entity and its filings on or before `on`, newest first."""
    if not name.split():
        raise BlankDealName("name the deal to look up")
    match = resolve(source, name)
    if match.cik is not None:
        found = entity_filings(source, match.cik)
    else:
        found = depositor_filings(source, name)
    unique = {filing.accession: filing for filing in found if filing.filing_date <= on}
    ordered = sorted(
        unique.values(), key=lambda filing: (filing.filing_date, filing.accession), reverse=True
    )
    return Discovery(deal=" ".join(name.split()), on=on, match=match, filings=tuple(ordered))
