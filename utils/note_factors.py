"""Фиксированный справочник структурированных факторов дневной заметки."""
from __future__ import annotations

from collections.abc import Iterable


# Внутренние ключи стабильны и не зависят от отображаемого текста кнопок.
NOTE_FACTOR_OPTIONS: tuple[tuple[str, str], ...] = (
    ("energy", "⚡ Много энергии"),
    ("tired", "😴 Усталость"),
    ("bad_sleep", "💤 Недосып"),
    ("good_sleep", "😌 Хороший сон"),
    ("hunger", "🍽 Голод"),
    ("overeating", "🍔 Переедание"),
    ("workout", "🏋️ Тренировка"),
    ("high_activity", "🚶 Много активности"),
    ("low_activity", "🛋 Мало активности"),
    ("rest", "🧘 Отдых"),
    ("stress", "😵‍💫 Стресс"),
    ("good_mood", "😊 Хорошее настроение"),
    ("bad_mood", "😔 Плохое настроение"),
    ("productive", "🔥 Продуктивный день"),
)

NOTE_FACTOR_LABELS = dict(NOTE_FACTOR_OPTIONS)
ALLOWED_NOTE_FACTORS = frozenset(NOTE_FACTOR_LABELS)
ALLOWED_NOTE_RATINGS = frozenset(range(1, 6))
NOTE_FACTOR_CALLBACK_PREFIX = "note_factor:"


def sanitize_note_factors(factors: Iterable[object] | None) -> list[str]:
    """Оставляет только уникальные разрешённые ключи в исходном порядке."""
    sanitized: list[str] = []
    for value in factors or ():
        key = str(value)
        if key in ALLOWED_NOTE_FACTORS and key not in sanitized:
            sanitized.append(key)
    return sanitized


def normalize_note_rating(value: object) -> int | None:
    """Возвращает допустимую внутреннюю оценку дня или ``None``."""
    try:
        rating = int(value)
    except (TypeError, ValueError):
        return None
    return rating if rating in ALLOWED_NOTE_RATINGS else None


def parse_note_factor_callback(callback_data: str | None) -> str | None:
    """Извлекает разрешённый ключ из callback data без доверия клиенту."""
    if not callback_data or not callback_data.startswith(NOTE_FACTOR_CALLBACK_PREFIX):
        return None
    key = callback_data.removeprefix(NOTE_FACTOR_CALLBACK_PREFIX)
    return key if key in ALLOWED_NOTE_FACTORS else None
