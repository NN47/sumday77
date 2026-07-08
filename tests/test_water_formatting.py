import os
import sys
from pathlib import Path

os.environ.setdefault("API_TOKEN", "test-token")

ROOT_PATH = Path(__file__).resolve().parents[1]
if str(ROOT_PATH) not in sys.path:
    sys.path.insert(0, str(ROOT_PATH))

from handlers.water import build_water_added_text
from utils.keyboards import (
    quick_actions_inline,
    steps_confirmation_menu,
    steps_menu,
    water_quick_add_inline,
)


def test_build_water_added_text_bolds_confirmation_and_labels():
    text = build_water_added_text(300, 1850, 2476, "🟦🟦🟦🟦🟦🟦🟦⬜⬜⬜")

    assert text == (
        "<b>✅ Добавил 300 мл воды</b>\n\n"
        "<b>💧 Всего за сегодня</b>: 1850 мл\n"
        "<b>🎯 Норма</b>: 2476 мл\n"
        "<b>📈 Прогресс</b>: 75%\n"
        "🟦🟦🟦🟦🟦🟦🟦⬜⬜⬜"
    )


def test_build_water_added_text_formats_negative_adjustment():
    text = build_water_added_text(-300, 1550, 2476, "🟦🟦🟦🟦🟦🟦⬜⬜⬜⬜")

    assert text == (
        "<b>✅ Убрал 300 мл воды</b>\n\n"
        "<b>💧 Всего за сегодня</b>: 1550 мл\n"
        "<b>🎯 Норма</b>: 2476 мл\n"
        "<b>📈 Прогресс</b>: 63%\n"
        "🟦🟦🟦🟦🟦🟦⬜⬜⬜⬜"
    )


def test_water_quick_add_inline_has_requested_adjustment_buttons():
    row = water_quick_add_inline.inline_keyboard[0]

    assert [button.text for button in row] == ["-300", "+250", "+300", "+500"]
    assert [button.callback_data for button in row] == [
        "quick_water_add_-300",
        "quick_water_add_250",
        "quick_water_add_300",
        "quick_water_add_500",
    ]


def test_steps_menu_numeric_rows_have_four_buttons():
    numeric_rows = [
        row
        for row in steps_menu.keyboard
        if all(button.text.isdigit() for button in row)
    ]

    assert len(numeric_rows) == 10
    assert all(len(row) == 4 for row in numeric_rows)
    assert [button.text for button in numeric_rows[0]] == ["500", "1000", "1500", "2000"]
    assert [button.text for button in numeric_rows[-1]] == ["18500", "19000", "19500", "20000"]


def test_steps_confirmation_menu_has_no_delete_steps_button():
    buttons = [button.text for row in steps_confirmation_menu.keyboard for button in row]

    assert "🗑 Удалить шаги" not in buttons
    assert buttons == ["✅ Сохранить", "✏️ Изменить", "⬅️ Назад"]


def test_quick_actions_water_button_keeps_legacy_callback_supported_by_common_handler():
    water_button = quick_actions_inline.inline_keyboard[0][3]

    assert water_button.text == "💧+300"
    assert water_button.callback_data == "quick_water_300"
