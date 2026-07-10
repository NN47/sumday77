import os
import sys
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("API_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.workout_utils import calculate_workout_calories


def test_sup_boarding_calories_use_yazio_like_average_met_formula():
    with patch("utils.workout_utils.WeightRepository.get_last_weight", return_value=76.0):
        calories = calculate_workout_calories("user-id", "🏄 Сапбординг", "Минуты", 80)

    assert calories == 5.5 * 76.0 * (80 / 60)
