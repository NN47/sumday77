import asyncio
import unittest
from datetime import date, datetime
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


class ActivityAnalysisGigaChatPromptTests(unittest.TestCase):
    def test_gigachat_prompt_includes_weight_notes_supplements_and_activity_adjustment(self):
        target_date = date(2026, 4, 8)

        def fake_note_for_date(_user_id, current_date):
            if current_date == target_date:
                return SimpleNamespace(
                    date=target_date,
                    day_rating=4,
                    factors=["stress", "sleep"],
                    text="Был сильный вечерний аппетит и усталость",
                )
            return None

        supplements = [
            {
                "name": "Омега-3",
                "history": [{"timestamp": datetime(2026, 4, 8, 9, 0), "amount": None}],
            }
        ]

        supplement_entries_for_day = [
            {"supplement_name": "Омега-3", "time_text": "09:00"},
            {"supplement_name": "Магний", "time_text": "22:00"},
        ]

        weights = [
            SimpleNamespace(date=target_date, value="78.4"),
            SimpleNamespace(date=date(2026, 4, 7), value="78.9"),
            SimpleNamespace(date=date(2026, 4, 3), value="79.3"),
        ]

        captured = {}

        def fake_gigachat(prompt):
            captured["prompt"] = prompt
            return (
                "<b>🏋️ Тренировки</b>\n"
                "• Тип дня: активность без тренировки.\n\n"
                "<b>🍽️ Питание</b>\n"
                "• Калории умеренно выше цели.\n\n"
                "<b>⚖️ Вес</b>\n"
                "• Есть снижение относительно прошлого замера.\n\n"
                "<b>📈 Гипотеза</b>\n"
                "Если удерживать калории ближе к цели и закрывать белок, то самочувствие станет стабильнее, "
                "потому что снизятся вечерние колебания аппетита.\n\n"
                "<b>Краткий вывод</b>\n"
                "День в рабочем диапазоне.\n\n"
                "<b>План на завтра</b>\n"
                "1. Удержать калории в диапазоне."
            )

        with (
            patch("database.repositories.WorkoutRepository.get_workouts_for_period", return_value=[]),
            patch("database.repositories.WorkoutRepository.get_workouts_for_day", return_value=[]),
            patch("database.repositories.MealRepository.get_kbju_settings", return_value=SimpleNamespace(
                goal="lose", gender="female", calories=1900, protein=130, fat=60, carbs=200
            )),
            patch(
                "database.repositories.MealRepository.get_meals_for_date",
                return_value=[SimpleNamespace(calories=2200, protein=120, fat=80, carbs=210)],
            ),
            patch("database.repositories.WeightRepository.get_weights_for_date_range", return_value=weights),
            patch("database.repositories.WaterRepository.get_daily_total", return_value=1200),
            patch("database.repositories.SupplementRepository.get_supplements", return_value=supplements),
            patch(
                "database.repositories.SupplementRepository.get_entries_for_day",
                return_value=supplement_entries_for_day,
            ),
            patch("database.repositories.ProcedureRepository.get_procedures_for_day", return_value=[]),
            patch("database.repositories.WellbeingRepository.get_entries_for_period", return_value=[]),
            patch("database.repositories.NoteRepository.get_note_for_date", side_effect=fake_note_for_date),
            patch("handlers.activity.gigachat_service", new=SimpleNamespace(analyze_activity_prompt=fake_gigachat)),
        ):
            asyncio.run(
                generate_activity_analysis("123", target_date, target_date, "за день", backend="gigachat")
            )

        prompt = captured["prompt"]
        self.assertIn("анализируй динамику", prompt.lower())
        self.assertIn("С учётом расхода на тренировке", prompt)
        self.assertIn("📝 Заметки дня", prompt)
        self.assertIn("💊 Добавки", prompt)
        self.assertIn("Был сильный вечерний аппетит и усталость", prompt)
