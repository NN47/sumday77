"""Утилиты для работы с тренировками."""
import logging
from typing import Optional
from database.repositories import WeightRepository

logger = logging.getLogger(__name__)


def estimate_met_for_exercise(exercise: str) -> float:
    """
    Оценивает MET (Metabolic Equivalent of Task) для упражнения.
    MET - это единица измерения энергетических затрат.
    """
    met_values = {
        # С собственным весом
        "Подтягивания": 8.0,
        "Отжимания": 6.0,
        "Приседания": 5.0,
        "Пресс": 4.0,
        "Берпи": 8.0,
        "Шаги": 3.0,
        "Шаги (Ходьба)": 3.0,
        "Пробежка": 7.0,
        "Скакалка": 10.0,
        "Становая тяга без утяжелителя": 4.0,
        "Румынская тяга без утяжелителя": 4.0,
        "Планка": 3.0,
        "Йога": 2.5,
        
        # С утяжелителем
        "Приседания со штангой": 6.0,
        "Жим штанги лёжа": 5.0,
        "Становая тяга с утяжелителем": 6.0,
        "Румынская тяга с утяжелителем": 5.0,
        "Тяга штанги в наклоне": 5.0,
        "Жим гантелей лёжа": 5.0,
        "Жим гантелей сидя": 4.0,
        "Подъёмы гантелей на бицепс": 4.0,
        "Тяга верхнего блока": 4.0,
        "Тяга нижнего блока": 4.0,
        "Жим ногами": 5.0,
        "Разведения гантелей": 3.0,
        "Тяга горизонтального блока": 4.0,
        "Сгибание ног в тренажёре": 3.0,
        "Разгибание ног в тренажёре": 3.0,
        "Гиперэкстензия с утяжелителем": 4.0,
    }
    
    # Старая логика: только точные соответствия, иначе дефолт 3.0
    return met_values.get(exercise, 3.0)


def calculate_workout_calories(
    user_id: str,
    exercise: str,
    variant: Optional[str],
    count: int,
) -> float:
    """
    Вычисляет примерные калории, сожжённые на тренировке (старая формула).

    Старая формула из проекта:
    калории = MET × вес(кг) × время(часы)

    - Если variant указывает на секунды/минуты — переводим в часы и считаем по времени.
    - Иначе (включая шаги и повторы) — старая грубая оценка по количеству:
      duration_hours = (count / 100) * 0.1  (≈ 0.1 часа на 100 повторений/условных единиц)
    """
    weight = WeightRepository.get_last_weight(user_id) or 70.0
    met = estimate_met_for_exercise(exercise)

    try:
        value = float(count or 0)
    except (TypeError, ValueError):
        value = 0.0

    v = (variant or "").strip().lower()

    # Время: секунды
    if v in {"сек", "сек.", "секунды", "seconds", "second", "seconds."} or (variant == "Секунды"):
        duration_hours = value / 3600.0
        return max(met * weight * duration_hours, 0.0)

    # Время: минуты
    if v in {"мин", "мин.", "минуты", "minutes", "minute", "minutes."} or (variant == "Минуты"):
        duration_hours = value / 60.0
        return max(met * weight * duration_hours, 0.0)

    # Шаги: специальная формула на основе ориентира 16705 шагов = 634 ккал
    if (
        v in {"количество шагов", "шаги", "steps"}
        or (variant == "Количество шагов")
        or (exercise in {"Шаги", "Шаги (Ходьба)"})
    ):
        # Формула: калории = шаги × (634 / 16705) × (вес / 70)
        # 634 / 16705 ≈ 0.03796 ккал на шаг при весе 70 кг
        base_calories_per_step = 634.0 / 16705.0  # ≈ 0.03796
        base_weight = 70.0
        calories = value * base_calories_per_step * (weight / base_weight)
        return max(calories, 0.0)

    # Всё остальное (повторы / иные варианты)
    # Используем калибровку "ккал за повтор" и масштабируем по весу.
    # Это заметно точнее для коротких силовых подходов, чем старая грубая
    # оценка через очень маленькую длительность.
    kcal_per_rep_at_80kg = {
        "Подтягивания": 2.0,
        "Отжимания": 0.6,
        "Приседания": 0.5,
        "Пресс": 0.35,
        "Берпи": 1.2,
        "Становая тяга без утяжелителя": 0.65,
        "Румынская тяга без утяжелителя": 0.6,
        "Приседания со штангой": 0.8,
        "Жим штанги лёжа": 0.6,
        "Становая тяга с утяжелителем": 0.9,
        "Румынская тяга с утяжелителем": 0.8,
        "Тяга штанги в наклоне": 0.65,
        "Жим гантелей лёжа": 0.6,
        "Жим гантелей сидя": 0.6,
        "Подъёмы гантелей на бицепс": 0.45,
        "Тяга верхнего блока": 0.6,
        "Тяга нижнего блока": 0.6,
        "Жим ногами": 0.8,
        "Разведения гантелей": 0.45,
        "Тяга горизонтального блока": 0.6,
        "Сгибание ног в тренажёре": 0.45,
        "Разгибание ног в тренажёре": 0.45,
        "Гиперэкстензия с утяжелителем": 0.55,
    }

    kcal_per_rep = kcal_per_rep_at_80kg.get(exercise)
    if kcal_per_rep is not None:
        return max(value * kcal_per_rep * (weight / 80.0), 0.0)

    # Фолбэк для неизвестных упражнений: MET + умеренная оценка темпа повторов.
    # ~6 секунд на повтор => 100 повторов ≈ 10 минут.
    duration_hours = value * (6.0 / 3600.0)
    return max(met * weight * duration_hours, 0.0)


def get_daily_workout_calories(user_id: str, entry_date) -> float:
    """Получает суммарные калории, сожжённые на тренировках за день."""
    from database.repositories import WorkoutRepository
    
    workouts = WorkoutRepository.get_workouts_for_day(user_id, entry_date)
    total = 0.0
    
    for w in workouts:
        if w.calories:
            total += w.calories
        else:
            total += calculate_workout_calories(user_id, w.exercise, w.variant, w.count)
    
    return total
