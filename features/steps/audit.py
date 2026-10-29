"""Steps for features/governance/audit_log.feature: a review decision's entry, and an automated
change naming its run. The log's reads and its append-only refusal are in governance.py.

Loan 2's borrower request waits for review after its September 2026 comment is extracted on
2026-10-01 (`tests/test_review_world.py`); the reviewer decides it on 2026-10-02. The refresh
loads the fixture deal's September 2026 loan tape (tests/fixtures/schedule_al_minimal.xml).
"""

import datetime as dt

from behave import given, then, use_step_matcher, when  # pyright: ignore[reportAttributeAccessIssue]
from sqlalchemy import select

from cmbs import clock
from cmbs.access.roles import Action
from cmbs.access.service import authorize
from cmbs.audit.service import LOAN, loan_history, loan_key, read_log
from cmbs.domain import Period
from cmbs.extraction import fields
from cmbs.extraction.service import comment_view, decide_review, open_review_items
from cmbs.runs.service import finish_run, start_run
from cmbs.tape.service import LOAD_ACTION, load_tape
from cmbs.tape.tables import LoanPeriod
from cmbs.testing.bdd import attempt, in_session
from cmbs.testing.world import FIXTURE_CIK, FIXTURE_DEAL, set_next_run_number
from tests.conftest import FIXTURES
from tests.test_review_world import SEPTEMBER, loan_two

use_step_matcher("re")

DECIDED_AT = dt.datetime(2026, 10, 2, 9, 30, tzinfo=dt.UTC)
TAPE = FIXTURES / "schedule_al_minimal.xml"
TAPE_ACCESSION = "0009900001-26-000042"
TAPE_FILED = dt.date(2026, 9, 29)
RISK = "rae"


@given(r'loan "(?P<loan>[^"]+)"\'s (?P<label>[a-z -]+?) was empty and waiting for review')
def step_waiting_for_review(context, loan, label):
    assert (loan, label) == ("2", "borrower request"), "the world scripts loan 2's request"
    in_session(lambda db: loan_two(db, answer="forbearance", answer_p=0.55))
    context.deal = FIXTURE_DEAL
    spec = fields.by_label(label)
    view = in_session(
        lambda db: comment_view(db, "pat", deal=FIXTURE_DEAL, loan=loan, period=SEPTEMBER)
    )
    assert view is not None
    shown = view.fields[spec.name]
    assert (shown.status, shown.value) == ("needs review", None), shown
    [item] = [
        item
        for item in in_session(lambda db: open_review_items(db, "pat"))
        if (item.loan, item.field) == (loan, spec.name)
    ]
    context.item_id = item.id


@when(
    r'"(?P<user>[^"]+)" decides the (?P<label>[a-z -]+?) is "(?P<value>[^"]+)", citing '
    r"sentence (?P<sentence>\d+)"
)
def step_decides_value(context, user, label, value, sentence):
    clock.set_now(DECIDED_AT)
    assert fields.by_label(label).name == "borrower_request"
    attempt(
        context,
        lambda: in_session(
            lambda db: decide_review(db, user, context.item_id, value=value, sentence=int(sentence))
        ),
    )
    assert context.refusal is None, f"refused: {context.refusal}"


@then(
    r'the audit log has an entry for loan "(?P<loan>[^"]+)": user "(?P<user>[^"]+)", field '
    r'"(?P<field>[^"]+)", before "(?P<before>[^"]*)", after "(?P<after>[^"]*)", reason '
    r'"(?P<reason>[^"]*)"'
)
def step_audit_entry_for_loan(context, loan, user, field, before, after, reason):
    history = in_session(lambda db: loan_history(db, RISK, deal=context.deal, loan=loan))
    changes = [entry for entry in history if entry.field is not None]
    found = [(e.user_name, e.field, e.before, e.after, e.reason) for e in changes]
    assert found == [(user, field, before, after, reason)], found
    [entry] = changes
    assert (entry.role, entry.at, entry.outcome) == ("analyst", DECIDED_AT, "done"), entry


@when(
    r'refresh run (?P<number>\d+), started by "(?P<user>[^"]+)", loads (?P<month>\w+ \d{4}) '
    r'for "(?P<deal>[^"]+)"'
)
def step_refresh_run_loads(context, number, user, month, deal):
    clock.set_now(dt.datetime(2026, 9, 30, 6, tzinfo=dt.UTC))
    context.deal = deal

    def refresh(db):
        set_next_run_number(db, int(number))
        actor = authorize(db, user, Action.RUN_REFRESH)
        run = start_run(db, kind="refresh", started_by=user)
        summary = load_tape(
            db,
            deal=deal,
            content=TAPE.read_bytes(),
            file_name=TAPE.name,
            accession=TAPE_ACCESSION,
            filing_date=TAPE_FILED,
            cik=FIXTURE_CIK,
            actor=actor,
            run_id=run.id,
        )
        finish_run(db, run, status="succeeded", summary={"loans loaded": summary.loans_loaded})
        return run.id, summary

    context.run_id, context.loaded = in_session(refresh)
    assert context.run_id == int(number), context.run_id
    assert context.loaded.period == Period.parse(month), context.loaded.period


@then(
    r'every loan row it writes has an audit entry naming run (?P<number>\d+) and "(?P<user>[^"]+)"'
)
def step_every_row_audited(context, number, user):
    rows = in_session(
        lambda db: list(
            db.scalars(select(LoanPeriod.loan).where(LoanPeriod.accession == TAPE_ACCESSION))
        )
    )
    assert len(rows) == context.loaded.loans_loaded > 0, rows
    entries = in_session(lambda db: read_log(db, RISK, run=int(number)))
    loaded = [entry for entry in entries if entry.action == LOAD_ACTION]
    assert {entry.user_name for entry in entries} == {user}, entries
    assert all(entry.subject == LOAN for entry in loaded)
    keys = sorted(entry.subject_key or "" for entry in loaded)
    assert keys == sorted(loan_key(context.deal, loan) for loan in rows), keys
    assert all(
        entry.detail == {"accession": TAPE_ACCESSION, "period": "2026-09-01"} for entry in loaded
    )


# Leave the default matcher for the step modules loaded after this one.
use_step_matcher("parse")
