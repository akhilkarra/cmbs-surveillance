"""The dashboard's pages read from a database: the book view, a loan, and their routes.

The world: Fixture Trust 2026-F1's August and September 2026 tapes (September loaded by a
run, transferring loan 7), its September 10-D from tests/fixtures/distribution/ with loan 2's
comment extracted from the synthetic recording, and a book holding $25,000,000.00 of the deal.
"""

import datetime as dt
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from cmbs import clock
from cmbs.access.roles import Role
from cmbs.access.service import Actor, PermissionRefused, UnknownUser
from cmbs.alerts import service as alerts
from cmbs.api.app import create_app
from cmbs.book.service import PositionLine, load_positions
from cmbs.checks.service import raise_flag
from cmbs.dashboard import service
from cmbs.dashboard.service import Check, LoanRef, MonthRef
from cmbs.distribution.service import load_report
from cmbs.domain import money
from cmbs.extraction.service import decide_review, extract_comments
from cmbs.extraction.tables import ReviewItem
from cmbs.model.client import ModelClient
from cmbs.model.recordings import DbRecordings
from cmbs.panel.service import UnknownDeal
from cmbs.review.service import decline, override
from cmbs.runs.service import NotLoaded, start_run
from cmbs.tape.service import load_tape
from cmbs.testing.world import FIXTURE_CIK
from tests.conftest import FIXTURES, needs_db
from tests.fixtures.model.synthetic import LOAN_2_SEPTEMBER, loan_2_september_comment, recording
from tests.fixtures.model.synthetic import load as load_recordings
from tests.test_alert_service import (
    AUGUST,
    F1,
    SEPTEMBER,
    TRANSFER,
    deal_id,
    load_month,
    month_tape,
)

REPORT = FIXTURES / "distribution" / "fixture-trust-2026-f1_10d_2026-09.htm"
REPORT_ACCESSION = "0009900001-26-000009"
TAPE_ACCESSION = "0009900001-26-000901"
BUILT = dt.datetime(2026, 9, 30, 12, tzinfo=dt.UTC)
READ = dt.datetime(2026, 10, 1, 12, tzinfo=dt.UTC)
HEADERS = {"X-CMBS-User": "pat"}
PASSED = "passed"


def build_world(db: Session, *, unsettled_workout: bool = False) -> dict[str, int]:
    """The module's world; `unsettled_workout` extracts loan 2 with its workout strategy answer
    inside the abstention band, so that field waits for review."""
    load_month(db, F1, AUGUST)
    clock.set_now(dt.datetime(2026, 9, 29, 18, tzinfo=dt.UTC))
    run = start_run(db, kind="refresh deal", started_by="olly")
    load_tape(
        db,
        deal=F1,
        content=month_tape(SEPTEMBER, {("7", TRANSFER): "09-02-2026"}),
        file_name="2026-09.xml",
        accession=TAPE_ACCESSION,
        filing_date=dt.date(2026, 9, 29),
        cik=FIXTURE_CIK,
        actor=Actor("olly", Role.OPERATOR),
        run_id=run.id,
    )
    load_report(
        db,
        user="quinn",
        deal=F1,
        accession=REPORT_ACCESSION,
        cik=FIXTURE_CIK,
        filing_date=dt.date(2026, 9, 29),
        file_name=REPORT.name,
        content=REPORT.read_bytes(),
        period=SEPTEMBER,
    )
    load_positions(
        db,
        user="quinn",
        as_of=dt.date(2026, 9, 30),
        lines=[
            PositionLine(F1, "E", money("20,000,000.00")),
            PositionLine(F1, "F", money("5,000,000.00")),
        ],
    )
    clock.set_now(BUILT)
    alerts.build_alerts(db, deal_id=deal_id(db, F1), period=SEPTEMBER)
    if unsettled_workout:
        answers = {**LOAN_2_SEPTEMBER, "workout_strategy": ("modification", 0.55, 0.92, 4)}
        DbRecordings(db).put(recording(loan_2_september_comment(), answers))
    else:
        load_recordings(DbRecordings(db))
    client = ModelClient(DbRecordings(db), provider=None, replay_only=True, allow_synthetic=True)
    extract_comments(db, user="quinn", deal=F1, period=SEPTEMBER, loan="2", client=client)
    db.commit()
    clock.set_now(READ)
    return {"run": run.id}


@pytest.fixture
def world(seeded: Session) -> dict[str, int]:
    return build_world(seeded)


@pytest.fixture
def reviewing(seeded: Session) -> int:
    """The world with loan 2's workout strategy waiting for review; the item's id."""
    build_world(seeded, unsettled_workout=True)
    return seeded.scalars(select(ReviewItem.id).where(ReviewItem.field == "workout_strategy")).one()


# ---------------------------------------------------------------------------- the book


@needs_db
def test_the_book_page_lists_the_months_open_alerts(world, seeded):
    page = service.book_page(seeded, reader="pat", period=SEPTEMBER)
    assert page.month == MonthRef("2026-09", "September 2026")
    assert (page.previous, page.next) == (
        MonthRef("2026-08", "August 2026"),
        MonthRef("2026-10", "October 2026"),
    )
    assert (page.as_of, page.note) == (dt.date(2026, 10, 1), None)
    [row] = page.rows
    assert (row.severity, row.message, row.loans) == (
        "high",
        "transferred to special servicing on 2026-09-02",
        (LoanRef(F1, "7", "7"),),
    )
    assert (row.exposure, row.balance, row.classes) == (
        Decimal("25000000.00"),
        Decimal("9000000.00"),
        ("E", "F"),
    )
    assert (row.state, row.state_text) == ("new", "new")


@needs_db
def test_the_book_page_defaults_to_the_latest_month_built(world, seeded):
    assert service.book_page(seeded, reader="pat").month.key == "2026-09"


@needs_db
def test_a_month_with_no_open_alerts_says_so(world, seeded):
    [row] = service.book_page(seeded, reader="pat", period=SEPTEMBER).rows
    alerts.close(seeded, user="alex", alert_id=row.alert_id, note="known")
    page = service.book_page(seeded, reader="pat", period=SEPTEMBER)
    assert (page.rows, page.note) == ((), "no open alerts on the book for September 2026")


@needs_db
def test_with_nothing_built_the_book_page_shows_last_month(seeded):
    clock.set_now(READ)
    page = service.book_page(seeded, reader="pat")
    assert (page.month.key, page.rows) == ("2026-09", ())


@needs_db
def test_the_book_page_needs_a_known_reader(world, seeded):
    with pytest.raises(UnknownUser):
        service.book_page(seeded, reader="nobody", period=SEPTEMBER)


# ---------------------------------------------------------------------------- a loan


@needs_db
def test_the_loan_page_shows_the_tape_with_its_checks_and_run(world, seeded):
    page = service.loan_page(seeded, reader="pat", deal=F1, loan="2")
    assert (page.deal, page.loan, page.month, page.as_of) == (
        F1,
        "2",
        MonthRef("2026-09", "September 2026"),
        dt.date(2026, 10, 1),
    )
    assert (page.property_name, page.property_type) == ("Lakeside Hotel Portfolio", "LO")
    tape = page.tape
    assert (tape.source.form, tape.source.accession, tape.source.filing_date) == (
        "ABS-EE",
        TAPE_ACCESSION,
        dt.date(2026, 9, 29),
    )
    assert tape.source.url.startswith(
        "https://www.sec.gov/Archives/edgar/data/9900001/000990000126000901/"
    )
    assert (tape.loaded.run, tape.loaded.by, tape.loaded.text) == (
        world["run"],
        "olly",
        f"run {world['run']}",
    )
    balance = tape.values[0]
    assert (balance.field, balance.value) == ("ending_balance", "30000000.00")
    assert balance.checks == (
        Check("contract passed", PASSED),
        Check(
            "scheduled balance roll-forward not checked: "
            "beginning balance and scheduled ending balance not reported",
            "not checked",
        ),
    )
    financials = page.financials
    assert (financials.end, financials.days_at_period_end) == (dt.date(2024, 12, 31), 619)


@needs_db
def test_the_loan_page_shows_each_extracted_value_with_its_sentence(world, seeded):
    comment = service.loan_page(seeded, reader="pat", deal=F1, loan="2").comment
    assert comment is not None
    assert (comment.source.form, comment.source.accession) == ("10-D", REPORT_ACCESSION)
    assert comment.source.url.startswith(
        "https://www.sec.gov/Archives/edgar/data/9900001/000990000126000009/"
    )
    assert (comment.loaded.by, comment.loaded.text) == ("quinn", "extracted by quinn outside a run")
    assert (comment.model, comment.prompt_version, comment.settings_version) == ("jev-1.14.0", 3, 7)
    assert comment.sentences[2] == "The borrower has requested a 12-month maturity extension."
    values = {value.field: value for value in comment.values}
    request = values["borrower_request"]
    assert (request.value, request.sentence, request.evidence) == (
        "maturity extension",
        3,
        "The borrower has requested a 12-month maturity extension.",
    )
    assert request.checks == (Check("auto-accepted", PASSED), Check("evidence checked", PASSED))
    workout = values["workout_strategy"]
    assert (workout.status, workout.value, workout.checks) == ("not stated", None, ())


@needs_db
def test_a_loan_without_an_extracted_comment_says_so_and_lists_its_alerts(world, seeded):
    page = service.loan_page(seeded, reader="pat", deal=F1, loan="7")
    assert page.comment is None
    assert page.comment_note == "no servicer comment extracted for September 2026"
    assert [(a.severity, a.message, a.state_text) for a in page.alerts] == [
        ("high", "transferred to special servicing on 2026-09-02", "new")
    ]


@needs_db
def test_a_month_loaded_outside_the_audit_trail_says_so(world, seeded):
    page = service.loan_page(seeded, reader="pat", deal=F1, loan="2", period=AUGUST)
    assert page.month.key == "2026-08"
    assert page.tape.loaded.text == "load not in the audit trail"
    assert (page.comment, page.alerts) == (None, ())


@needs_db
def test_the_loan_page_is_point_in_time(world, seeded):
    before = service.loan_page(seeded, reader="pat", deal=F1, loan="2", as_of=dt.date(2026, 9, 28))
    assert before.month.key == "2026-08"
    with pytest.raises(NotLoaded) as refused:
        service.loan_page(
            seeded, reader="pat", deal=F1, loan="2", period=SEPTEMBER, as_of=dt.date(2026, 9, 28)
        )
    assert str(refused.value) == (
        "no September 2026 values of loan 2 of Fixture Trust 2026-F1 by 2026-09-28"
    )


@needs_db
def test_check_flags_show_for_the_month_once_raised(world, seeded):
    deal = deal_id(seeded, F1)
    clock.set_now(dt.datetime(2026, 9, 30, 8, tzinfo=dt.UTC))
    raise_flag(
        seeded,
        deal_id=deal,
        loan="2",
        check="reconciliation",
        message="paid-through differs",
        period=SEPTEMBER.first_day,
    )
    raise_flag(
        seeded,
        deal_id=deal,
        loan="2",
        check="reconciliation",
        message="August only",
        period=AUGUST.first_day,
    )
    raise_flag(
        seeded,
        deal_id=deal,
        loan="5",
        check="reconciliation",
        message="another loan",
        period=SEPTEMBER.first_day,
    )
    clock.set_now(dt.datetime(2026, 10, 2, 8, tzinfo=dt.UTC))
    raise_flag(
        seeded,
        deal_id=deal,
        loan="2",
        check="text vs tape",
        message="later",
        period=SEPTEMBER.first_day,
    )
    seeded.commit()
    page = service.loan_page(seeded, reader="pat", deal=F1, loan="2", as_of=dt.date(2026, 10, 1))
    assert [(flag.check, flag.message) for flag in page.flags] == [
        ("reconciliation", "paid-through differs")
    ]
    later = service.loan_page(seeded, reader="pat", deal=F1, loan="2", as_of=dt.date(2026, 10, 2))
    assert [flag.message for flag in later.flags] == ["paid-through differs", "later"]


@needs_db
def test_the_loan_page_refuses_unknown_deals_loans_and_readers(world, seeded):
    with pytest.raises(UnknownDeal) as unknown:
        service.loan_page(seeded, reader="pat", deal="No Such Trust", loan="2")
    assert str(unknown.value) == "no deal named 'No Such Trust'"
    with pytest.raises(NotLoaded) as missing:
        service.loan_page(seeded, reader="pat", deal=F1, loan="99")
    assert (
        str(missing.value)
        == "no September 2026 values of loan 99 of Fixture Trust 2026-F1 by 2026-10-01"
    )
    with pytest.raises(UnknownUser):
        service.loan_page(seeded, reader="nobody", deal=F1, loan="2")


@needs_db
def test_a_user_without_the_panel_is_refused(world, seeded):
    from cmbs.access.service import ensure_user

    ensure_user(seeded, "nemo", set())
    seeded.commit()
    with pytest.raises(PermissionRefused):
        service.loan_page(seeded, reader="nemo", deal=F1, loan="2")


# ---------------------------------------------------------------------------- the routes


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


@needs_db
def test_the_book_route_sends_money_as_text(world, client):
    response = client.get("/dashboard/book?month=2026-09", headers=HEADERS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["month"] == {"key": "2026-09", "label": "September 2026"}
    [row] = body["rows"]
    assert (row["exposure"], row["balance"], row["severity"]) == (
        "25000000.00",
        "9000000.00",
        "high",
    )
    assert row["loans"] == [{"deal": F1, "loan": "7", "label": "7"}]
    assert client.get("/dashboard/book", headers=HEADERS).json()["month"]["key"] == "2026-09"


@needs_db
def test_the_loan_route_reads_a_month_and_a_day(world, client):
    response = client.get(
        "/dashboard/loan",
        params={"deal": F1, "loan": "2", "month": "2026-09", "as_of": "2026-10-01"},
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tape"]["values"][0] == {
        "field": "ending_balance",
        "label": "ending balance",
        "kind": "money",
        "value": "30000000.00",
        "checks": [
            {"label": "contract passed", "outcome": "passed"},
            {
                "label": "scheduled balance roll-forward not checked: "
                "beginning balance and scheduled ending balance not reported",
                "outcome": "not checked",
            },
        ],
    }
    assert body["financials"]["days_at_period_end"] == 619
    default = client.get("/dashboard/loan", params={"deal": F1, "loan": "2"}, headers=HEADERS)
    assert default.json()["month"]["key"] == "2026-09"


@needs_db
def test_the_routes_refuse_bad_months_unknown_loans_and_missing_users(world, client):
    bad = client.get("/dashboard/book?month=2026-13", headers=HEADERS)
    assert (bad.status_code, bad.json()) == (422, {"detail": "not a month like 2026-09: '2026-13'"})
    bad_loan_month = client.get(
        "/dashboard/loan", params={"deal": F1, "loan": "2", "month": "Sept"}, headers=HEADERS
    )
    assert bad_loan_month.status_code == 422
    missing = client.get("/dashboard/loan", params={"deal": F1, "loan": "99"}, headers=HEADERS)
    assert missing.status_code == 422
    assert missing.json()["detail"].startswith("no September 2026 values of loan 99")
    assert client.get("/dashboard/book").status_code == 401


# ---------------------------------------------------------------------------- review's review


def comment_values(db: Session, **kw) -> dict[str, service.CommentValue]:
    page = service.loan_page(db, reader="pat", deal=F1, loan="2", **kw)
    assert page.comment is not None
    return {value.field: value for value in page.comment.values}


@needs_db
def test_an_overridden_value_shows_who_when_why_and_the_models_value(world, seeded):
    clock.set_now(dt.datetime(2026, 10, 2, 9, 30, tzinfo=dt.UTC))
    override(
        seeded,
        "alex",
        deal=F1,
        loan="2",
        period=SEPTEMBER,
        field="borrower_request",
        value="loan modification",
        sentence=3,
        reason="the borrower asked for new terms, not only time",
    )
    seeded.commit()
    shown = comment_values(seeded, as_of=dt.date(2026, 10, 2))["borrower_request"]
    assert (shown.status, shown.value, shown.model_value) == (
        "overridden",
        "loan modification",
        "maturity extension",
    )
    assert (shown.overridden_by, shown.override_reason) == (
        "alex",
        "the borrower asked for new terms, not only time",
    )
    assert shown.overridden_at == dt.datetime(2026, 10, 2, 9, 30, tzinfo=dt.UTC)
    assert shown.decision == (
        "overridden by alex on 2026-10-02: the borrower asked for new terms, not only time; "
        'the model read "maturity extension"'
    )
    assert shown.checks == (Check("overridden", "decided"), Check("evidence checked", PASSED))
    before = comment_values(seeded, as_of=dt.date(2026, 10, 1))["borrower_request"]
    assert (before.status, before.decision) == ("auto-accepted", None)


@needs_db
def test_a_human_reviewed_value_shows_who_decided_it_and_when(reviewing, seeded):
    clock.set_now(dt.datetime(2026, 10, 2, 9, 30, tzinfo=dt.UTC))
    decide_review(seeded, "alex", reviewing, value="modification", sentence=4)
    seeded.commit()
    shown = comment_values(seeded, as_of=dt.date(2026, 10, 2))["workout_strategy"]
    assert (shown.status, shown.value, shown.decided_by) == (
        "human-reviewed",
        "modification",
        "alex",
    )
    assert shown.decision == "human-reviewed by alex on 2026-10-02"
    assert shown.checks == (
        Check("human-reviewed", "decided"),
        Check("evidence checked", PASSED),
    )


@needs_db
def test_a_declined_field_shows_not_determinable_with_who_and_why(reviewing, seeded):
    clock.set_now(dt.datetime(2026, 10, 2, 9, 30, tzinfo=dt.UTC))
    decline(seeded, "alex", reviewing, reason="the comment names no strategy yet")
    seeded.commit()
    shown = comment_values(seeded, as_of=dt.date(2026, 10, 2))["workout_strategy"]
    assert (shown.status, shown.value, shown.checks) == ("not determinable", None, ())
    assert (shown.decided_by, shown.declined_reason) == (
        "alex",
        "the comment names no strategy yet",
    )
    assert shown.decision == (
        "declined as not determinable by alex on 2026-10-02: the comment names no strategy yet"
    )
    waiting = comment_values(seeded, as_of=dt.date(2026, 10, 1))["workout_strategy"]
    assert (waiting.status, waiting.decision, waiting.declined_reason) == (
        "needs review",
        None,
        None,
    )


# ---------------------------------------------------------------------------- review fixes


@needs_db
def test_the_loans_alerts_stand_as_they_were_on_the_day(world, seeded):
    page = service.loan_page(seeded, reader="pat", deal=F1, loan="7")
    [alert] = page.alerts
    clock.set_now(dt.datetime(2026, 10, 2, 9, tzinfo=dt.UTC))
    alerts.acknowledge(seeded, user="pat", alert_id=alert.alert_id)
    clock.set_now(dt.datetime(2026, 10, 3, 9, tzinfo=dt.UTC))
    alerts.close(seeded, user="alex", alert_id=alert.alert_id, note="known")
    seeded.commit()

    def state(day: dt.date) -> list[str]:
        found = service.loan_page(seeded, reader="pat", deal=F1, loan="7", as_of=day).alerts
        return [shown.state_text for shown in found]

    assert state(dt.date(2026, 10, 1)) == ["new"]
    assert state(dt.date(2026, 10, 2)) == ["acknowledged by pat on 2026-10-02"]
    assert state(dt.date(2026, 10, 3)) == ["closed by alex"]
    # Built on 2026-09-30: the day before, the alert did not exist.
    assert state(dt.date(2026, 9, 29)) == []


@needs_db
def test_a_superseded_alert_leaves_the_loan_from_the_day_it_was_superseded(world, seeded):
    [alert] = service.loan_page(seeded, reader="pat", deal=F1, loan="7").alerts
    clock.set_now(dt.datetime(2026, 10, 5, 9, tzinfo=dt.UTC))
    load_tape(
        seeded,
        deal=F1,
        content=month_tape(SEPTEMBER),
        file_name="2026-09-amended.xml",
        accession="0009900001-26-000903",
        filing_date=dt.date(2026, 10, 5),
        cik=FIXTURE_CIK,
        form="ABS-EE/A",
        amends=TAPE_ACCESSION,
    )
    alerts.build_alerts(seeded, deal_id=deal_id(seeded, F1), period=SEPTEMBER)
    seeded.commit()
    assert [
        a.alert_id
        for a in service.loan_page(
            seeded, reader="pat", deal=F1, loan="7", period=SEPTEMBER, as_of=dt.date(2026, 10, 4)
        ).alerts
    ] == [alert.alert_id]
    assert (
        service.loan_page(
            seeded, reader="pat", deal=F1, loan="7", period=SEPTEMBER, as_of=dt.date(2026, 10, 5)
        ).alerts
        == ()
    )


@needs_db
def test_a_loan_number_is_read_as_stored(world, seeded):
    page = service.loan_page(seeded, reader="pat", deal=F1, loan="02")
    assert page.loan == "2"
    assert page.comment is not None
    assert page.tape.values[0].value == "30000000.00"


@needs_db
def test_flags_carry_their_ids(world, seeded):
    flag = raise_flag(
        seeded,
        deal_id=deal_id(seeded, F1),
        loan="2",
        check="reconciliation",
        message="paid-through differs",
        period=SEPTEMBER.first_day,
    )
    seeded.commit()
    [shown] = service.loan_page(seeded, reader="pat", deal=F1, loan="2").flags
    assert (shown.id, shown.check) == (flag.id, "reconciliation")


@needs_db
def test_a_month_not_built_is_told_apart_from_an_empty_book(world, seeded):
    page = service.book_page(seeded, reader="pat", period=AUGUST)
    assert (page.rows, page.note) == ((), "the August 2026 alerts have not been built")


ROLL = {
    "reportPeriodBeginningScheduleLoanBalanceAmount": "30000000.00",
    "reportPeriodEndScheduledLoanBalanceAmount": "30000000.00",
    "scheduledPrincipalAmount": "0.00",
    "unscheduledPrincipalCollectedAmount": "0.00",
    "otherPrincipalAdjustmentAmount": "0.00",
}


def rolled_months(db: Session, september_ending: str) -> None:
    load_month(db, F1, AUGUST, {("2", tag): value for tag, value in ROLL.items()})
    september = {**ROLL, "reportPeriodEndScheduledLoanBalanceAmount": september_ending}
    load_month(db, F1, SEPTEMBER, {("2", tag): value for tag, value in september.items()})
    db.commit()
    clock.set_now(READ)


@needs_db
@pytest.mark.parametrize(
    ("ending", "check"),
    [
        ("30000000.00", Check("scheduled balance roll-forward passed", PASSED)),
        ("29999999.00", Check("scheduled balance roll-forward broken", "failed")),
    ],
)
def test_the_balances_show_the_scheduled_balance_roll_forward(seeded, ending, check):
    rolled_months(seeded, ending)
    values = service.loan_page(seeded, reader="pat", deal=F1, loan="2").tape.values
    by_field = {value.field: value for value in values}
    for name in ("ending_balance", "beginning_balance"):
        assert by_field[name].checks == (Check("contract passed", PASSED), check)
    assert by_field["payment_status"].checks == (Check("contract passed", PASSED),)


@needs_db
def test_the_roll_forward_is_read_point_in_time(seeded):
    rolled_months(seeded, "30000000.00")
    page = service.loan_page(
        seeded, reader="pat", deal=F1, loan="2", period=AUGUST, as_of=dt.date(2026, 9, 1)
    )
    # August alone: its own roll ties, and no July tape is needed for a pass.
    assert page.tape.values[0].checks[1] == Check("scheduled balance roll-forward passed", PASSED)
