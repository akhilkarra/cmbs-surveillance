"""The fixture world every scenario starts from (features/README.md).

Six users with their roles, and settings version 7 approved by "rae" on 2026-09-15 after
"quinn" proposed it. Scenarios add deals, filings and positions on top.
"""

import datetime as dt
from decimal import Decimal

from sqlalchemy import text
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
# The synthetic issuing-entity CIK of the fixture deal. Real CIKs are below 2,200,000, so this
# cannot collide with one. Fixture accession numbers start with its ten-digit form, 0009900001-.
FIXTURE_CIK = 9_900_001
# Every synthetic fixture deal is its own trust with its own CIK (one CIK is one deal).
FIXTURE_CIKS = {
    FIXTURE_DEAL: FIXTURE_CIK,
    "Fixture Trust 2026-F2": 9_900_002,
    "Fixture Trust 2026-F3": 9_900_003,
}


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


def set_next_run_number(db: Session, number: int) -> None:
    """Make the next run take `number`, for scenarios that talk about "run 42".

    Fixture setup only, before anything else runs: production code never chooses run numbers,
    because moving a shared sequence safely under concurrency is not worth the risk.
    """
    db.execute(
        text("SELECT setval(pg_get_serial_sequence('app.run', 'id'), :number, false)"),
        {"number": number},
    )
