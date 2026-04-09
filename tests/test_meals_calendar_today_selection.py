import asyncio
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from handlers import meals


def _build_callback(callback_data: str):
    callback = SimpleNamespace()
    callback.data = callback_data
    callback.from_user = SimpleNamespace(id=12345)
    callback.message = SimpleNamespace()
    callback.answer = AsyncMock()
    return callback


def test_selecting_today_opens_today_report():
    today = date.today().isoformat()
    callback = _build_callback(f"meal_cal_day:{today}")

    with (
        patch("handlers.meals.send_today_results", new=AsyncMock()) as send_today,
        patch("handlers.meals.show_day_meals", new=AsyncMock()) as show_day,
    ):
        asyncio.run(meals.select_kbju_calendar_day(callback))

    callback.answer.assert_awaited_once()
    send_today.assert_awaited_once_with(callback.message, str(callback.from_user.id))
    show_day.assert_not_awaited()


def test_selecting_non_today_opens_selected_day():
    other_day = (date.today() - timedelta(days=1)).isoformat()
    callback = _build_callback(f"meal_cal_day:{other_day}")

    with (
        patch("handlers.meals.send_today_results", new=AsyncMock()) as send_today,
        patch("handlers.meals.show_day_meals", new=AsyncMock()) as show_day,
    ):
        asyncio.run(meals.select_kbju_calendar_day(callback))

    callback.answer.assert_awaited_once()
    send_today.assert_not_awaited()
    show_day.assert_awaited_once_with(
        callback.message,
        str(callback.from_user.id),
        date.fromisoformat(other_day),
    )
