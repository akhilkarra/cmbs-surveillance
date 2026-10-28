"""Read the settings in force at a moment."""

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from cmbs import clock
from cmbs.settings.tables import SettingsVersion
from cmbs.settings.values import Settings

# An arbitrary constant naming the settings-ledger lock among Postgres advisory locks.
SETTINGS_LOCK_KEY = 0x5E771265


class NoSettings(RuntimeError):
    pass


@dataclass(frozen=True)
class InForce:
    version: int
    values: Settings


class OutOfOrderVersion(ValueError):
    """Versions are numbered in approval order; a new one must come after the latest."""


def in_force(db: Session, at: dt.datetime | None = None) -> InForce:
    """The latest version approved at or before `at` (default: now).

    `store_version` keeps version numbers in approval order, so the highest number approved by
    `at` is the one in force.
    """
    moment = at or clock.now()
    row = db.scalars(
        select(SettingsVersion)
        .where(SettingsVersion.approved_at <= moment)
        .order_by(SettingsVersion.version.desc())
        .limit(1)
    ).one_or_none()
    if row is None:
        raise NoSettings(f"no settings approved by {moment.isoformat()}")
    return InForce(version=row.version, values=Settings.model_validate(row.values))


def store_version(
    db: Session,
    *,
    version: int,
    values: Settings,
    proposed_by: str,
    approved_by: str,
    approved_at: dt.datetime,
) -> SettingsVersion:
    # Serialize writers: the check below and the insert must see every committed version, even
    # when the ledger is empty, so concurrent approvals queue on a transaction-scoped lock.
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": SETTINGS_LOCK_KEY})
    latest = db.scalars(
        select(SettingsVersion).order_by(SettingsVersion.version.desc()).limit(1)
    ).one_or_none()
    if latest is not None and (version <= latest.version or approved_at < latest.approved_at):
        raise OutOfOrderVersion(
            f"version {version} approved {approved_at.isoformat()} does not follow version "
            f"{latest.version} approved {latest.approved_at.isoformat()}"
        )
    row = SettingsVersion(
        version=version,
        values=values.model_dump(mode="json"),
        proposed_by=proposed_by,
        approved_by=approved_by,
        approved_at=approved_at,
    )
    db.add(row)
    db.flush()
    return row
