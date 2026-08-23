"""Единая расчётная логика физической активности.

Все функции этого модуля чистые: обработчики и форматтеры используют одинаковые
формулы, а сохранённые записи содержат снимок результата и версии расчёта.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable
from datetime import date
from sqlalchemy.exc import OperationalError, ProgrammingError

from utils.activity_catalog import (
    CALCULATION_VERSION,
    EXERCISE_BY_CODE,
    TIMED_ACTIVITY_BY_CODE,
    WORKOUT_INTENSITY_METS,
)


DEFAULT_WEIGHT_KG = 70.0
MIN_WEIGHT_KG = 25.0
MAX_WEIGHT_KG = 350.0
MAX_ACTIVITY_MINUTES = 720.0
MAX_WORKOUT_SECONDS = 6 * 60 * 60
MAX_STEPS = 200_000
REST_BETWEEN_SETS_SECONDS = 60
REST_BETWEEN_EXERCISES_SECONDS = 90
STEPS_KCAL_AT_70_KG = 0.04


class ActivityValidationError(ValueError):
    """Некорректные или физиологически невозможные исходные данные."""


@dataclass(frozen=True)
class EnergyEstimate:
    gross_calories: float
    credited_calories: float
    met_value: float
    weight_kg: float
    duration_minutes: float
    calculation_version: str = CALCULATION_VERSION


@dataclass(frozen=True)
class StepsEstimate:
    steps: int
    gross_calories: float
    credited_calories: float
    weight_kg: float
    calculation_version: str = CALCULATION_VERSION


@dataclass(frozen=True)
class DailyActivityEnergySummary:
    gross_calories: float
    credited_calories: float
    timed_gross_calories: float
    timed_credited_calories: float
    workout_gross_calories: float
    workout_credited_calories: float
    steps_gross_calories: float
    steps_credited_calories: float
    steps: int
    overlapping_steps: int
    residual_steps: int


def _finite_number(value: object, field_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ActivityValidationError(f"{field_name}: нужно указать число") from exc
    if not math.isfinite(number):
        raise ActivityValidationError(f"{field_name}: недопустимое значение")
    return number


def validate_weight(weight_kg: object) -> float:
    weight = _finite_number(weight_kg, "Вес")
    if weight < MIN_WEIGHT_KG or weight > MAX_WEIGHT_KG:
        raise ActivityValidationError(
            f"Вес должен быть от {int(MIN_WEIGHT_KG)} до {int(MAX_WEIGHT_KG)} кг"
        )
    return weight


def validate_duration_minutes(duration_minutes: object) -> float:
    duration = _finite_number(duration_minutes, "Продолжительность")
    if duration <= 0:
        raise ActivityValidationError("Продолжительность должна быть больше нуля")
    if duration > MAX_ACTIVITY_MINUTES:
        raise ActivityValidationError(
            f"Продолжительность не может превышать {int(MAX_ACTIVITY_MINUTES)} минут"
        )
    return duration


def calculate_met_energy(*, met: object, weight_kg: object, duration_minutes: object) -> EnergyEstimate:
    """Считает общий и активный расход по стандартной формуле MET."""
    met_value = _finite_number(met, "MET")
    if met_value < 0 or met_value > 25:
        raise ActivityValidationError("MET должен находиться в диапазоне от 0 до 25")
    weight = validate_weight(weight_kg)
    duration = validate_duration_minutes(duration_minutes)
    gross = met_value * 3.5 * weight / 200.0 * duration
    credited = max(met_value - 1.0, 0.0) * 3.5 * weight / 200.0 * duration
    return EnergyEstimate(
        gross_calories=max(gross, 0.0),
        credited_calories=max(credited, 0.0),
        met_value=met_value,
        weight_kg=weight,
        duration_minutes=duration,
    )


def calculate_timed_activity_energy(
    *, activity_code: str, intensity: str, weight_kg: object, duration_minutes: object
) -> EnergyEstimate:
    activity = TIMED_ACTIVITY_BY_CODE.get(activity_code)
    if activity is None:
        raise ActivityValidationError("Неизвестный вид активности")
    met = activity.intensity_mets.get(intensity)
    if met is None:
        raise ActivityValidationError("Эта интенсивность не поддерживается")
    return calculate_met_energy(met=met, weight_kg=weight_kg, duration_minutes=duration_minutes)


def calculate_workout_energy(
    *, intensity: str, weight_kg: object, duration_seconds: object
) -> EnergyEstimate:
    seconds = _finite_number(duration_seconds, "Продолжительность тренировки")
    if seconds <= 0:
        raise ActivityValidationError("Продолжительность тренировки должна быть больше нуля")
    if seconds > MAX_WORKOUT_SECONDS:
        raise ActivityValidationError("Тренировка не может длиться больше 6 часов")
    met = WORKOUT_INTENSITY_METS.get(intensity)
    if met is None:
        raise ActivityValidationError("Неизвестная интенсивность тренировки")
    return calculate_met_energy(
        met=met,
        weight_kg=weight_kg,
        duration_minutes=seconds / 60.0,
    )


def calculate_steps_energy(*, steps: object, weight_kg: object) -> StepsEstimate:
    raw_steps = _finite_number(steps, "Шаги")
    if not raw_steps.is_integer():
        raise ActivityValidationError("Количество шагов должно быть целым числом")
    step_count = int(raw_steps)
    if step_count < 0:
        raise ActivityValidationError("Количество шагов не может быть отрицательным")
    if step_count > MAX_STEPS:
        raise ActivityValidationError(f"Количество шагов не может превышать {MAX_STEPS:,}".replace(",", " "))
    weight = validate_weight(weight_kg)
    # Это уже оценка активной энергии ходьбы, поэтому она же прибавляется к норме.
    calories = step_count * STEPS_KCAL_AT_70_KG * (weight / 70.0)
    return StepsEstimate(
        steps=step_count,
        gross_calories=max(calories, 0.0),
        credited_calories=max(calories, 0.0),
        weight_kg=weight,
    )


def estimate_workout_duration_seconds(sets: Iterable[object]) -> int:
    """Оценивает длительность ручной тренировки по подходам и переходам."""
    normalized = list(sets)
    if not normalized:
        raise ActivityValidationError("Добавь хотя бы один подход")
    active_seconds = 0.0
    exercise_ids: list[int] = []
    sets_per_exercise: dict[int, int] = {}
    for workout_set in normalized:
        repetitions = getattr(workout_set, "repetitions", None)
        duration = getattr(workout_set, "duration_seconds", None)
        distance = getattr(workout_set, "distance_meters", None)
        exercise_code = getattr(workout_set, "exercise_code", None)
        session_exercise_id = int(getattr(workout_set, "session_exercise_id", 0) or 0)
        exercise_ids.append(session_exercise_id)
        sets_per_exercise[session_exercise_id] = sets_per_exercise.get(session_exercise_id, 0) + 1
        if duration:
            active_seconds += float(duration)
            continue
        if distance:
            # Консервативная скорость функциональной переноски/саней ~1,5 м/с.
            active_seconds += float(distance) / 1.5
            continue
        if repetitions:
            config = EXERCISE_BY_CODE.get(str(exercise_code or ""))
            tempo = config.tempo_seconds_per_rep if config else 3.0
            active_seconds += int(repetitions) * tempo
    rest_seconds = sum(
        max(set_count - 1, 0) * REST_BETWEEN_SETS_SECONDS
        for set_count in sets_per_exercise.values()
    )
    exercise_transitions = max(len(dict.fromkeys(exercise_ids)) - 1, 0)
    total = active_seconds + rest_seconds + exercise_transitions * REST_BETWEEN_EXERCISES_SECONDS
    return max(int(round(total)), 1)


def _estimated_overlapping_steps(timed_entries: Iterable[object]) -> int:
    total = 0.0
    for entry in timed_entries:
        config = TIMED_ACTIVITY_BY_CODE.get(str(getattr(entry, "activity_code", "")))
        if config is None or config.cadence_steps_per_minute is None:
            continue
        duration = max(float(getattr(entry, "duration_minutes", 0) or 0), 0.0)
        total += config.cadence_steps_per_minute * duration
    return max(int(round(total)), 0)


def summarize_daily_activity_energy(
    *, timed_entries: Iterable[object], workout_sessions: Iterable[object], daily_steps: object | None
) -> DailyActivityEnergySummary:
    """Сводит день и вычитает шаги, оценочно вошедшие в ходьбу/бег."""
    timed = list(timed_entries)
    sessions = [session for session in workout_sessions if getattr(session, "status", None) == "completed"]
    timed_gross = sum(max(float(getattr(item, "gross_calories", 0) or 0), 0.0) for item in timed)
    timed_credited = sum(max(float(getattr(item, "credited_calories", 0) or 0), 0.0) for item in timed)
    workout_gross = sum(max(float(getattr(item, "gross_calories", 0) or 0), 0.0) for item in sessions)
    workout_credited = sum(max(float(getattr(item, "credited_calories", 0) or 0), 0.0) for item in sessions)

    steps = int(getattr(daily_steps, "steps", 0) or 0) if daily_steps is not None else 0
    overlap = min(_estimated_overlapping_steps(timed), steps)
    residual = max(steps - overlap, 0)
    if daily_steps is not None and steps > 0:
        share = residual / steps
        steps_gross = max(float(getattr(daily_steps, "gross_calories", 0) or 0), 0.0) * share
        steps_credited = max(float(getattr(daily_steps, "credited_calories", 0) or 0), 0.0) * share
    else:
        steps_gross = 0.0
        steps_credited = 0.0

    return DailyActivityEnergySummary(
        gross_calories=timed_gross + workout_gross + steps_gross,
        credited_calories=timed_credited + workout_credited + steps_credited,
        timed_gross_calories=timed_gross,
        timed_credited_calories=timed_credited,
        workout_gross_calories=workout_gross,
        workout_credited_calories=workout_credited,
        steps_gross_calories=steps_gross,
        steps_credited_calories=steps_credited,
        steps=steps,
        overlapping_steps=overlap,
        residual_steps=residual,
    )


def get_daily_activity_energy_summary(user_id: str, target_date: date) -> DailyActivityEnergySummary:
    """Загружает все новые сущности дня и возвращает согласованную сводку."""
    from database.repositories.activity_repository import ActivityRepository

    try:
        return summarize_daily_activity_energy(
            timed_entries=ActivityRepository.get_timed_activities_for_day(user_id, target_date),
            workout_sessions=ActivityRepository.get_workout_sessions_for_day(user_id, target_date),
            daily_steps=ActivityRepository.get_steps_for_day(user_id, target_date),
        )
    except (OperationalError, ProgrammingError):
        # Безопасное окно rolling deploy: до init_db новая таблица может ещё не
        # существовать. После запуска на новой БД эта ветка не используется.
        return summarize_daily_activity_energy(
            timed_entries=[], workout_sessions=[], daily_steps=None,
        )
