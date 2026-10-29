"""Refresh runs against a database, with EDGAR replayed (cmbs.refresh.service and claims).

The fixture deals' filings are the synthetic recordings in tests/fixtures/refresh; a test may
replace one file to make a month fail.
"""

import datetime as dt
import sys
import types
from pathlib import Path

import pytest
from sqlalchemy import func, select

from cmbs import clock
from cmbs.access.roles import Role
from cmbs.access.service import Actor, PermissionRefused
from cmbs.audit.tables import AuditEntry
from cmbs.book.service import PositionLine, load_positions
from cmbs.db import engine
from cmbs.deals.service import by_name, get_or_create
from cmbs.distribution.service import load_report
from cmbs.distribution.tables import DistributionReport
from cmbs.domain import Period, money
from cmbs.edgar.archives import filing_base_url
from cmbs.edgar.replay import Replay
from cmbs.errors import Refused
from cmbs.panel.service import UnknownDeal
from cmbs.refresh import service
from cmbs.refresh.claims import (
    ABANDONED,
    AlreadyRefreshing,
    Claims,
    held_message,
    holders,
    record_claims,
)
from cmbs.refresh.plan import FilingRef, MonthFilings
from cmbs.refresh.service import BadRange, EmptyBook, MonthNotLoaded, build_alerts, month_status
from cmbs.refresh.sources import ArchiveReplay, Fetched, FirstRecorded, Sources
from cmbs.refresh.tables import RunClaim, RunMonth
from cmbs.runs import service as runs
from cmbs.runs.service import NotLoaded
from cmbs.runs.tables import Run
from cmbs.tape.service import load_tape
from cmbs.tape.tables import LoanPeriod, LoanTape
from tests.conftest import needs_db

pytestmark = needs_db

FIXTURES = Path(__file__).parent / "fixtures"
EDGAR = (FIXTURES / "refresh" / "edgar", FIXTURES / "edgar")
ARCHIVE = FIXTURES / "refresh" / "archive"
F1, F2, HYT = "Fixture Trust 2026-F1", "Fixture Trust 2026-F2", "HYT 2024-RGCY"
CIKS = {F1: 9_900_001, F2: 9_900_002}
AUG, SEP, OCT = Period(2026, 8), Period(2026, 9), Period(2026, 10)
TODAY = dt.datetime(2026, 10, 28, 9, 0, tzinfo=dt.UTC)
NOT_A_REPORT = b"<html><body>not a report</body></html>"


class Archive:
    def __init__(self) -> None:
        self.recorded = ArchiveReplay(ARCHIVE)
        self.replaced: dict[str, bytes] = {}
        self.fetched: list[str] = []

    def get_bytes(self, url: str) -> bytes:
        self.fetched.append(url)
        if url in self.replaced:
            return self.replaced[url]
        return self.recorded.get_bytes(url)


def accession(deal: str, period: Period, form: str) -> str:
    filed = dt.date(period.year, period.month, 29)
    sequence = 400 + period.month * 2 + (1 if form == "ABS-EE" else 0)
    return f"{CIKS[deal]:010d}-{filed.year % 100:02d}-{sequence:06d}"


def exhibit_url(deal: str, period: Period, form: str) -> str:
    slug = deal.lower().replace(" ", "-")
    kind = "ex102" if form == "ABS-EE" else "ex991"
    suffix = "xml" if form == "ABS-EE" else "htm"
    name = f"{slug}_{kind}-{period.year}{period.month:02d}.{suffix}"
    return filing_base_url(CIKS[deal], accession(deal, period, form)) + name


@pytest.fixture
def archive() -> Archive:
    return Archive()


@pytest.fixture
def sources(archive) -> Sources:
    return Sources(edgar=FirstRecorded(*(Replay(d) for d in EDGAR)), archive=archive)


@pytest.fixture
def book(seeded):
    lines = [
        PositionLine(F1, "E", money("20000000")),
        PositionLine(F2, "D", money("40000000")),
        PositionLine(HYT, "E", money("30000000")),
    ]
    load_positions(seeded, user="olly", as_of=dt.date(2026, 9, 30), lines=lines)
    seeded.commit()
    return seeded


def deal_id(db, name: str) -> int:
    deal = by_name(db, name)
    assert deal is not None
    return deal.id


def months_of(report) -> list[tuple[str, str, str, str | None]]:
    return [
        (deal["deal"], month["period"], month["outcome"], month["reason"])
        for deal in report.run.summary["deals"]
        for month in deal["months"]
    ]


def count(db, table, **where) -> int:
    query = select(func.count()).select_from(table)
    for column, value in where.items():
        query = query.where(getattr(table, column) == value)
    return db.scalar(query) or 0


# ---------------------------------------------------------------------------- refreshing the book


def test_a_book_refresh_loads_the_newest_month_of_each_public_deal(book, sources):
    clock.set_now(TODAY)
    report = service.refresh_book("olly", sources=sources)
    run = report.run
    assert (run.kind, run.started_by, run.status) == ("refresh book", "olly", "succeeded")
    assert [(d.deal, d.outcome.value, d.reason) for d in report.deals] == [
        (F1, "refreshed", None),
        (F2, "refreshed", None),
        (HYT, "skipped", "not publicly reported"),
    ]
    assert months_of(report) == [
        (F1, "September 2026", "loaded", None),
        (F2, "September 2026", "loaded", None),
    ]
    summary = run.summary
    assert (summary["deals_refreshed"], summary["deals_skipped"], summary["loan_rows"]) == (
        2,
        1,
        14,
    )
    assert summary["alerts_raised"] is None  # cmbs.alerts is not installed
    assert report.text().splitlines()[:2] == [
        "run 1: olly, 2026-10-28 09:00 to 09:00, 2 deals refreshed, 1 skipped, "
        "14 loan rows loaded, alerts not built",
        "refresh book, succeeded",
    ]
    db = book
    db.expire_all()
    assert count(db, LoanPeriod, deal_id=deal_id(db, F1), period=SEP.first_day) == 7
    assert count(db, DistributionReport, deal_id=deal_id(db, F2), period=SEP.first_day) == 1
    assert count(db, RunClaim) == 0
    recorded = db.scalars(select(RunMonth).order_by(RunMonth.id)).all()
    assert [(m.run_id, m.period, m.outcome, m.loan_rows) for m in recorded] == [
        (run.number, SEP.first_day, "loaded", 7),
        (run.number, SEP.first_day, "loaded", 7),
    ]
    assert (recorded[0].tape_accession, recorded[0].report_accession) == (
        accession(F1, SEP, "ABS-EE"),
        accession(F1, SEP, "10-D"),
    )
    assert recorded[0].alerts is None
    # Every write names the run.
    actions = db.execute(
        select(AuditEntry.action, func.count())
        .where(AuditEntry.run_id == run.number)
        .group_by(AuditEntry.action)
    ).all()
    assert dict(actions) == {
        "start refresh run": 1,
        "discover filings": 3,
        "load loan row": 14,
        "parse distribution report": 2,
    }
    start = db.scalars(select(AuditEntry).where(AuditEntry.action == "start refresh run")).one()
    assert start.detail == {"kind": "refresh book", "deals": [F1, F2, HYT]}


def test_a_second_refresh_finds_nothing_new_then_the_next_month(book, sources, archive):
    clock.set_now(TODAY)
    service.refresh_book("olly", sources=sources)
    again = service.refresh_book("olly", sources=sources)
    assert months_of(again) == []
    assert again.run.summary["loan_rows"] == 0
    assert again.text().splitlines()[2:] == [
        f"{F1}: refreshed",
        f"{F2}: refreshed",
        f"{HYT}: skipped, not publicly reported",
    ]
    assert [d.text() for d in again.deals][:1] == [f"{F1}: refreshed, nothing new to load"]
    clock.set_now(dt.datetime(2026, 10, 30, 9, 0, tzinfo=dt.UTC))
    later = service.refresh_book("olly", sources=sources)
    assert months_of(later) == [
        (F1, "October 2026", "loaded", None),
        (F2, "October 2026", "loaded", None),
    ]


def test_a_book_refresh_needs_a_book_and_the_right(seeded, sources):
    clock.set_now(TODAY)
    with pytest.raises(EmptyBook, match="^no positions file is dated on or before 2026-10-28$"):
        service.refresh_book("olly", sources=sources)
    with pytest.raises(PermissionRefused, match="^pm may not run refreshes and backfills$"):
        service.refresh_book("pat", sources=sources)
    seeded.expire_all()
    assert count(seeded, Run) == 0
    refusal = seeded.scalars(
        select(AuditEntry).where(
            AuditEntry.action == "refresh book", AuditEntry.outcome == "refused"
        )
    ).one()
    assert refusal.reason == "no positions file is dated on or before 2026-10-28"


# ---------------------------------------------------------------------------- one run per deal


def hold(db, name: str) -> tuple[Claims, int]:
    """Run 'olly' holding a deal, as a live run would."""
    deal = get_or_create(db, name, cik=CIKS.get(name))
    db.commit()
    claims = Claims.open()
    assert claims.try_take(deal.id)
    run = runs.start_run(db, kind="refresh deal", started_by="olly")
    record_claims(db, run, [deal.id])
    db.commit()
    return claims, run.id


def test_a_deal_another_run_holds_refuses_a_run_of_that_deal(seeded, sources):
    clock.set_now(TODAY)
    claims, holder = hold(seeded, F1)
    try:
        with pytest.raises(AlreadyRefreshing) as refused:
            service.refresh_deal("quinn", F1, sources=sources)
        assert str(refused.value) == f"run {holder} is already refreshing {F1}"
        with pytest.raises(AlreadyRefreshing):
            service.backfill("quinn", F1, AUG, SEP, sources=sources)
        seeded.expire_all()
        assert [r.id for r in seeded.scalars(select(Run))] == [holder]
        assert count(seeded, LoanTape) == 0
        assert holders(seeded) == {deal_id(seeded, F1): holder}
        refusals = seeded.scalars(
            select(AuditEntry.reason).where(AuditEntry.outcome == "refused")
        ).all()
        assert refusals == [f"run {holder} is already refreshing {F1}"] * 2
    finally:
        claims.close()
    # Once the holder lets go, the deal can be refreshed.
    assert service.refresh_deal("quinn", F1, sources=sources).run.status == "succeeded"


def test_a_book_refresh_skips_a_deal_another_run_holds(book, sources):
    clock.set_now(TODAY)
    claims, holder = hold(book, F2)
    try:
        report = service.refresh_book("olly", sources=sources)
    finally:
        claims.close()
    assert [(d.deal, d.outcome.value, d.reason) for d in report.deals] == [
        (F1, "refreshed", None),
        (F2, "skipped", f"run {holder} is already refreshing {F2}"),
        (HYT, "skipped", "not publicly reported"),
    ]
    book.expire_all()
    assert holders(book) == {deal_id(book, F2): holder}  # the holder's claim is left alone


def test_a_claim_without_its_lock_was_left_by_a_dead_run(seeded, sources):
    clock.set_now(TODAY)
    claims, dead = hold(seeded, F1)
    claims.close()  # the process died: its locks went with its connection; its row stayed
    report = service.refresh_deal("quinn", F1, sources=sources)
    assert report.run.status == "succeeded"
    seeded.expire_all()
    old = seeded.get_one(Run, dead)
    assert old.status == "failed"
    assert old.summary == {"stopped": {"error": ABANDONED}}
    assert count(seeded, RunClaim) == 0


def test_a_claim_left_by_a_run_that_finished_is_taken_over_quietly(seeded, sources):
    clock.set_now(TODAY)
    claims, earlier = hold(seeded, F1)
    run = seeded.get_one(Run, earlier)
    runs.finish_run(seeded, run, status="succeeded", summary={"loan_rows": 0})
    seeded.commit()
    claims.close()  # finished, then died before deleting its claim row
    assert service.refresh_deal("quinn", F1, sources=sources).run.status == "succeeded"
    seeded.expire_all()
    old = seeded.get_one(Run, earlier)
    assert (old.status, old.summary) == ("succeeded", {"loan_rows": 0})


def test_another_runs_claim_message_when_the_row_is_not_written_yet(seeded):
    deal = get_or_create(seeded, F1)
    seeded.commit()
    assert held_message(seeded, deal) == f"another run is already refreshing {F1}"


def test_a_tape_for_another_month_than_planned_is_not_loaded(seeded, archive):
    """A filing registered without a period cannot be told apart by the registry."""
    actor = Actor("quinn", Role.QUANT)
    deal = get_or_create(seeded, F2, cik=CIKS[F2])
    planned = MonthFilings(
        AUG,
        FilingRef(accession(F2, SEP, "ABS-EE"), CIKS[F2], "ABS-EE", dt.date(2026, 9, 29), AUG),
        FilingRef(accession(F2, SEP, "10-D"), CIKS[F2], "10-D", dt.date(2026, 9, 29), AUG),
    )

    def fetched(form: str) -> Fetched:
        url = exhibit_url(F2, SEP, form)
        return Fetched(accession(F2, SEP, form), url.rsplit("/", 1)[1], archive.get_bytes(url))

    with pytest.raises(MonthNotLoaded) as refused:
        service.load_month(
            seeded,
            actor=actor,
            run_id=1,
            deal=deal,
            month=planned,
            tape=fetched("ABS-EE"),
            report=fetched("10-D"),
            on=TODAY.date(),
        )
    assert (refused.value.reason, refused.value.detail) == (
        "ABS-EE reports another month",
        f"{accession(F2, SEP, 'ABS-EE')} reports September 2026",
    )
    assert str(refused.value) == (
        f"ABS-EE reports another month: {accession(F2, SEP, 'ABS-EE')} reports September 2026"
    )
    assert str(MonthNotLoaded("no detail")) == "no detail"


# ---------------------------------------------------------------------------- backfill


def test_a_backfill_loads_its_months_oldest_first(seeded, sources):
    clock.set_now(TODAY)
    report = service.backfill("quinn", F1, Period(2025, 11), Period(2026, 2), sources=sources)
    assert report.run.kind == "backfill"
    assert [m[1] for m in months_of(report)] == [
        "November 2025",
        "December 2025",
        "January 2026",
        "February 2026",
    ]
    seeded.expire_all()
    assert service.loaded_months(seeded, deal_id(seeded, F1)) == {
        Period(2025, 11),
        Period(2025, 12),
        Period(2026, 1),
        Period(2026, 2),
    }
    # Already loaded months are not tried again.
    again = service.backfill("quinn", F1, Period(2026, 1), Period(2026, 3), sources=sources)
    assert months_of(again) == [(F1, "March 2026", "loaded", None)]


def test_a_backfill_past_the_filings_records_the_missing_months(seeded, sources):
    clock.set_now(TODAY)
    report = service.backfill("quinn", F2, Period(2026, 7), AUG, sources=sources)
    assert months_of(report) == [
        (F2, "July 2026", "not loaded", "no ABS-EE or 10-D filed for July 2026"),
        (F2, "August 2026", "loaded", None),
    ]
    assert report.run.status == "completed with problems"
    seeded.expire_all()
    found = month_status(seeded, deal_id=deal_id(seeded, F2), period=Period(2026, 7))
    assert (found.status, found.reason, found.run) == (
        "not loaded",
        "no ABS-EE or 10-D filed for July 2026",
        report.run.number,
    )
    assert month_status(seeded, deal_id=deal_id(seeded, F2), period=AUG).status == "loaded"
    never = month_status(seeded, deal_id=deal_id(seeded, F2), period=OCT)
    assert (never.status, never.reason, never.run) == ("not loaded", None, None)


def test_a_backfill_that_ends_before_it_starts_is_refused(seeded, sources):
    with pytest.raises(BadRange, match="^a backfill from September 2026 to August 2026 ends"):
        service.backfill("quinn", F1, SEP, AUG, sources=sources)
    seeded.expire_all()
    assert count(seeded, Run) == 0
    with pytest.raises(PermissionRefused):
        service.backfill("pat", F1, SEP, AUG, sources=sources)


# ---------------------------------------------------------------------------- months that fail


def failing_backfill(sources, archive, url: str, content: bytes | None):
    if content is None:
        archive.recorded.responses.pop(url)
    else:
        archive.replaced[url] = content
    clock.set_now(TODAY)
    return service.backfill("quinn", F2, AUG, OCT, sources=sources)


@pytest.mark.parametrize(
    ("form", "content", "reason", "detail"),
    [
        (
            "10-D",
            NOT_A_REPORT,
            "10-D failed to parse",
            "the report's certificate administrator is not recognised",
        ),
        (
            "ABS-EE",
            b"not xml",
            "ABS-EE failed to parse",
            "the file is not XML: syntax error: line 1, column 0",
        ),
        ("10-D", None, "10-D could not be fetched", None),
        ("ABS-EE", None, "ABS-EE could not be fetched", None),
    ],
)
def test_a_month_that_fails_stops_its_deal(seeded, sources, archive, form, content, reason, detail):
    report = failing_backfill(sources, archive, exhibit_url(F2, SEP, form), content)
    assert months_of(report) == [
        (F2, "August 2026", "loaded", None),
        (F2, "September 2026", "not loaded", reason),
    ]
    month = report.run.summary["deals"][0]["months"][1]
    if detail is not None:
        assert month["detail"] == detail
    else:
        assert month["detail"].startswith("no recording of https://www.sec.gov/Archives/")
    assert report.run.status == "failed"
    assert report.run.stopped == {
        "deal": F2,
        "period": "September 2026",
        "error": reason,
        "detail": month["detail"],
    }
    seeded.expire_all()
    f2 = deal_id(seeded, F2)
    assert count(seeded, LoanTape, deal_id=f2, period=SEP.first_day) == 0
    assert count(seeded, DistributionReport, deal_id=f2, period=SEP.first_day) == 0
    assert count(seeded, LoanTape, deal_id=f2, period=AUG.first_day) == 1
    assert month_status(seeded, deal_id=f2, period=SEP).reason == reason
    # October was never tried: it waits for September.
    assert count(seeded, RunMonth, period=OCT.first_day) == 0


def test_a_report_for_another_month_is_not_loaded(seeded, sources, archive):
    august = archive.recorded.get_bytes(exhibit_url(F2, AUG, "10-D"))
    report = failing_backfill(sources, archive, exhibit_url(F2, SEP, "10-D"), august)
    assert months_of(report)[1] == (
        F2,
        "September 2026",
        "not loaded",
        "10-D reports another month",
    )
    assert (
        report.run.summary["stopped"]["detail"]
        == "the report is for August 2026, not September 2026"
    )


def test_a_tape_for_another_month_is_refused(seeded, sources, archive):
    august = archive.recorded.get_bytes(exhibit_url(F2, AUG, "ABS-EE"))
    report = failing_backfill(sources, archive, exhibit_url(F2, SEP, "ABS-EE"), august)
    assert months_of(report)[1] == (F2, "September 2026", "not loaded", "ABS-EE was refused")
    assert report.run.summary["stopped"]["detail"] == (
        f"{accession(F2, SEP, 'ABS-EE')} already has period "
        "datetime.date(2026, 9, 1), not datetime.date(2026, 8, 1)"
    )


def test_a_report_the_loader_refuses_is_not_loaded(seeded, sources, archive):
    load_report(
        seeded,
        user="quinn",
        deal=F2,
        accession=accession(F2, SEP, "10-D"),
        cik=CIKS[F2],
        filing_date=dt.date(2026, 9, 29),
        file_name="another-name.htm",
        content=archive.recorded.get_bytes(exhibit_url(F2, SEP, "10-D")),
        period=SEP,
    )
    seeded.commit()
    clock.set_now(TODAY)
    report = service.refresh_deal("quinn", F2, sources=sources)
    assert months_of(report) == [(F2, "September 2026", "not loaded", "10-D was refused")]
    assert report.run.summary["stopped"]["detail"] == (
        f"{accession(F2, SEP, '10-D')} was already parsed from another-name.htm, "
        "not fixture-trust-2026-f2_ex991-202609.htm"
    )
    seeded.expire_all()
    assert count(seeded, LoanTape, deal_id=deal_id(seeded, F2)) == 0  # the tape rolled back


def test_a_failing_deal_fails_the_run_but_the_others_are_refreshed(book, sources, archive):
    archive.replaced[exhibit_url(F1, SEP, "10-D")] = NOT_A_REPORT
    clock.set_now(TODAY)
    report = service.refresh_book("olly", sources=sources)
    assert [(d.deal, d.outcome.value) for d in report.deals] == [
        (F1, "failed"),
        (F2, "refreshed"),
        (HYT, "skipped"),
    ]
    assert report.run.status == "failed"
    assert report.run.summary["loan_rows"] == 7
    assert (
        report.text()
        .splitlines()[0]
        .endswith("1 deals refreshed, 1 failed, 1 skipped, 7 loan rows loaded, alerts not built")
    )


def test_a_deal_discovery_cannot_find_fails_without_a_month(seeded, sources):
    clock.set_now(TODAY)
    report = service.refresh_deal("quinn", "Unrecorded Trust 2026-X", sources=sources)
    assert report.run.status == "failed"
    deal = report.deals[0]
    assert (deal.deal, deal.outcome.value) == ("Unrecorded Trust 2026-X", "failed")
    assert deal.reason is not None
    assert deal.reason.startswith("filing discovery failed: no recording of https://efts.sec.gov/")
    assert report.run.stopped == {
        "deal": "Unrecorded Trust 2026-X",
        "period": None,
        "error": deal.reason,
        "detail": None,
    }


def test_a_deal_whose_discovery_is_refused_fails(seeded, sources):
    # EDGAR says another registered name is the same trust: the registry refuses to merge them.
    get_or_create(seeded, F1, cik=CIKS[F1])
    copy = "Fixture Trust 2026-F1 Copy"
    get_or_create(seeded, copy)
    seeded.commit()

    class Answers:
        def get_json(self, url: str):
            if "keysTyped=Fixture+Trust+2026-F1+Copy" in url:
                hit = {"_id": str(CIKS[F1]), "_source": {"entity": copy}}
                return {"hits": {"hits": [hit], "total": {"value": 1}}}
            return sources.edgar.get_json(url)

    clock.set_now(TODAY)
    report = service.refresh_deal("quinn", copy, sources=Sources(Answers(), sources.archive))
    assert report.deals[0].reason == (
        f'filing discovery failed: "{copy}" is registered apart from "{F1}", which already has '
        f"CIK {CIKS[F1]}; they are the same trust"
    )
    assert report.run.status == "failed"


def test_an_unexpected_error_finishes_the_run_as_failed_and_is_raised(book, sources, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("the database went away")

    working = service.load_month
    monkeypatch.setattr(service, "load_month", broken)
    clock.set_now(TODAY)
    with pytest.raises(RuntimeError, match="the database went away"):
        service.refresh_book("olly", sources=sources)
    book.expire_all()
    run = book.scalars(select(Run)).one()
    assert run.status == "failed"
    assert run.ended_at is not None
    assert run.summary["stopped"] == {
        "deal": F1,
        "period": "September 2026",
        "error": "RuntimeError: the database went away",
        "detail": None,
    }
    assert count(book, RunClaim) == 0
    # The claims were let go: the next run takes the deals and loads them.
    monkeypatch.setattr(service, "load_month", working)
    clock.set_now(TODAY)
    assert service.refresh_book("olly", sources=sources).run.status == "succeeded"


# ---------------------------------------------------------------------------- alerts


def test_alerts_are_built_once_the_alerts_feature_exists(seeded, monkeypatch):
    actor = Actor("quinn", Role.QUANT)
    assert build_alerts(seeded, deal_id=1, period=SEP, actor=actor, run_id=1) is None
    calls = []
    fake = types.ModuleType("cmbs.alerts.service")

    def build(db, *, deal_id, period, actor, run_id):
        calls.append((deal_id, period, actor, run_id))
        return types.SimpleNamespace(raised=3)

    fake.build_alerts = build  # pyright: ignore[reportAttributeAccessIssue]
    monkeypatch.setitem(sys.modules, "cmbs.alerts.service", fake)
    assert build_alerts(seeded, deal_id=4, period=SEP, actor=actor, run_id=9) == 3
    assert calls == [(4, SEP, actor, 9)]


def test_a_broken_alerts_feature_is_not_mistaken_for_a_missing_one(seeded, monkeypatch):
    def missing_dependency(name):
        raise ModuleNotFoundError("No module named 'somewhere'", name="somewhere")

    monkeypatch.setattr(service.importlib, "import_module", missing_dependency)
    with pytest.raises(ModuleNotFoundError):
        build_alerts(seeded, deal_id=1, period=SEP, actor=Actor("quinn", Role.QUANT), run_id=1)


def test_loaded_months_get_their_alert_counts(book, sources, monkeypatch):
    fake = types.ModuleType("cmbs.alerts.service")
    fake.build_alerts = lambda db, **kw: types.SimpleNamespace(raised=2)  # pyright: ignore[reportAttributeAccessIssue]
    monkeypatch.setitem(sys.modules, "cmbs.alerts.service", fake)
    clock.set_now(TODAY)
    report = service.refresh_book("olly", sources=sources)
    assert report.run.summary["alerts_raised"] == 4
    assert report.run.headline().endswith("14 loan rows loaded, 4 alerts raised")
    book.expire_all()
    assert [m.alerts for m in book.scalars(select(RunMonth))] == [2, 2]


def test_a_month_whose_alerts_are_refused_is_not_loaded(seeded, sources, monkeypatch):
    class NoEarlierMonth(Refused):
        pass

    def build(db, **kw):
        raise NoEarlierMonth("no month before September 2026 to compare with")

    fake = types.ModuleType("cmbs.alerts.service")
    fake.build_alerts = build  # pyright: ignore[reportAttributeAccessIssue]
    monkeypatch.setitem(sys.modules, "cmbs.alerts.service", fake)
    clock.set_now(TODAY)
    report = service.refresh_deal("quinn", F2, sources=sources)
    assert months_of(report) == [(F2, "September 2026", "not loaded", "alerts could not be built")]
    assert report.run.summary["stopped"]["detail"] == (
        "no month before September 2026 to compare with"
    )
    seeded.expire_all()
    assert count(seeded, LoanTape) == 0  # the month rolled back with its alerts


# ---------------------------------------------------------------------------- tracing


def test_a_loaded_value_traces_to_its_run(book, sources):
    clock.set_now(dt.datetime(2026, 10, 27, 9, 0, tzinfo=dt.UTC))
    run = service.refresh_book("olly", sources=sources).run
    with engine.session() as db:
        found = runs.trace(db, "pat", deal=F1, loan="7", period=SEP)
    assert (found.run, found.loaded_by, found.balance) == (run.number, "olly", money("9000000"))
    assert found.text() == (
        f"loan 7 of {F1}, September 2026 balance $9,000,000.00, from "
        f"{accession(F1, SEP, 'ABS-EE')} filed 2026-09-29; loaded by run {run.number} on "
        "2026-10-27"
    )
    with engine.session() as db:
        with pytest.raises(
            NotLoaded, match=f"^no August 2026 values of loan 7 of {F1} by 2026-10-27$"
        ):
            runs.trace(db, "pat", deal=F1, loan="7", period=AUG)
        with pytest.raises(UnknownDeal, match="^no deal named 'Nobody'$"):
            runs.trace(db, "pat", deal="Nobody", loan="7", period=SEP)
        with pytest.raises(PermissionRefused, match="^operator may not read the panel$"):
            runs.trace(db, "olly", deal=F1, loan="7", period=SEP)


def test_a_value_loaded_outside_a_run_says_so(seeded):
    content = (FIXTURES / "schedule_al_minimal.xml").read_bytes()
    common = {"deal": F1, "content": content, "cik": CIKS[F1], "file_name": "tape.xml"}
    clock.set_now(dt.datetime(2026, 10, 2, 9, 0, tzinfo=dt.UTC))
    load_tape(
        seeded,
        accession="0009900001-26-000101",
        filing_date=dt.date(2026, 9, 29),
        actor=Actor("quinn", Role.QUANT),
        **common,
    )
    seeded.commit()
    found = runs.trace(seeded, "pat", deal=F1, loan="1", period=SEP)
    assert found.text() == (
        f"loan 1 of {F1}, September 2026 balance $50,000,000.00, from 0009900001-26-000101 "
        "filed 2026-09-29; loaded by quinn on 2026-10-02, outside a run"
    )
    assert (found.run, found.loaded_by) == (None, "quinn")
    assert found.loaded_at == dt.datetime(2026, 10, 2, 9, 0, tzinfo=dt.UTC)
    # Asked as of a day before the filing, there is nothing to trace.
    with pytest.raises(NotLoaded):
        runs.trace(seeded, "pat", deal=F1, loan="1", period=SEP, as_of=dt.date(2026, 9, 28))
    # A newer tape loaded without an actor has no audit entry to trace.
    load_tape(seeded, accession="0009900001-26-000102", filing_date=dt.date(2026, 9, 30), **common)
    seeded.commit()
    unaudited = runs.trace(seeded, "pat", deal=F1, loan="1", period=SEP)
    assert unaudited.accession == "0009900001-26-000102"
    assert unaudited.text() == (
        f"loan 1 of {F1}, September 2026 balance $50,000,000.00, from 0009900001-26-000102 "
        "filed 2026-09-30; its load is not in the audit trail"
    )
