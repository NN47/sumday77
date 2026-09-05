from __future__ import annotations

import asyncio
from datetime import date
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault("API_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")

from handlers.activity import handle_daily_analysis_preflight
from services.daily_analysis_preflight_service import (
    build_daily_preflight_keyboard,
    build_daily_preflight_text,
    collect_daily_preflight,
)


TARGET = date(2026, 8, 23)


class DailyAnalysisPreflightSummaryTests(unittest.TestCase):
    def _collect(self, *, meals=None, water=0, workouts=None, weight=None, note=None):
        meals = meals or []
        workouts = workouts or []
        with (
            patch("services.daily_analysis_preflight_service.MealRepository.get_meals_for_date", return_value=meals),
            patch(
                "services.daily_analysis_preflight_service.MealRepository.get_daily_totals",
                return_value={"calories": sum(float(getattr(meal, "calories", 0) or 0) for meal in meals)},
            ),
            patch("services.daily_analysis_preflight_service.WaterRepository.get_daily_total", return_value=water),
            patch("services.daily_analysis_preflight_service.WorkoutRepository.get_workouts_for_day", return_value=workouts),
            patch("services.daily_analysis_preflight_service.WeightRepository.get_weight_for_date", return_value=weight),
            patch("services.daily_analysis_preflight_service.NoteRepository.get_note_for_date", return_value=note),
        ):
            return collect_daily_preflight("123", TARGET)

    def test_empty_day_is_blockable_and_absent_activity_is_neutral(self):
        data = self._collect(water=500)
        text = build_daily_preflight_text(data)
        self.assertTrue(data.is_empty)
        self.assertIn("В дневнике воды: 500 мл", text)
        self.assertIn("Активность: не внесена. Если её не было, ничего добавлять не нужно", text)

    def test_partial_day_is_allowed_and_summary_contains_each_section(self):
        meals = [SimpleNamespace(id=1, meal_type="breakfast", calories=420, protein=20, fat=12, carbs=50)]
        workouts = [
            SimpleNamespace(id=1, exercise="Шаги", count=8240, duration_minutes=None, distance_km=None, jumps_count=None),
            SimpleNamespace(id=2, exercise="Бег", count=30, duration_minutes=30, distance_km=None, jumps_count=None),
        ]
        data = self._collect(
            meals=meals,
            water=1500,
            workouts=workouts,
            weight=SimpleNamespace(value="78.4"),
            note=SimpleNamespace(day_rating=4, factors_json="[]"),
        )
        text = build_daily_preflight_text(data)
        self.assertFalse(data.is_empty)
        self.assertIn("Проверь, всё ли внесено за 23 августа", text)
        self.assertIn("Питание: 420 ккал", text)
        self.assertIn("Шаги: 8 240", text)
        self.assertIn("Активность: бег 30 мин", text)
        self.assertIn("Вес: 78,4 кг", text)
        self.assertIn("Заметка дня: добавлена", text)
        self.assertEqual(len(data.snapshot_hash), 64)

    def test_food_summary_does_not_present_product_rows_as_meals(self):
        meals = [
            SimpleNamespace(
                id=index,
                meal_type="breakfast",
                calories=2014 / 17,
                protein=0,
                fat=0,
                carbs=0,
            )
            for index in range(1, 18)
        ]

        text = build_daily_preflight_text(self._collect(meals=meals))

        self.assertIn("Питание: 2 014 ккал", text)
        self.assertNotIn("17 приём", text)

    def test_completed_workout_shows_exercise_repetitions_instead_of_estimated_duration(self):
        session = SimpleNamespace(
            id=7,
            status="completed",
            duration_seconds=720,
            intensity="moderate",
        )
        exercise = SimpleNamespace(
            id=11,
            exercise_code="parallel_bar_dips",
            exercise_name_snapshot="Отжимания на брусьях",
        )
        workout_sets = [
            SimpleNamespace(
                id=index,
                session_exercise_id=exercise.id,
                repetitions=20,
                duration_seconds=None,
                distance_meters=None,
                load_kg=None,
                load_kind=None,
            )
            for index in range(1, 6)
        ]
        with (
            patch("services.daily_analysis_preflight_service.MealRepository.get_meals_for_date", return_value=[]),
            patch("services.daily_analysis_preflight_service.MealRepository.get_daily_totals", return_value={"calories": 0}),
            patch("services.daily_analysis_preflight_service.WaterRepository.get_daily_total", return_value=0),
            patch("services.daily_analysis_preflight_service.ActivityRepository.get_timed_activities_for_day", return_value=[]),
            patch("services.daily_analysis_preflight_service.ActivityRepository.get_workout_sessions_for_day", return_value=[session]),
            patch("services.daily_analysis_preflight_service.ActivityRepository.get_steps_for_day", return_value=None),
            patch("services.daily_analysis_preflight_service.ActivityRepository.get_session_exercises", return_value=[exercise]),
            patch("services.daily_analysis_preflight_service.ActivityRepository.get_session_sets", return_value=workout_sets),
            patch("services.daily_analysis_preflight_service.WeightRepository.get_weight_for_date", return_value=None),
            patch("services.daily_analysis_preflight_service.NoteRepository.get_note_for_date", return_value=None),
        ):
            data = collect_daily_preflight("123", TARGET)

        text = build_daily_preflight_text(data)
        self.assertIn("Активность: Отжимания на брусьях — 100 раз (5 подходов)", text)
        self.assertNotIn("тренировка 12 мин", text.casefold())
        self.assertFalse(data.is_empty)

    def test_keyboard_has_context_actions_and_one_back_button(self):
        keyboard = build_daily_preflight_keyboard(TARGET)
        labels = [button.text for row in keyboard.inline_keyboard for button in row]
        callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
        self.assertEqual(labels.count("⬅️ Назад"), 1)
        self.assertEqual(
            labels,
            [
                "🍱 Проверить питание",
                "💧 Внести воду",
                "👣 Указать шаги",
                "🏃 Внести активность",
                "📝 Заметка дня",
                "✅ Всё внесено — начать анализ",
                "⬅️ Назад",
            ],
        )
        self.assertTrue(all(TARGET.isoformat() in callback for callback in callbacks))


class DailyAnalysisPreflightNavigationTests(unittest.TestCase):
    def _callback(self, action: str):
        message = SimpleNamespace(bot=SimpleNamespace(), answer=AsyncMock())
        return SimpleNamespace(
            data=f"day_pf:{action}:{TARGET.isoformat()}",
            from_user=SimpleNamespace(id=123),
            message=message,
            answer=AsyncMock(),
        )

    def test_food_water_steps_activity_and_note_routes_keep_target_date(self):
        async def run():
            state = SimpleNamespace(
                clear=AsyncMock(),
                update_data=AsyncMock(),
                set_state=AsyncMock(),
            )
            with (
                patch("handlers.meals.show_day_meals", new=AsyncMock()) as food,
                patch("handlers.water.start_add_water", new=AsyncMock()) as water,
                patch("handlers.activity_tracking.start_steps_flow", new=AsyncMock()) as steps,
                patch("handlers.activity_tracking.start_timed_activity_flow", new=AsyncMock()) as activity,
                patch("handlers.wellbeing.start_note_flow", new=AsyncMock()) as note,
            ):
                for action in ("food", "water", "steps", "activity", "note"):
                    await handle_daily_analysis_preflight(self._callback(action), state)
            food.assert_awaited_once()
            self.assertEqual(food.await_args.args[-1], TARGET)
            water.assert_awaited_once()
            self.assertEqual(water.await_args.kwargs["entry_date"], TARGET)
            steps.assert_awaited_once()
            self.assertEqual(steps.await_args.args[-1], TARGET)
            activity.assert_awaited_once()
            note.assert_awaited_once()
            self.assertEqual(note.await_args.args[2], TARGET)

        asyncio.run(run())

    def test_confirm_is_the_only_route_that_executes_analysis(self):
        async def run():
            callback = self._callback("confirm")
            state = SimpleNamespace(clear=AsyncMock())
            with patch("handlers.activity.run_detailed_activity_analysis", new=AsyncMock()) as execute:
                await handle_daily_analysis_preflight(callback, state)
            execute.assert_awaited_once_with(callback.message, "123", TARGET)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
