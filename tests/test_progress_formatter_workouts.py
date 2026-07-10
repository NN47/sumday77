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


def test_detailed_today_workouts_shows_total_repetitions_by_exercise():
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

    assert "Всего повторений:\n• Отжимания: 170" in text
    assert "Итого по упражнениям: 6 запись (~98 ккал)" in text


def test_format_today_workouts_block_shows_sup_boarding_duration():
    from types import SimpleNamespace
    from unittest.mock import patch
    from utils.progress_formatters import format_today_workouts_block

    workout = SimpleNamespace(exercise="🏄 Сапбординг", count=80.0, calories=557.0, variant="Минуты")
    with patch("utils.progress_formatters.WorkoutRepository.get_workouts_for_day", return_value=[workout]):
        text = format_today_workouts_block("user-id", include_date=False, include_exercise_details=True)

    assert "• 🏄 Сапбординг: 80 мин (~557 ккал)" in text
