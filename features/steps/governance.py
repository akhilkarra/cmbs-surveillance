"""Steps for features/governance: users and roles, the settings ledger and the audit log.

These steps use regular expressions (behave resets the matcher after each module) so a quoted
argument never swallows the rest of a longer step from another feature, such as
`"pat" opens loan "2" of "Fixture Trust 2026-F1"`.
"""

import datetime as dt
import itertools
import re

from behave import given, then, use_step_matcher, when  # pyright: ignore[reportAttributeAccessIssue]
from sqlalchemy import select

from cmbs import clock
from cmbs.access.roles import PERMISSIONS, Action, Role, parse_roles
from cmbs.access.service import authorize, ensure_user, grant_role, roles_of
from cmbs.audit import service as audit
from cmbs.audit.tables import AuditEntry
from cmbs.domain import Period
from cmbs.settings import service as ledger
from cmbs.settings.tables import REJECTED, SettingsProposal, SettingsVersion
from cmbs.settings.values import SHOWN
from cmbs.testing.bdd import attempt, in_session
from cmbs.testing.world import FIXTURE_DEAL

use_step_matcher("re")

A = Action
DATE = r"\d{4}-\d{2}-\d{2}"
# A step that proposes a change without saying why still needs a reason: proposals require one.
UNSTATED_REASON = "reason not stated in the scenario"


def _day(text: str) -> dt.date:
    return dt.date.fromisoformat(text)


def _midnight(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(), dt.UTC)


def _deal(context) -> str:
    return getattr(context, "deal", None) or FIXTURE_DEAL


def _window(context, action):
    """Run `action`, keeping the clock readings just before and after it in `context.window`."""
    started = clock.now()
    try:
        return action()
    finally:
        context.window = (started, clock.now())


def _within_window(context, moment: dt.datetime) -> bool:
    started, ended = context.window
    return started <= moment <= ended


# --- Users and roles ----------------------------------------------------------------------------

# Each clause of the "may" column, as the actions it grants. "read everything" is reading the
# panel and asking questions of it (the ask surface is read-only), plus what it names.
MAY_CLAUSES: dict[str, frozenset[Action]] = {
    "read the panel": frozenset({A.READ_PANEL}),
    "ask questions": frozenset({A.ASK}),
    "acknowledge": frozenset({A.ACKNOWLEDGE_ALERT}),
    "assign and close alerts": frozenset({A.ASSIGN_ALERT, A.CLOSE_ALERT}),
    "decide review items": frozenset({A.DECIDE_REVIEW}),
    "override values with a reason": frozenset({A.OVERRIDE_VALUE}),
    "run refreshes and backfills": frozenset({A.RUN_REFRESH}),
    "export": frozenset({A.EXPORT}),
    "propose settings": frozenset({A.PROPOSE_SETTING}),
    "load positions": frozenset({A.LOAD_POSITIONS}),
    "read everything including the audit log and run history": frozenset(
        {A.READ_PANEL, A.ASK, A.READ_AUDIT_LOG, A.SEE_RUN_HISTORY}
    ),
    "approve or reject settings": frozenset({A.DECIDE_SETTING}),
    "see run history": frozenset({A.SEE_RUN_HISTORY}),
    "manage users and roles": frozenset({A.MANAGE_USERS}),
    "read the audit log": frozenset({A.READ_AUDIT_LOG}),
}
EVERYTHING_A_ROLE_MAY = re.compile(r"^everything an? (\w+) may$")


def _table_permissions(rows: dict[str, str]) -> dict[Role, frozenset[Action]]:
    """Read the feature's table into actions, resolving 'everything a pm may' from the table."""
    resolved: dict[Role, frozenset[Action]] = {}

    def resolve(role_name: str) -> frozenset[Action]:
        role = Role(role_name)
        if role not in resolved:
            granted: set[Action] = set()
            for clause in (part.strip() for part in rows[role_name].split(",")):
                inherited = EVERYTHING_A_ROLE_MAY.match(clause)
                if inherited:
                    granted |= resolve(inherited.group(1))
                else:
                    assert clause in MAY_CLAUSES, f"no actions known for {clause!r}"
                    granted |= MAY_CLAUSES[clause]
            resolved[role] = frozenset(granted)
        return resolved[role]

    for name in rows:
        resolve(name)
    return resolved


@then("the roles permit exactly:")
def step_roles_permit_exactly(context):
    rows = {row["role"].strip(): row["may"].strip() for row in context.table}
    expected = _table_permissions(rows)
    assert set(expected) == set(Role), f"the table covers {sorted(expected)}, not every role"
    for role, actions in expected.items():
        granted = PERMISSIONS[role]
        assert granted == actions, (
            f"{role}: the matrix also grants {sorted(granted - actions)} "
            f"and lacks {sorted(actions - granted)}"
        )


@when(r'"(?P<user>[^"]+)" tries to decide a review item for loan "(?P<loan>[^"]+)"')
def step_tries_to_decide_review_item(context, user, loan):
    # The review queue arrives in a later wave; it checks permission the same way, by calling
    # authorize(db, user, Action.DECIDE_REVIEW) before touching the item.
    context.actor = user
    _window(context, lambda: attempt(context, lambda: in_session(_decide_review(user))))


def _decide_review(user):
    return lambda db: authorize(db, user, Action.DECIDE_REVIEW)


@then("the refusal is written to the audit log with the user and the time")
def step_refusal_written_to_audit_log(context):
    assert context.refusal is not None, "nothing was refused"

    def refused(db):
        query = select(AuditEntry).where(AuditEntry.outcome == "refused")
        return db.scalars(query).all()

    entries = in_session(refused)
    assert len(entries) == 1, f"{len(entries)} refusals recorded"
    entry = entries[0]
    assert (entry.user_name, entry.action, entry.reason) == (
        context.actor,
        str(Action.DECIDE_REVIEW),
        str(context.refusal),
    ), (entry.user_name, entry.action, entry.reason)
    assert _within_window(context, entry.at), f"recorded at {entry.at}, not {context.window}"


def _changes(what: str, value: str) -> dict[str, str]:
    """The fields a step's wording changes: 'DSCR alert threshold' to '1.05x', and so on."""
    if what == "DSCR alert threshold":
        return {"dscr_alert_threshold": value.removesuffix("x")}
    if what == "abstention band":
        low, high = (part.strip() for part in value.split(" to "))
        return {"abstention_low": low, "abstention_high": high}
    if what == "prompt version":
        return {"prompt_version": value}
    if what == "model":
        return {"model": value}
    raise AssertionError(f"no setting is called {what!r} in these steps")


def _propose(context, user, what, value, reason, quality_report=None):
    context.proposal_reason = reason or UNSTATED_REASON

    def make(db):
        proposal = ledger.propose(
            db,
            user,
            changes=_changes(what, value),
            reason=context.proposal_reason,
            quality_report=quality_report,
        )
        return proposal.id

    return make


@when(
    r'"(?P<user>[^"]+)" proposes the (?P<what>.+?) be changed to (?P<value>.+?)'
    r'(?: because "(?P<reason>[^"]+)")?'
)
def step_proposes(context, user, what, value, reason=None):
    context.proposal_id = attempt(
        context, lambda: in_session(_propose(context, user, what, value, reason))
    )


@given(r'"(?P<user>[^"]+)" proposed the (?P<what>.+?) be changed to (?P<value>.+)')
def step_proposed(context, user, what, value):
    context.proposal_id = in_session(_propose(context, user, what, value, None))


@then(r'the proposal records "(?P<user>[^"]+)" acting as "(?P<role>[^"]+)"')
def step_proposal_records_acting_role(context, user, role):
    assert context.refusal is None, f"the proposal was refused: {context.refusal}"
    proposal = in_session(lambda db: db.get(SettingsProposal, context.proposal_id))
    assert proposal is not None, "no proposal was stored"
    assert (proposal.proposed_by, proposal.proposer_role) == (user, role), (
        proposal.proposed_by,
        proposal.proposer_role,
    )


@given(r'"(?P<user>[^"]+)" also holds the role "(?P<role>[^"]+)"')
def step_also_holds_role(context, user, role):
    in_session(lambda db: ensure_user(db, user, roles_of(db, user) | {Role(role)}))


@when(r'"(?P<user>[^"]+)" tries to approve that proposal')
def step_tries_to_approve(context, user):
    attempt(context, lambda: in_session(lambda db: ledger.approve(db, user, context.proposal_id)))


@then(r'the (?:approval|proposal|deletion|read) is refused with "(?P<message>[^"]+)"')
def step_this_is_refused_with(context, message):
    assert context.refusal is not None, "it was not refused"
    assert str(context.refusal) == message, f"refused with {str(context.refusal)!r}"


@when(r'"(?P<by>[^"]+)" adds the role "(?P<role>[^"]+)" to "(?P<user>[^"]+)"')
def step_adds_role(context, by, role, user):
    _window(
        context, lambda: in_session(lambda db: grant_role(db, by=by, user=user, role=Role(role)))
    )


@then(r'"(?P<user>[^"]+)" holds the roles "(?P<roles>[^"]+)"')
def step_holds_roles(context, user, roles):
    held = in_session(lambda db: roles_of(db, user))
    assert held == parse_roles(roles), f"{user} holds {sorted(held)}"


@then(
    r'the audit log records that "(?P<by>[^"]+)" granted "(?P<role>[^"]+)" to "(?P<user>[^"]+)"'
    r" and when"
)
def step_audit_records_grant(context, by, role, user):
    entries = in_session(lambda db: audit.entries_for_subject(db, "user", user))
    grants = [entry for entry in entries if entry.action == "grant role"]
    assert len(grants) == 1, f"{len(grants)} role grants recorded for {user}"
    entry = grants[0]
    assert (entry.user_name, entry.detail, entry.outcome) == (by, {"role": role}, "done"), (
        entry.user_name,
        entry.detail,
        entry.outcome,
    )
    assert _within_window(context, entry.at), f"recorded at {entry.at}, not {context.window}"


# --- Settings ledger ----------------------------------------------------------------------------


@given(
    r"settings version (?P<version>\d+) is in force, with the DSCR alert threshold at "
    r"(?P<dscr>[\d.]+)x and the abstention band at (?P<low>[\d.]+) to (?P<high>[\d.]+)"
)
def step_settings_version_in_force(context, version, dscr, low, high):
    # Scenarios start the moment this version came into force, so later steps can name dates
    # (an approval "on 2026-10-01") that come after every proposal.
    row = in_session(lambda db: db.get(SettingsVersion, int(version)))
    assert row is not None, f"no settings version {version}"
    clock.set_now(row.approved_at)
    current = in_session(lambda db: ledger.in_force(db))
    values = current.values
    assert current.version == int(version), f"version {current.version} is in force"
    assert (str(values.dscr_alert_threshold), str(values.abstention_low)) == (dscr, low)
    assert str(values.abstention_high) == high
    context.baseline_version = current.version


@when(r'"(?P<user>[^"]+)" reads the settings')
def step_reads_settings(context, user):
    context.settings_view = in_session(lambda db: ledger.read_settings(db, user))


@then(
    r"the (?P<label>.+?) shows (?P<value>\S+), version (?P<version>\d+), approved by "
    rf'"(?P<approver>[^"]+)" on (?P<day>{DATE}), proposed by "(?P<proposer>[^"]+)"'
)
def step_setting_shows(context, label, value, version, approver, day, proposer):
    line = context.settings_view.line(label)
    since = line.since
    shown = (line.value, since.version, since.approved_by, since.approved_at.date())
    assert shown == (value, int(version), approver, _day(day)), shown
    assert since.proposed_by == proposer, since.proposed_by


def _shown_in_force(label: str) -> tuple[int, str]:
    (shown,) = [item for item in SHOWN if item.label == label]
    current = in_session(lambda db: ledger.in_force(db))
    return current.version, shown.text(current.values)


@then(r"the (?P<label>DSCR alert threshold|abstention band) in force is still (?P<value>.+)")
def step_still_in_force(context, label, value):
    version, text = _shown_in_force(label)
    assert (version, text) == (context.baseline_version, value), (version, text)


@then(r'the proposal is listed as "(?P<status>[^"]+)" with the reason')
def step_proposal_listed(context, status):
    listed = in_session(lambda db: ledger.proposals(db, "rae"))
    (proposal,) = [item for item in listed if item.id == context.proposal_id]
    assert proposal.status == status, proposal.status
    assert proposal.reason == context.proposal_reason, proposal.reason
    if status == REJECTED:
        assert proposal.decision_reason == context.decision_reason, proposal.decision_reason


@when(rf'"(?P<user>[^"]+)" approves the proposal on (?P<day>{DATE})')
def step_approves_on(context, user, day):
    clock.set_now(_day(day))
    in_session(lambda db: ledger.approve(db, user, context.proposal_id))


@then(
    r"settings version (?P<version>\d+) is in force with the DSCR alert threshold at "
    r"(?P<dscr>[\d.]+)x"
)
def step_new_version_in_force(context, version, dscr):
    assert _shown_in_force("DSCR alert threshold") == (int(version), f"{dscr}x")


@then(
    rf"alerts built (?P<side>after|before) (?P<day>{DATE}) (?:still )?record settings version "
    r"(?P<version>\d+)"
)
def step_alerts_record_version(context, side, day, version):
    # Alerts arrive in a later wave. An alert records the version in force when it is built, so
    # this checks what `in_force` returns at moments on each side of the date.
    start = _midnight(_day(day))
    if side == "after":
        moments = [start + dt.timedelta(days=1), start + dt.timedelta(days=60)]
    else:
        moments = [start - dt.timedelta(microseconds=1), start - dt.timedelta(days=10)]
    found = [in_session(lambda db, at=at: ledger.in_force(db, at).version) for at in moments]
    assert found == [int(version)] * len(moments), dict(zip(moments, found, strict=True))


@when(r'"(?P<user>[^"]+)" rejects the proposal because "(?P<reason>[^"]+)"')
def step_rejects(context, user, reason):
    context.decision_reason = reason
    in_session(lambda db: ledger.reject(db, user, context.proposal_id, reason=reason))


@when(
    r'"(?P<user>[^"]+)" proposes the (?P<what>.+?) be changed from (?P<old>\S+) to '
    r"(?P<new>\S+) without a quality report"
)
def step_proposes_without_quality_report(context, user, what, old, new):
    _, current = _shown_in_force(what)
    assert current == old, f"the {what} is {current}, not {old}"
    propose = _propose(context, user, what, new, None, quality_report=None)
    context.proposal_id = attempt(context, lambda: in_session(propose))


# --- Audit log ----------------------------------------------------------------------------------


def _record_at(moment: dt.datetime, **entry) -> int:
    with clock.frozen(moment):
        return in_session(lambda db: audit.record(db, **entry).id)


@given(
    r'the audit log holds entries for loans "(?P<first>[^"]+)" and "(?P<second>[^"]+)" made by '
    r'"(?P<one>[^"]+)" and "(?P<other>[^"]+)" during (?P<month>\w+ \d{4})'
)
def step_audit_log_holds_entries(context, first, second, one, other, month):
    """One entry per loan and user inside the month, plus one each just outside it, so reading
    by date has something to leave out."""
    period = Period.parse(month)
    start = _midnight(period.first_day)
    end = _midnight(period.shift(1).first_day)
    pairs = list(itertools.product((first, second), (one, other)))
    moments = [start + dt.timedelta(days=2 + 7 * index, hours=10) for index in range(len(pairs))]
    plan = list(zip(pairs, moments, strict=True))
    plan.append(((first, one), start - dt.timedelta(microseconds=1)))
    plan.append(((first, one), end))
    context.audit_seeded = []
    for (loan, user), moment in plan:
        entry_id = _record_at(
            moment,
            user=user,
            action="override value",
            subject=audit.LOAN,
            subject_key=audit.loan_key(_deal(context), loan),
            field="occupancy",
            before="0.91",
            after="0.88",
            reason="September operating statement",
        )
        context.audit_seeded.append((entry_id, loan, user, moment))


@when(r'"(?P<reader>[^"]+)" reads the audit log for loan "(?P<loan>[^"]+)"')
def step_reads_audit_log_for_loan(context, reader, loan):
    deal = _deal(context)
    context.read = in_session(lambda db: audit.loan_history(db, reader, deal=deal, loan=loan))


def _ids_newest_first(seeded) -> list[int]:
    return [item[0] for item in sorted(seeded, key=lambda item: (item[3], item[0]), reverse=True)]


@then(r'only loan "(?P<loan>[^"]+)"\'s entries are returned, newest first')
def step_only_loan_entries(context, loan):
    expected = _ids_newest_first(item for item in context.audit_seeded if item[1] == loan)
    assert [entry.id for entry in context.read] == expected, [e.id for e in context.read]


@when(
    rf'"(?P<reader>[^"]+)" reads the audit log for user "(?P<user>[^"]+)" between '
    rf"(?P<start>{DATE}) and (?P<end>{DATE})"
)
def step_reads_audit_log_for_user_between(context, reader, user, start, end):
    context.read = in_session(
        lambda db: audit.read_log(db, reader, user=user, start=_day(start), end=_day(end))
    )


@then(r'only "(?P<user>[^"]+)"\'s (?P<month>\w+ \d{4}) entries are returned')
def step_only_user_entries_in_month(context, user, month):
    period = Period.parse(month)
    expected = _ids_newest_first(
        item
        for item in context.audit_seeded
        if item[2] == user and Period.of(item[3].date()) == period
    )
    assert expected, "the scenario recorded nothing to find"
    assert [entry.id for entry in context.read] == expected, [e.id for e in context.read]


@given(rf'an audit entry made by "(?P<user>[^"]+)" on (?P<day>{DATE})')
def step_an_audit_entry(context, user, day):
    context.entry_id = _record_at(
        _midnight(_day(day)) + dt.timedelta(hours=15),
        user=user,
        action="override value",
        subject=audit.LOAN,
        subject_key=audit.loan_key(_deal(context), "2"),
        field="occupancy",
        before="0.91",
        after="0.88",
        reason="September operating statement",
    )


@when(r'"(?P<user>[^"]+)" tries to delete that entry')
def step_tries_to_delete_entry(context, user):
    context.actor = user
    attempt(context, lambda: in_session(lambda db: audit.delete_entry(db, user, context.entry_id)))


@then("the attempt is itself recorded")
def step_attempt_recorded(context):
    def read(db):
        refused = select(AuditEntry).where(AuditEntry.outcome == "refused")
        return db.scalars(refused).all(), db.get(AuditEntry, context.entry_id)

    refusals, original = in_session(read)
    assert original is not None, "the entry is gone"
    assert len(refusals) == 1, f"{len(refusals)} refusals recorded"
    entry = refusals[0]
    found = (entry.user_name, entry.action, entry.subject_key, entry.reason)
    expected = (context.actor, "delete audit entry", str(context.entry_id), str(context.refusal))
    assert found == expected, found


@when(r'"(?P<user>[^"]+)" opens loan "(?P<loan>[^"]+)"')
def step_opens_loan(context, user, loan):
    # The loan view arrives with the dashboard; its change history is read the same way.
    deal = _deal(context)
    context.history = attempt(
        context,
        lambda: in_session(lambda db: audit.loan_history(db, user, deal=deal, loan=loan)),
    )


@then(r'"(?P<user>[^"]+)" sees loan "(?P<loan>[^"]+)"\'s change history')
def step_sees_change_history(context, user, loan):
    assert context.refusal is None, f"{user} was refused: {context.refusal}"
    key = audit.loan_key(_deal(context), loan)
    stored = in_session(lambda db: audit.entries_for_subject(db, audit.LOAN, key))
    assert [entry.id for entry in context.history] == [entry.id for entry in stored]


@when(r'"(?P<reader>[^"]+)" tries to read the audit log for user "(?P<user>[^"]+)"')
def step_tries_to_read_audit_log_for_user(context, reader, user):
    attempt(context, lambda: in_session(lambda db: audit.read_log(db, reader, user=user)))
