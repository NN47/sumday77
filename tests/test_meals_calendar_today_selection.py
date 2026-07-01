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


def test_back_from_kbju_calendar_renders_today_report():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from handlers import common
    from utils.keyboards import calendar_back_menu, kbju_menu, main_menu

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=12345),
        bot=SimpleNamespace(menu_stack=[main_menu, kbju_menu, calendar_back_menu]),
        answer=AsyncMock(),
    )
    state = SimpleNamespace(clear=AsyncMock())

    with patch("handlers.meals.send_today_results", new=AsyncMock()) as send_today:
        asyncio.run(common.go_back(message, state))

    send_today.assert_awaited_once_with(message, "12345")
    message.answer.assert_not_awaited()
    assert message.bot.menu_stack == [main_menu, kbju_menu]
