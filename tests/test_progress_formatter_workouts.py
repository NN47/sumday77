import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("API_TOKEN", "test-token")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.progress_formatters import format_today_workouts_block


def workout(exercise, count, calories, variant="Повторения"):
    return SimpleNamespace(exercise=exercise, count=count, calories=calories, variant=variant)


def test_detailed_today_workouts_shows_exercise_totals_with_calories():
    workouts = [
        workout("Отжимания", 30, 17.1),
        workout("Отжимания", 30, 17.1),
        workout("Отжимания", 30, 17.1),
        workout("Отжимания", 30, 17.1),
        workout("Отжимания", 30, 17.1),
        workout("Отжимания", 20, 12.4),
    ]

    with patch("utils.progress_formatters.WorkoutRepository.get_workouts_for_day", return_value=workouts):
        text = format_today_workouts_block("user-id", include_date=False, include_exercise_details=True)

    assert "Всего повторений:" not in text
    assert "Итого по упражнениям:" not in text
    assert "📊 <b>Итоги:</b>\n• Отжимания: 170 повторений (~98 ккал)" in text


def test_detailed_today_workouts_totals_follow_first_appearance_order():
    workouts = [
        workout("Отжимания", 15, 9),
        workout("Приседания", 40, 18),
        workout("Отжимания", 10, 6),
        workout("Подтягивания", 12, 18),
        workout("Приседания", 40, 18),
    ]

    with patch("utils.progress_formatters.WorkoutRepository.get_workouts_for_day", return_value=workouts):
        text = format_today_workouts_block("user-id", include_date=False, include_exercise_details=True)

    expected = (
        "📊 <b>Итоги:</b>\n"
        "• Отжимания: 25 повторений (~15 ккал)\n"
        "• Приседания: 80 повторений (~36 ккал)\n"
        "• Подтягивания: 12 повторений (~18 ккал)"
    )
    assert expected in text


def test_format_today_workouts_block_shows_sup_boarding_duration():
    workout = SimpleNamespace(exercise="🏄 Сапбординг", count=80.0, calories=557.0, variant="Минуты")
    with patch("utils.progress_formatters.WorkoutRepository.get_workouts_for_day", return_value=[workout]):
        text = format_today_workouts_block("user-id", include_date=False, include_exercise_details=True)

    assert "• 🏄 Сапбординг: 80 мин (~557 ккал)" in text


def test_detailed_today_workouts_shows_hammer_curl_sets_and_single_weight_total():
    workouts = [
        SimpleNamespace(exercise="Молот на бицепс", count=14, calories=8, variant="reps", working_weight=30.0),
        SimpleNamespace(exercise="Молот на бицепс", count=12, calories=7, variant="reps", working_weight=30.0),
    ]

    with patch("utils.progress_formatters.WorkoutRepository.get_workouts_for_day", return_value=workouts):
        text = format_today_workouts_block("user-id", include_date=False, include_exercise_details=True)

    assert "• Молот на бицепс: 14 × 30 кг (~8 ккал)" in text
    assert "• Молот на бицепс: 12 × 30 кг (~7 ккал)" in text
    assert "• Молот на бицепс: 26 повторений, 30 кг (~15 ккал)" in text


def test_detailed_today_workouts_shows_weight_range_for_mixed_hammer_curl_weights():
    workouts = [
        SimpleNamespace(exercise="Молот на бицепс", count=14, calories=8, variant="reps", working_weight=25.0),
        SimpleNamespace(exercise="Молот на бицепс", count=12, calories=7, variant="reps", working_weight=30.0),
    ]

    with patch("utils.progress_formatters.WorkoutRepository.get_workouts_for_day", return_value=workouts):
        text = format_today_workouts_block("user-id", include_date=False, include_exercise_details=True)

    assert "• Молот на бицепс: 26 повторений, вес 25–30 кг (~15 ккал)" in text
