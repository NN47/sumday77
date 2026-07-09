import unittest
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
            patch("utils.progress_formatters.get_daily_workout_calories", return_value=517.0),
        ):
            text = format_progress_block("1")

        self.assertNotIn("Активность всего:", text)
        self.assertNotIn("Учтено в норме:", text)
        self.assertNotIn("Базовая норма:", text)
        self.assertNotIn("Цель:", text)
        self.assertIn("Скорректированная норма:</b> 2372 ккал", text)
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
        self.assertIn("Скорректированная норма:</b> 2068 ккал", text)


if __name__ == "__main__":
    unittest.main()
