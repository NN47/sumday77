import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("API_TOKEN", "test-token")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from handlers.workouts import _format_today_activity_overview


def workout(exercise, count, calories, variant="Повторения"):
    return SimpleNamespace(exercise=exercise, count=count, calories=calories, variant=variant)


def test_activity_overview_shows_exercise_details_separately_from_steps():
    workouts = [
        workout("Шаги", 5500, 229, "Количество шагов"),
        workout("Отжимания", 30, 12),
        workout("Планка", 2, 7, "минуты"),
        workout("Сгибание рук", 12, 9, "Гантели 10 кг"),
        workout("Пробежка", 20, 30, "минуты"),
    ]

    settings = SimpleNamespace(activity="low")

    with (
        patch("handlers.workouts.WorkoutRepository.get_workouts_for_day", return_value=workouts),
        patch("handlers.workouts.MealRepository.get_kbju_settings", return_value=settings),
    ):
        text = _format_today_activity_overview("user-id")

    assert "🏃 Активность за день" in text
    assert "👣 Шаги: 5 500 (~229 ккал)" in text
    assert "🏃 Активность:" in text
    assert "• Отжимания — 30 раз (~12 ккал)" in text
    assert "• Планка — 2 мин (~7 ккал)" in text
    assert "• Сгибание рук — 12 раз (~9 ккал)" in text
    assert "• Бег — 20 мин (~30 ккал)" in text
    assert "🔥 Всего сожжено: ~287 ккал" in text


def test_activity_overview_shows_sup_boarding_duration_and_calories():
    workouts = [workout("🏄 Сапбординг", 80, 557, "Минуты")]
    settings = SimpleNamespace(activity="low")

    with (
        patch("handlers.workouts.WorkoutRepository.get_workouts_for_day", return_value=workouts),
        patch("handlers.workouts.MealRepository.get_kbju_settings", return_value=settings),
    ):
        text = _format_today_activity_overview("user-id")

    assert "• 🏄 Сапбординг — 80 мин (~557 ккал)" in text


def test_activity_overview_sums_repeated_repetition_sets_with_same_weight():
    workouts = [
        workout("Шаги", 9000, 373, "Количество шагов"),
        workout("Подтягивания", 15, 29),
        workout("Тяга штанги в наклоне", 25, 16, "reps"),
        workout("Тяга штанги в наклоне", 15, 9, "reps"),
        workout("Тяга штанги в наклоне", 20, 12, "reps"),
        workout("Тяга штанги в наклоне", 20, 12, "reps"),
        workout("Тяга штанги в наклоне", 20, 12, "reps"),
    ]
    for entry in workouts[2:]:
        entry.working_weight = 30.0

    with patch("handlers.workouts.WorkoutRepository.get_workouts_for_day", return_value=workouts):
        text = _format_today_activity_overview("user-id")

    assert "• Подтягивания — 15 раз (~29 ккал)" in text
    assert "• Тяга штанги в наклоне — 100 раз, 30 кг (~61 ккал)" in text
    assert text.count("Тяга штанги в наклоне") == 1
    assert "🔥 Всего сожжено: ~463 ккал" in text
