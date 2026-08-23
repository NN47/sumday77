"""Совместимые утилиты для старых записей активности.

Новые записи всегда проходят через ``activity_energy_service``. Этот адаптер
оставлен только для чтения старой таблицы ``workouts`` и использует те же MET-
формулы — фиксированных «ккал за повтор» здесь больше нет.
"""
from __future__ import annotations

from typing import Optional

from database.repositories import WeightRepository
from services.activity_energy_service import (
    DEFAULT_WEIGHT_KG,
    calculate_met_energy,
    calculate_steps_energy,
    calculate_workout_energy,
)
from utils.activity_catalog import EXERCISES, TIMED_ACTIVITY_BY_CODE


LEGACY_TIMED_ACTIVITY_CODES = {
    "бег": "running_general",
    "пробежка": "running_general",
    "скакалка": "jump_rope",
    "йога": "yoga",
    "🏄 сапбординг": "sup_boarding",
    "сапбординг": "sup_boarding",
    "велосипед": "cycling_general",
}

LEGACY_EXERCISE_CODES = {
    item.name.casefold().replace("ё", "е"): item.code for item in EXERCISES
}
LEGACY_EXERCISE_CODES.update({
    "пресс": "crunches",
    "становая тяга с утяжелителем": "deadlift",
    "румынская тяга с утяжелителем": "romanian_deadlift",
    "армейский жим с гантелями": "standing_barbell_press",
})


def _key(value: str | None) -> str:
    return (value or "").strip().casefold().replace("ё", "е")


def estimate_met_for_exercise(exercise: str) -> float:
    """Возвращает средний MET из централизованного каталога."""
    timed_code = LEGACY_TIMED_ACTIVITY_CODES.get(_key(exercise))
    if timed_code:
        activity = TIMED_ACTIVITY_BY_CODE[timed_code]
        return activity.intensity_mets.get("moderate", next(iter(activity.intensity_mets.values())))
    return 5.0


def calculate_workout_calories(
    user_id: str,
    exercise: str,
    variant: Optional[str],
    count: int | float,
) -> float:
    """Оценивает gross-калории старой записи без фиктивной цены повтора."""
    weight = WeightRepository.get_last_weight(str(user_id)) or DEFAULT_WEIGHT_KG
    try:
        value = max(float(count or 0), 0.0)
    except (TypeError, ValueError):
        value = 0.0
    if value <= 0:
        return 0.0

    variant_key = _key(variant)
    exercise_key = _key(exercise)
    if variant_key in {"количество шагов", "шаги", "steps"} or exercise_key in {"шаги", "шаги (ходьба)"}:
        return calculate_steps_energy(steps=int(value), weight_kg=weight).gross_calories

    if variant_key in {"сек", "сек.", "секунды", "seconds", "second"}:
        duration_minutes = value / 60.0
    elif variant_key in {"мин", "мин.", "минуты", "minutes", "minute"}:
        duration_minutes = value
    else:
        exercise_code = LEGACY_EXERCISE_CODES.get(exercise_key)
        tempo = next((item.tempo_seconds_per_rep for item in EXERCISES if item.code == exercise_code), 3.0)
        duration_minutes = value * tempo / 60.0

    timed_code = LEGACY_TIMED_ACTIVITY_CODES.get(exercise_key)
    if timed_code:
        activity = TIMED_ACTIVITY_BY_CODE[timed_code]
        met = activity.intensity_mets.get("moderate", next(iter(activity.intensity_mets.values())))
        return calculate_met_energy(
            met=met, weight_kg=weight, duration_minutes=duration_minutes,
        ).gross_calories

    return calculate_workout_energy(
        intensity="moderate", weight_kg=weight,
        duration_seconds=max(int(round(duration_minutes * 60)), 1),
    ).gross_calories


def get_daily_workout_calories(user_id: str, entry_date) -> float:
    """Сумма gross-калорий старых строк; используется только fallback-отчётами."""
    from database.repositories import WorkoutRepository

    total = 0.0
    for workout in WorkoutRepository.get_workouts_for_day(str(user_id), entry_date):
        total += float(workout.calories or 0) or calculate_workout_calories(
            str(user_id), workout.exercise, workout.variant, workout.count,
        )
    return total
