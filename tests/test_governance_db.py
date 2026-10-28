"""Role changes, the settings ledger and audit log reads against Postgres.

Skipped without DATABASE_URL.
"""

import datetime as dt
import threading
import time
from collections.abc import Callable
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from cmbs import clock
from cmbs.access.roles import Role
from cmbs.access.service import (
    PermissionRefused,
    RoleUnchanged,
    SelfRoleChange,
    UnknownUser,
    all_users,
    ensure_user,
    grant_role,
    revoke_role,
    roles_of,
)
from cmbs.audit.service import (
    AppendOnly,
    BadRange,
    change_entry,
    delete_entry,
    entries_for_subject,
    loan_history,
    loan_key,
    read_log,
    record,
)
from cmbs.audit.tables import AuditEntry
from cmbs.db import engine
from cmbs.settings.service import (
    AlreadyDecided,
    MissingQualityReport,
    MissingReason,
    NoSettings,
    SelfApproval,
    StaleProposal,
    UnknownProposal,
    approve,
    in_force,
    proposals,
    propose,
    read_settings,
    reject,
)
from cmbs.settings.tables import SettingsProposal, SettingsVersion
from cmbs.settings.values import InvalidSetting
from tests.conftest import needs_db

pytestmark = needs_db

SEPT_20 = dt.datetime(2026, 9, 20, 9, tzinfo=dt.UTC)
OCT_1 = dt.datetime(2026, 10, 1, 9, tzinfo=dt.UTC)
RAISE_DSCR = {"dscr_alert_threshold": "1.05"}


@pytest.fixture(autouse=True)
def _after_the_baseline():
    """Start every test after settings version 7 (2026-09-15) and before OCT_1."""
    clock.set_now(SEPT_20)


def refusals(db: Session) -> list[AuditEntry]:
    return list(db.scalars(select(AuditEntry).where(AuditEntry.outcome == "refused")))


def done(db: Session, action: str) -> list[AuditEntry]:
    query = select(AuditEntry).where(AuditEntry.outcome == "done", AuditEntry.action == action)
    return list(db.scalars(query.order_by(AuditEntry.id)))


# --- Role changes -------------------------------------------------------------------------------


def test_a_granted_role_is_held_and_audited_with_before_and_after(seeded):
    clock.set_now(OCT_1)
    assert grant_role(seeded, by="olly", user="quinn", role=Role.RISK) == {Role.QUANT, Role.RISK}
    assert roles_of(seeded, "quinn") == {Role.QUANT, Role.RISK}
    (entry,) = done(seeded, "grant role")
    assert (entry.user_name, entry.role, entry.subject, entry.subject_key) == (
        "olly",
        "operator",
        "user",
        "quinn",
    )
    assert (entry.field, entry.before, entry.after, entry.detail, entry.at) == (
        "roles",
        "quant",
        "quant, risk",
        {"role": "risk"},
        OCT_1,
    )


def test_a_revoked_role_is_gone_and_audited(seeded):
    assert revoke_role(seeded, by="olly", user="morgan", role=Role.ANALYST) == {Role.QUANT}
    seeded.commit()
    assert roles_of(seeded, "morgan") == {Role.QUANT}
    (entry,) = done(seeded, "revoke role")
    assert (entry.user_name, entry.subject_key, entry.before, entry.after) == (
        "olly",
        "morgan",
        "analyst, quant",
        "quant",
    )
    assert entry.detail == {"role": "analyst"}


def test_revoking_the_last_role_leaves_none(seeded):
    assert revoke_role(seeded, by="olly", user="pat", role=Role.PM) == frozenset()
    assert done(seeded, "revoke role")[0].after == "none"


def test_only_user_managers_change_roles(seeded):
    with pytest.raises(PermissionRefused, match="^quant may not manage users and roles$"):
        grant_role(seeded, by="quinn", user="pat", role=Role.RISK)
    seeded.rollback()
    assert roles_of(seeded, "pat") == {Role.PM}
    assert [entry.user_name for entry in refusals(seeded)] == ["quinn"]


@pytest.mark.parametrize(
    ("user", "role", "grant", "error", "message"),
    [
        ("olly", Role.RISK, True, SelfRoleChange, "nobody changes their own roles"),
        ("olly", Role.OPERATOR, False, SelfRoleChange, "nobody changes their own roles"),
        ("nobody", Role.RISK, True, UnknownUser, "no user named 'nobody'"),
        ("quinn", Role.QUANT, True, RoleUnchanged, "quinn already holds the role quant"),
        ("quinn", Role.RISK, False, RoleUnchanged, "quinn does not hold the role risk"),
    ],
)
def test_refused_role_changes_are_recorded(seeded, user, role, grant, error, message):
    change = grant_role if grant else revoke_role
    with pytest.raises(error) as refused:
        change(seeded, by="olly", user=user, role=role)
    assert str(refused.value) == message
    seeded.rollback()
    (entry,) = refusals(seeded)
    assert (entry.user_name, entry.role, entry.action, entry.subject_key, entry.reason) == (
        "olly",
        "operator",
        "grant role" if grant else "revoke role",
        user,
        message,
    )
    assert done(seeded, "grant role") == done(seeded, "revoke role") == []


def test_user_managers_list_every_user_with_roles(seeded):
    listed = all_users(seeded, "olly")
    assert list(listed) == ["alex", "morgan", "olly", "pat", "quinn", "rae"]
    assert listed["morgan"] == {Role.ANALYST, Role.QUANT}
    ensure_user(seeded, "nora", set())
    assert all_users(seeded, "olly")["nora"] == frozenset()
    with pytest.raises(PermissionRefused, match="^pm may not manage users and roles$"):
        all_users(seeded, "pat")


def while_first_holds(first: Callable[[Session], object], second: Callable[[Session], object]):
    """Run `first` in a session and keep its transaction open while `second` starts in another
    session on a thread; commit the first, then return what the second returned or raised."""
    outcome: dict[str, object] = {}

    def in_thread() -> None:
        with Session(engine.engine(), expire_on_commit=False) as other:
            try:
                outcome["value"] = second(other)
                other.commit()
            except Exception as error:
                outcome["error"] = error

    with Session(engine.engine()) as holder:
        first(holder)
        thread = threading.Thread(target=in_thread)
        thread.start()
        time.sleep(0.3)  # let the second session reach the lock the first one holds
        holder.commit()
        thread.join(timeout=10)
    return outcome.get("error", outcome.get("value"))


def test_concurrent_grants_of_one_role_queue_and_the_second_is_refused(seeded):
    def grant(db: Session):
        return grant_role(db, by="olly", user="quinn", role=Role.RISK)

    second = while_first_holds(grant, grant)
    assert isinstance(second, RoleUnchanged)
    assert str(second) == "quinn already holds the role risk"
    assert len(done(seeded, "grant role")) == 1


def test_concurrent_grants_of_two_roles_each_see_the_other(seeded):
    second = while_first_holds(
        lambda db: grant_role(db, by="olly", user="quinn", role=Role.RISK),
        lambda db: grant_role(db, by="olly", user="quinn", role=Role.PM),
    )
    assert second == {Role.PM, Role.QUANT, Role.RISK}
    assert [entry.after for entry in done(seeded, "grant role")] == [
        "quant, risk",
        "pm, quant, risk",
    ]


# --- The settings ledger ------------------------------------------------------------------------


def test_a_proposal_records_who_under_which_role_and_changes_nothing(seeded):
    clock.set_now(OCT_1)
    proposal = propose(seeded, "morgan", changes=RAISE_DSCR, reason="market moved")
    assert (proposal.proposed_by, proposal.proposer_role, proposal.proposed_at) == (
        "morgan",
        "quant",
        OCT_1,
    )
    assert (proposal.changes, proposal.proposed_from) == (
        {"dscr_alert_threshold": "1.05"},
        {"dscr_alert_threshold": "1.00"},
    )
    assert (proposal.status, proposal.reason, proposal.quality_report) == (
        "awaiting approval",
        "market moved",
        None,
    )
    assert in_force(seeded).version == 7
    (entry,) = done(seeded, "propose setting")
    assert (entry.user_name, entry.role, entry.subject, entry.subject_key) == (
        "morgan",
        "quant",
        "settings proposal",
        str(proposal.id),
    )
    assert (entry.after, entry.reason) == ("DSCR alert threshold 1.00x to 1.05x", "market moved")
    assert entry.detail == {"changes": {"dscr_alert_threshold": "1.05"}, "quality_report": None}


def test_only_fields_that_change_are_proposed(seeded):
    proposal = propose(
        seeded,
        "quinn",
        changes={"abstention_low": "0.30", "abstention_high": "0.60"},
        reason="tighter band",
    )
    assert proposal.changes == {"abstention_high": "0.60"}
    assert proposal.proposed_from == {"abstention_high": "0.70"}


def test_a_reason_is_stripped_and_kept(seeded):
    assert propose(seeded, "quinn", changes=RAISE_DSCR, reason="  why  ").reason == "why"


@pytest.mark.parametrize(
    ("changes", "reason", "report", "error", "message"),
    [
        (RAISE_DSCR, "  ", None, MissingReason, "a proposal needs a reason"),
        (
            {"prompt_version": 4},
            "better prompt",
            None,
            MissingQualityReport,
            "a model or prompt change needs a quality report",
        ),
        (
            {"model": "jev-1.15.0"},
            "new model",
            "   ",
            MissingQualityReport,
            "a model or prompt change needs a quality report",
        ),
        ({"dscr": "1.05"}, "typo", None, InvalidSetting, "there is no setting named 'dscr'"),
        ({"prompt_version": 3}, "same", None, InvalidSetting, "the proposal changes nothing"),
    ],
)
def test_refused_proposals_are_recorded_and_store_nothing(
    seeded, changes, reason, report, error, message
):
    with pytest.raises(error) as refused:
        propose(seeded, "quinn", changes=changes, reason=reason, quality_report=report)
    assert str(refused.value) == message
    seeded.rollback()
    assert seeded.scalars(select(SettingsProposal)).all() == []
    (entry,) = refusals(seeded)
    assert (entry.user_name, entry.role, entry.action, entry.subject, entry.reason) == (
        "quinn",
        "quant",
        "propose setting",
        "settings proposal",
        message,
    )


def test_a_model_change_with_a_quality_report_is_proposed(seeded):
    proposal = propose(
        seeded,
        "quinn",
        changes={"model": "jev-1.15.0", "prompt_version": 4},
        reason="better recall on borrower requests",
        quality_report=" quality report 12 ",
    )
    assert proposal.quality_report == "quality report 12"
    assert proposal.changes == {"model": "jev-1.15.0", "prompt_version": 4}


def test_only_proposers_propose(seeded):
    with pytest.raises(PermissionRefused, match="^risk may not propose settings$"):
        propose(seeded, "rae", changes=RAISE_DSCR, reason="r")


def proposed(db: Session, changes=None, by: str = "quinn", reason: str = "why") -> int:
    proposal = propose(db, by, changes=changes or RAISE_DSCR, reason=reason)
    db.commit()
    return proposal.id


def test_an_approval_creates_the_next_version_in_force_from_now(seeded):
    proposal_id = proposed(seeded, reason="sponsors default above 1.00x")
    clock.set_now(OCT_1)
    version = approve(seeded, "rae", proposal_id)
    assert (version.version, version.approved_at, version.proposed_by, version.approved_by) == (
        8,
        OCT_1,
        "quinn",
        "rae",
    )
    assert in_force(seeded).values.dscr_alert_threshold == Decimal("1.05")
    assert in_force(seeded, OCT_1 - dt.timedelta(microseconds=1)).version == 7
    proposal = seeded.get(SettingsProposal, proposal_id)
    assert proposal is not None
    assert (proposal.status, proposal.decided_by, proposal.decider_role) == (
        "approved",
        "rae",
        "risk",
    )
    assert (proposal.decided_at, proposal.version, proposal.decision_reason) == (OCT_1, 8, None)
    (entry,) = done(seeded, "approve setting")
    assert (entry.user_name, entry.role, entry.subject, entry.subject_key, entry.field) == (
        "rae",
        "risk",
        "setting",
        "dscr_alert_threshold",
        "DSCR alert threshold",
    )
    assert (entry.before, entry.after, entry.reason) == (
        "1.00x",
        "1.05x",
        "sponsors default above 1.00x",
    )
    assert entry.detail == {"proposal": proposal_id, "proposed_by": "quinn", "version": 8}


def test_an_approval_applies_the_change_on_top_of_the_latest_version(seeded):
    first = proposed(seeded)
    second = proposed(seeded, {"transfer_date_tolerance_periods": 1})
    clock.set_now(OCT_1)
    approve(seeded, "rae", first)
    clock.set_now(OCT_1 + dt.timedelta(hours=1))
    assert approve(seeded, "rae", second).version == 9
    values = in_force(seeded).values
    assert (values.dscr_alert_threshold, values.transfer_date_tolerance_periods) == (
        Decimal("1.05"),
        1,
    )


def test_a_proposal_overtaken_on_its_own_field_is_refused(seeded):
    first = proposed(seeded)
    second = proposed(seeded, {"dscr_alert_threshold": "1.10"})
    approve(seeded, "rae", first)
    seeded.commit()
    with pytest.raises(StaleProposal) as refused:
        approve(seeded, "rae", second)
    assert str(refused.value) == (
        f"the DSCR alert threshold has changed from 1.00x to 1.05x since proposal {second}; "
        "propose the change again"
    )
    seeded.rollback()
    assert in_force(seeded).version == 8
    assert refusals(seeded)[0].subject_key == str(second)


@pytest.mark.parametrize("by", ["morgan", "quinn"])
def test_nobody_approves_their_own_proposal(seeded, by):
    ensure_user(seeded, by, roles_of(seeded, by) | {Role.RISK})
    proposal_id = proposed(seeded, by=by)
    with pytest.raises(SelfApproval, match="^a proposal cannot be approved by its proposer$"):
        approve(seeded, by, proposal_id)
    seeded.rollback()
    stored = seeded.get(SettingsProposal, proposal_id)
    assert stored is not None and stored.status == "awaiting approval"
    (entry,) = refusals(seeded)
    assert (entry.user_name, entry.role, entry.action, entry.subject_key) == (
        by,
        "risk",
        "approve setting",
        str(proposal_id),
    )
    assert in_force(seeded).version == 7


def test_a_proposer_may_withdraw_by_rejecting(seeded):
    ensure_user(seeded, "morgan", {Role.ANALYST, Role.QUANT, Role.RISK})
    proposal_id = proposed(seeded, by="morgan")
    assert reject(seeded, "morgan", proposal_id, reason="withdrawn").status == "rejected"


def test_only_risk_decides(seeded):
    proposal_id = proposed(seeded)
    with pytest.raises(PermissionRefused, match="^quant may not approve or reject settings$"):
        approve(seeded, "quinn", proposal_id)
    with pytest.raises(PermissionRefused, match="^pm may not approve or reject settings$"):
        reject(seeded, "pat", proposal_id, reason="no")


def test_unknown_proposals_are_refused(seeded):
    with pytest.raises(UnknownProposal, match="^there is no proposal 999$"):
        approve(seeded, "rae", 999)
    with pytest.raises(UnknownProposal, match="^there is no proposal 999$"):
        reject(seeded, "rae", 999, reason="no")


def test_a_proposal_is_decided_once(seeded):
    approved_id = proposed(seeded)
    approve(seeded, "rae", approved_id)
    rejected_id = proposed(seeded, {"prompt_version": 3, "transfer_date_tolerance_periods": 3})
    reject(seeded, "rae", rejected_id, reason="no")
    seeded.commit()
    for decide in (
        lambda key: approve(seeded, "rae", key),
        lambda key: reject(seeded, "rae", key, reason="again"),
    ):
        with pytest.raises(AlreadyDecided, match=f"^proposal {approved_id} was already approved$"):
            decide(approved_id)
        with pytest.raises(AlreadyDecided, match=f"^proposal {rejected_id} was already rejected$"):
            decide(rejected_id)


def test_a_rejection_keeps_the_settings_and_the_reason(seeded):
    proposal_id = proposed(seeded, {"abstention_low": "0.40", "abstention_high": "0.60"})
    clock.set_now(OCT_1)
    rejected = reject(seeded, "rae", proposal_id, reason="  needs a quality report first ")
    assert (rejected.status, rejected.decision_reason, rejected.decided_at) == (
        "rejected",
        "needs a quality report first",
        OCT_1,
    )
    assert (rejected.decided_by, rejected.decider_role, rejected.version) == ("rae", "risk", None)
    assert in_force(seeded).version == 7
    (entry,) = done(seeded, "reject setting")
    assert (entry.user_name, entry.subject_key, entry.reason) == (
        "rae",
        str(proposal_id),
        "needs a quality report first",
    )
    assert entry.detail == {
        "proposed_by": "quinn",
        "changes": {"abstention_low": "0.40", "abstention_high": "0.60"},
    }


def test_a_rejection_needs_a_reason(seeded):
    proposal_id = proposed(seeded)
    with pytest.raises(MissingReason, match="^a rejection needs a reason$"):
        reject(seeded, "rae", proposal_id, reason=" ")
    seeded.rollback()
    stored = seeded.get(SettingsProposal, proposal_id)
    assert stored is not None and stored.status == "awaiting approval"
    assert refusals(seeded)[0].action == "reject setting"


def test_proposals_are_listed_newest_first_and_by_status(seeded):
    first = proposed(seeded)
    second = proposed(seeded, {"prompt_version": 3, "transfer_date_tolerance_periods": 3})
    reject(seeded, "rae", first, reason="no")
    assert [p.id for p in proposals(seeded, "pat")] == [second, first]
    assert [p.id for p in proposals(seeded, "pat", "rejected")] == [first]
    assert [p.id for p in proposals(seeded, "pat", "awaiting approval")] == [second]
    with pytest.raises(ValueError, match="^no proposal status 'open'$"):
        proposals(seeded, "pat", "open")
    with pytest.raises(PermissionRefused, match="^operator may not read the panel$"):
        proposals(seeded, "olly")


def test_settings_are_read_as_of_a_moment_with_their_history(seeded):
    proposal_id = proposed(seeded)
    clock.set_now(OCT_1)
    approve(seeded, "rae", proposal_id)
    view = read_settings(seeded, "pat")
    assert view.version == 8
    line = view.line("DSCR alert threshold")
    assert (line.value, line.since.version, [c.version for c in line.history]) == (
        "1.05x",
        8,
        [8, 7],
    )
    assert view.line("abstention band").since.version == 7
    earlier = read_settings(seeded, "pat", OCT_1 - dt.timedelta(days=1))
    assert (earlier.version, earlier.line("DSCR alert threshold").value) == (7, "1.00x")
    with pytest.raises(KeyError):
        view.line("nothing")
    with pytest.raises(PermissionRefused, match="^operator may not read the panel$"):
        read_settings(seeded, "olly")


def test_two_reviewers_cannot_both_decide_one_proposal(seeded):
    proposal_id = proposed(seeded)
    ensure_user(seeded, "rory", {Role.RISK})
    seeded.commit()
    second = while_first_holds(
        lambda db: approve(db, "rae", proposal_id),
        lambda db: reject(db, "rory", proposal_id, reason="late"),
    )
    assert isinstance(second, AlreadyDecided)
    assert str(second) == f"proposal {proposal_id} was already approved"
    assert in_force(seeded).version == 8


def test_two_approvals_of_different_proposals_queue_for_version_numbers(seeded):
    first = proposed(seeded)
    second = proposed(seeded, {"transfer_date_tolerance_periods": 1})
    later = while_first_holds(
        lambda db: approve(db, "rae", first), lambda db: approve(db, "rae", second)
    )
    assert isinstance(later, SettingsVersion) and later.version == 9


def test_the_database_refuses_a_self_approved_proposal(seeded):
    proposal_id = proposed(seeded)
    with pytest.raises(IntegrityError, match="not_self_approved"):
        seeded.execute(
            text(
                "UPDATE app.settings_proposal SET status = 'approved', decided_by = proposed_by, "
                "decider_role = 'risk', decided_at = proposed_at, version = 7 WHERE id = :id"
            ),
            {"id": proposal_id},
        )


# --- The audit log ------------------------------------------------------------------------------

DEAL = "Fixture Trust 2026-F1"


def entry_on(db: Session, moment: dt.datetime, user: str, loan: str) -> int:
    with clock.frozen(moment):
        return record(
            db,
            user=user,
            action="override value",
            subject="loan",
            subject_key=loan_key(DEAL, loan),
        ).id


def test_a_loans_history_is_newest_first_for_anyone_who_reads_the_panel(seeded):
    early = entry_on(seeded, dt.datetime(2026, 9, 3, tzinfo=dt.UTC), "alex", "2")
    late = entry_on(seeded, dt.datetime(2026, 9, 20, tzinfo=dt.UTC), "olly", "2")
    entry_on(seeded, dt.datetime(2026, 9, 10, tzinfo=dt.UTC), "alex", "7")
    assert [e.id for e in loan_history(seeded, "pat", deal=DEAL, loan="2")] == [late, early]
    assert loan_history(seeded, "pat", deal="Fixture Trust 2026-F2", loan="2") == []
    with pytest.raises(PermissionRefused, match="^operator may not read the panel$"):
        loan_history(seeded, "olly", deal=DEAL, loan="2")


def test_the_log_is_read_by_user_and_whole_utc_days(seeded):
    before = entry_on(seeded, dt.datetime(2026, 8, 31, 23, 59, 59, tzinfo=dt.UTC), "alex", "2")
    first = entry_on(seeded, dt.datetime(2026, 9, 1, tzinfo=dt.UTC), "alex", "2")
    last = entry_on(seeded, dt.datetime(2026, 9, 30, 23, 59, 59, tzinfo=dt.UTC), "alex", "7")
    after = entry_on(seeded, dt.datetime(2026, 10, 1, tzinfo=dt.UTC), "alex", "7")
    olly = entry_on(seeded, dt.datetime(2026, 9, 15, tzinfo=dt.UTC), "olly", "2")
    start, end = dt.date(2026, 9, 1), dt.date(2026, 9, 30)
    by_alex = read_log(seeded, "rae", user="alex", start=start, end=end)
    assert [e.id for e in by_alex] == [last, first]
    assert [e.id for e in read_log(seeded, "olly", start=start, end=end)] == [last, olly, first]
    assert [e.id for e in read_log(seeded, "rae", user="alex")] == [after, last, first, before]
    assert [e.id for e in read_log(seeded, "rae", start=dt.date(2026, 10, 1))] == [after]
    assert [e.id for e in read_log(seeded, "rae", end=dt.date(2026, 8, 31))] == [before]
    with pytest.raises(BadRange):
        read_log(seeded, "rae", start=dt.date(2026, 9, 30), end=dt.date(2026, 9, 1))


def test_pms_and_quants_do_not_read_the_log_by_user(seeded):
    with pytest.raises(PermissionRefused, match="^pm may not read the audit log by user$"):
        read_log(seeded, "pat", user="alex")
    with pytest.raises(PermissionRefused, match="^quant may not read the audit log by user$"):
        read_log(seeded, "quinn")


@pytest.mark.parametrize(
    ("attempt", "action"),
    [(delete_entry, "delete audit entry"), (change_entry, "change audit entry")],
)
def test_entries_cannot_be_deleted_or_changed_and_the_attempt_is_recorded(seeded, attempt, action):
    entry_id = entry_on(seeded, dt.datetime(2026, 9, 30, tzinfo=dt.UTC), "alex", "2")
    seeded.commit()
    clock.set_now(OCT_1)
    with pytest.raises(AppendOnly, match="^the audit log is append-only$"):
        attempt(seeded, "olly", entry_id)
    seeded.rollback()
    kept = seeded.get(AuditEntry, entry_id)
    assert kept is not None and kept.action == "override value"
    (entry,) = refusals(seeded)
    assert (entry.user_name, entry.action, entry.subject, entry.subject_key) == (
        "olly",
        action,
        "audit entry",
        str(entry_id),
    )
    assert (entry.reason, entry.at) == ("the audit log is append-only", OCT_1)


def test_role_changes_are_part_of_the_users_subject_history(seeded):
    grant_role(seeded, by="olly", user="quinn", role=Role.RISK)
    revoke_role(seeded, by="olly", user="quinn", role=Role.RISK)
    assert [e.action for e in entries_for_subject(seeded, "user", "quinn")] == [
        "revoke role",
        "grant role",
    ]


def test_an_approval_that_would_break_the_band_is_refused(seeded):
    raise_low = proposed(seeded, {"abstention_low": "0.60"})
    lower_high = proposed(seeded, {"abstention_high": "0.55"})
    approve(seeded, "rae", lower_high)
    seeded.commit()
    with pytest.raises(InvalidSetting) as refused:
        approve(seeded, "rae", raise_low)
    assert str(refused.value) == "the abstention band must rise from low to high within 0 to 1"
    seeded.rollback()
    assert in_force(seeded).version == 8
    (entry,) = refusals(seeded)
    assert (entry.action, entry.subject_key) == ("approve setting", str(raise_low))


def test_nothing_is_proposed_before_the_first_version(db):
    ensure_user(db, "quinn", {Role.QUANT})
    with pytest.raises(NoSettings, match="^no settings have been approved yet$"):
        propose(db, "quinn", changes=RAISE_DSCR, reason="why")
