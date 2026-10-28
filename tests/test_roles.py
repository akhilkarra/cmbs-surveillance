"""The permission matrix must match features/governance/users_and_roles.feature exactly."""

import pytest

from cmbs.access.roles import PERMISSIONS, Action, Role, acting_role, refusal_message

A = Action
EXPECTED = {
    Role.PM: {A.READ_PANEL, A.ASK, A.ACKNOWLEDGE_ALERT, A.ASSIGN_ALERT, A.CLOSE_ALERT},
    Role.ANALYST: {
        A.READ_PANEL,
        A.ASK,
        A.ACKNOWLEDGE_ALERT,
        A.ASSIGN_ALERT,
        A.CLOSE_ALERT,
        A.DECIDE_REVIEW,
        A.OVERRIDE_VALUE,
    },
    Role.QUANT: {A.READ_PANEL, A.RUN_REFRESH, A.EXPORT, A.PROPOSE_SETTING, A.LOAD_POSITIONS},
    Role.RISK: {A.READ_PANEL, A.ASK, A.READ_AUDIT_LOG, A.DECIDE_SETTING, A.SEE_RUN_HISTORY},
    Role.OPERATOR: {
        A.RUN_REFRESH,
        A.SEE_RUN_HISTORY,
        A.MANAGE_USERS,
        A.READ_AUDIT_LOG,
        A.LOAD_POSITIONS,
    },
}


def test_each_role_has_exactly_its_permissions():
    assert {role: set(actions) for role, actions in PERMISSIONS.items()} == EXPECTED


def test_every_action_is_granted_to_some_role():
    granted = set().union(*PERMISSIONS.values())
    assert granted == set(Action)


def test_a_user_acts_under_the_first_role_that_permits_the_action():
    morgan = frozenset({Role.ANALYST, Role.QUANT})
    assert acting_role(morgan, A.PROPOSE_SETTING) is Role.QUANT
    assert acting_role(morgan, A.DECIDE_REVIEW) is Role.ANALYST
    assert acting_role(morgan, A.READ_PANEL) is Role.ANALYST


def test_no_permitting_role_means_none():
    assert acting_role(frozenset({Role.PM}), A.DECIDE_REVIEW) is None
    assert acting_role(frozenset(), A.READ_PANEL) is None


@pytest.mark.parametrize(
    ("roles", "action", "message"),
    [
        ({Role.PM}, A.DECIDE_REVIEW, "pm may not decide review items"),
        ({Role.QUANT}, A.CLOSE_ALERT, "quant may not close alerts"),
        ({Role.PM}, A.OVERRIDE_VALUE, "pm may not override values"),
        ({Role.PM}, A.READ_AUDIT_LOG, "pm may not read the audit log by user"),
        ({Role.QUANT, Role.PM}, A.DECIDE_SETTING, "pm or quant may not approve or reject settings"),
        (set(), A.READ_PANEL, "a user without roles may not read the panel"),
    ],
)
def test_refusal_messages(roles, action, message):
    assert refusal_message(frozenset(roles), action) == message
