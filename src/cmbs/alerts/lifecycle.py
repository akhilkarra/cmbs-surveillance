"""An alert's shared state, and which changes are allowed from each. Pure: no database.

    new --acknowledge--> acknowledged --assign--> assigned --close (with a note)--> closed
     |                        |                    ^   |
     |                        +--------------------+   +--reassign to someone else
     +--assign / close--> ...

An alert can be assigned or closed without first being acknowledged, reassigned to someone
else while assigned, and closed from any open state. A closed alert never reopens; the next
month's change is a new alert (grill log Q44). Who may act is the role matrix's business
(`cmbs.access.roles`); this module only knows the states.
"""

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum

from cmbs.errors import Refused


class State(StrEnum):
    NEW = "new"
    ACKNOWLEDGED = "acknowledged"
    ASSIGNED = "assigned"
    CLOSED = "closed"


OPEN_STATES = frozenset({State.NEW, State.ACKNOWLEDGED, State.ASSIGNED})


class AlertStateRefused(Refused):
    """A change the alert's current state does not allow."""


@dataclass(frozen=True)
class Status:
    state: State
    acknowledged_by: str | None = None
    acknowledged_on: dt.date | None = None
    assigned_to: str | None = None
    closed_by: str | None = None


def state_text(status: Status) -> str:
    """'new', 'acknowledged by pat on 2026-09-30', 'assigned to alex' or 'closed by alex'."""
    match status.state:
        case State.ACKNOWLEDGED:
            on = status.acknowledged_on.isoformat() if status.acknowledged_on else "an unknown day"
            return f"acknowledged by {status.acknowledged_by} on {on}"
        case State.ASSIGNED:
            return f"assigned to {status.assigned_to}"
        case State.CLOSED:
            return f"closed by {status.closed_by}"
        case _:
            return "new"


def _closed(status: Status) -> AlertStateRefused | None:
    if status.state is State.CLOSED:
        return AlertStateRefused(
            f"the alert was {state_text(status)}, and a closed alert never reopens"
        )
    return None


def acknowledge_problem(status: Status) -> AlertStateRefused | None:
    """Only a new alert is acknowledged."""
    if status.state is State.NEW:
        return None
    return _closed(status) or AlertStateRefused(f"the alert is already {state_text(status)}")


def assign_problem(status: Status, to: str) -> AlertStateRefused | None:
    """Any open alert may be assigned, except to the person who already has it."""
    if status.state is State.ASSIGNED and status.assigned_to == to:
        return AlertStateRefused(f"the alert is already {state_text(status)}")
    return _closed(status)


def close_problem(status: Status, note: str) -> AlertStateRefused | None:
    """Any open alert may be closed, with a note saying why."""
    closed = _closed(status)
    if closed is not None:
        return closed
    if not note.strip():
        return AlertStateRefused("closing an alert needs a note saying why")
    return None
