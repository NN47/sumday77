from datetime import date

from utils.keyboards import kbju_menu
from utils.meal_formatters import build_meals_actions_keyboard



def _reply_keyboard_texts(markup) -> list[str]:
    return [button.text for row in markup.keyboard for button in row]


def test_kbju_menu_hides_duplicate_daily_report_button():
    texts = _reply_keyboard_texts(kbju_menu)

    assert "📊 Дневной отчёт" not in texts
    assert "📆 Календарь КБЖУ" in texts



def test_meal_actions_keyboard_uses_supported_callback_prefixes():
    keyboard = build_meals_actions_keyboard(
        meals=[
            type("MealStub", (), {"meal_type": "breakfast"})(),
            type("MealStub", (), {"meal_type": "lunch"})(),
        ],
        target_date=date(2026, 4, 8),
    )

    callback_data = [button.callback_data for row in keyboard.inline_keyboard for button in row]

    assert all(not data.startswith("food:") for data in callback_data)
    assert "add_meal:breakfast:2026-04-08" in callback_data
    assert "edit_meal:breakfast:2026-04-08" in callback_data
    assert "clear_meal:breakfast:2026-04-08" in callback_data
