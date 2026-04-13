"""Утилиты времени для единой работы с МСК в админке."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

MOSCOW_TZ = ZoneInfo("Europe/Moscow")
UTC_TZ = ZoneInfo("UTC")


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
