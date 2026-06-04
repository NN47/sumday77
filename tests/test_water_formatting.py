import os
import sys
from pathlib import Path

os.environ.setdefault("API_TOKEN", "test-token")

ROOT_PATH = Path(__file__).resolve().parents[1]
if str(ROOT_PATH) not in sys.path:
    sys.path.insert(0, str(ROOT_PATH))

from handlers.water import build_water_added_text
from utils.keyboards import water_quick_add_inline


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

    assert [button.text for button in row] == ["+250", "+300", "+500", "-300"]
    assert [button.callback_data for button in row] == [
        "quick_water_add_250",
        "quick_water_add_300",
        "quick_water_add_500",
        "quick_water_add_-300",
    ]
