"""Сервис расчёта КБЖУ с прозрачной логикой (BMR -> TDEE -> цель)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


CALORIES_PER_GRAM_PROTEIN = 4
CALORIES_PER_GRAM_FAT = 9
CALORIES_PER_GRAM_CARBS = 4

PROTEIN_PER_KG_DEFAULT = 1.8
PROTEIN_PER_KG_GAIN = 1.6
FAT_PER_KG = 0.9

DEFAULT_AGE = 30.0
DEFAULT_HEIGHT = 170.0
DEFAULT_WEIGHT = 70.0

ACTIVITY_MULTIPLIERS: dict[str, float] = {
    "minimal": 1.2,
    "low": 1.375,
    "light": 1.375,
    "moderate": 1.55,
    "medium": 1.55,
    "high": 1.725,
    "very_high": 1.9,
}
DEFAULT_ACTIVITY = "medium"

GOAL_ADJUSTMENTS: dict[str, float] = {
    "loss": -0.15,
    "loss_fast": -0.20,
    "maintain": 0.0,
    "gain": 0.10,
}
DEFAULT_GOAL = "maintain"

GOAL_LABELS: dict[str, str] = {
    "loss": "Похудение",
    "loss_fast": "Быстрое похудение",
    "maintain": "Поддержание веса",
    "gain": "Набор массы",
}


@dataclass(frozen=True)
class NutritionProfile:
    """Результат полного расчёта нормы КБЖУ."""

    bmr: int
    tdee: int
    target_calories: int
    proteins: int
    fats: int
    carbs: int
    activity_multiplier: float
    goal: str
    goal_label: str
    adjustment_percent: int


def calculate_bmr(gender: str, age: float, height: float, weight: float) -> float:
    """Считает BMR по формуле Миффлина — Сан Жеора."""
    if gender == "female":
        return 10 * weight + 6.25 * height - 5 * age - 161
    return 10 * weight + 6.25 * height - 5 * age + 5


def get_activity_multiplier(activity: str) -> float:
    """Возвращает коэффициент активности."""
    return ACTIVITY_MULTIPLIERS.get(activity, ACTIVITY_MULTIPLIERS[DEFAULT_ACTIVITY])


def calculate_tdee(bmr: float, activity_multiplier: float) -> float:
    """Считает поддержку (TDEE)."""
    return bmr * activity_multiplier


def get_goal_adjustment(goal: str) -> float:
    """Возвращает поправку к поддержанию для цели."""
    return GOAL_ADJUSTMENTS.get(goal, GOAL_ADJUSTMENTS[DEFAULT_GOAL])


def calculate_target_calories(tdee: float, goal_adjustment: float) -> float:
    """Считает целевую калорийность по цели."""
    return tdee * (1 + goal_adjustment)


def calculate_macros(weight: float, target_calories: float, goal: str) -> tuple[int, int, int]:
    """Считает БЖУ по понятным правилам."""
    protein_per_kg = PROTEIN_PER_KG_GAIN if goal == "gain" else PROTEIN_PER_KG_DEFAULT

    proteins = round(weight * protein_per_kg)
    fats = round(weight * FAT_PER_KG)

    protein_kcal = proteins * CALORIES_PER_GRAM_PROTEIN
    fat_kcal = fats * CALORIES_PER_GRAM_FAT
    remaining_kcal = max(target_calories - protein_kcal - fat_kcal, 0)
    carbs = round(remaining_kcal / CALORIES_PER_GRAM_CARBS)

    return max(proteins, 0), max(fats, 0), max(carbs, 0)


def calculate_nutrition_profile(data: Mapping[str, object]) -> NutritionProfile:
    """Верхнеуровневый расчёт профиля КБЖУ из данных анкеты."""
    gender = str(data.get("gender") or "male")
    age = float(data.get("age") or DEFAULT_AGE)
    height = float(data.get("height") or DEFAULT_HEIGHT)
    weight = float(data.get("weight") or DEFAULT_WEIGHT)
    activity = str(data.get("activity") or DEFAULT_ACTIVITY)
    goal = str(data.get("goal") or DEFAULT_GOAL)

    bmr_value = calculate_bmr(gender=gender, age=age, height=height, weight=weight)
    activity_multiplier = get_activity_multiplier(activity)
    tdee_value = calculate_tdee(bmr=bmr_value, activity_multiplier=activity_multiplier)

    goal_adjustment = get_goal_adjustment(goal)
    target_calories_value = calculate_target_calories(tdee=tdee_value, goal_adjustment=goal_adjustment)

    proteins, fats, carbs = calculate_macros(weight=weight, target_calories=target_calories_value, goal=goal)

    goal_label = GOAL_LABELS.get(goal, GOAL_LABELS[DEFAULT_GOAL])

    return NutritionProfile(
        bmr=round(bmr_value),
        tdee=round(tdee_value),
        target_calories=round(target_calories_value),
        proteins=proteins,
        fats=fats,
        carbs=carbs,
        activity_multiplier=activity_multiplier,
        goal=goal,
        goal_label=goal_label,
        adjustment_percent=round(goal_adjustment * 100),
    )
