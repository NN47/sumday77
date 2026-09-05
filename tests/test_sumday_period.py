from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from time_utils import (
    MOSCOW_TZ,
    next_sumday_reset,
    sumday_period_key,
    sumday_period_start_utc,
)


def test_sumday_period_changes_exactly_at_03_msk():
    assert sumday_period_key(
        datetime(2026, 9, 5, 2, 59, 59, tzinfo=MOSCOW_TZ)
    ) == date(2026, 9, 4)
    assert sumday_period_key(
        datetime(2026, 9, 5, 3, 0, 0, tzinfo=MOSCOW_TZ)
    ) == date(2026, 9, 5)


def test_sumday_period_handles_month_and_year_boundaries():
    assert sumday_period_key(
        datetime(2027, 1, 1, 2, 59, tzinfo=MOSCOW_TZ)
    ) == date(2026, 12, 31)
    assert sumday_period_key(
        datetime(2026, 3, 1, 2, 59, tzinfo=MOSCOW_TZ)
    ) == date(2026, 2, 28)


def test_sumday_period_is_independent_of_input_timezone():
    utc_instant = datetime(2026, 9, 5, 0, 0, tzinfo=timezone.utc)
    tokyo_instant = utc_instant.astimezone(ZoneInfo("Asia/Tokyo"))

    assert sumday_period_key(utc_instant) == date(2026, 9, 5)
    assert sumday_period_key(tokyo_instant) == date(2026, 9, 5)


def test_sumday_start_and_next_reset_are_the_same_utc_boundary():
    before = datetime(2026, 9, 5, 2, 59, 59, tzinfo=MOSCOW_TZ)
    after = datetime(2026, 9, 5, 3, 0, 0, tzinfo=MOSCOW_TZ)

    assert next_sumday_reset(before) == after
    assert sumday_period_start_utc(after) == datetime(2026, 9, 5, 0, 0, tzinfo=timezone.utc)
