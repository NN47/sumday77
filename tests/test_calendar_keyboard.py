import os

os.environ.setdefault("API_TOKEN", "test-token")

from utils.calendar_utils import build_calendar_keyboard
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
