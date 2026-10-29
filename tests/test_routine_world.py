"""The fixture deal as the refresh routine, the export and the ask scenarios find it.

"Fixture Trust 2026-F1" is loaded the way a quant would load it: a backfill run over the
synthetic EDGAR recordings in tests/fixtures/refresh (see its README), with each month's
special-servicing comments extracted from the synthetic model recordings in tests/fixtures/model.
Every month's 10-D carries the same comment on loan 2, so the one recording answers them all.

Step modules import these helpers (behave runs step files as scripts, so they cannot import each
other); the tests below check the helpers themselves.
"""

import datetime as dt
import urllib.error
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from cmbs import clock
from cmbs.deals.service import by_name, get_or_create
from cmbs.domain import Period
from cmbs.edgar.archives import filing_base_url
from cmbs.edgar.replay import Replay
from cmbs.extraction.decision import UNSETTLED_ANSWER, Outcome
from cmbs.extraction.questions import WORDING
from cmbs.extraction.tables import NEEDS_REVIEW, OPEN, ExtractedField, Extraction, ReviewItem
from cmbs.model.client import ModelClient
from cmbs.model.recordings import DbRecordings
from cmbs.refresh import service as refresh
from cmbs.refresh.service import RunReport
from cmbs.refresh.sources import ArchiveReplay, FirstRecorded, Sources
from cmbs.testing.world import FIXTURE_CIKS, FIXTURE_DEAL
from tests.conftest import needs_db
from tests.fixtures.model.synthetic import load as load_recordings

FIXTURES = Path(__file__).parent / "fixtures"
EDGAR = (FIXTURES / "refresh" / "edgar", FIXTURES / "edgar")
ARCHIVE = FIXTURES / "refresh" / "archive"
QUANT = "quinn"
SEPTEMBER = Period(2026, 9)
# Loading days: after the September 2026 filings (filed on the 29th), before October's.
LOAD_DAY = dt.datetime(2026, 10, 1, 9, 0, tzinfo=dt.UTC)
# The answer probability that leaves a field unsettled, inside settings version 7's band.
UNSETTLED = Decimal("0.55")


class Archive:
    """The recorded archive, with files a scenario replaces and URLs that EDGAR refuses."""

    def __init__(self) -> None:
        self.recorded = FirstRecorded(ArchiveReplay(ARCHIVE))
        self.replaced: dict[str, bytes] = {}
        self.forbidden: set[str] = set()

    def get_bytes(self, url: str) -> bytes:
        if url in self.forbidden:
            raise urllib.error.HTTPError(url, 403, "Forbidden", None, None)  # pyright: ignore[reportArgumentType]
        return self.replaced.get(url) or self.recorded.get_bytes(url)


def synthetic_model(db: Session, user: str) -> ModelClient:
    """Replay only, from the synthetic recordings (loaded into the cache first)."""
    load_recordings(DbRecordings(db))
    return ModelClient(DbRecordings(db), replay_only=True, allow_synthetic=True)


def sources(archive: Archive | None = None) -> Sources:
    return Sources(
        edgar=FirstRecorded(*(Replay(directory) for directory in EDGAR)),
        archive=archive or Archive(),
        model=synthetic_model,
    )


def accession(deal: str, period: Period, form: str) -> str:
    """A fixture filing's accession, as tests/fixtures/refresh/build.py numbers them."""
    filed = dt.date(period.year, period.month, 17) + dt.timedelta(days=12)
    number = 400 + period.month * 2 + (1 if form == "ABS-EE" else 0)
    return f"{FIXTURE_CIKS[deal]:010d}-{filed.year % 100:02d}-{number:06d}"


def exhibit_url(deal: str, period: Period, form: str) -> str:
    """The fixture filing's EX-102 (ABS-EE) or EX-99.1 (10-D)."""
    slug = deal.lower().replace(" ", "-")
    stamp = f"{period.year}{period.month:02d}"
    name = f"{slug}_ex102-{stamp}.xml" if form == "ABS-EE" else f"{slug}_ex991-{stamp}.htm"
    return filing_base_url(FIXTURE_CIKS[deal], accession(deal, period, form)) + name


def register(db: Session, deal: str = FIXTURE_DEAL) -> int:
    """A refresh needs the deal registered with its CIK, as filing discovery registers it."""
    return get_or_create(db, deal, cik=FIXTURE_CIKS[deal]).id


def backfill(
    db_session,
    start: Period,
    end: Period,
    *,
    archive: Archive | None = None,
    deal: str = FIXTURE_DEAL,
    on: dt.datetime = LOAD_DAY,
) -> RunReport:
    """Load the deal's months from `start` to `end` as a quant's backfill, comments extracted.

    `db_session` runs a function in its own committed session (`cmbs.testing.bdd.in_session`).
    """
    db_session(lambda db: register(db, deal))
    clock.set_now(on)
    report = refresh.backfill(QUANT, deal, start, end, sources=sources(archive))
    assert report.run.status == "succeeded", report.text()
    return report


def wait_for_review(db: Session, *, field: str, loan: str = "2", period: Period = SEPTEMBER):
    """Make the field of the loan's latest extracted comment wait for review, as an unsettled
    answer would have: no panel value, an open review item. The model's answer is kept."""
    deal = by_name(db, FIXTURE_DEAL)
    assert deal is not None, "the fixture deal is not loaded"
    extraction = db.scalars(
        select(Extraction)
        .where(
            Extraction.deal_id == deal.id,
            Extraction.loan_id == loan,
            Extraction.period == period.first_day,
        )
        .order_by(Extraction.id.desc())
        .limit(1)
    ).one_or_none()
    assert extraction is not None, f"loan {loan}'s {period} comment is not extracted"
    row = db.scalars(
        select(ExtractedField).where(
            ExtractedField.extraction_id == extraction.id, ExtractedField.field == field
        )
    ).one()
    row.answer_probability = UNSETTLED
    row.outcome = Outcome.REVIEW.value
    row.review_reason = UNSETTLED_ANSWER
    row.status, row.value, row.sentence = NEEDS_REVIEW, None, None
    db.flush()
    db.add(
        ReviewItem(
            field_id=row.id,
            deal_id=deal.id,
            loan_id=extraction.loan_id,
            field=field,
            reason=UNSETTLED_ANSWER,
            question=WORDING[field].value,
            probability=UNSETTLED,
            created_at=clock.now(),
            status=OPEN,
        )
    )
    db.flush()
    return row


# ---------------------------------------------------------------------------- the helpers


def test_accessions_and_urls_follow_the_fixture_numbering():
    assert accession(FIXTURE_DEAL, SEPTEMBER, "10-D") == "0009900001-26-000418"
    assert accession(FIXTURE_DEAL, SEPTEMBER, "ABS-EE") == "0009900001-26-000419"
    assert accession(FIXTURE_DEAL, Period(2025, 12), "ABS-EE") == "0009900001-25-000425"
    assert exhibit_url(FIXTURE_DEAL, SEPTEMBER, "ABS-EE") == (
        "https://www.sec.gov/Archives/edgar/data/9900001/000990000126000419/"
        "fixture-trust-2026-f1_ex102-202609.xml"
    )
    archive = Archive()
    assert archive.get_bytes(exhibit_url(FIXTURE_DEAL, SEPTEMBER, "10-D")).startswith(b"<")


def test_the_archive_replaces_and_refuses():
    archive = Archive()
    url = exhibit_url(FIXTURE_DEAL, SEPTEMBER, "10-D")
    archive.replaced[url] = b"other"
    assert archive.get_bytes(url) == b"other"
    archive.forbidden.add(url)
    with pytest.raises(urllib.error.HTTPError) as refused:
        archive.get_bytes(url)
    assert refused.value.code == 403


@needs_db
def test_a_backfill_extracts_and_a_field_can_be_made_to_wait(seeded):
    from cmbs.testing.bdd import in_session

    report = backfill(in_session, Period(2026, 8), SEPTEMBER)
    months = report.run.summary["deals"][0]["months"]
    assert [(m["period"], m["comments"], m["review_items"]) for m in months] == [
        ("August 2026", 1, 0),
        ("September 2026", 1, 0),
    ]
    row = in_session(lambda db: wait_for_review(db, field="workout_strategy"))
    assert (row.status, row.value, row.model_value) == (NEEDS_REVIEW, None, "modification")
    items = in_session(lambda db: db.scalars(select(ReviewItem)).all())
    assert [(i.field, i.reason, i.probability, i.status) for i in items] == [
        ("workout_strategy", UNSETTLED_ANSWER, UNSETTLED, OPEN)
    ]
