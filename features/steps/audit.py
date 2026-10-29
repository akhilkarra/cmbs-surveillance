"""Steps for features/governance/audit_log.feature: a review decision's entry, and an automated
change naming its run. The log's reads and its append-only refusal are in governance.py.

Loan 2's borrower request waits for review after its September 2026 comment is extracted on
2026-10-01 (`tests/test_review_world.py`); the reviewer decides it on 2026-10-02. The refresh is
`cmbs.refresh`'s backfill of September 2026, against the recorded EDGAR answers the operations
scenarios use (tests/fixtures/refresh/).
"""

import datetime as dt

from behave import given, then, use_step_matcher, when  # pyright: ignore[reportAttributeAccessIssue]
from sqlalchemy import select

from cmbs import clock
from cmbs.audit.service import LOAN, loan_history, loan_key, read_log
from cmbs.deals.service import get_or_create
from cmbs.domain import Period
from cmbs.edgar.replay import Replay
from cmbs.extraction import fields
from cmbs.extraction.service import comment_view, decide_review, open_review_items
from cmbs.refresh import service as refresh
from cmbs.refresh.sources import ArchiveReplay, FirstRecorded, Sources
from cmbs.tape.service import LOAD_ACTION
from cmbs.tape.tables import LoanPeriod, LoanTape
from cmbs.testing.bdd import attempt, in_session
from cmbs.testing.world import FIXTURE_CIKS, FIXTURE_DEAL, set_next_run_number
from tests.conftest import FIXTURES
from tests.test_review_world import SEPTEMBER, loan_two

use_step_matcher("re")

DECIDED_AT = dt.datetime(2026, 10, 2, 9, 30, tzinfo=dt.UTC)
EDGAR = (FIXTURES / "refresh" / "edgar", FIXTURES / "edgar")
ARCHIVE = FIXTURES / "refresh" / "archive"
# After the September 2026 filings, before October's.
REFRESH_TODAY = dt.datetime(2026, 10, 28, 9, tzinfo=dt.UTC)
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
    clock.set_now(REFRESH_TODAY)
    context.deal = deal
    period = Period.parse(month)
    in_session(lambda db: get_or_create(db, deal, cik=FIXTURE_CIKS[deal]))
    in_session(lambda db: set_next_run_number(db, int(number)))
    sources = Sources(
        edgar=FirstRecorded(*(Replay(directory) for directory in EDGAR)),
        archive=FirstRecorded(ArchiveReplay(ARCHIVE)),
    )
    report = refresh.backfill(user, deal, period, period, sources=sources)
    assert (report.run.number, report.run.started_by) == (int(number), user), report.run
    assert report.run.status == "succeeded", report.text()

    def tape(db):
        found = db.scalars(
            select(LoanTape).where(
                LoanTape.deal_id == get_or_create(db, deal).id,
                LoanTape.period == period.first_day,
            )
        ).one()
        return found.accession

    context.tape_accession = in_session(tape)
    context.period = period


@then(
    r'every loan row it writes has an audit entry naming run (?P<number>\d+) and "(?P<user>[^"]+)"'
)
def step_every_row_audited(context, number, user):
    accession = context.tape_accession
    rows = in_session(
        lambda db: list(
            db.scalars(select(LoanPeriod.loan).where(LoanPeriod.accession == accession))
        )
    )
    assert rows, "the run wrote no loan rows"
    entries = in_session(lambda db: read_log(db, RISK, run=int(number)))
    assert entries and {entry.user_name for entry in entries} == {user}, entries
    loaded = [entry for entry in entries if entry.action == LOAD_ACTION]
    assert all(entry.subject == LOAN for entry in loaded)
    keys = sorted(entry.subject_key or "" for entry in loaded)
    assert keys == sorted(loan_key(context.deal, loan) for loan in rows), keys
    detail = {"accession": accession, "period": context.period.first_day.isoformat()}
    assert all(entry.detail == detail for entry in loaded), [e.detail for e in loaded]


# Leave the default matcher for the step modules loaded after this one.
use_step_matcher("parse")
