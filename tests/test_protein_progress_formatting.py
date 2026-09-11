from types import SimpleNamespace
from unittest.mock import patch

import pytest

from utils.meal_formatters import format_daily_totals_message
from utils.progress_formatters import (
    PROTEIN_GOAL_REACHED_FILL,
    build_progress_bar,
    format_progress_block,
)


@pytest.mark.parametrize(
    ("current", "expected"),
    [
        (0, "⬜" * 10),
        (50, "🟩" * 5 + "⬜" * 5),
        (99, "🟩" * 10),
        (100, "🟩" * 10),
        (101, "🟩" * 10),
        (150, "🟩" * 10),
        (250, "🟩" * 10),
    ],
)
def test_protein_progress_stays_green_at_and_above_target(current, expected):
    assert build_progress_bar(current, 100, goal_reached_fill=PROTEIN_GOAL_REACHED_FILL) == expected


def test_daily_diary_keeps_protein_green_without_changing_other_macros():
    settings = SimpleNamespace(
        calories=100,
        protein=100,
        fat=100,
        carbs=100,
        goal="maintain",
    )
    text = format_daily_totals_message(
        {
            "calories": 150,
            "protein_g": 150,
            "fat_total_g": 150,
            "carbohydrates_total_g": 101,
        },
        "09.09.2026",
        settings,
    )

    assert "<b>🥩 Белки:</b> 150/100 г (150%)\n" + "🟩" * 10 in text
    assert "<b>🥑 Жиры:</b> 150/100 г (150%)\n" + "🟥" * 10 in text
    assert "<b>🍚 Углеводы:</b> 101/100 г (101%)\n" + "🟩" * 10 in text


def test_shared_dashboard_keeps_protein_green_and_numeric_values():
    settings = SimpleNamespace(
        calories=2000,
        protein=100,
        fat=70,
        carbs=200,
        activity="medium",
    )
    with (
        patch("utils.progress_formatters.MealRepository.get_kbju_settings", return_value=settings),
        patch(
            "utils.progress_formatters.MealRepository.get_daily_totals",
            return_value={
                "calories": 1000,
                "protein_g": 137,
                "fat_total_g": 35,
                "carbohydrates_total_g": 100,
            },
        ),
        patch("utils.progress_formatters.get_daily_workout_calories", return_value=0),
    ):
        text = format_progress_block("1")

    assert "🥩 <b>Белки</b>: 137/100 г (137%)\n" + "🟩" * 10 in text
