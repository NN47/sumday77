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
        self.assertIn("Питание: 1 приём, 420 ккал", text)
        self.assertIn("Шаги: 8 240", text)
        self.assertIn("Активность: бег 30 мин", text)
        self.assertIn("Вес: 78,4 кг", text)
        self.assertIn("Заметка дня: добавлена", text)
        self.assertEqual(len(data.snapshot_hash), 64)

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
