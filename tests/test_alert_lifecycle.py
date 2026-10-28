"""Alert states and the changes each allows: pure, exact messages."""

import datetime as dt

import pytest

from cmbs.alerts.lifecycle import (
    OPEN_STATES,
    AlertStateRefused,
    State,
    Status,
    acknowledge_problem,
    assign_problem,
    close_problem,
    state_text,
)

NEW = Status(State.NEW)
ACKNOWLEDGED = Status(State.ACKNOWLEDGED, "pat", dt.date(2026, 9, 30))
ASSIGNED = Status(State.ASSIGNED, "pat", dt.date(2026, 9, 30), assigned_to="alex")
CLOSED = Status(State.CLOSED, "pat", dt.date(2026, 9, 30), assigned_to="alex", closed_by="alex")
CLOSED_MESSAGE = "the alert was closed by alex, and a closed alert never reopens"


@pytest.mark.parametrize(
    ("status", "text"),
    [
        (NEW, "new"),
        (ACKNOWLEDGED, "acknowledged by pat on 2026-09-30"),
        (Status(State.ACKNOWLEDGED, "pat"), "acknowledged by pat on an unknown day"),
        (ASSIGNED, "assigned to alex"),
        (CLOSED, "closed by alex"),
    ],
)
def test_state_text(status, text):
    assert state_text(status) == text


def test_open_states_are_all_but_closed():
    assert {State.NEW, State.ACKNOWLEDGED, State.ASSIGNED} == OPEN_STATES


def _message(problem: AlertStateRefused | None) -> str | None:
    return None if problem is None else str(problem)


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (NEW, None),
        (ACKNOWLEDGED, "the alert is already acknowledged by pat on 2026-09-30"),
        (ASSIGNED, "the alert is already assigned to alex"),
        (CLOSED, CLOSED_MESSAGE),
    ],
)
def test_only_a_new_alert_is_acknowledged(status, message):
    assert _message(acknowledge_problem(status)) == message


@pytest.mark.parametrize(
    ("status", "to", "message"),
    [
        (NEW, "alex", None),
        (ACKNOWLEDGED, "alex", None),
        (ASSIGNED, "morgan", None),
        (ASSIGNED, "alex", "the alert is already assigned to alex"),
        (CLOSED, "morgan", CLOSED_MESSAGE),
        (CLOSED, "alex", CLOSED_MESSAGE),
    ],
)
def test_any_open_alert_is_assigned_except_to_whoever_has_it(status, to, message):
    assert _message(assign_problem(status, to)) == message


@pytest.mark.parametrize(
    ("status", "note", "message"),
    [
        (NEW, "duplicate of last month", None),
        (ACKNOWLEDGED, "resolved", None),
        (ASSIGNED, "resolved", None),
        (ASSIGNED, "  ", "closing an alert needs a note saying why"),
        (CLOSED, "again", CLOSED_MESSAGE),
        (CLOSED, "", CLOSED_MESSAGE),
    ],
)
def test_any_open_alert_is_closed_with_a_note(status, note, message):
    assert _message(close_problem(status, note)) == message


def test_problems_are_refusals():
    problem = close_problem(CLOSED, "again")
    assert isinstance(problem, AlertStateRefused)
