"""Валидаторы для ввода пользователя."""
from datetime import datetime
import re


def validate_date(date_str: str) -> bool:
    """Проверяет формат даты DD.MM.YYYY."""
    pattern = r"^\d{2}\.\d{2}\.\d{4}$"
    if not re.match(pattern, date_str):
        return False
    try:
        datetime.strptime(date_str, "%d.%m.%Y")
        return True
    except ValueError:
        return False


def validate_weight(weight_str: str) -> bool:
    """Проверяет формат веса (число с точкой или запятой)."""
    pattern = r"^\d+([.,]\d+)?$"
    return bool(re.match(pattern, weight_str))


def parse_weight(weight_str: str) -> float | None:
    """Парсит строку веса в число."""
    try:
        return float(weight_str.replace(",", "."))
    except (ValueError, AttributeError):
        return None


def parse_date(date_str: str) -> datetime | None:
    """Парсит строку даты в datetime."""
    try:
        return datetime.strptime(date_str, "%d.%m.%Y")
    except ValueError:
        return None

