"""The one clock every feature reads, so a scenario can fix "today".

Production code calls `now()` or `today()`, never `datetime.now()`. Tests and scenarios pin the
clock with `frozen(...)` or `set_now(...)`. Times are timezone-aware UTC.
"""

import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_pinned: ContextVar[dt.datetime | None] = ContextVar("cmbs_pinned_now", default=None)


def _as_utc(moment: dt.datetime | dt.date) -> dt.datetime:
    if not isinstance(moment, dt.datetime):
        moment = dt.datetime.combine(moment, dt.time())
    return moment.replace(tzinfo=dt.UTC) if moment.tzinfo is None else moment.astimezone(dt.UTC)


def now() -> dt.datetime:
    pinned = _pinned.get()
    return pinned if pinned is not None else dt.datetime.now(dt.UTC)


def today() -> dt.date:
    return now().date()


def set_now(moment: dt.datetime | dt.date | None) -> None:
    """Pin the clock until it is pinned again; None releases it."""
    _pinned.set(None if moment is None else _as_utc(moment))


@contextmanager
def frozen(moment: dt.datetime | dt.date) -> Iterator[None]:
    token = _pinned.set(_as_utc(moment))
    try:
        yield
    finally:
        _pinned.reset(token)
