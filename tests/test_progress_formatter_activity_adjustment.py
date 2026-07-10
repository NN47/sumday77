import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from utils.progress_formatters import format_progress_block


class ProgressFormatterActivityAdjustmentTests(unittest.TestCase):
    def test_uses_counted_activity_for_corrected_goal_without_technical_breakdown(self):
        settings = SimpleNamespace(
            calories=2000.0,
            protein=120.0,
            fat=70.0,
            carbs=240.0,
            goal="maintain",
            activity="low",
        )

        selected_date = date(2026, 7, 10)
        with (
            patch("utils.progress_formatters.MealRepository.get_kbju_settings", return_value=settings),
            patch(
                "utils.progress_formatters.MealRepository.get_daily_totals",
                return_value={
                    "calories": 1500.0,
                    "protein_g": 100.0,
                    "fat_total_g": 50.0,
                    "carbohydrates_total_g": 180.0,
                },
            ) as get_totals,
            patch("utils.progress_formatters.get_daily_workout_calories", return_value=517.0) as get_activity,
        ):
            text = format_progress_block("1", selected_date)

        get_totals.assert_called_once_with("1", selected_date)
        get_activity.assert_called_once_with("1", selected_date)
        self.assertNotIn("Активность всего:", text)
        self.assertNotIn("Учтено в норме:", text)
        self.assertNotIn("Базовая норма:", text)
        self.assertNotIn("Цель:", text)
        self.assertNotIn("Скорректированная норма", text)
        self.assertIn("🍽 <b>Съедено:</b> 1500 ккал", text)
        self.assertIn("🎯 <b>Осталось:</b> 872 ккал", text)
        self.assertIn("🔥 <b>Сожжено:</b> 517 ккал", text)
        self.assertNotIn("⚠️ <b>Превышение:</b>", text)
        self.assertIn("🔥 <b>Калории</b>: 1500/2372 ккал (63%)", text)
        self.assertIn("💪 <b>Белки</b>: 100/142 г (70%)", text)
        self.assertIn("🥑 <b>Жиры</b>: 50/83 г (60%)", text)
        self.assertIn("🍩 <b>Углеводы</b>: 180/285 г (63%)", text)

    def test_unknown_activity_defaults_to_medium_coefficient(self):
        settings = SimpleNamespace(
            calories=2000.0,
            protein=120.0,
            fat=70.0,
            carbs=240.0,
            goal="maintain",
            activity="unexpected",
        )

        with (
            patch("utils.progress_formatters.MealRepository.get_kbju_settings", return_value=settings),
            patch(
                "utils.progress_formatters.MealRepository.get_daily_totals",
                return_value={
                    "calories": 1500.0,
                    "protein_g": 100.0,
                    "fat_total_g": 50.0,
                    "carbohydrates_total_g": 180.0,
                },
            ),
            patch("utils.progress_formatters.get_daily_workout_calories", return_value=100.0),
        ):
            text = format_progress_block("1")

        self.assertNotIn("Учтено в норме:", text)
        self.assertIn("🎯 <b>Осталось:</b> 568 ккал", text)
        self.assertIn("🔥 <b>Калории</b>: 1500/2068 ккал (73%)", text)

    def test_shows_zero_left_and_overage_when_eaten_exceeds_adjusted_goal(self):
        settings = SimpleNamespace(
            calories=2000.0,
            protein=120.0,
            fat=70.0,
            carbs=240.0,
            goal="maintain",
            activity="medium",
        )

        with (
            patch("utils.progress_formatters.MealRepository.get_kbju_settings", return_value=settings),
            patch(
                "utils.progress_formatters.MealRepository.get_daily_totals",
                return_value={
                    "calories": 2113.0,
                    "protein_g": 0.0,
                    "fat_total_g": 0.0,
                    "carbohydrates_total_g": 0.0,
                },
            ),
            patch("utils.progress_formatters.get_daily_workout_calories", return_value=0.0),
        ):
            text = format_progress_block("1")

        self.assertIn("🎯 <b>Осталось:</b> 0 ккал", text)
        self.assertIn("⚠️ <b>Превышение:</b> 113 ккал", text)


if __name__ == "__main__":
    unittest.main()
