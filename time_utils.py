"""Единые утилиты UTC/МСК и бизнес-суток Sumday77."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

MOSCOW_TZ = ZoneInfo("Europe/Moscow")
UTC_TZ = ZoneInfo("UTC")
SUMDAY_DAILY_RESET_HOUR_MSK = 3


def ensure_utc(dt: datetime) -> datetime:
    """Возвращает datetime с UTC tzinfo, считая naive-время как UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC_TZ)
    return dt.astimezone(UTC_TZ)


def to_moscow(dt: datetime | None) -> datetime | None:
    """Переводит datetime в МСК, считая naive-время как UTC."""
    if dt is None:
        return None
    return ensure_utc(dt).astimezone(MOSCOW_TZ)


def now_moscow() -> datetime:
    return datetime.now(MOSCOW_TZ)


def _as_moscow(dt: datetime | None = None) -> datetime:
    moment = dt or now_moscow()
    if moment.tzinfo is None:
        return moment.replace(tzinfo=MOSCOW_TZ)
    return moment.astimezone(MOSCOW_TZ)


def sumday_period_key(now: datetime | None = None) -> date:
    """Дата суток Sumday77: 03:00–02:59:59 МСК независимо от TZ сервера."""
    return (_as_moscow(now) - timedelta(hours=SUMDAY_DAILY_RESET_HOUR_MSK)).date()


def next_sumday_reset(now: datetime | None = None) -> datetime:
    """Ближайшая граница новых суток Sumday77 (03:00 МСК)."""
    moment = _as_moscow(now)
    reset = moment.replace(
        hour=SUMDAY_DAILY_RESET_HOUR_MSK,
        minute=0,
        second=0,
        microsecond=0,
    )
    return reset + timedelta(days=1) if moment >= reset else reset


def sumday_period_start_utc(now: datetime | None = None) -> datetime:
    """Начало текущих суток Sumday77 в UTC для запросов к timestamp-логам."""
    period = sumday_period_key(now)
    start_msk = datetime.combine(
        period,
        time(hour=SUMDAY_DAILY_RESET_HOUR_MSK),
        tzinfo=MOSCOW_TZ,
    )
    return start_msk.astimezone(UTC_TZ)
