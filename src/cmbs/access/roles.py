"""The role and permission matrix. Pure data and logic, no database.

Each role is a short list of actions. A user may hold several roles and acts under the first
role, in ROLE_ORDER, that permits the action.
"""

from enum import StrEnum


class Role(StrEnum):
    PM = "pm"
    ANALYST = "analyst"
    QUANT = "quant"
    RISK = "risk"
    OPERATOR = "operator"


ROLE_ORDER: tuple[Role, ...] = (Role.PM, Role.ANALYST, Role.QUANT, Role.RISK, Role.OPERATOR)


class Action(StrEnum):
    """Each value is the phrase used in refusal messages: '<role> may not <phrase>'."""

    READ_PANEL = "read the panel"
    ASK = "ask questions"
    ACKNOWLEDGE_ALERT = "acknowledge alerts"
    ASSIGN_ALERT = "assign alerts"
    CLOSE_ALERT = "close alerts"
    DECIDE_REVIEW = "decide review items"
    OVERRIDE_VALUE = "override values"
    RUN_REFRESH = "run refreshes and backfills"
    EXPORT = "export"
    PROPOSE_SETTING = "propose settings"
    READ_AUDIT_LOG = "read the audit log by user"
    DECIDE_SETTING = "approve or reject settings"
    SEE_RUN_HISTORY = "see run history"
    MANAGE_USERS = "manage users and roles"
    LOAD_POSITIONS = "load positions"


_PM = frozenset(
    {
        Action.READ_PANEL,
        Action.ASK,
        Action.ACKNOWLEDGE_ALERT,
        Action.ASSIGN_ALERT,
        Action.CLOSE_ALERT,
    }
)

PERMISSIONS: dict[Role, frozenset[Action]] = {
    Role.PM: _PM,
    Role.ANALYST: _PM | {Action.DECIDE_REVIEW, Action.OVERRIDE_VALUE},
    Role.QUANT: frozenset(
        {
            Action.READ_PANEL,
            Action.RUN_REFRESH,
            Action.EXPORT,
            Action.PROPOSE_SETTING,
            Action.LOAD_POSITIONS,
        }
    ),
    Role.RISK: frozenset(
        {Action.READ_PANEL, Action.ASK, Action.READ_AUDIT_LOG, Action.DECIDE_SETTING}
        | {Action.SEE_RUN_HISTORY}
    ),
    Role.OPERATOR: frozenset(
        {
            Action.RUN_REFRESH,
            Action.SEE_RUN_HISTORY,
            Action.MANAGE_USERS,
            Action.READ_AUDIT_LOG,
            Action.LOAD_POSITIONS,
        }
    ),
}


def acting_role(roles: set[Role] | frozenset[Role], action: Action) -> Role | None:
    """The first role, in ROLE_ORDER, that permits the action; None if no role does."""
    for role in ROLE_ORDER:
        if role in roles and action in PERMISSIONS[role]:
            return role
    return None


def refusal_message(roles: set[Role] | frozenset[Role], action: Action) -> str:
    """'pm may not decide review items'. Several roles read 'analyst or quant may not ...'."""
    names = (
        " or ".join(role.value for role in ROLE_ORDER if role in roles) or "a user without roles"
    )
    return f"{names} may not {action.value}"
