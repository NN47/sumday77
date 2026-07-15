import os
import sys
from pathlib import Path
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("API_TOKEN", "test-token")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from handlers import workouts
from handlers.workouts import _format_activity_overview, _format_today_activity_overview


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
    assert "• Тяга штанги в наклоне — 25 раз, 30 кг (~16 ккал)" in text
    assert "• Тяга штанги в наклоне — 15 раз, 30 кг (~9 ккал)" in text
    assert text.count("Тяга штанги в наклоне") == 5
    assert "🔥 Всего сожжено: ~463 ккал" in text


def test_finished_workout_report_groups_sets_and_keeps_total_calories():
    workouts = [
        workout("Шаги", 0, 0, "Количество шагов"),
        SimpleNamespace(exercise="Армейский жим с гантелями", count=12, calories=5, variant="reps", working_weight=30.0),
        SimpleNamespace(exercise="Армейский жим с гантелями", count=12, calories=5, variant="reps", working_weight=30.0),
        SimpleNamespace(exercise="Армейский жим с гантелями", count=12, calories=5, variant="reps", working_weight=30.0),
        SimpleNamespace(exercise="Отжимания", count=20, calories=11, variant="Повторения"),
    ]

    with patch("handlers.workouts.WorkoutRepository.get_workouts_for_day", return_value=workouts):
        text, _ = _format_activity_overview("user-id", date.today(), group_workout_sets=True)

    assert "👣 Шаги: <b>0</b> (~0 ккал)" in text
    assert "<b>Армейский жим с гантелями</b>\n• 30 кг × 12\n• 30 кг × 12\n• 30 кг × 12\n≈ 15 ккал" in text
    assert "<b>Отжимания</b>\n• 20 раз\n≈ 11 ккал" in text
    assert "12 раз, 30 кг" not in text
    assert text.count("≈ 15 ккал") == 1
    assert "🔥 <b>Всего сожжено:</b> <b>~26 ккал</b>" in text


def test_finished_workout_report_preserves_mixed_weight_set_order():
    workouts = [
        SimpleNamespace(exercise="Тяга штанги в наклоне", count=10, calories=6, variant="reps", working_weight=60.0),
        SimpleNamespace(exercise="Тяга штанги в наклоне", count=8, calories=5, variant="reps", working_weight=60.0),
        SimpleNamespace(exercise="Тяга штанги в наклоне", count=10, calories=6, variant="reps", working_weight=55.0),
    ]

    with patch("handlers.workouts.WorkoutRepository.get_workouts_for_day", return_value=workouts):
        text, _ = _format_activity_overview("user-id", date.today(), group_workout_sets=True)

    expected = "<b>Тяга штанги в наклоне</b>\n• 60 кг × 10\n• 60 кг × 8\n• 55 кг × 10\n≈ 17 ккал"
    assert expected in text
    assert text.count("<b>") == 4  # exercise name + total label/value


def test_activity_category_menu_uses_new_logical_order():
    rows = [[button.text for button in row] for row in workouts.activity_category_menu.keyboard[:4]]

    assert rows == [
        ["🏃 Кардио"],
        ["💪 Собственный вес"],
        ["🏋️ Свободные веса и тренажёры"],
        ["🏄 Спорт и активный отдых"],
    ]
    assert list(workouts.ACTIVITY_CATEGORIES)[:4] == ["cardio", "bodyweight", "gym", "sport"]
