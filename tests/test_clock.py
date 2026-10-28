import datetime as dt

from cmbs import clock


def test_frozen_pins_now_and_today_then_releases():
    with clock.frozen(dt.date(2026, 10, 27)):
        assert clock.now() == dt.datetime(2026, 10, 27, tzinfo=dt.UTC)
        assert clock.today() == dt.date(2026, 10, 27)
    assert clock.now().year >= 2026
    assert clock.now() != dt.datetime(2026, 10, 27, tzinfo=dt.UTC)


def test_naive_times_are_read_as_utc_and_aware_times_converted():
    clock.set_now(dt.datetime(2026, 10, 27, 9, 0))
    assert clock.now() == dt.datetime(2026, 10, 27, 9, 0, tzinfo=dt.UTC)
    eastern = dt.timezone(dt.timedelta(hours=-4))
    clock.set_now(dt.datetime(2026, 10, 27, 9, 0, tzinfo=eastern))
    assert clock.now() == dt.datetime(2026, 10, 27, 13, 0, tzinfo=dt.UTC)


def test_set_now_none_releases_the_pin():
    clock.set_now(dt.date(2020, 1, 1))
    clock.set_now(None)
    assert clock.today() > dt.date(2020, 1, 1)
