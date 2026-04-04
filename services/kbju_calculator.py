"""Сервис-обёртка для совместимости старого интерфейса расчёта КБЖУ."""

from typing import Tuple

from services.nutrition_calculator import calculate_nutrition_profile


def calculate_kbju_from_test(data: dict) -> Tuple[float, float, float, float, str]:
    """Возвращает кортеж в прежнем формате для существующих хендлеров."""
    profile = calculate_nutrition_profile(data)
    return (
        float(profile.target_calories),
        float(profile.proteins),
        float(profile.fats),
        float(profile.carbs),
        profile.goal_label,
    )
