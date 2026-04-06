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
    "sedentary": 1.2,
    "light": 1.375,
    "moderate": 1.45,
    "active": 1.6,
    # Legacy aliases for previously saved onboarding values
    "minimal": 1.2,
    "low": 1.2,
    "medium": 1.45,
    "high": 1.6,
    "very_high": 1.6,
}
DEFAULT_ACTIVITY = "moderate"

GOAL_MULTIPLIERS: dict[str, float] = {
    "loss": 0.85,
    "maintain": 1.0,
    "gain": 1.15,
}
DEFAULT_GOAL = "maintain"
DEFAULT_GOAL_PERCENT = 15

GOAL_LABELS: dict[str, str] = {
    "loss": "Похудение",
    "maintain": "Поддержание веса",
    "gain": "Набор массы",
}

MAX_ACTIVITY_COUNTED_CALORIES = 800
WORKOUT_CALORIES_COEFFICIENT = 0.90


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
    goal_explanation: str
    goal_percent: int


@dataclass(frozen=True)
class DailyCalorieSummary:
    """Сводка дневного лимита калорий с частичным учётом активности."""

    base_goal: int
    eaten_calories: int
    activity_total: int
    activity_counted: int
    daily_limit: int
    calories_left: int


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


def apply_goal(tdee: float, goal: str, goal_percent: int | None = None) -> float:
    """Применяет цель к TDEE и возвращает целевую калорийность."""
    if goal == "maintain":
        return tdee

    if goal_percent is not None:
        ratio = max(goal_percent, 0) / 100
        if goal == "loss":
            return tdee * (1 - ratio)
        if goal == "gain":
            return tdee * (1 + ratio)

    goal_multiplier = GOAL_MULTIPLIERS.get(goal, GOAL_MULTIPLIERS[DEFAULT_GOAL])
    return tdee * goal_multiplier


def build_goal_explanation(goal: str, goal_percent: int) -> str:
    """Возвращает пояснение, как цель повлияла на калорийность."""
    if goal == "loss":
        return f"Для похудения: −{goal_percent}%"
    if goal == "gain":
        return f"Для набора: +{goal_percent}%"
    return "Для поддержания: без изменений"


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


def get_steps_calories_coefficient(steps: int) -> float:
    """Возвращает коэффициент учёта калорий от шагов в зависимости от количества шагов."""
    if steps < 3000:
        return 0.0
    if steps < 7000:
        return 0.30
    if steps < 12000:
        return 0.50
    return 0.65


def calculate_counted_steps_calories(steps: int, steps_calories: int) -> int:
    """Считает учитываемые калории от шагов с округлением до целого."""
    coefficient = get_steps_calories_coefficient(steps=steps)
    return round(max(steps_calories, 0) * coefficient)


def calculate_counted_workout_calories(workout_calories: int) -> int:
    """Считает учитываемые калории от тренировок с округлением до целого."""
    return round(max(workout_calories, 0) * WORKOUT_CALORIES_COEFFICIENT)


def calculate_daily_calorie_summary(
    *,
    base_goal: int,
    eaten_calories: int,
    steps: int,
    steps_calories: int,
    workout_calories: int,
) -> DailyCalorieSummary:
    """
    Формирует итоговую дневную сводку:
    - activity_total = steps_calories + workout_calories
    - activity_counted = counted_steps_calories + counted_workout_calories (но не более 800 ккал)
    - daily_limit = base_goal + activity_counted
    - calories_left = daily_limit - eaten_calories
    """
    safe_base_goal = round(max(base_goal, 0))
    safe_eaten_calories = round(max(eaten_calories, 0))
    safe_steps_calories = round(max(steps_calories, 0))
    safe_workout_calories = round(max(workout_calories, 0))

    counted_steps_calories = calculate_counted_steps_calories(
        steps=steps,
        steps_calories=safe_steps_calories,
    )
    counted_workout_calories = calculate_counted_workout_calories(
        workout_calories=safe_workout_calories,
    )

    activity_total = safe_steps_calories + safe_workout_calories
    activity_counted = min(
        counted_steps_calories + counted_workout_calories,
        MAX_ACTIVITY_COUNTED_CALORIES,
    )

    daily_limit = safe_base_goal + activity_counted
    calories_left = daily_limit - safe_eaten_calories

    return DailyCalorieSummary(
        base_goal=safe_base_goal,
        eaten_calories=safe_eaten_calories,
        activity_total=activity_total,
        activity_counted=activity_counted,
        daily_limit=daily_limit,
        calories_left=calories_left,
    )


def calculate_nutrition_profile(data: Mapping[str, object]) -> NutritionProfile:
    """Верхнеуровневый расчёт профиля КБЖУ из данных анкеты."""
    gender = str(data.get("gender") or "male")
    age = float(data.get("age") or DEFAULT_AGE)
    height = float(data.get("height") or DEFAULT_HEIGHT)
    weight = float(data.get("weight") or DEFAULT_WEIGHT)
    activity = str(data.get("activity") or DEFAULT_ACTIVITY)
    goal = str(data.get("goal") or DEFAULT_GOAL)
    raw_goal_percent = data.get("goal_percent")
    goal_percent = int(raw_goal_percent) if raw_goal_percent is not None else DEFAULT_GOAL_PERCENT

    bmr_value = calculate_bmr(gender=gender, age=age, height=height, weight=weight)
    activity_multiplier = get_activity_multiplier(activity)
    tdee_value = calculate_tdee(bmr=bmr_value, activity_multiplier=activity_multiplier)

    target_calories_value = apply_goal(tdee=tdee_value, goal=goal, goal_percent=goal_percent)

    proteins, fats, carbs = calculate_macros(weight=weight, target_calories=target_calories_value, goal=goal)

    goal_label = GOAL_LABELS.get(goal, GOAL_LABELS[DEFAULT_GOAL])
    goal_explanation = build_goal_explanation(goal=goal, goal_percent=goal_percent)

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
        goal_explanation=goal_explanation,
        goal_percent=goal_percent if goal in {"loss", "gain"} else 0,
    )
