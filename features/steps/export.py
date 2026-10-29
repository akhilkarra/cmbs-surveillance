"""Steps for features/data/reproducibility_and_export.feature, and the shared step that makes a
value wait for review (also used by features/surfaces/ask.feature and dashboard.feature).

"The panel" is "Fixture Trust 2026-F1" loaded from August 2025 to September 2026 by a quant's
backfill on 2026-10-01, every month's comments extracted (`tests/test_routine_world.py`). A
scenario that exports without saying what is loaded exports that panel. Exports are taken on
2026-10-07 (an export is as of a day that has ended); an override in a Given step is made on
2026-10-06.
"""

import csv
import datetime as dt
import io

import parse
from behave import given, register_type, then, when  # pyright: ignore[reportAttributeAccessIssue]
from sqlalchemy import func, select

from cmbs import clock
from cmbs.audit.tables import AuditEntry
from cmbs.deals.service import by_name
from cmbs.distribution.tables import ReportComment
from cmbs.domain import Period
from cmbs.export import service as export
from cmbs.export.service import export_panel, panel_state, rebuild
from cmbs.export.tables import PanelExport
from cmbs.extraction import fields
from cmbs.extraction.service import extract_comments
from cmbs.extraction.tables import Extraction
from cmbs.tape.tables import LoanTape
from cmbs.testing.bdd import attempt, in_session
from cmbs.testing.world import FIXTURE_DEAL
from tests.test_routine_world import (
    QUANT,
    SEPTEMBER,
    backfill,
    synthetic_model,
    wait_for_review,
)

FIRST, LAST = Period(2025, 8), Period(2026, 9)
EXPORT_TIME = dt.datetime(2026, 10, 7, 9, 0, tzinfo=dt.UTC)
OVERRIDE_TIME = dt.datetime(2026, 10, 6, 10, 0, tzinfo=dt.UTC)
OVERRIDE = ("forbearance", 3, "the request is for payment relief, not more time")


@parse.with_pattern(r"[A-Z][a-z]+ \d{4}")
def _month(string: str) -> Period:
    return Period.parse(string)


@parse.with_pattern(r"\d{4}-\d{2}-\d{2}")
def _day(string: str) -> dt.date:
    return dt.date.fromisoformat(string)


register_type(ExportMonth=_month, ExportDay=_day)


def _tapes() -> int:
    return in_session(lambda db: db.scalar(select(func.count()).select_from(LoanTape))) or 0


def _ensure_panel(context) -> None:
    """The fixture panel, unless the scenario loaded something already."""
    context.deal = getattr(context, "deal", None) or FIXTURE_DEAL
    if not _tapes():
        backfill(in_session, FIRST, LAST)


def _ensure_extracted(context, loan: str) -> None:
    """Loan's September 2026 comment extracted: from the 10-D already stored if there is one
    (a scenario may have loaded it its own way), otherwise from the fixture panel."""

    def stored(db):
        return db.scalar(
            select(func.count())
            .select_from(ReportComment)
            .where(ReportComment.loan_id == loan, ReportComment.period == SEPTEMBER.first_day)
        )

    if not in_session(stored):
        _ensure_panel(context)
    context.deal = getattr(context, "deal", None) or FIXTURE_DEAL

    def extract(db):
        return extract_comments(
            db,
            user=QUANT,
            deal=context.deal,
            period=SEPTEMBER,
            loan=loan,
            client=synthetic_model(db, QUANT),
        )

    in_session(extract)


# ---------------------------------------------------------------------------- Given


@given('"{deal}" is loaded from {start:ExportMonth} to {end:ExportMonth}')
def step_loaded_from(context, deal, start, end):
    context.deal = deal
    backfill(in_session, start, end, deal=deal)


@given('loan "{loan}"\'s {label} is waiting for review')
def step_waiting_for_review(context, loan, label):
    spec = fields.by_label(label)
    _ensure_extracted(context, loan)
    row = in_session(lambda db: wait_for_review(db, field=spec.name, loan=loan))
    assert row.value is None, row


@given('loan "{loan}"\'s {label} was overridden by "{user}"')
def step_overridden_by(context, loan, label, user):
    """The analyst replaces the value with "forbearance", citing sentence 3 (agent D's
    `cmbs.review.service.override`)."""
    from cmbs.review.service import override

    spec = fields.by_label(label)
    _ensure_extracted(context, loan)
    value, sentence, reason = OVERRIDE
    clock.set_now(OVERRIDE_TIME)
    in_session(
        lambda db: override(
            db,
            user,
            deal=context.deal,
            loan=loan,
            period=SEPTEMBER,
            field=spec.name,
            value=value,
            sentence=sentence,
            reason=reason,
        )
    )


# ---------------------------------------------------------------------------- When


@when("the panel is rebuilt from the stored raw filings")
def step_rebuilt(context):
    deal = in_session(lambda db: by_name(db, context.deal))
    assert deal is not None
    context.deal_id = deal.id
    context.before = in_session(lambda db: panel_state(db, [deal.id]))
    context.rebuild = attempt(context, lambda: in_session(lambda db: rebuild(db, QUANT)))
    assert context.refusal is None, f"the rebuild was refused: {context.refusal}"


@when('"{user}" exports the panel as of {day:ExportDay}')
def step_exports(context, user, day):
    _ensure_panel(context)
    clock.set_now(EXPORT_TIME)
    made = attempt(context, lambda: in_session(lambda db: export_panel(db, user, as_of=day)))
    assert made is not None, f"the export was refused: {context.refusal}"
    context.export = made
    context.export_rows = list(csv.DictReader(io.StringIO(made.content.decode())))


# ---------------------------------------------------------------------------- Then


@then("every loan row is identical to before, field for field")
def step_identical(context):
    found = context.rebuild
    assert found.differences == [], "\n".join(d.text() for d in found.differences)
    assert found.rows == len(context.before.loans) > 0, (found.rows, len(context.before.loans))
    assert found.tapes == FIRST.months_until(LAST) + 1, found.tapes
    # The rebuild changed nothing: the stored panel is still the one before it.
    after = in_session(lambda db: panel_state(db, [context.deal_id]))
    assert after == context.before


@then("the rebuilt panel's checksum matches the original")
def step_checksum_matches(context):
    found = context.rebuild
    assert found.rebuilt_checksum == found.stored_checksum == context.before.checksum, found
    assert found.identical


@then("the export holds periods through {last:ExportMonth} and no {absent:ExportMonth} rows")
def step_export_periods(context, last, absent):
    periods = {Period.of(dt.date.fromisoformat(row["period"])) for row in context.export_rows}
    assert max(periods) == last, sorted(periods)
    assert absent not in periods, sorted(periods)
    assert min(periods) == FIRST, sorted(periods)
    assert context.export.rows == len(context.export_rows)


@then(
    'the export file names the as-of date, settings version {version:d}, model "{model}" and '
    "prompt version {prompt:d}"
)
def step_export_names(context, version, model, prompt):
    made = context.export
    for part in (
        f"as-of-{made.as_of.isoformat()}",
        f"settings-{version}",
        model,
        f"prompt-{prompt}",
    ):
        assert part in made.file_name, (part, made.file_name)
    stored = in_session(lambda db: db.get_one(PanelExport, made.number))
    found = (stored.as_of, stored.settings_version, stored.model, stored.prompt_version)
    assert found == (made.as_of, version, model, prompt), found


def _loan_row(context, loan: str) -> dict[str, str]:
    rows = [
        row
        for row in context.export_rows
        if row["loan"] == loan and row["period"] == SEPTEMBER.first_day.isoformat()
    ]
    assert len(rows) == 1, rows
    return rows[0]


def _exported(context, loan: str, label: str) -> tuple[str, str]:
    row = _loan_row(context, loan)
    column = f"comment_{fields.by_label(label).name}"
    return row[column], row[f"{column}_status"]


@then('loan "{loan}"\'s {label} is empty with status "{status}"')
def step_export_empty(context, loan, label, status):
    assert _exported(context, loan, label) == ("", status), _loan_row(context, loan)


@then('loan "{loan}"\'s {label} is "{value}" with status "{status}"')
def step_export_value(context, loan, label, value, status):
    assert _exported(context, loan, label) == (value, status), _loan_row(context, loan)


@then('the audit log records the export, its as-of date and "{user}"')
def step_export_audited(context, user):
    made = context.export
    entries = in_session(
        lambda db: db.scalars(select(AuditEntry).where(AuditEntry.action == export.EXPORT)).all()
    )
    assert len(entries) == 1, entries
    entry = entries[0]
    assert (entry.user_name, entry.subject_key, entry.after) == (
        user,
        str(made.number),
        made.file_name,
    )
    assert entry.detail is not None
    assert (entry.detail["as_of"], entry.detail["sha256"], entry.detail["rows"]) == (
        made.as_of.isoformat(),
        made.sha256,
        made.rows,
    )
    extracted = in_session(lambda db: db.scalar(select(func.count()).select_from(Extraction)))
    assert extracted, "the panel had no extracted comments"
