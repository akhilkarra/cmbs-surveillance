"""The fixture world every scenario starts from (features/README.md).

Six users with their roles, and settings version 7 approved by "rae" on 2026-09-15 after
"quinn" proposed it. Scenarios add deals, filings and positions on top.
"""

import datetime as dt
from decimal import Decimal

from sqlalchemy.orm import Session

from cmbs.access.roles import Role
from cmbs.access.service import ensure_user
from cmbs.settings.service import store_version
from cmbs.settings.values import Settings

USERS: dict[str, frozenset[Role]] = {
    "pat": frozenset({Role.PM}),
    "alex": frozenset({Role.ANALYST}),
    "quinn": frozenset({Role.QUANT}),
    "rae": frozenset({Role.RISK}),
    "olly": frozenset({Role.OPERATOR}),
    "morgan": frozenset({Role.ANALYST, Role.QUANT}),
}

BASELINE_SETTINGS_VERSION = 7
BASELINE_SETTINGS = Settings(
    dscr_alert_threshold=Decimal("1.00"),
    abstention_low=Decimal("0.30"),
    abstention_high=Decimal("0.70"),
    transfer_date_tolerance_periods=2,
    model="jev-1.14.0",
    prompt_version=3,
)
BASELINE_APPROVED_AT = dt.datetime(2026, 9, 15, tzinfo=dt.UTC)

FIXTURE_DEAL = "Fixture Trust 2026-F1"


def seed(db: Session) -> None:
    for user, roles in USERS.items():
        ensure_user(db, user, roles)
    store_version(
        db,
        version=BASELINE_SETTINGS_VERSION,
        values=BASELINE_SETTINGS,
        proposed_by="quinn",
        approved_by="rae",
        approved_at=BASELINE_APPROVED_AT,
    )
