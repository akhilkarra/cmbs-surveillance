"""Raise and read check flags."""

import datetime as dt
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cmbs import clock
from cmbs.checks.tables import CheckFlag


def raise_flag(
    db: Session,
    *,
    deal_id: int,
    loan: str,
    check: str,
    message: str,
    period: dt.date | None = None,
    sides: dict[str, Any] | None = None,
) -> CheckFlag:
    flag = CheckFlag(
        deal_id=deal_id,
        loan=loan,
        period=period,
        check=check,
        message=message,
        sides=sides or {},
        raised_at=clock.now(),
    )
    db.add(flag)
    db.flush()
    return flag


def flags_for(db: Session, *, deal_id: int, loan: str) -> Sequence[CheckFlag]:
    query = select(CheckFlag).where(CheckFlag.deal_id == deal_id, CheckFlag.loan == loan)
    return db.scalars(query.order_by(CheckFlag.id)).all()
