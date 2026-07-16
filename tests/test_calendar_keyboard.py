import os
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ.setdefault("API_TOKEN", "test-token")

from utils.calendar_utils import build_calendar_keyboard, show_calendar_back_button
from utils.keyboards import calendar_back_menu


def _button_texts(markup):
    return [button.text for row in markup.inline_keyboard for button in row]


def test_calendar_keyboard_has_month_navigation_without_inline_close():
    keyboard = build_calendar_keyboard("user", 2026, 6)

    texts = _button_texts(keyboard)
    nav_row = keyboard.inline_keyboard[-1]

    assert "Закрыть" not in texts
    assert [button.text for button in nav_row] == ["◀️", "▶️"]
    assert nav_row[0].callback_data == "cal_nav:2026-05"
    assert nav_row[1].callback_data == "cal_nav:2026-07"


def test_calendar_bottom_keyboard_contains_only_back_button():
    assert len(calendar_back_menu.keyboard) == 1
    assert len(calendar_back_menu.keyboard[0]) == 1
    assert calendar_back_menu.keyboard[0][0].text == "⬅️ Назад"


def test_show_calendar_back_button_pushes_calendar_menu_to_stack():
    bot = SimpleNamespace(menu_stack=[])
    message = SimpleNamespace(bot=bot, answer=AsyncMock())

    asyncio.run(show_calendar_back_button(message))

    assert bot.menu_stack[-1] is calendar_back_menu
    message.answer.assert_awaited_once()
    assert message.answer.await_args.kwargs["reply_markup"] is calendar_back_menu


def test_activity_analysis_calendar_marks_days_with_brain(monkeypatch):
    from utils.calendar_utils import build_activity_analysis_calendar_keyboard

    monkeypatch.setattr(
        "utils.calendar_utils.get_month_activity_analysis_days",
        lambda user_id, year, month: {15},
    )

    keyboard = build_activity_analysis_calendar_keyboard("user", 2026, 7)
    texts = _button_texts(keyboard)

    assert "15🧠" in texts
    assert "15AI" not in texts


def test_activity_analysis_day_add_button_starts_detailed_analysis():
    from datetime import date
    from utils.calendar_utils import build_activity_analysis_day_actions_keyboard

    keyboard = build_activity_analysis_day_actions_keyboard([], date(2026, 7, 15))
    add_button = keyboard.inline_keyboard[0][0]

    assert add_button.text == "🧠 Подробный AI-анализ"
    assert add_button.callback_data == "act_cal_add:2026-07-15"
