"""The lock that makes work on one deal's alerts take turns.

Builds of a deal's alerts, and whole-loan links that name the deal, take this lock until their
transaction ends, so a link can never land between a build's read of the links and its write
of the alerts. It lives apart from the alerts service so the book can take it without importing
the alerts service, which imports the book.
"""

from sqlalchemy import text
from sqlalchemy.orm import Session

# The advisory-lock space for work on one deal's alerts; the deal's id is the key within it.
BUILD_LOCK_SPACE = 0x0A1E


def lock_builds(db: Session, deal_id: int) -> None:
    """Wait for, then hold until the transaction ends, the deal's alerts lock."""
    db.execute(
        text("SELECT pg_advisory_xact_lock(CAST(:space AS integer), CAST(:deal AS integer))"),
        {"space": BUILD_LOCK_SPACE, "deal": deal_id},
    )
