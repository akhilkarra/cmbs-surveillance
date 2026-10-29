"""Setup shared by the review tests and the review, override and audit scenarios.

Comments are extracted through the real pipeline, with `ScriptedProvider` standing in for the
model so each field's judgments are exactly as a test states them; no request leaves the
process. Two worlds:

- loan 2 of "Fixture Trust 2026-F1" from its synthetic September 2026 10-D (filed 2026-09-29),
  as in the extraction features;
- the review queue's three items (`QUEUE`), each from a short synthetic comment stored as if a
  10-D had reported it, and the book of features/README.md (positions dated 2026-09-30), so the
  queue has exposure to order by.

Everything here is synthetic; no real deal, holding or comment.
"""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from cmbs import clock
from cmbs.book.service import PositionLine, load_positions
from cmbs.deals.service import get_or_create
from cmbs.distribution.service import load_report
from cmbs.distribution.tables import DistributionReport, ReportComment
from cmbs.domain import Period
from cmbs.extraction.questions import sentence_name, support_name, value_name
from cmbs.extraction.sentences import split
from cmbs.extraction.service import ExtractionSummary, extract_comments
from cmbs.extraction.tables import ReviewItem
from cmbs.filings.service import register_filing, store_file
from cmbs.model.client import ModelClient
from cmbs.model.recordings import DbRecordings
from cmbs.model.scripted import ScriptedProvider, choice_answer, respond
from cmbs.model.types import Answer, Choice, ModelRequest, NoulAnswer
from cmbs.testing.world import FIXTURE_CIK, FIXTURE_DEAL
from tests.conftest import FIXTURES

SEPTEMBER = Period(2026, 9)
REPORT = FIXTURES / "distribution" / "fixture-trust-2026-f1_10d_2026-09.htm"
REPORT_ACCESSION = "0009900001-26-000009"
REPORT_FILED = dt.date(2026, 9, 29)
# After the September 10-D is public: when the scenarios extract loan 2's comment.
EXTRACTED_AT = dt.datetime(2026, 10, 1, 12, tzinfo=dt.UTC)
LOADER = "quinn"  # a quant loads reports and runs extraction

SECOND_DEAL = "Fixture Trust 2026-F2"
SECOND_CIK = 9_900_002  # synthetic, like FIXTURE_CIK
BOOK_DATE = dt.date(2026, 9, 30)
BOOK = (
    PositionLine(FIXTURE_DEAL, "E", Decimal("20000000.00")),
    PositionLine(FIXTURE_DEAL, "F", Decimal("5000000.00")),
    PositionLine(SECOND_DEAL, "D", Decimal("40000000.00")),
)


def scripted(
    field: str, answer: str, answer_p: float, support_p: float, sentence: int, comment: str
) -> ScriptedProvider:
    """A provider giving `answer` at `answer_p` for `field`, support `support_p`, and citing
    `sentence` of `comment`; every other field gets "no" answers, so it is not stated."""
    cited = split(comment)[sentence - 1].text

    def answer_request(request: ModelRequest):
        value = request.questions[value_name(field)]
        quoted = request.questions[sentence_name(field)]
        assert isinstance(value, Choice) and isinstance(quoted, Choice)
        answers: dict[str, Answer] = {
            value_name(field): choice_answer(value, answer, answer_p),
            support_name(field): NoulAnswer(noul=support_p),
            sentence_name(field): choice_answer(quoted, cited, 0.9),
        }
        return respond(request, answers)

    return ScriptedProvider(answer_request)


def stored_comment(db: Session, deal_id: int, loan: str, period: Period) -> str:
    return db.scalars(
        select(ReportComment.comment).where(
            ReportComment.deal_id == deal_id,
            ReportComment.loan_id == loan,
            ReportComment.period == period.first_day,
        )
    ).one()


def extract(
    db: Session,
    *,
    deal: str,
    loan: str,
    field: str,
    answer: str,
    answer_p: float,
    support_p: float,
    sentence: int,
    period: Period = SEPTEMBER,
) -> ExtractionSummary:
    """Extract one loan's stored comment with the model's judgments scripted for one field."""
    deal_id = get_or_create(db, deal).id
    comment = stored_comment(db, deal_id, loan, period)
    provider = scripted(field, answer, answer_p, support_p, sentence, comment)
    client = ModelClient(DbRecordings(db), provider=provider, replay_only=False)
    return extract_comments(db, user=LOADER, deal=deal, period=period, loan=loan, client=client)


def load_september_report(db: Session) -> None:
    """The fixture deal's September 2026 10-D, loaded by a quant."""
    load_report(
        db,
        user=LOADER,
        deal=FIXTURE_DEAL,
        accession=REPORT_ACCESSION,
        cik=FIXTURE_CIK,
        filing_date=REPORT_FILED,
        file_name=REPORT.name,
        content=REPORT.read_bytes(),
        period=SEPTEMBER,
    )


def loan_two(
    db: Session, *, answer: str, answer_p: float, support_p: float = 0.9, sentence: int = 3
) -> ExtractionSummary:
    """Loan 2's September comment extracted on 2026-10-01 with its borrower request scripted.

    The comment's sentence 3 is "The borrower has requested a 12-month maturity extension.";
    0.93 makes the answer auto-accepted, 0.55 sends it to review.
    """
    clock.set_now(EXTRACTED_AT)
    load_september_report(db)
    return extract(
        db,
        deal=FIXTURE_DEAL,
        loan="2",
        field="borrower_request",
        answer=answer,
        answer_p=answer_p,
        support_p=support_p,
        sentence=sentence,
    )


# The review queue's world.


@dataclass(frozen=True)
class QueueItem:
    deal: str
    loan: str
    field: str
    answer: str
    probability: float  # unsettled, so the field waits for review
    sentence: int  # the sentence the model cites, and a reviewer would
    comment: str
    since: dt.datetime


@dataclass(frozen=True)
class Report:
    deal: str
    cik: int
    accession: str
    filed: dt.date


REPORTS = (
    Report(FIXTURE_DEAL, FIXTURE_CIK, "0009900001-26-000301", dt.date(2026, 9, 25)),
    Report(SECOND_DEAL, SECOND_CIK, "0009900002-26-000301", dt.date(2026, 10, 2)),
)
QUEUE = {
    "A": QueueItem(
        FIXTURE_DEAL,
        "2",
        "borrower_request",
        "loan modification",
        0.45,
        2,
        "The loan transferred to special servicing on 3/6/2025 due to imminent monetary default. "
        "The borrower has submitted a request to the special servicer. "
        "Its terms have not been disclosed.",
        dt.datetime(2026, 10, 1, 10, tzinfo=dt.UTC),
    ),
    "B": QueueItem(
        SECOND_DEAL,
        "3",
        "borrower_request",
        "forbearance",
        0.6,
        2,
        "The loan transferred to special servicing on 8/3/2026 due to monetary default. "
        "The borrower has requested forbearance through December 2026.",
        dt.datetime(2026, 10, 5, 10, tzinfo=dt.UTC),
    ),
    "C": QueueItem(
        FIXTURE_DEAL,
        "5",
        "transfer_reason",
        "imminent monetary default",
        0.5,
        2,
        "The loan transferred to the special servicer on 9/15/2026. "
        "The borrower reported that cash flow no longer covers debt service.",
        dt.datetime(2026, 9, 26, 10, tzinfo=dt.UTC),
    ),
}


def store_comments(db: Session, report: Report, comments: dict[str, str]) -> None:
    """Store comments as if `report`'s 10-D had reported them for September 2026."""
    deal = get_or_create(db, report.deal, cik=report.cik)
    register_filing(
        db,
        accession=report.accession,
        cik=report.cik,
        form="10-D",
        filing_date=report.filed,
        deal_id=deal.id,
        period=SEPTEMBER.first_day,
    )
    name = f"synthetic-{report.accession}.htm"
    body = "\n".join(f"{loan}: {text}" for loan, text in comments.items())
    store_file(db, accession=report.accession, name=name, content=body.encode())
    db.add(
        DistributionReport(
            accession=report.accession,
            file_name=name,
            deal_id=deal.id,
            period=SEPTEMBER.first_day,
            administrator="synthetic",
            summary="synthetic report for the review queue",
        )
    )
    db.flush()
    db.add_all(
        ReportComment(
            accession=report.accession,
            loan_id=loan,
            source_table="special servicing comments",
            deal_id=deal.id,
            period=SEPTEMBER.first_day,
            comment=text,
        )
        for loan, text in comments.items()
    )
    db.flush()


def load_book(db: Session) -> None:
    load_positions(db, user=LOADER, as_of=BOOK_DATE, lines=BOOK, source_name="synthetic book")


def open_queue(db: Session) -> dict[str, int]:
    """The book and the three queue items, each extracted when it started waiting; returns
    each item's review item id by its letter."""
    clock.set_now(dt.datetime(2026, 9, 25, 18, tzinfo=dt.UTC))
    load_book(db)
    for report in REPORTS:
        mine = {item.loan: item.comment for item in QUEUE.values() if item.deal == report.deal}
        store_comments(db, report, mine)
    ids = {}
    for letter, item in sorted(QUEUE.items(), key=lambda pair: pair[1].since):
        clock.set_now(item.since)
        extract(
            db,
            deal=item.deal,
            loan=item.loan,
            field=item.field,
            answer=item.answer,
            answer_p=item.probability,
            support_p=0.9,
            sentence=item.sentence,
        )
        ids[letter] = db.scalars(
            select(ReviewItem.id).where(
                ReviewItem.loan_id == item.loan,
                ReviewItem.deal_id == get_or_create(db, item.deal).id,
                ReviewItem.field == item.field,
            )
        ).one()
    return ids
