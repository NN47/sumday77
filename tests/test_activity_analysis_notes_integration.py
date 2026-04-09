import asyncio
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from handlers.activity import generate_activity_analysis


class ActivityAnalysisNotesIntegrationTests(unittest.TestCase):
    def test_daily_analysis_prompt_includes_day_notes(self):
        target_date = date(2026, 4, 8)

        def fake_note_for_date(_user_id, current_date):
            if current_date == target_date:
                return SimpleNamespace(
                    date=target_date,
                    day_rating=4,
                    factors=["stress", "workout"],
                    text="Было тяжело держать питание вечером",
                )
            return None

        with (
            patch("database.repositories.WorkoutRepository.get_workouts_for_period", return_value=[]),
            patch("database.repositories.WorkoutRepository.get_workouts_for_day", return_value=[]),
            patch("database.repositories.MealRepository.get_kbju_settings", return_value=None),
            patch("database.repositories.MealRepository.get_meals_for_date", return_value=[]),
            patch("database.repositories.WeightRepository.get_weights_for_date_range", return_value=[]),
            patch("database.repositories.WaterRepository.get_daily_total", return_value=0),
            patch("database.repositories.SupplementRepository.get_supplements", return_value=[]),
            patch("database.repositories.ProcedureRepository.get_procedures_for_day", return_value=[]),
            patch("database.repositories.WellbeingRepository.get_entries_for_period", return_value=[]),
            patch("database.repositories.NoteRepository.get_note_for_date", side_effect=fake_note_for_date),
            patch("handlers.activity.gemini_service", new=SimpleNamespace(analyze=lambda prompt: prompt)),
        ):
            result = asyncio.run(
                generate_activity_analysis("123", target_date, target_date, "за день")
            )

        self.assertIn("Заметки дня", result)
        self.assertIn("Оценка дня: 4/5", result)
        self.assertIn("Факторы: stress, workout", result)
        self.assertIn("Было тяжело держать питание вечером", result)


if __name__ == "__main__":
    unittest.main()
