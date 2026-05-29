"""Константы и утилиты для типов приёма пищи."""
import html
from enum import StrEnum


class MealType(StrEnum):
    """Поддерживаемые типы приёма пищи."""

    BREAKFAST = "breakfast"
    LUNCH = "lunch"
    DINNER = "dinner"
    SNACK = "snack"
    UNKNOWN = "unknown"


MEAL_TYPE_ORDER: list[str] = [
    MealType.BREAKFAST.value,
    MealType.LUNCH.value,
    MealType.DINNER.value,
    MealType.SNACK.value,
]

MEAL_TYPE_LABELS: dict[str, str] = {
    MealType.BREAKFAST.value: "🍳 Завтрак",
    MealType.LUNCH.value: "🍲 Обед",
    MealType.DINNER.value: "🍽 Ужин",
    MealType.SNACK.value: "🍎 Перекус",
    MealType.UNKNOWN.value: "🍎 Перекус",
}


def normalize_meal_type(value: str | None, fallback: str = MealType.SNACK.value) -> str:
    """Нормализует meal_type к поддерживаемому значению."""
    if not value:
        return fallback
    value = str(value).strip().lower()
    if value in {
        MealType.BREAKFAST.value,
        MealType.LUNCH.value,
        MealType.DINNER.value,
        MealType.SNACK.value,
        MealType.UNKNOWN.value,
    }:
        return value
    return fallback


def display_meal_type(value: str | None) -> str:
    """Возвращает отображаемое название типа приёма пищи."""
    normalized = normalize_meal_type(value, fallback=MealType.SNACK.value)
    return MEAL_TYPE_LABELS.get(normalized, MEAL_TYPE_LABELS[MealType.SNACK.value])


def display_meal_type_with_bold_name(value: str | None) -> str:
    """Возвращает название приёма пищи с жирным HTML-выделением текстовой части."""
    label = display_meal_type(value)
    emoji, separator, name = label.partition(" ")
    if not separator:
        return f"<b>{html.escape(label)}</b>"
    return f"{html.escape(emoji)}{separator}<b>{html.escape(name)}</b>"
