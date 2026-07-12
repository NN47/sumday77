import os

os.environ.setdefault("API_TOKEN", "test-token")

from utils.keyboards import (
    ACTIVITY_ANALYSIS_CALENDAR_BUTTON_TEXT,
    ACTIVITY_ANALYSIS_DETAILED_DEEPSEEK_BUTTON_TEXT,
    MAIN_MENU_BUTTON_TEXT,
    kbju_add_menu,
    activity_analysis_menu,
)
from handlers.admin import _admin_menu_kb
from utils.admin_formatters import format_openrouter, format_gigachat
from services.openrouter_service import OpenRouterService


def _reply_keyboard_texts(markup) -> list[str]:
    return [button.text for row in markup.keyboard for button in row]


def _inline_keyboard_texts(markup) -> list[str]:
    return [button.text for row in markup.inline_keyboard for button in row]


def test_kbju_add_menu_hides_openrouter_button():
    texts = _reply_keyboard_texts(kbju_add_menu)
    assert "🧪 Ввести текст через OpenRouter" not in texts


def test_activity_analysis_menu_hides_removed_day_buttons():
    texts = _reply_keyboard_texts(activity_analysis_menu)
    assert "🪄 ИИ-разбор дня" not in texts
    assert "📅 Сегодня через GigaChat" not in texts


def test_activity_analysis_menu_only_keeps_detailed_calendar_and_main_menu():
    texts = _reply_keyboard_texts(activity_analysis_menu)
    assert texts == [
        ACTIVITY_ANALYSIS_DETAILED_DEEPSEEK_BUTTON_TEXT,
        ACTIVITY_ANALYSIS_CALENDAR_BUTTON_TEXT,
        MAIN_MENU_BUTTON_TEXT,
    ]
    assert "📅 Сегодня" not in texts
    assert "📅 Сегодня копия" not in texts
    assert "📅 Сегодня копия 2" not in texts
    assert "📊 Неделя" not in texts
    assert "📈 Месяц" not in texts
    assert "📅 Сегодня гигачат" not in texts


def test_openrouter_formatter_shows_free_model():
    text = format_openrouter({"model_name": "openrouter/free", "tariff": "free"})
    assert "openrouter/free" in text
    assert "free" in text


def test_admin_menu_has_gigachat_button():
    texts = _inline_keyboard_texts(_admin_menu_kb())
    assert "GigaChat / AI" in texts


def test_gigachat_formatter_shows_ranges():
    text = format_gigachat({"started_today": 3, "sent_today": 2, "failed_today": 1, "success_rate_today": 66.7})
    assert "GigaChat / AI" in text
    assert "66.7%" in text


def test_openrouter_parse_kbju_json_normalizes_synonym_keys():
    raw = """
    {
      "items": [
        {
          "name": "Яйца варёные",
          "amount_g": 100,
          "calories": 157,
          "proteins": 13,
          "fats": 11,
          "carbohydrates": 1.1
        }
      ],
      "total": {
        "ккал": 157,
        "белки": 13,
        "жиры": 11,
        "углеводы": 1.1
      }
    }
    """
    parsed = OpenRouterService.parse_kbju_json(raw)

    assert parsed is not None
    assert parsed["items"][0]["grams"] == 100
    assert parsed["items"][0]["kcal"] == 157
    assert parsed["items"][0]["protein"] == 13
    assert parsed["items"][0]["fat"] == 11
    assert parsed["items"][0]["carbs"] == 1.1
    assert parsed["total"]["kcal"] == 157
    assert parsed["total"]["protein"] == 13
    assert parsed["total"]["fat"] == 11
    assert parsed["total"]["carbs"] == 1.1


def test_openrouter_parse_ai_response_text_mode_returns_raw_text():
    raw_text = "<b>Отчёт за день</b>\nОбычный текст без JSON."
    parsed = OpenRouterService.parse_ai_response(raw_text, mode="text")
    assert parsed == raw_text


def test_openrouter_parse_kbju_json_returns_none_for_plain_text_response():
    raw_text = "<b>Отчёт за день</b>\nОбычный текст без JSON."
    parsed = OpenRouterService.parse_kbju_json(raw_text)
    assert parsed is None
