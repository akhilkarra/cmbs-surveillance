"""Building, reading and acting on alerts against the database.

Months are made from the fixture tape (tests/fixtures/alerts/README.md): `month_tape` moves
the reporting period end and paid-through dates, so two consecutive months made this way show
no change until a test sets a tag.
"""

import datetime as dt
import xml.etree.ElementTree as ET
from decimal import Decimal
from html import escape

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cmbs import clock
from cmbs.absee.schedule_al import NAMESPACE
from cmbs.access.roles import Role
from cmbs.access.service import Actor, PermissionRefused, UnknownUser
from cmbs.alerts import service
from cmbs.alerts.lifecycle import AlertStateRefused, State
from cmbs.alerts.rules import Severity
from cmbs.alerts.service import (
    AlertSummary,
    AlertView,
    BackInTime,
    CannotTakeAlerts,
    CoveredLoan,
    NoTape,
    Supersession,
    UnknownAlert,
    list_note,
    order_key,
    period_end,
    subject_of,
)
from cmbs.alerts.tables import Alert, AlertBuild, AlertLoan
from cmbs.audit.tables import AuditEntry
from cmbs.book.service import NoteRef, PositionLine, link_whole_loan, load_positions
from cmbs.deals.service import by_name
from cmbs.distribution.service import load_report
from cmbs.distribution.tables import ReportComment
from cmbs.domain import Period, money
from cmbs.panel.service import UnknownDeal
from cmbs.runs.service import start_run
from cmbs.settings.service import NoSettings, store_version
from cmbs.settings.values import Settings
from cmbs.tape.service import AMENDMENT, load_tape
from cmbs.testing.world import FIXTURE_CIKS
from tests.conftest import FIXTURES, needs_db

F1, F2, F3 = "Fixture Trust 2026-F1", "Fixture Trust 2026-F2", "Fixture Trust 2026-F3"
CIKS = FIXTURE_CIKS
AUGUST, SEPTEMBER, OCTOBER = Period(2026, 8), Period(2026, 9), Period(2026, 10)
TEMPLATE = FIXTURES / "alerts" / "fixture_10d_template.htm"
URI = NAMESPACE.strip("{}")
TRANSFER = "mostRecentSpecialServicerTransferDate"
STATUS = "paymentStatusLoanCode"
COMMENT = "The loan transferred effective 3/6/2025. The borrower has requested an extension."


# ---------------------------------------------------------------------------- fixture months


def month_tape(period: Period, changes: dict[tuple[str, str], str | None] | None = None) -> bytes:
    """The fixture tape moved to `period`, with (loan, tag) set to a value or removed."""
    root = ET.fromstring((FIXTURES / "schedule_al_minimal.xml").read_bytes())
    months = SEPTEMBER.months_until(period)
    for asset in root.findall(NAMESPACE + "assets"):
        for tag in ("reportingPeriodEndDate", "paidThroughDate"):
            element = asset.find(NAMESPACE + tag)
            if element is not None and element.text:
                day = dt.datetime.strptime(element.text, "%m-%d-%Y").date()
                moved = Period.of(day).shift(months)
                element.text = dt.date(moved.year, moved.month, day.day).strftime("%m-%d-%Y")
    for (loan, tag), value in (changes or {}).items():
        asset = next(
            a
            for a in root.findall(NAMESPACE + "assets")
            if a.findtext(NAMESPACE + "assetNumber") == loan
        )
        element = asset.find(NAMESPACE + tag)
        if value is None:
            if element is not None:
                asset.remove(element)
            continue
        if element is None:
            element = ET.SubElement(asset, NAMESPACE + tag)
        element.text = value
    ET.register_namespace("", URI)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def accession(deal: str, period: Period, form: int) -> str:
    return f"{CIKS[deal]:010d}-{period.year % 100:02d}-{period.month:04d}{form:02d}"


def filed(period: Period, day: int = 29) -> dt.date:
    return dt.date(period.year, period.month, day)


def load_month(
    db: Session,
    deal: str,
    period: Period,
    changes: dict[tuple[str, str], str | None] | None = None,
    *,
    on: dt.date | None = None,
) -> str:
    """Load the deal's tape for the month; returns its accession."""
    number = accession(deal, period, 1)
    load_tape(
        db,
        deal=deal,
        content=month_tape(period, changes),
        file_name=f"{period.first_day:%Y-%m}.xml",
        accession=number,
        filing_date=on or filed(period),
        cik=CIKS[deal],
    )
    return number


def load_10d(
    db: Session,
    deal: str,
    period: Period,
    comments: dict[str, str],
    *,
    on: dt.date | None = None,
    form: int = 2,
) -> str:
    rows = "\n".join(
        f"<tr><td>{loan}</td><td>3{loan}</td><td>{escape(text)}</td></tr>"
        for loan, text in comments.items()
    )
    content = TEMPLATE.read_text().format(
        deal=deal,
        distribution_date=f"{period.month:02d}/17/{period.year % 100:02d}",
        determination_date=f"{period.month:02d}/11/{period.year % 100:02d}",
        rows=rows,
    )
    number = accession(deal, period, form)
    load_report(
        db,
        user="quinn",
        deal=deal,
        accession=number,
        cik=CIKS[deal],
        filing_date=on or filed(period),
        file_name=f"{period.first_day:%Y-%m}.htm",
        content=content.encode(),
        period=period,
    )
    return number


def deal_id(db: Session, name: str = F1) -> int:
    deal = by_name(db, name)
    assert deal is not None
    return deal.id


def build(db: Session, period: Period = SEPTEMBER, deal: str = F1, **kw) -> AlertSummary:
    return service.build_alerts(db, deal_id=deal_id(db, deal), period=period, **kw)


def vintages(db: Session, deal: str = F1, period: Period = SEPTEMBER) -> list[AlertBuild]:
    return list(
        db.scalars(
            select(AlertBuild)
            .where(AlertBuild.deal_id == deal_id(db, deal), AlertBuild.period == period.first_day)
            .order_by(AlertBuild.id)
        )
    )


def messages(summary: AlertSummary) -> list[tuple[str, str, tuple[str, ...]]]:
    return [
        (view.severity.value, view.message, tuple(c.loan for c in view.loans))
        for view in summary.alerts
    ]


SEP_30 = dt.datetime(2026, 9, 30, 12, tzinfo=dt.UTC)
QUANT = Actor("quinn", Role.QUANT)


@pytest.fixture
def two_months(seeded):
    """August and September of the fixture deal; September transfers loan 7."""
    load_month(seeded, F1, AUGUST)
    load_month(seeded, F1, SEPTEMBER, {("7", TRANSFER): "09-02-2026"})
    seeded.commit()
    clock.set_now(SEP_30)
    return seeded


# ---------------------------------------------------------------------------- pure helpers


def test_period_end_is_the_last_day_of_the_month():
    assert period_end(SEPTEMBER) == dt.date(2026, 9, 30)
    assert period_end(Period(2026, 12)) == dt.date(2026, 12, 31)
    assert period_end(Period(2028, 2)) == dt.date(2028, 2, 29)


@pytest.mark.parametrize(
    ("built", "compared", "empty", "note"),
    [
        (True, True, False, None),
        (False, False, False, None),
        (False, False, True, "the September 2026 alerts have not been built"),
        (True, False, True, "no August 2026 loan tape to compare with"),
        (True, True, True, "no changes since August 2026"),
    ],
)
def test_an_empty_list_says_why(built, compared, empty, note):
    assert list_note(SEPTEMBER, built=built, compared=compared, empty=empty) == note


def test_subjects_name_the_whole_loan_when_linked():
    assert subject_of(4, "7", None) == "4/7"
    assert subject_of(4, "7", 2) == "whole loan 2"


def view(
    id: int, severity: Severity, exposure: str, balance: str | None, deal: str = F1, loan="7"
) -> AlertView:
    return AlertView(
        id=id,
        period=SEPTEMBER,
        severity=severity,
        title="t",
        message="m",
        added=(),
        settings_version=7,
        as_of=dt.date(2026, 9, 29),
        raised_at=SEP_30,
        state=State.NEW,
        state_text="new",
        note=None,
        assigned_to=None,
        loans=(
            CoveredLoan(
                deal, loan, "a", Decimal(balance) if balance else None, money(exposure), ("E",)
            ),
        ),
    )


def test_alerts_order_by_severity_then_exposure_then_balance():
    views = [
        view(1, Severity.LOW, "99", "99"),
        view(2, Severity.MEDIUM, "25", "4"),
        view(3, Severity.MEDIUM, "25", "30"),
        view(4, Severity.MEDIUM, "40", "8", deal=F2, loan="3"),
        view(5, Severity.HIGH, "0", None),
        view(6, Severity.MEDIUM, "25", None),
        view(7, Severity.MEDIUM, "25", "30", loan="10"),
        view(8, Severity.MEDIUM, "25", "30", loan="2"),
    ]
    assert [v.id for v in sorted(views, key=order_key)] == [5, 4, 8, 3, 7, 2, 6, 1]


def test_a_view_sums_exposure_and_balance_over_its_deals():
    linked = AlertView(
        **{
            **view(1, Severity.HIGH, "25", "50").__dict__,
            "loans": (
                CoveredLoan(F1, "1", "a", Decimal("50.00"), money("25"), ("E", "F")),
                CoveredLoan(F2, "3", "b", None, money("40"), ("D",)),
            ),
        }
    )
    assert linked.exposure == Decimal("65.00")
    assert linked.balance == Decimal("50.00")
    assert linked.classes == ("E", "F", "D")
    assert linked.deals == (F1, F2)
    assert linked.accessions == ("a", "b")
    assert linked.is_open
    assert view(2, Severity.LOW, "0", None).balance is None


def test_a_summary_reads_as_one_line():
    summary = AlertSummary(F1, SEPTEMBER, dt.date(2026, 9, 29), 7, 1, (), None)
    assert summary.text() == (
        f"{F1} September 2026 as of 2026-09-29 (settings version 7): 1 new alert"
    )
    none = AlertSummary(
        F1, SEPTEMBER, dt.date(2026, 9, 29), 7, 0, (), "no changes since August 2026"
    )
    assert none.text() == (
        f"{F1} September 2026 as of 2026-09-29 (settings version 7): 0 new alerts; "
        "no changes since August 2026"
    )


# ---------------------------------------------------------------------------- building


@needs_db
def test_a_month_is_built_once_with_its_settings_and_filing(two_months):
    db = two_months
    summary = build(db)
    db.commit()
    assert messages(summary) == [("high", "transferred to special servicing on 2026-09-02", ("7",))]
    assert (summary.raised, summary.settings_version, summary.as_of, summary.note) == (
        1,
        7,
        dt.date(2026, 9, 29),
        None,
    )
    alert = summary.alerts[0]
    assert alert.accessions == (accession(F1, SEPTEMBER, 1),)
    assert (alert.settings_version, alert.as_of, alert.raised_at, alert.state_text) == (
        7,
        dt.date(2026, 9, 29),
        SEP_30,
        "new",
    )
    assert alert.loans[0].balance == Decimal("9000000.00")
    stored = db.get_one(Alert, alert.id)
    assert (stored.subject, stored.rule, stored.whole_loan_id, stored.run_id) == (
        f"{deal_id(db)}/7",
        "special servicing transfer",
        None,
        None,
    )
    (built,) = vintages(db)
    assert (summary.vintage, stored.raised_in) == (built.id, built.id)
    assert (built.deal_id, built.period, built.as_of) == (
        deal_id(db),
        SEPTEMBER.first_day,
        dt.date(2026, 9, 29),
    )
    assert (
        built.accession,
        built.report_accession,
        built.compared_with,
        built.compared_report,
    ) == (
        accession(F1, SEPTEMBER, 1),
        None,
        accession(F1, AUGUST, 1),
        None,
    )
    assert (built.settings_version, built.built_at, built.built_by, built.run_id) == (
        7,
        SEP_30,
        None,
        None,
    )

    clock.set_now(SEP_30 + dt.timedelta(days=3))
    again = build(db)
    db.commit()
    assert (again.raised, again.superseded, again.vintage) == (0, 0, built.id)
    assert [v.id for v in again.alerts] == [alert.id]
    assert db.scalar(select(func.count()).select_from(Alert)) == 1
    assert db.scalar(select(func.count()).select_from(AlertLoan)) == 1
    assert [(b.id, b.built_at) for b in vintages(db)] == [(built.id, SEP_30)]
    # Without an actor nothing is audited.
    assert db.scalars(select(AuditEntry).where(AuditEntry.action == "raise alert")).all() == []


@needs_db
def test_an_actor_and_run_audit_every_alert_raised(two_months):
    db = two_months
    run = start_run(db, kind="refresh", started_by="quinn")
    summary = build(db, actor=QUANT, run_id=run.id)
    db.commit()
    stored = db.get_one(Alert, summary.alerts[0].id)
    assert stored.run_id == run.id
    entry = db.scalars(select(AuditEntry).where(AuditEntry.action == "raise alert")).one()
    assert (entry.user_name, entry.role, entry.run_id, entry.subject, entry.subject_key) == (
        "quinn",
        "quant",
        run.id,
        "alert",
        str(stored.id),
    )
    assert entry.after == "transferred to special servicing on 2026-09-02"
    assert entry.detail == {
        "deal": F1,
        "loan": "7",
        "period": "September 2026",
        "rule": "special servicing transfer",
        "severity": "high",
        "accession": accession(F1, SEPTEMBER, 1),
    }
    build(db, actor=QUANT, run_id=run.id)
    assert (
        db.scalar(
            select(func.count()).select_from(AuditEntry).where(AuditEntry.action == "raise alert")
        )
        == 1
    )


@needs_db
def test_build_arguments_that_are_programming_errors(two_months):
    db = two_months
    with pytest.raises(
        ValueError, match="^a run's writes are audited, so a run id needs an actor$"
    ):
        build(db, run_id=1)
    with pytest.raises(ValueError, match="^an actor acting as pm cannot build alerts$"):
        build(db, actor=Actor("pat", Role.PM))
    with pytest.raises(ValueError, match="^no deal with id 999999$"):
        service.build_alerts(db, deal_id=999999, period=SEPTEMBER)


@needs_db
def test_a_month_without_a_tape_is_refused_and_audited_with_an_actor(two_months):
    db = two_months
    message = f"no October 2026 loan tape of {F1} had been filed by 2026-09-30"
    with pytest.raises(NoTape, match=f"^{message}$"):
        build(db, OCTOBER)
    with pytest.raises(NoTape, match=f"^{message}$"):
        build(db, OCTOBER, actor=QUANT)
    db.rollback()
    entry = db.scalars(select(AuditEntry).where(AuditEntry.outcome == "refused")).one()
    assert (entry.user_name, entry.action, entry.subject, entry.subject_key, entry.reason) == (
        "quinn",
        "raise alert",
        "deal",
        F1,
        message,
    )
    with pytest.raises(NoTape, match="had been filed by 2026-09-28$"):
        build(db, as_of=dt.date(2026, 9, 28))


@needs_db
def test_a_month_built_before_any_settings_were_approved_is_refused(two_months):
    db = two_months
    with pytest.raises(NoSettings, match="^no settings approved by 2026-09-01"):
        clock.set_now(dt.datetime(2026, 9, 1, tzinfo=dt.UTC))
        build(db, AUGUST, as_of=dt.date(2026, 8, 29))


@needs_db
def test_a_first_month_has_nothing_to_compare_with(seeded):
    load_month(seeded, F1, SEPTEMBER)
    clock.set_now(SEP_30)
    summary = build(seeded)
    assert (summary.raised, summary.alerts, summary.note) == (
        0,
        (),
        "no August 2026 loan tape to compare with",
    )
    listed = service.month_alerts(seeded, reader="pat", deal=F1, period=SEPTEMBER)
    assert (listed.alerts, listed.note) == ((), "no August 2026 loan tape to compare with")


@needs_db
def test_an_unbuilt_month_says_so_and_unknown_deals_are_refused(seeded):
    load_month(seeded, F1, SEPTEMBER)
    listed = service.month_alerts(seeded, reader="pat", deal=F1, period=SEPTEMBER)
    assert (listed.deal, listed.period, listed.note) == (
        F1,
        SEPTEMBER,
        "the September 2026 alerts have not been built",
    )
    with pytest.raises(UnknownDeal):
        service.month_alerts(seeded, reader="pat", deal="No Such Trust", period=SEPTEMBER)
    with pytest.raises(PermissionRefused, match="^operator may not read the panel$"):
        service.month_alerts(seeded, reader="olly", deal=F1, period=SEPTEMBER)


@needs_db
def test_months_are_compared_as_published_on_the_newer_months_filing_date(seeded):
    """An August amendment filed after September was published does not change the comparison;
    a build as of a later day sees it."""
    load_month(seeded, F1, AUGUST)
    load_month(seeded, F1, SEPTEMBER, {("4", STATUS): "1"})
    load_tape(
        seeded,
        deal=F1,
        content=month_tape(AUGUST, {("4", STATUS): "0"}),
        file_name="2026-08-amended.xml",
        accession="0009900001-26-000899",
        filing_date=dt.date(2026, 10, 5),
        form=AMENDMENT,
        amends=accession(F1, AUGUST, 1),
        cik=CIKS[F1],
    )
    clock.set_now(dt.datetime(2026, 10, 6, tzinfo=dt.UTC))
    published = build(seeded)
    assert messages(published) == []
    assert published.as_of == dt.date(2026, 9, 29)
    later = build(seeded, as_of=dt.date(2026, 10, 5))
    assert messages(later) == [("medium", "payment status worsened: current to 30 days", ("4",))]


@needs_db
def test_a_10d_filed_after_the_tape_moves_the_publication_day_and_adds_comments(seeded):
    load_month(seeded, F1, AUGUST)
    load_10d(seeded, F1, AUGUST, {"2": "The loan transferred effective 3/6/2025."})
    load_month(seeded, F1, SEPTEMBER)
    load_10d(seeded, F1, SEPTEMBER, {"2": COMMENT}, on=dt.date(2026, 10, 2))
    clock.set_now(SEP_30)
    before_report = build(seeded)
    assert (messages(before_report), before_report.as_of) == ([], dt.date(2026, 9, 29))
    clock.set_now(dt.datetime(2026, 10, 3, tzinfo=dt.UTC))
    after_report = build(seeded)
    assert after_report.as_of == dt.date(2026, 10, 2)
    assert messages(after_report) == [("low", "servicer comment changed: 1 sentence added", ("2",))]
    (alert,) = after_report.alerts
    assert alert.added == ("The borrower has requested an extension.",)
    assert alert.accessions == (accession(F1, SEPTEMBER, 2),)
    # Two vintages: the first, without the report, is kept as it was.
    assert [(b.as_of, b.report_accession, b.compared_report) for b in vintages(seeded)] == [
        (dt.date(2026, 9, 29), None, accession(F1, AUGUST, 2)),
        (dt.date(2026, 10, 2), accession(F1, SEPTEMBER, 2), accession(F1, AUGUST, 2)),
    ]
    # Reading the same tapes and reports again is the same vintage.
    clock.set_now(dt.datetime(2026, 10, 9, tzinfo=dt.UTC))
    assert build(seeded).vintage == after_report.vintage
    assert len(vintages(seeded)) == 2


@needs_db
def test_new_settings_make_a_new_vintage_even_on_the_same_day(two_months):
    db = two_months
    first = build(db)
    store_version(
        db,
        version=8,
        values=Settings(dscr_alert_threshold=Decimal("1.05")),
        proposed_by="quinn",
        approved_by="rae",
        approved_at=SEP_30,
    )
    second = build(db)
    assert (second.as_of, second.settings_version) == (first.as_of, 8)
    assert second.vintage != first.vintage
    assert (second.raised, second.superseded, [v.id for v in second.alerts]) == (
        0,
        0,
        [first.alerts[0].id],
    )
    assert [b.settings_version for b in vintages(db)] == [7, 8]


@needs_db
def test_the_latest_10d_of_the_month_is_the_one_compared(seeded):
    load_month(seeded, F1, AUGUST)
    load_10d(seeded, F1, AUGUST, {"2": "First."})
    load_month(seeded, F1, SEPTEMBER)
    load_10d(seeded, F1, SEPTEMBER, {"2": "First. Later report."}, form=3)
    load_10d(seeded, F1, SEPTEMBER, {"2": "First. Earlier report."}, on=dt.date(2026, 9, 28))
    clock.set_now(SEP_30)
    (alert,) = build(seeded).alerts
    assert (alert.added, alert.accessions) == (("Later report.",), (accession(F1, SEPTEMBER, 3),))


@needs_db
def test_a_loans_comments_from_several_tables_are_read_as_one_in_table_order(seeded):
    load_month(seeded, F1, AUGUST)
    load_10d(seeded, F1, AUGUST, {"2": "First."})
    load_month(seeded, F1, SEPTEMBER)
    report = load_10d(seeded, F1, SEPTEMBER, {"2": "First."})
    seeded.add(
        ReportComment(
            accession=report,
            loan_id="2",
            source_table="A watchlist table",
            deal_id=deal_id(seeded),
            period=SEPTEMBER.first_day,
            comment="Watchlisted.",
        )
    )
    seeded.flush()
    clock.set_now(SEP_30)
    (alert,) = build(seeded).alerts
    assert alert.added == ("Watchlisted.",)
    month = service.read_month(seeded, deal_id(seeded), SEPTEMBER, dt.date(2026, 9, 29))
    assert month is not None and month.comments is not None
    assert month.comments.by_loan == {"2": "Watchlisted. First."}


@needs_db
def test_a_quarantined_loan_is_reported_missing_with_the_reason(seeded):
    load_month(seeded, F1, AUGUST)
    load_month(seeded, F1, SEPTEMBER, {("7", "reportPeriodEndActualBalanceAmount"): "-1.00"})
    clock.set_now(SEP_30)
    (alert,) = build(seeded).alerts
    assert alert.message.startswith(
        f"no longer reported: quarantined in {accession(F1, SEPTEMBER, 1)}: "
    )
    assert alert.loans[0].balance is None


@needs_db
def test_exposure_is_the_book_at_the_end_of_the_alerts_month(two_months):
    db = two_months
    alert_id = build(db).alerts[0].id
    assert service.read_alert(db, reader="pat", alert_id=alert_id).exposure == Decimal("0.00")
    load_positions(
        db, user="quinn", as_of=dt.date(2026, 9, 30), lines=[PositionLine(F1, "F", money("5"))]
    )
    load_positions(
        db,
        user="quinn",
        as_of=dt.date(2026, 9, 15),
        lines=[PositionLine(F1, "E", money("20")), PositionLine(F2, "D", money("40"))],
    )
    load_positions(
        db, user="quinn", as_of=dt.date(2026, 10, 1), lines=[PositionLine(F1, "A", money("99"))]
    )
    read = service.read_alert(db, reader="pat", alert_id=alert_id)
    assert (read.exposure, read.classes) == (Decimal("5.00"), ("F",))
    with pytest.raises(UnknownAlert, match="^no alert 999999$"):
        service.read_alert(db, reader="pat", alert_id=999999)


@needs_db
def test_a_linked_whole_loan_raises_one_alert_listing_every_deal(seeded):
    for deal, loan in ((F1, "1"), (F2, "3")):
        load_month(seeded, deal, AUGUST)
        load_month(seeded, deal, SEPTEMBER, {(loan, TRANSFER): "09-02-2026"})
    link_whole_loan(seeded, user="quinn", notes=[NoteRef(F1, "1"), NoteRef(F2, "3")])
    load_positions(
        seeded,
        user="quinn",
        as_of=dt.date(2026, 9, 30),
        lines=[
            PositionLine(F1, "E", money("20,000,000.00")),
            PositionLine(F1, "F", money("5,000,000.00")),
            PositionLine(F2, "D", money("40,000,000.00")),
        ],
    )
    clock.set_now(SEP_30)
    first, second = build(seeded, deal=F1), build(seeded, deal=F2)
    assert (first.raised, second.raised) == (1, 0)
    (alert,) = second.alerts
    assert first.alerts[0].id == alert.id
    assert [(c.deal, c.loan, c.accession, c.balance) for c in alert.loans] == [
        (F1, "1", accession(F1, SEPTEMBER, 1), Decimal("50000000.00")),
        (F2, "3", accession(F2, SEPTEMBER, 1), Decimal("8000000.00")),
    ]
    assert (alert.exposure, alert.classes, alert.balance) == (
        Decimal("65000000.00"),
        ("E", "F", "D"),
        Decimal("58000000.00"),
    )
    assert seeded.get_one(Alert, alert.id).subject.startswith("whole loan ")


@needs_db
def test_the_book_view_holds_open_alerts_on_held_deals_in_order(seeded):
    for deal in (F1, F2, F3):
        load_month(seeded, deal, AUGUST, {("4", STATUS): "0"})
        load_month(seeded, deal, SEPTEMBER, {("4", STATUS): "1", ("7", TRANSFER): "09-02-2026"})
    load_positions(
        seeded,
        user="quinn",
        as_of=dt.date(2026, 9, 30),
        lines=[PositionLine(F1, "E", money("25")), PositionLine(F2, "D", money("40"))],
    )
    clock.set_now(SEP_30)
    for deal in (F1, F2, F3):
        build(seeded, deal=deal)
    shown = service.book_view(seeded, reader="pat", period=SEPTEMBER)
    assert [(v.severity.value, v.deals, v.loans[0].loan) for v in shown] == [
        ("high", (F2,), "7"),
        ("high", (F1,), "7"),
        ("medium", (F2,), "4"),
        ("medium", (F1,), "4"),
    ]
    service.close(seeded, user="alex", alert_id=shown[0].id, note="sponsor cured")
    after_close = service.book_view(seeded, reader="pat", period=SEPTEMBER)
    assert [v.id for v in after_close] == [v.id for v in shown[1:]]
    assert len(service.open_alerts(seeded, reader="pat", period=SEPTEMBER)) == 5
    assert service.open_alerts(seeded, reader="pat", period=OCTOBER) == []
    assert len(service.open_alerts(seeded, reader="pat")) == 5


# ---------------------------------------------------------------------------- vintages


OCT_11 = dt.datetime(2026, 10, 11, 12, tzinfo=dt.UTC)


def amend_september(db: Session, changes, *, on: dt.date, number: int, amends: str) -> str:
    """Load an ABS-EE/A of the fixture deal's September tape; returns its accession."""
    amendment = accession(F1, SEPTEMBER, number)
    load_tape(
        db,
        deal=F1,
        content=month_tape(SEPTEMBER, changes),
        file_name=f"2026-09-amendment-{number}.xml",
        accession=amendment,
        filing_date=on,
        form=AMENDMENT,
        amends=amends,
        cik=CIKS[F1],
    )
    return amendment


@needs_db
def test_an_amendment_makes_a_new_vintage_that_supersedes_what_it_no_longer_shows(seeded):
    load_month(seeded, F1, AUGUST, {("4", STATUS): "0"})
    original = load_month(seeded, F1, SEPTEMBER, {("4", STATUS): "1"})
    run = start_run(seeded, kind="refresh", started_by="quinn")
    clock.set_now(SEP_30)
    first = build(seeded, actor=QUANT, run_id=run.id)
    assert messages(first) == [("medium", "payment status worsened: current to 30 days", ("4",))]
    loan_4 = first.alerts[0].id
    service.acknowledge(seeded, user="pat", alert_id=loan_4)
    # The amendment puts loan 4 back to current and transfers loan 7.
    amendment = amend_september(
        seeded,
        {("4", STATUS): "0", ("7", TRANSFER): "09-02-2026"},
        on=dt.date(2026, 10, 10),
        number=3,
        amends=original,
    )
    clock.set_now(OCT_11)
    second = build(seeded, actor=QUANT, run_id=run.id)
    assert (second.raised, second.superseded, second.as_of) == (1, 1, dt.date(2026, 10, 10))
    assert second.text().endswith(": 1 new alert, 1 superseded")
    assert messages(second) == [("high", "transferred to special servicing on 2026-09-02", ("7",))]
    assert second.alerts[0].accessions == (amendment,)
    reason = f"no longer shown by September 2026 as published on 2026-10-10 (tape {amendment})"
    (gone,) = second.superseded_alerts
    assert gone.id == loan_4
    assert gone.superseded == Supersession(second.vintage or 0, OCT_11, reason)
    # Never rewritten: the message, the state and the filing it came from stay as they were.
    assert (gone.message, gone.state_text, gone.accessions, gone.is_open) == (
        "payment status worsened: current to 30 days",
        "acknowledged by pat on 2026-09-30",
        (original,),
        False,
    )
    first_vintage, second_vintage = vintages(seeded)
    assert (first_vintage.id, first_vintage.as_of, first_vintage.accession) == (
        first.vintage,
        dt.date(2026, 9, 29),
        original,
    )
    assert (first_vintage.built_at, first_vintage.built_by, first_vintage.run_id) == (
        SEP_30,
        "quinn",
        run.id,
    )
    assert (second_vintage.id, second_vintage.as_of, second_vintage.accession) == (
        second.vintage,
        dt.date(2026, 10, 10),
        amendment,
    )
    stored = seeded.get_one(Alert, loan_4)
    assert (stored.raised_in, stored.superseded_by, stored.superseded_reason) == (
        first_vintage.id,
        second_vintage.id,
        reason,
    )
    listed = service.month_alerts(seeded, reader="pat", deal=F1, period=SEPTEMBER)
    assert [v.id for v in listed.alerts] == [v.id for v in second.alerts]
    assert [v.id for v in listed.superseded_alerts] == [loan_4]
    assert loan_4 not in [v.id for v in service.open_alerts(seeded, reader="pat")]
    assert service.read_alert(seeded, reader="rae", alert_id=loan_4).superseded is not None
    entry = seeded.scalars(select(AuditEntry).where(AuditEntry.action == "supersede alert")).one()
    assert (entry.user_name, entry.role, entry.run_id, entry.subject_key, entry.field) == (
        "quinn",
        "quant",
        run.id,
        str(loan_4),
        "superseded",
    )
    assert (entry.after, entry.detail) == (
        reason,
        {
            "deal": F1,
            "loan": "4",
            "period": "September 2026",
            "vintage": second_vintage.id,
            "alert superseded": True,
        },
    )

    # Building again reads nothing new: no vintage, nothing raised or superseded.
    clock.set_now(OCT_11 + dt.timedelta(days=1))
    again = build(seeded, actor=QUANT)
    assert (again.raised, again.superseded, again.vintage) == (0, 0, second_vintage.id)
    assert len(vintages(seeded)) == 2

    # A build may not read the month as it stood before the latest vintage.
    message = (
        f"the September 2026 alerts of {F1} were last built as of 2026-10-10; a build as of "
        "2026-09-29 would go back in time"
    )
    with pytest.raises(BackInTime, match=f"^{message}$"):
        build(seeded, actor=QUANT, as_of=dt.date(2026, 9, 29))
    seeded.rollback()
    refused = seeded.scalars(select(AuditEntry).where(AuditEntry.outcome == "refused")).one()
    assert refused.reason == message


@needs_db
def test_a_change_shown_again_after_being_superseded_is_a_new_alert(seeded):
    load_month(seeded, F1, AUGUST, {("4", STATUS): "0"})
    original = load_month(seeded, F1, SEPTEMBER, {("4", STATUS): "1"})
    clock.set_now(SEP_30)
    first = build(seeded).alerts[0].id
    corrected = amend_september(
        seeded, {("4", STATUS): "0"}, on=dt.date(2026, 10, 10), number=3, amends=original
    )
    clock.set_now(OCT_11)
    assert build(seeded).superseded == 1
    amend_september(
        seeded, {("4", STATUS): "1"}, on=dt.date(2026, 10, 20), number=4, amends=corrected
    )
    clock.set_now(dt.datetime(2026, 10, 21, tzinfo=dt.UTC))
    third = build(seeded)
    assert (third.raised, third.superseded) == (1, 0)
    (back,) = third.alerts
    assert back.id != first and back.message == "payment status worsened: current to 30 days"
    assert [v.id for v in third.superseded_alerts] == [first]
    assert len(vintages(seeded)) == 3


@needs_db
def test_a_new_vintage_keeps_the_alerts_it_still_shows(seeded):
    load_month(seeded, F1, AUGUST, {("4", STATUS): "0"})
    original = load_month(seeded, F1, SEPTEMBER, {("4", STATUS): "1"})
    clock.set_now(SEP_30)
    first = build(seeded)
    amend_september(
        seeded,
        {("4", STATUS): "1", ("7", TRANSFER): "09-02-2026"},
        on=dt.date(2026, 10, 10),
        number=3,
        amends=original,
    )
    clock.set_now(OCT_11)
    second = build(seeded)
    assert (second.raised, second.superseded, second.superseded_alerts) == (1, 0, ())
    kept = next(v for v in second.alerts if v.loans[0].loan == "4")
    assert kept.id == first.alerts[0].id
    # The kept alert still names the filing that first showed it.
    assert kept.accessions == (original,)
    assert seeded.scalar(select(func.count()).select_from(Alert)) == 2


@needs_db
def test_a_link_made_after_a_month_was_built_still_gives_one_alert(seeded):
    for deal, loan in ((F1, "1"), (F2, "3")):
        load_month(seeded, deal, AUGUST)
        load_month(seeded, deal, SEPTEMBER, {(loan, TRANSFER): "09-02-2026"})
    clock.set_now(SEP_30)
    (before_link,) = build(seeded, deal=F1).alerts
    link_whole_loan(seeded, user="quinn", notes=[NoteRef(F1, "1"), NoteRef(F2, "3")])
    second = build(seeded, deal=F2)
    assert second.raised == 0
    (alert,) = second.alerts
    assert alert.id == before_link.id
    assert [(c.deal, c.loan) for c in alert.loans] == [(F1, "1"), (F2, "3")]
    assert seeded.scalar(select(func.count()).select_from(Alert)) == 1


@needs_db
def test_a_whole_loan_alert_lasts_while_any_deal_still_shows_it(seeded):
    for deal, loan in ((F1, "1"), (F2, "3")):
        load_month(seeded, deal, AUGUST)
        load_month(seeded, deal, SEPTEMBER, {(loan, TRANSFER): "09-02-2026"})
    link_whole_loan(seeded, user="quinn", notes=[NoteRef(F1, "1"), NoteRef(F2, "3")])
    load_positions(
        seeded,
        user="quinn",
        as_of=dt.date(2026, 9, 30),
        lines=[PositionLine(F1, "E", money("25")), PositionLine(F2, "D", money("40"))],
    )
    clock.set_now(SEP_30)
    (alert,) = build(seeded, deal=F1).alerts
    build(seeded, deal=F2)
    # F1's September is amended: its note never transferred. F2's still did.
    amend_september(
        seeded, {}, on=dt.date(2026, 10, 10), number=3, amends=accession(F1, SEPTEMBER, 1)
    )
    clock.set_now(OCT_11)
    f1 = build(seeded, deal=F1)
    assert (f1.superseded, f1.alerts) == (1, ())
    (shown,) = f1.superseded_alerts
    assert (shown.id, shown.superseded, shown.is_open) == (alert.id, None, True)
    assert [(c.deal, c.superseded) for c in shown.loans] == [(F1, True), (F2, False)]
    assert shown.exposure == Decimal("40.00")
    f2 = service.month_alerts(seeded, reader="pat", deal=F2, period=SEPTEMBER)
    assert [v.id for v in f2.alerts] == [alert.id]
    assert [v.id for v in service.book_view(seeded, reader="pat", period=SEPTEMBER)] == [alert.id]
    entry = seeded.scalars(select(AuditEntry).where(AuditEntry.action == "supersede alert")).all()
    assert entry == []  # built without an actor: nothing audited


@needs_db
def test_an_alert_stored_by_another_build_meanwhile_is_reused_not_duplicated(seeded, monkeypatch):
    """The lookup misses an alert another transaction committed just before the insert: the
    insert's conflict is caught and the stored alert reused."""
    load_month(seeded, F1, AUGUST, {("4", STATUS): "0"})
    original = load_month(seeded, F1, SEPTEMBER, {("4", STATUS): "1"})
    clock.set_now(SEP_30)
    (first,) = build(seeded).alerts
    amend_september(
        seeded, {("4", STATUS): "1"}, on=dt.date(2026, 10, 10), number=3, amends=original
    )
    lookup = service._open_alert
    calls = []

    def first_lookup_misses(*args):
        calls.append(args)
        return None if len(calls) == 1 else lookup(*args)

    monkeypatch.setattr(service, "_open_alert", first_lookup_misses)
    clock.set_now(OCT_11)
    second = build(seeded)
    assert (second.raised, second.superseded, [v.id for v in second.alerts]) == (0, 0, [first.id])
    assert len(calls) == 2
    assert seeded.scalar(select(func.count()).select_from(Alert)) == 1


# ---------------------------------------------------------------------------- lifecycle


@pytest.fixture
def alert_id(two_months) -> int:
    alert = build(two_months).alerts[0].id
    two_months.commit()
    return alert


def _changes(db: Session, alert: int) -> list[tuple]:
    return [
        (e.user_name, e.role, e.action, e.field, e.before, e.after, e.reason, e.outcome)
        for e in db.scalars(
            select(AuditEntry)
            .where(AuditEntry.subject == "alert", AuditEntry.subject_key == str(alert))
            .order_by(AuditEntry.id)
        )
    ]


@needs_db
def test_an_alert_is_acknowledged_assigned_and_closed_with_every_step_audited(two_months, alert_id):
    db = two_months
    seen = service.acknowledge(db, user="pat", alert_id=alert_id)
    assert (seen.state, seen.state_text, seen.note) == (
        State.ACKNOWLEDGED,
        "acknowledged by pat on 2026-09-30",
        None,
    )
    given = service.assign(
        db, user="pat", alert_id=alert_id, to="alex", note=" check the rent roll "
    )
    assert (given.state_text, given.note, given.assigned_to) == (
        "assigned to alex",
        "check the rent roll",
        "alex",
    )
    assert [v.id for v in service.work_list(db, user="alex")] == [alert_id]
    assert service.work_list(db, user="morgan") == []
    stored = db.get_one(Alert, alert_id)
    assert (stored.assigned_by, stored.assigned_at, stored.acknowledged_at) == (
        "pat",
        SEP_30,
        SEP_30,
    )
    done = service.close(db, user="alex", alert_id=alert_id, note=" sponsor funded the shortfall ")
    assert (done.state, done.state_text, done.note, done.is_open) == (
        State.CLOSED,
        "closed by alex",
        "sponsor funded the shortfall",
        False,
    )
    assert (stored.closed_by, stored.closed_at) == ("alex", SEP_30)
    assert service.work_list(db, user="alex") == []
    db.commit()
    assert _changes(db, alert_id) == [
        (
            "pat",
            "pm",
            "acknowledge alert",
            "state",
            "new",
            "acknowledged by pat on 2026-09-30",
            None,
            "done",
        ),
        (
            "pat",
            "pm",
            "assign alert",
            "state",
            "acknowledged by pat on 2026-09-30",
            "assigned to alex",
            "check the rent roll",
            "done",
        ),
        (
            "alex",
            "analyst",
            "close alert",
            "state",
            "assigned to alex",
            "closed by alex",
            "sponsor funded the shortfall",
            "done",
        ),
    ]


@needs_db
def test_an_assignment_without_a_note_and_a_reassignment(two_months, alert_id):
    db = two_months
    first = service.assign(db, user="alex", alert_id=alert_id, to="pat", note="  ")
    assert (first.state_text, first.note) == ("assigned to pat", None)
    second = service.assign(db, user="pat", alert_id=alert_id, to="morgan")
    assert second.state_text == "assigned to morgan"


@needs_db
def test_lifecycle_refusals_are_audited_and_change_nothing(two_months, alert_id):
    db = two_months
    with pytest.raises(PermissionRefused, match="^quant may not close alerts$"):
        service.close(db, user="quinn", alert_id=alert_id, note="done")
    with pytest.raises(PermissionRefused, match="^risk may not acknowledge alerts$"):
        service.acknowledge(db, user="rae", alert_id=alert_id)
    with pytest.raises(PermissionRefused, match="^operator may not assign alerts$"):
        service.assign(db, user="olly", alert_id=alert_id, to="alex")
    with pytest.raises(
        CannotTakeAlerts, match="^quinn cannot be assigned alerts: quant may not close alerts$"
    ):
        service.assign(db, user="pat", alert_id=alert_id, to="quinn")
    db.rollback()
    with pytest.raises(UnknownUser, match="^no user named 'bob'$"):
        service.assign(db, user="pat", alert_id=alert_id, to="bob")
    db.rollback()
    with pytest.raises(UnknownAlert, match="^no alert 999999$"):
        service.acknowledge(db, user="pat", alert_id=999999)
    db.rollback()
    with pytest.raises(AlertStateRefused, match="^closing an alert needs a note saying why$"):
        service.close(db, user="alex", alert_id=alert_id, note=" ")
    db.rollback()
    service.close(db, user="alex", alert_id=alert_id, note="cured")
    db.commit()
    reopen = "the alert was closed by alex, and a closed alert never reopens"
    for attempt in (
        lambda: service.acknowledge(db, user="pat", alert_id=alert_id),
        lambda: service.assign(db, user="pat", alert_id=alert_id, to="alex"),
        lambda: service.close(db, user="pat", alert_id=alert_id, note="again"),
    ):
        with pytest.raises(AlertStateRefused, match=f"^{reopen}$"):
            attempt()
        db.rollback()
    assert db.get_one(Alert, alert_id).state == "closed"
    refused = [
        (e.user_name, e.action, e.subject_key, e.reason)
        for e in db.scalars(
            select(AuditEntry).where(AuditEntry.outcome == "refused").order_by(AuditEntry.id)
        )
    ]
    key = str(alert_id)
    assert refused == [
        ("quinn", "close alerts", None, "quant may not close alerts"),
        ("rae", "acknowledge alerts", None, "risk may not acknowledge alerts"),
        ("olly", "assign alerts", None, "operator may not assign alerts"),
        ("pat", "assign alert", key, "quinn cannot be assigned alerts: quant may not close alerts"),
        ("pat", "assign alert", key, "no user named 'bob'"),
        ("pat", "acknowledge alert", "999999", "no alert 999999"),
        ("alex", "close alert", key, "closing an alert needs a note saying why"),
        ("pat", "acknowledge alert", key, reopen),
        ("pat", "assign alert", key, reopen),
        ("pat", "close alert", key, reopen),
    ]


@needs_db
def test_a_closed_alert_stays_closed_and_next_months_change_is_new(two_months, alert_id):
    db = two_months
    service.close(db, user="alex", alert_id=alert_id, note="cured")
    load_month(
        db,
        F1,
        OCTOBER,
        {("7", TRANSFER): "09-02-2026", ("7", "workoutStrategyCode"): "2"},
    )
    clock.set_now(dt.datetime(2026, 10, 30, 12, tzinfo=dt.UTC))
    october = build(db, OCTOBER)
    assert messages(october) == [
        ("medium", "workout strategy changed: not reported to foreclosure", ("7",))
    ]
    assert october.alerts[0].id != alert_id
    assert service.read_alert(db, reader="alex", alert_id=alert_id).state_text == "closed by alex"
